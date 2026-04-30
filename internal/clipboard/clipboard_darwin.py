"""macOS clipboard implementation.

Uses subprocess to call macOS clipboard commands:
- pbpaste / pbcopy for text
- pbpaste -Prefer html / rtf for reading rich formats
- osascript with temp files for writing rich formats (avoids escaping issues)
- PIL.ImageGrab for image handling

Clipboard monitoring polls NSPasteboard.general.changeCount via osascript,
since macOS provides no event-driven clipboard API.
"""

import logging
import os
import subprocess
import tempfile
import threading
import time
from io import BytesIO

from internal.clipboard.clipboard import ClipboardMonitor, ClipboardReader, ClipboardWriter
from internal.clipboard.format import ClipboardContent, ContentType

logger = logging.getLogger(__name__)

POLL_INTERVAL = 0.4


class _ClipboardReader(ClipboardReader):
    def read(self) -> ClipboardContent:
        content = ClipboardContent(timestamp=time.time())

        text = self._get_text()
        if text:
            content.types[ContentType.TEXT] = text

        html = self._get_html()
        if html:
            content.types[ContentType.HTML] = html

        rtf = self._get_rtf()
        if rtf:
            content.types[ContentType.RTF] = rtf

        img = self._get_image()
        if img:
            content.types[ContentType.IMAGE_PNG] = img

        return content

    def _get_text(self) -> bytes:
        try:
            result = subprocess.run(
                ["pbpaste", "-Prefer", "txt"],
                capture_output=True, timeout=2,
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout
        except Exception:
            logger.debug("pbpaste text read failed", exc_info=True)
        return b""

    def _get_html(self) -> bytes:
        try:
            result = subprocess.run(
                ["pbpaste", "-Prefer", "html"],
                capture_output=True, timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                data = result.stdout
                if b"<" in data and b">" in data:
                    return data
        except Exception:
            logger.debug("pbpaste html read failed", exc_info=True)
        return b""

    def _get_rtf(self) -> bytes:
        try:
            result = subprocess.run(
                ["pbpaste", "-Prefer", "rtf"],
                capture_output=True, timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                data = result.stdout
                if data.startswith(b"{\\rtf"):
                    return data
        except Exception:
            logger.debug("pbpaste rtf read failed", exc_info=True)
        return b""

    def _get_image(self) -> bytes:
        try:
            from PIL import ImageGrab
        except ImportError:
            logger.debug("PIL import failed, image read unavailable")
            return b""

        try:
            img = ImageGrab.grabclipboard()
            if img is None:
                return b""
            buf = BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            logger.debug("ImageGrab read failed", exc_info=True)
            return b""


class _ClipboardWriter(ClipboardWriter):
    def write(self, content: ClipboardContent):
        best = content.best_format()
        if best is None:
            return
        fmt_type, data = best

        if fmt_type == ContentType.TEXT:
            self._set_text(data)
        elif fmt_type == ContentType.HTML:
            self._set_html(data)
        elif fmt_type == ContentType.RTF:
            self._set_rtf(data)
        elif fmt_type == ContentType.IMAGE_PNG:
            self._set_image(data)

    def _set_text(self, data: bytes):
        try:
            subprocess.run(["pbcopy"], input=data, timeout=2)
        except Exception:
            logger.debug("pbcopy write failed", exc_info=True)

    def _set_html(self, data: bytes):
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".html", delete=False,
            ) as f:
                f.write(data)
                tmp_path = f.name

            script = (
                f'set the clipboard to (read (POSIX file "{tmp_path}") '
                f'as «class HTML»)'
            )
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, timeout=3,
            )
        except Exception:
            logger.debug("osascript html write failed, falling back to text", exc_info=True)
            self._set_text(data)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    logger.debug("Failed to remove temp file: %s", tmp_path, exc_info=True)

    def _set_rtf(self, data: bytes):
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".rtf", delete=False,
            ) as f:
                f.write(data)
                tmp_path = f.name

            script = (
                f'set the clipboard to (read (POSIX file "{tmp_path}") '
                f'as «class RTF »)'
            )
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, timeout=3,
            )
        except Exception:
            logger.debug("osascript rtf write failed", exc_info=True)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    logger.debug("Failed to remove temp file: %s", tmp_path, exc_info=True)

    def _set_image(self, data: bytes):
        tmp_path = None
        try:
            from PIL import Image
            Image.open(BytesIO(data))

            with tempfile.NamedTemporaryFile(
                suffix=".png", delete=False,
            ) as f:
                f.write(data)
                tmp_path = f.name

            script = (
                f'set the clipboard to (read (POSIX file "{tmp_path}") '
                f'as «class PNGf»)'
            )
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, timeout=3,
            )
        except Exception:
            logger.debug("osascript image write failed", exc_info=True)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    logger.debug("Failed to remove temp file: %s", tmp_path, exc_info=True)


class DarwinClipboardMonitor(ClipboardMonitor):
    """Poll-based clipboard monitor for macOS."""

    def __init__(self):
        self._running = False
        self._thread = None
        self._callback = None

    def start(self, callback):
        logger.info("Clipboard monitor started")
        self._callback = callback
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        logger.info("Clipboard monitor stopped")
        self._running = False

    def _poll_loop(self):
        last_count = self._get_change_count()
        logger.debug("Initial changeCount: %d", last_count)
        while self._running:
            time.sleep(POLL_INTERVAL)
            current = self._get_change_count()
            if current != last_count:
                logger.debug("Clipboard change detected, changeCount: %d", current)
                last_count = current
                if self._callback:
                    try:
                        self._callback()
                    except Exception:
                        pass

    def _get_change_count(self) -> int:
        try:
            result = subprocess.run(
                ["osascript", "-e", "get the clipboard's «class ccnt»"],
                capture_output=True, timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                return int(result.stdout.strip())
        except (ValueError, Exception):
            pass
        return 0


def create_monitor() -> ClipboardMonitor:
    return DarwinClipboardMonitor()


def create_reader() -> ClipboardReader:
    return _ClipboardReader()


def create_writer() -> ClipboardWriter:
    return _ClipboardWriter()
