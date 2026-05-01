"""Peer-to-peer file transfer for CopyBoard.

Handles chunked file transfers between paired devices over the existing
TLS-encrypted transport. Messages are encoded with the standard binary
frame format and route through the same connections as clipboard sync.

Message types (stored in ``msg_type`` field of the JSON payload):
  file_request  -- sender announces a file the receiver may accept/reject
  file_chunk    -- a 64 KB base64-encoded slice of the file
  file_ack      -- receiver accepts a file_request
  file_reject   -- receiver declines a file_request
  file_complete -- receiver confirms successful (or failed) reception

Transfer flow (sender):
  1. User selects file -> send_file() called
  2. FILE_REQUEST sent to all connected peers via broadcast_fn
  3. Wait for FILE_ACK from at least one peer
  4. Read file in 64 KB chunks, send each as FILE_CHUNK
  5. After final chunk, wait for FILE_COMPLETE (with timeout)

Transfer flow (receiver):
  1. FILE_REQUEST arrives -> callback to UI for user decision
  2. If accepted -> FILE_ACK sent
  3. FILE_CHUNK arrives -> write to temp file, report progress
  4. Last chunk -> verify size, move to output directory
  5. FILE_COMPLETE sent with status (success / error_*)
"""

import base64
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from internal.protocol.codec import encode_frame

logger = logging.getLogger(__name__)

# ---- Constants -----------------------------------------------------------

CHUNK_SIZE = 65536                     # 64 KB per chunk
TRANSFER_TIMEOUT = 120.0               # seconds -- overall transfer deadline
COMPLETION_WAIT_TIMEOUT = 60.0         # seconds -- wait for FILE_COMPLETE after last chunk

