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
from typing import Callable

from internal.clipboard.format import ClipboardContent, SyncMessage
from internal.clipboard.platform import create_monitor, create_reader, create_writer

logger = logging.getLogger(__name__)

# Minimum interval between outgoing syncs (debounce)
SYNC_DEBOUNCE = 0.3
# Hash ring size for recently-synced content dedup
DEDUP_RING_SIZE = 64


class SyncManager:
    def __init__(self, device_id: str, device_name: str,
                 reader=None, writer=None, monitor=None):
        self._device_id = device_id
        self._device_name = device_name
        self._reader = reader if reader is not None else create_reader()
        self._writer = writer if writer is not None else create_writer()
        self._monitor = monitor if monitor is not None else create_monitor()
        self._enabled = True
        self._on_send: Callable | None = None
        self._lock = threading.Lock()
        self._last_local_hash: str | None = None
        self._last_send_time = 0.0
        self._dedup_ring: list[str] = []

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

        # Write to local clipboard
        logger.debug(
            "Writing remote clipboard from %s: %d format(s)",
            msg.source_device, len(content.types),
        )
        self._writer.write(content)

    def _on_clipboard_change(self):
        """Called by the clipboard monitor when local clipboard changes."""
        with self._lock:
            if not self._enabled:
                return

        content = self._reader.read()
        if content.is_empty():
            return

        content_hash = content.hash_key()

        with self._lock:
            now = time.time()
            if now - self._last_send_time < SYNC_DEBOUNCE:
                return
            if content_hash == self._last_local_hash:
                return
            self._last_local_hash = content_hash
            self._last_send_time = now

            self._dedup_ring.append(content_hash)
            if len(self._dedup_ring) > DEDUP_RING_SIZE:
                self._dedup_ring = self._dedup_ring[-DEDUP_RING_SIZE:]

        msg = SyncMessage(
            content=content,
            msg_id=uuid.uuid4().hex,
            source_device=self._device_id,
        )

        logger.debug("Local clipboard changed: %d format(s)", len(content.types))

        if self._on_send:
            self._on_send(msg)
