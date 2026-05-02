"""Sync Manager — central coordinator for clipboard synchronization.

Responsibilities:
- Listen for local clipboard changes and broadcast to peers
- Receive clipboard content from peers and write to local clipboard
- Deduplication (hash-based)
- Loop prevention (don't reflect remote changes back)
- Throttle rapid changes
"""

import logging
import threading
import time
import uuid
from typing import Callable, Optional

from internal.clipboard.format import ClipboardContent, SyncMessage
from internal.clipboard.history import ClipboardHistory
from internal.clipboard.platform import create_monitor, create_reader, create_writer

logger = logging.getLogger(__name__)

# Minimum interval between outgoing syncs (debounce)
SYNC_DEBOUNCE = 0.3
# Hash ring size for recently-synced content dedup
DEDUP_RING_SIZE = 64


class SyncManager:
    def __init__(self, device_id: str, device_name: str,
                 reader=None, writer=None, monitor=None,
                 history: Optional[ClipboardHistory] = None,
                 sync_debounce: float = 0.3):
        self._device_id = device_id
        self._device_name = device_name
        self._reader = reader if reader is not None else create_reader()
        self._writer = writer if writer is not None else create_writer()
        self._monitor = monitor if monitor is not None else create_monitor()
        self._history = history
        self._enabled = True
        self._on_send: Callable | None = None
        self._lock = threading.Lock()
        self._last_local_hash: str | None = None
        self._dedup_ring: list[str] = []
        self._sync_debounce = sync_debounce
        self._pending_timer: threading.Timer | None = None

    @property
    def on_send(self) -> Callable | None:
        return self._on_send

    @on_send.setter
    def on_send(self, callback: Callable):
        self._on_send = callback

    def set_enabled(self, enabled: bool):
        with self._lock:
            self._enabled = enabled

    def start(self):
        self._monitor.start(self._on_clipboard_change)
        logger.info("SyncManager started on %s", self._device_name)

    def stop(self):
        with self._lock:
            if self._pending_timer is not None:
                self._pending_timer.cancel()
                self._pending_timer = None
        self._monitor.stop()
        logger.info("SyncManager stopped")

    def handle_remote_message(self, msg: SyncMessage):
        """Process a clipboard message received from a peer."""
        with self._lock:
            if not self._enabled:
                return

        content = msg.content
        if content.is_empty():
            return

        content_hash = content.hash_key()

        with self._lock:
            # Skip if we just sent this content (loop prevention)
            if content_hash == self._last_local_hash:
                return

            # Skip if recently processed
            if content_hash in self._dedup_ring:
                return

            self._dedup_ring.append(content_hash)
            if len(self._dedup_ring) > DEDUP_RING_SIZE:
                self._dedup_ring = self._dedup_ring[-DEDUP_RING_SIZE:]

            # Set _last_local_hash so the clipboard monitor ignores the
            # write we're about to make (prevents re-broadcasting remote content).
            self._last_local_hash = content_hash

        # Write to local clipboard
        logger.info(
            "Writing remote clipboard from %s: %d format(s)",
            msg.source_device, len(content.types),
        )
        self._writer.write(content)

    def _on_clipboard_change(self):
        """Called by the clipboard monitor when local clipboard changes.

        Uses a coalescing timer: rapid changes within the debounce window
        cancel the pending send and restart the timer, so only one send
        fires after the clipboard has settled. This prevents duplicate
        sends caused by applications that set clipboard formats in
        multiple steps (each triggering WM_CLIPBOARDUPDATE on Windows).
        """
        with self._lock:
            if not self._enabled:
                return

            had_pending = self._pending_timer is not None

            # Cancel any pending send — restart the coalescing window
            if self._pending_timer is not None:
                self._pending_timer.cancel()
                self._pending_timer = None

        # Read clipboard content (outside lock to avoid blocking)
        content = self._reader.read()
        if content.is_empty():
            return

        # Record in clipboard history (if available)
        if self._history is not None:
            try:
                self._history.add(content)
            except Exception:
                logger.debug("Failed to add to clipboard history", exc_info=True)

        content_hash = content.hash_key()

        with self._lock:
            if content_hash == self._last_local_hash:
                if had_pending:
                    # Same content re-emerged while a send was pending.
                    # The content hasn't been sent yet — restart the timer
                    # to coalesce these duplicate clipboard events.
                    pass  # fall through to reschedule the timer
                else:
                    # Already sent this exact content — suppress duplicate
                    return

            self._last_local_hash = content_hash

            self._dedup_ring.append(content_hash)
            if len(self._dedup_ring) > DEDUP_RING_SIZE:
                self._dedup_ring = self._dedup_ring[-DEDUP_RING_SIZE:]

        # Schedule the actual send after the debounce window
        with self._lock:
            self._pending_timer = threading.Timer(
                self._sync_debounce,
                self._do_send,
                args=[content, content_hash],
            )
            self._pending_timer.daemon = True
            self._pending_timer.start()

    def _do_send(self, content: ClipboardContent, content_hash: str):
        """Fire the actual send after the debounce timer expires."""
        with self._lock:
            self._pending_timer = None

        msg = SyncMessage(
            content=content,
            msg_id=uuid.uuid4().hex,
            source_device=self._device_id,
        )

        logger.info("Local clipboard changed: %d format(s)", len(content.types))

        if self._on_send:
            self._on_send(msg)