_MIME_BY_EXT: dict[str, str] = {
    ".txt": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".xml": "application/xml",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".mp4": "video/mp4",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".zip": "application/zip",
    ".tar": "application/x-tar",
    ".gz": "application/gzip",
    ".7z": "application/x-7z-compressed",
    ".py": "text/x-python",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def _guess_mime_type(file_name: str) -> str:
    """Return a MIME type for *file_name* based on its extension."""
    ext = Path(file_name).suffix.lower()
    return _MIME_BY_EXT.get(ext, "application/octet-stream")


def _safe_remove(path: Path) -> None:
    """Remove a file, suppressing any OSError."""
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _sanitize_file_name(file_name: str) -> str:
    """Strip path separators and traversal components from a remote file name."""
    name = Path(file_name).name
    name = name.lstrip(".")
    if not name:
        name = "unnamed_file"
    return name


# ---- FileTransferManager -------------------------------------------------

class FileTransferManager:
    """Manages peer-to-peer file transfers over the existing transport layer.

    Parameters
    ----------
    device_id:
        The local device identifier (used as the source in frame headers).
    output_dir:
        Directory where received files are saved.
        Defaults to ``~/Downloads/CopyBoard``.
    """

    CHUNK_SIZE = CHUNK_SIZE

    def __init__(self, device_id: str, output_dir: Optional[str] = None,
                 transfer_timeout: float = TRANSFER_TIMEOUT):
        self._device_id = device_id

        if output_dir is None:
            output_dir = str(Path.home() / "Downloads" / "CopyBoard")
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._transfer_timeout = transfer_timeout

        # transfer_id -> dict (see _init_outgoing / _init_incoming helpers)
        self._transfers: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

        # ---- UI callbacks ----
        self._on_transfer_progress: Optional[Callable[[str, float], None]] = None
        self._on_transfer_complete: Optional[Callable[[str, bool], None]] = None
        self._on_file_received: Optional[Callable[[str, str, str], None]] = None
        self._on_transfer_request: Optional[Callable[[str, str, int, str, Callable], None]] = None

    # ------------------------------------------------------------------
    # Callback registration
    # ------------------------------------------------------------------

    def set_on_transfer_progress(self, callback: Callable[[str, float], None]) -> None:
        """*callback(transfer_id, fraction)* -- called as chunks arrive or are sent."""
        self._on_transfer_progress = callback

    def set_on_transfer_complete(self, callback: Callable[[str, bool], None]) -> None:
        """*callback(transfer_id, success)* -- called when a transfer finishes or fails."""
        self._on_transfer_complete = callback

    def set_on_file_received(self, callback: Callable[[str, str, str], None]) -> None:
        """*callback(transfer_id, saved_path, file_name)* -- called after a file is
        saved successfully to the output directory."""
        self._on_file_received = callback

    def set_on_transfer_request(
        self, callback: Callable[[str, str, int, str, Callable], None],
    ) -> None:
        """*callback(transfer_id, file_name, file_size, mime_type, send_fn)* --
        called when a remote peer wants to send a file.

        The callback should call :meth:`accept_transfer` or :meth:`reject_transfer`
        with *transfer_id* and *send_fn* to indicate the user's choice.
        """
        self._on_transfer_request = callback

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_file(self, file_path: str, broadcast_fn: Callable[[bytes], None]) -> str:
        """Start sending *file_path* to all connected peers.

        Parameters
        ----------
        file_path:
            Absolute or relative path to the file to send.
        broadcast_fn:
            Callable that takes encoded ``bytes`` and sends them to all
            connected peers (typically ``TransportManager.broadcast``).

        Returns
        -------
        transfer_id:
            A unique hex string identifying this transfer.

        Raises
        ------
        FileNotFoundError:
            If *file_path* does not exist or is not a regular file.
        """
        file_path = Path(file_path).resolve()
        if not file_path.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")

        transfer_id = uuid.uuid4().hex
        file_size = file_path.stat().st_size
        file_name = file_path.name
        mime_type = _guess_mime_type(file_name)
        total_chunks = max((file_size + self.CHUNK_SIZE - 1) // self.CHUNK_SIZE, 1)

        with self._lock:
            self._transfers[transfer_id] = {
                "type": "outgoing",
                "file_path": str(file_path),
                "file_name": file_name,
                "file_size": file_size,
                "mime_type": mime_type,
                "total_chunks": total_chunks,
                "state": "awaiting_ack",
                "start_time": time.time(),
                "acked": False,
            }

        self._send_as_frame(
            {
                "msg_type": "file_request",
                "transfer_id": transfer_id,
                "file_name": file_name,
                "file_size": file_size,
                "mime_type": mime_type,
            },
            broadcast_fn,
        )

        logger.info(
            "File transfer %s initiated: %s (%d bytes, %d chunks)",
            transfer_id[:8], file_name, file_size, total_chunks,
        )
        return transfer_id

    def accept_transfer(self, transfer_id: str, send_fn: Callable[[bytes], None]) -> None:
        """Accept an incoming file transfer request.

        Call this from the ``on_transfer_request`` callback to indicate
        that the user wants to receive the file.
        """
        with self._lock:
            transfer = self._transfers.get(transfer_id)
            if transfer is None or transfer.get("type") != "incoming":
                logger.warning("Cannot accept unknown or outgoing transfer: %s", transfer_id[:8])
                return
            if transfer["state"] != "pending":
                logger.debug("Transfer %s already in state %s", transfer_id[:8], transfer["state"])
                return

            transfer["state"] = "receiving"
            temp_path = self._output_dir / f".{transfer_id}.part"
            try:
                transfer["temp_fh"] = open(str(temp_path), "wb")
            except OSError as exc:
                logger.error("Cannot create temp file for transfer %s: %s", transfer_id[:8], exc)
                self._transfers.pop(transfer_id, None)
                self._send_as_frame(
                    {"msg_type": "file_reject", "transfer_id": transfer_id},
                    send_fn,
                )
                return

        self._send_as_frame(
            {"msg_type": "file_ack", "transfer_id": transfer_id},
            send_fn,
        )
        logger.info(
            "Accepted file transfer: %s (%s)",
            transfer_id[:8], transfer.get("file_name", "?"),
        )

    def cancel_transfer(self, transfer_id: str, broadcast_fn: Callable[[bytes], None] | None = None) -> bool:
        """Cancel an active transfer (incoming or outgoing).

        Returns True if the transfer was found and cancelled, False otherwise.
        """
        with self._lock:
            transfer = self._transfers.get(transfer_id)
            if transfer is None:
                return False
            transfer["cancelled"] = True

        # Clean up temp file for incoming transfers
        if transfer.get("type") == "incoming":
            temp_fh = transfer.get("temp_fh")
            if temp_fh is not None:
                try:
                    temp_fh.close()
                except Exception:
                    pass
            temp_path = self._output_dir / f".{transfer_id}.part"
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

        # Notify peer
        if broadcast_fn is not None:
            if transfer.get("type") == "outgoing" and not transfer.get("acked"):
                # Haven't started sending yet — just send reject
                pass  # peer already knows from the request
            self._send_as_frame(
                {"msg_type": "file_complete", "transfer_id": transfer_id, "status": "cancelled"},
                broadcast_fn,
            )

        with self._lock:
            self._transfers.pop(transfer_id, None)

        logger.info("Transfer %s cancelled by user", transfer_id[:8])
        if self._on_transfer_complete is not None:
            self._on_transfer_complete(transfer_id, False)
        return True

    def reject_transfer(self, transfer_id: str, send_fn: Callable[[bytes], None]) -> None:
        """Reject an incoming file transfer request.

        Call this from the ``on_transfer_request`` callback to indicate
        that the user does not want to receive the file.
        """
        with self._lock:
            transfer = self._transfers.pop(transfer_id, None)

        if transfer and transfer.get("temp_fh") is not None:
            try:
                transfer["temp_fh"].close()
            except Exception:
                pass
            _safe_remove(self._output_dir / f".{transfer_id}.part")

        self._send_as_frame(
            {"msg_type": "file_reject", "transfer_id": transfer_id},
            send_fn,
        )
        logger.info("Rejected file transfer: %s", transfer_id[:8])

    def handle_message(
        self,
        msg_type: str,
        payload: dict[str, Any],
        send_fn: Callable[[bytes], None],
    ) -> None:
        """Route an incoming file-transfer message to the correct handler.

        Parameters
        ----------
        msg_type:
            One of ``"file_request"``, ``"file_chunk"``, ``"file_ack"``,
            ``"file_reject"``, or ``"file_complete"``.
        payload:
            The fully-decoded JSON payload (the ``_raw_payload`` attribute
            from the decoded ``SyncMessage``).
        send_fn:
            Callable to send a response (typically ``TransportManager.broadcast``).
        """
        handler_map: dict[str, Callable] = {
            "file_request": self._handle_file_request,
            "file_chunk": self._handle_file_chunk,
            "file_ack": self._handle_file_ack,
            "file_reject": self._handle_file_reject,
            "file_complete": self._handle_file_complete,
        }
        handler = handler_map.get(msg_type)
        if handler is None:
            logger.debug("Unknown file transfer message type: %s", msg_type)
            return
        handler(payload, send_fn)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _send_as_frame(payload_dict: dict[str, Any], send_fn: Callable[[bytes], None]) -> None:
        """JSON-encode *payload_dict*, wrap it in a binary frame, and call *send_fn*."""
        data = encode_frame(payload_dict)
        send_fn(data)

    # ------------------------------------------------------------------
    # Message handlers (receiver side)
    # ------------------------------------------------------------------

    def _handle_file_request(self, payload: dict, send_fn: Callable[[bytes], None]) -> None:
        transfer_id = payload.get("transfer_id", "")
        file_name = _sanitize_file_name(payload.get("file_name", "unknown"))
        file_size = payload.get("file_size", 0)
        mime_type = payload.get("mime_type", "application/octet-stream")

        logger.info(
            "Incoming file transfer request: %s (%s, %d bytes)",
            file_name, transfer_id[:8], file_size,
        )

        total_chunks = max((file_size + self.CHUNK_SIZE - 1) // self.CHUNK_SIZE, 1) if file_size > 0 else 1

        with self._lock:
            self._transfers[transfer_id] = {
                "type": "incoming",
                "file_name": file_name,
                "file_size": file_size,
                "mime_type": mime_type,
                "total_chunks": total_chunks,
                "received_chunks": 0,
                "received_bytes": 0,
                "temp_fh": None,
                "state": "pending",
                "start_time": time.time(),
                "chunks": {},  # chunk_index -> bytes (sparse; supports out-of-order)
            }

        if self._on_transfer_request is not None:
            self._on_transfer_request(transfer_id, file_name, file_size, mime_type, send_fn)
        else:
            # No UI callback registered -- auto-accept for headless operation
            logger.info("Auto-accepting transfer %s (no UI callback registered)", transfer_id[:8])
            self.accept_transfer(transfer_id, send_fn)

    def _handle_file_chunk(self, payload: dict, send_fn: Callable[[bytes], None]) -> None:
        transfer_id = payload.get("transfer_id", "")
        chunk_index = payload.get("chunk_index", 0)
        total_chunks = payload.get("total_chunks", 0)
        b64_data = payload.get("data", "")

        with self._lock:
            transfer = self._transfers.get(transfer_id)
            if transfer is None:
                logger.debug("Chunk for unknown transfer: %s", transfer_id[:8])
                return
            if transfer.get("state") != "receiving":
                logger.debug(
                    "Chunk for transfer in state %s: %s",
                    transfer.get("state"), transfer_id[:8],
                )
                return

        # Decode outside the lock (b64decode can be slow for large chunks)
        try:
            chunk_data = base64.b64decode(b64_data)
        except Exception:
            logger.warning("Invalid base64 in chunk %d for transfer %s", chunk_index, transfer_id[:8])
            return

        with self._lock:
            # Re-acquire -- transfer may have been removed while we were decoding
            transfer = self._transfers.get(transfer_id)
            if transfer is None or transfer.get("state") != "receiving":
                return

            transfer["chunks"][chunk_index] = chunk_data
            transfer["received_bytes"] += len(chunk_data)
            transfer["received_chunks"] = len(transfer["chunks"])
            total = transfer.get("total_chunks", total_chunks)
            progress = transfer["received_chunks"] / max(total, 1)
            is_last = transfer["received_chunks"] >= total

        if self._on_transfer_progress is not None:
            self._on_transfer_progress(transfer_id, progress)

        if is_last:
            self._finalize_received_file(transfer_id, transfer, total, send_fn)

    def _finalize_received_file(
        self,
        transfer_id: str,
        transfer: dict,
        total_chunks: int,
        send_fn: Callable[[bytes], None],
    ) -> None:
        """Write all received chunks in order, verify size, move to output dir."""
        file_name = transfer.get("file_name", "unknown")
        temp_fh = transfer.get("temp_fh")

        if temp_fh is None:
            logger.error("No open temp file for transfer %s", transfer_id[:8])
            with self._lock:
                self._transfers.pop(transfer_id, None)
            self._send_as_frame(
                {"msg_type": "file_complete", "transfer_id": transfer_id, "status": "error_internal"},
                send_fn,
            )
            if self._on_transfer_complete:
                self._on_transfer_complete(transfer_id, False)
            return

        temp_path = self._output_dir / f".{transfer_id}.part"

        try:
            # Write chunks in sequential order to the temp file
            for idx in range(total_chunks):
                chunk_data = transfer["chunks"].get(idx)
                if chunk_data is None:
                    logger.error(
                        "Missing chunk %d/%d for transfer %s",
                        idx, total_chunks, transfer_id[:8],
                    )
                    temp_fh.close()
                    _safe_remove(temp_path)
                    with self._lock:
                        self._transfers.pop(transfer_id, None)
                    self._send_as_frame(
                        {
                            "msg_type": "file_complete",
                            "transfer_id": transfer_id,
                            "status": "error_missing_chunks",
                        },
                        send_fn,
                    )
                    if self._on_transfer_complete:
                        self._on_transfer_complete(transfer_id, False)
                    return
                temp_fh.write(chunk_data)

            temp_fh.close()
            transfer["temp_fh"] = None

            # Verify final file size matches what was advertised
            actual_size = temp_path.stat().st_size
            expected_size = transfer["file_size"]
            if actual_size != expected_size:
                logger.error(
                    "Size mismatch for transfer %s: expected %d, got %d",
                    transfer_id[:8], expected_size, actual_size,
                )
                _safe_remove(temp_path)
                with self._lock:
                    self._transfers.pop(transfer_id, None)
                self._send_as_frame(
                    {
                        "msg_type": "file_complete",
                        "transfer_id": transfer_id,
                        "status": "error_size_mismatch",
                    },
                    send_fn,
                )
                if self._on_transfer_complete:
                    self._on_transfer_complete(transfer_id, False)
                return

            # Move to final destination, avoiding name collisions
            dest_path = self._output_dir / _sanitize_file_name(file_name)
            if dest_path.resolve().parent != self._output_dir.resolve():
                logger.error("Path traversal blocked for transfer %s: %s", transfer_id[:8], file_name)
                _safe_remove(temp_path)
                with self._lock:
                    self._transfers.pop(transfer_id, None)
                self._send_as_frame(
                    {"msg_type": "file_complete", "transfer_id": transfer_id, "status": "error_security"},
                    send_fn,
                )
                if self._on_transfer_complete:
                    self._on_transfer_complete(transfer_id, False)
                return
            if dest_path.exists():
                stem = dest_path.stem
                suffix = dest_path.suffix
                counter = 1
                while dest_path.exists():
                    dest_path = self._output_dir / f"{stem} ({counter}){suffix}"
                    counter += 1

            os.rename(str(temp_path), str(dest_path))

            with self._lock:
                self._transfers.pop(transfer_id, None)

            self._send_as_frame(
                {"msg_type": "file_complete", "transfer_id": transfer_id, "status": "success"},
                send_fn,
            )
            logger.info("File received successfully: %s -> %s", file_name, dest_path)

            if self._on_file_received is not None:
                self._on_file_received(transfer_id, str(dest_path), file_name)
            if self._on_transfer_complete is not None:
                self._on_transfer_complete(transfer_id, True)

        except OSError as exc:
            logger.error("I/O error finalizing transfer %s: %s", transfer_id[:8], exc)
            if temp_fh is not None and not temp_fh.closed:
                try:
                    temp_fh.close()
                except Exception:
                    pass
            _safe_remove(temp_path)
            with self._lock:
                self._transfers.pop(transfer_id, None)
            self._send_as_frame(
                {"msg_type": "file_complete", "transfer_id": transfer_id, "status": "error_disk"},
                send_fn,
            )
            if self._on_transfer_complete:
                self._on_transfer_complete(transfer_id, False)

    # ------------------------------------------------------------------
    # Message handlers (sender side)
    # ------------------------------------------------------------------

    def _handle_file_ack(self, payload: dict, send_fn: Callable[[bytes], None]) -> None:
        transfer_id = payload.get("transfer_id", "")

        with self._lock:
            transfer = self._transfers.get(transfer_id)
            if transfer is None or transfer.get("type") != "outgoing":
                return
            if transfer.get("acked"):
                return  # chunks already being sent
            transfer["acked"] = True

        logger.info(
            "File transfer %s acknowledged by peer -- starting chunk send", transfer_id[:8],
        )

        thread = threading.Thread(
            target=self._send_chunks,
            args=(transfer_id, send_fn),
            daemon=True,
            name=f"file-xfer-{transfer_id[:8]}",
        )
        thread.start()

    def _handle_file_reject(self, payload: dict, send_fn: Callable[[bytes], None]) -> None:
        transfer_id = payload.get("transfer_id", "")

        with self._lock:
            transfer = self._transfers.pop(transfer_id, None)

        if transfer is not None and transfer.get("type") == "outgoing":
            logger.info(
                "File transfer %s rejected by peer (%s)",
                transfer_id[:8], transfer.get("file_name", "?"),
            )
            if self._on_transfer_complete is not None:
                self._on_transfer_complete(transfer_id, False)

    def _handle_file_complete(self, payload: dict, send_fn: Callable[[bytes], None]) -> None:
        transfer_id = payload.get("transfer_id", "")
        status = payload.get("status", "unknown")

        with self._lock:
            transfer = self._transfers.pop(transfer_id, None)

        if transfer is not None and transfer.get("type") == "outgoing":
            success = status == "success"
            logger.info(
                "File transfer %s %s (%s) -- status=%s",
                transfer_id[:8],
                "completed" if success else "failed",
                transfer.get("file_name", "?"),
                status,
            )
            if self._on_transfer_complete is not None:
                self._on_transfer_complete(transfer_id, success)

    # ------------------------------------------------------------------
    # Chunked send logic (runs in background thread)
    # ------------------------------------------------------------------

    def _send_chunks(self, transfer_id: str, broadcast_fn: Callable[[bytes], None]) -> None:
        """Read the file and send all chunks (called from a background thread)."""
        with self._lock:
            transfer = self._transfers.get(transfer_id)
            if transfer is None:
                return
            file_path = transfer["file_path"]
            total_chunks = transfer["total_chunks"]
            file_name = transfer.get("file_name", "?")

        logger.info(
            "Sending %d chunks for transfer %s (%s)",
            total_chunks, transfer_id[:8], file_name,
        )

        try:
            with open(file_path, "rb") as fh:
                for chunk_index in range(total_chunks):
                    # Check for cancellation
                    with self._lock:
                        transfer = self._transfers.get(transfer_id)
                        if transfer is None or transfer.get("cancelled"):
                            logger.info("Transfer %s cancelled mid-send", transfer_id[:8])
                            if self._on_transfer_complete is not None:
                                self._on_transfer_complete(transfer_id, False)
                            return

                    chunk_data = fh.read(self.CHUNK_SIZE)
                    b64_data = base64.b64encode(chunk_data).decode("ascii")

                    file_chunk_payload = {
                        "msg_type": "file_chunk",
                        "transfer_id": transfer_id,
                        "chunk_index": chunk_index,
                        "total_chunks": total_chunks,
                        "data": b64_data,
                    }
                    self._send_as_frame(file_chunk_payload, broadcast_fn)

                    progress = (chunk_index + 1) / total_chunks
                    if self._on_transfer_progress is not None:
                        self._on_transfer_progress(transfer_id, progress)

                    # Small yield to avoid flooding socket buffers
                    time.sleep(0.005)

        except Exception as exc:
            logger.error(
                "Failed sending chunks for transfer %s (%s): %s",
                transfer_id[:8], file_name, exc,
            )
            with self._lock:
                self._transfers.pop(transfer_id, None)
            if self._on_transfer_complete is not None:
                self._on_transfer_complete(transfer_id, False)
            return

        logger.info(
            "All %d chunks sent for transfer %s -- waiting for FILE_COMPLETE",
            total_chunks, transfer_id[:8],
        )

        # Wait for FILE_COMPLETE from the receiver (with timeout)
        deadline = time.time() + COMPLETION_WAIT_TIMEOUT
        while time.time() < deadline:
            with self._lock:
                if transfer_id not in self._transfers:
                    # Transfer was cleaned up by _handle_file_complete
                    return
            time.sleep(0.5)

        # Timeout -- receiver never acknowledged completion
        with self._lock:
            stale = self._transfers.pop(transfer_id, None)
        if stale is not None:
            logger.warning(
                "File transfer %s timed out waiting for FILE_COMPLETE", transfer_id[:8],
            )
            if self._on_transfer_complete is not None:
                self._on_transfer_complete(transfer_id, False)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_transfers(self) -> list[dict]:
        """Return a snapshot of active transfers for UI display.

        Each dict contains:
          ``transfer_id``, ``file_name``, ``file_size``, ``direction``
          (``"up"`` or ``"down"``), ``state``, and ``progress`` (0.0–1.0).
        """
        result: list[dict] = []
        with self._lock:
            for tid, t in self._transfers.items():
                direction = "up" if t.get("type") == "outgoing" else "down"
                state = t.get("state", "unknown")
                total = max(t.get("total_chunks", 1), 1)
                if t.get("type") == "incoming":
                    progress = t.get("received_chunks", 0) / total
                elif state == "awaiting_ack":
                    progress = 0.0
                else:
                    progress = 0.5  # sending chunks (exact progress not stored)
                result.append({
                    "transfer_id": tid,
                    "file_name": t.get("file_name", "?"),
                    "file_size": t.get("file_size", 0),
                    "direction": direction,
                    "state": state,
                    "progress": min(progress, 1.0),
                })
        return result

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def cleanup_stale_transfers(self) -> None:
        """Remove transfers that have exceeded ``TRANSFER_TIMEOUT``.

        Call this periodically (e.g. every 30 s) to prevent memory leaks from
        abandoned transfers. Partial temp files are deleted.
        """
        now = time.time()
        with self._lock:
            stale_ids = [
                tid for tid, t in self._transfers.items()
                if now - t.get("start_time", 0) > self._transfer_timeout
            ]

        for tid in stale_ids:
            with self._lock:
                transfer = self._transfers.pop(tid, None)
            if transfer is None:
                continue

            if transfer.get("temp_fh") is not None:
                try:
                    transfer["temp_fh"].close()
                except Exception:
                    pass
            _safe_remove(self._output_dir / f".{tid}.part")

            logger.info(
                "Cleaned up stale transfer %s (%s)",
                tid[:8], transfer.get("file_name", "?"),
            )
            if self._on_transfer_complete is not None:
                self._on_transfer_complete(tid, False)
