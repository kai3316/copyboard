"""Clipboard history with local JSON persistence.

Stores up to 50 most recent clipboard entries in
  {config_dir}/clipboard_history.json
"""

import base64
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from internal.clipboard.format import ClipboardContent, ContentType
from internal.config.config import _config_dir

if TYPE_CHECKING:
    from internal.security.encryption import EncryptionManager

logger = logging.getLogger(__name__)

_CONTENT_TYPE_LABELS: dict[ContentType, str] = {
    ContentType.TEXT: "TEXT",
    ContentType.HTML: "HTML",
    ContentType.RTF: "RTF",
    ContentType.IMAGE_PNG: "IMAGE",
}


def _strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    plain = re.sub(r"<[^>]*>", "", text)
    plain = re.sub(r"\s+", " ", plain)
    return plain.strip()


def _build_preview(types: dict[ContentType, bytes]) -> str:
    """Build a human-readable preview from clipboard content."""
    if ContentType.TEXT in types:
        text = types[ContentType.TEXT].decode("utf-8", errors="replace")
        return text[:200]
    if ContentType.HTML in types:
        html = types[ContentType.HTML].decode("utf-8", errors="replace")
        plain = _strip_html(html)
        return plain[:200] if plain else "[HTML]"
    if ContentType.IMAGE_PNG in types:
        return "[Image]"
    if ContentType.RTF in types:
        return "[Rich Text]"
    return ""


def _map_type_to_label(content_type: ContentType) -> str:
    return _CONTENT_TYPE_LABELS.get(content_type, "TEXT")


def _map_label_to_type(label: str) -> ContentType:
    for ct, lbl in _CONTENT_TYPE_LABELS.items():
        if lbl == label:
            return ct
    return ContentType.TEXT


class ClipboardHistory:
    """Thread-safe clipboard history persisted to a local JSON file."""

    def __init__(self, storage_path: str | None = None, max_entries: int = 50,
                 enc_mgr: "EncryptionManager | None" = None):
        if storage_path:
            self._path = Path(storage_path)
        else:
            self._path = _config_dir() / "clipboard_history.json"
        self.MAX_ENTRIES = max_entries
        self._entries: list[dict] = []
        self._lock = threading.Lock()
        self._enc_mgr = enc_mgr
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, content: ClipboardContent) -> None:
        """Add a clipboard entry. Silently ignores empty content."""
        if content.is_empty():
            return
        best = content.best_format()
        if best is None:
            return
        best_type, _best_data = best

        preview = _build_preview(content.types)
        entry: dict = {
            "timestamp": content.timestamp or time.time(),
            "content_type": _map_type_to_label(best_type),
            "text_preview": preview,
            "types": {
                _map_type_to_label(t): base64.b64encode(data).decode("ascii")
                for t, data in content.types.items()
            },
            "source_device": content.source_device,
        }

        with self._lock:
            self._entries.insert(0, entry)
            if len(self._entries) > self.MAX_ENTRIES:
                self._entries = self._entries[: self.MAX_ENTRIES]
            self._save()

    def get_all(self) -> list[dict]:
        """Return all entries, newest first."""
        with self._lock:
            return list(self._entries)

    def search(self, query: str) -> list[dict]:
        """Case-insensitive search in text previews. Returns matching entries, newest first."""
        q = query.lower()
        with self._lock:
            return [e for e in self._entries if q in e.get("text_preview", "").lower()]

    def get(self, index: int) -> dict | None:
        """Get a single entry by index (0 = newest). Returns None if out of bounds."""
        with self._lock:
            if 0 <= index < len(self._entries):
                return dict(self._entries[index])
            return None

    def delete(self, index: int) -> bool:
        """Delete a single entry by index (0 = newest). Returns True if deleted."""
        with self._lock:
            if 0 <= index < len(self._entries):
                self._entries.pop(index)
                self._save()
                return True
            return False

    def clear(self) -> None:
        """Delete all history entries and persist the empty state."""
        with self._lock:
            self._entries.clear()
            self._save()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load history: %s", exc)
            return

        if isinstance(data, list):
            self._entries = data[: self.MAX_ENTRIES]
            if self._enc_mgr:
                for entry in self._entries:
                    self._decrypt_entry(entry)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = None
        try:
            entries_to_save = self._entries
            if self._enc_mgr:
                entries_to_save = [self._encrypt_entry(e) for e in self._entries]
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(self._path.parent), prefix=".history_tmp_", suffix=".json",
            )
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(entries_to_save, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self._path)
        except Exception as exc:
            logger.error("Failed to save history: %s", exc)
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _encrypt_entry(self, entry: dict) -> dict:
        """Return a copy of entry with types values encrypted for at-rest storage."""
        enc = self._enc_mgr
        if not enc:
            return entry
        e = dict(entry)
        if "types" in e:
            e["types"] = {
                k: enc.encrypt_storage(v) for k, v in e["types"].items()
            }
        return e

    def _decrypt_entry(self, entry: dict) -> None:
        """Decrypt types values in-place."""
        enc = self._enc_mgr
        if not enc or "types" not in entry:
            return
        for k, v in list(entry["types"].items()):
            pt = enc.decrypt_storage(v)
            if pt is not None:
                entry["types"][k] = pt
