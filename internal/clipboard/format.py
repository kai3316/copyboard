"""Clipboard content format definitions."""

import enum
import hashlib
import struct
from dataclasses import dataclass, field


class ContentType(enum.Enum):
    TEXT = 1
    HTML = 2
    RTF = 3
    IMAGE_PNG = 4


@dataclass
class ClipboardContent:
    """A snapshot of clipboard content, possibly containing multiple formats."""

    types: dict[ContentType, bytes] = field(default_factory=dict)
    source_device: str = ""
    timestamp: float = 0.0

    def hash_key(self) -> str:
        """Content-based dedup key."""
        h = hashlib.sha256()
        for t in sorted(self.types.keys(), key=lambda x: x.value):
            h.update(struct.pack(">I", t.value))
            h.update(self.types[t])
        return h.hexdigest()

    def is_empty(self) -> bool:
        return len(self.types) == 0

    def best_format(self) -> tuple[ContentType, bytes] | None:
        """Return the best available format: HTML > RTF > TEXT > IMAGE."""
        for fmt in (ContentType.HTML, ContentType.RTF, ContentType.TEXT, ContentType.IMAGE_PNG):
            if fmt in self.types:
                return fmt, self.types[fmt]
        return None


@dataclass
class SyncMessage:
    """Message exchanged between peers."""
    content: ClipboardContent
    msg_id: str = ""
    source_device: str = ""
