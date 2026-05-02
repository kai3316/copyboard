"""Linux clipboard implementation.

Uses xclip (X11) or wl-paste/wl-copy (Wayland) for clipboard access.
Auto-detects the display server in use.
"""

import hashlib
import logging
import os
import subprocess
import threading
import time
from io import BytesIO

from internal.clipboard.clipboard import ClipboardMonitor, ClipboardReader, ClipboardWriter
from internal.clipboard.format import ClipboardContent, ContentType

logger = logging.getLogger(__name__)

POLL_INTERVAL = 0.4


def _detect_display_backend() -> str:
    """Detect whether we're on X11 or Wayland."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    # Try to detect
    try:
        result = subprocess.run(
            ["loginctl", "show-session", "self", "-p", "Type"],
            capture_output=True, text=True, timeout=2,
        )
        if "wayland" in result.stdout.lower():
            return "wayland"
    except Exception:
        pass
    return "x11"  # default


_BACKEND = _detect_display_backend()
logger.info("Detected display backend: %s", _BACKEND)


_xclip_warned = False


def _has_xclip() -> bool:
    global _xclip_warned
    try:
        subprocess.run(["xclip", "-version"], capture_output=True, timeout=2)
        return True
    except Exception:
        if not _xclip_warned:
            logger.warning("xclip not found, X11 clipboard unavailable")
            _xclip_warned = True
        return False


_wl_warned = False


def _has_wl_copy() -> bool:
    global _wl_warned
    try:
        subprocess.run(["wl-copy", "--version"], capture_output=True, timeout=2)
        return True
    except Exception:
        if not _wl_warned:
            logger.warning("wl-copy not found, Wayland clipboard unavailable")
            _wl_warned = True
        return False


class _ClipboardReader(ClipboardReader):
    def read(self) -> ClipboardContent:
        content = ClipboardContent(timestamp=time.time())
        self._image_fmt = ""

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
            content.image_fmt = self._image_fmt

        logger.debug("Read %d format(s) from clipboard", len(content.types))
        return content

    def _get_text(self) -> bytes:
        try:
            if _BACKEND == "wayland" and _has_wl_copy():
                result = subprocess.run(
                    ["wl-paste", "--no-newline"],
                    capture_output=True, timeout=2,
                )
            else:
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o"],
                    capture_output=True, timeout=2,
                )
            if result.returncode == 0 and result.stdout:
                return result.stdout
        except Exception:
            logger.debug("Failed to read text from clipboard via %s", _BACKEND)
        return b""

    def _get_html(self) -> bytes:
        try:
            if _BACKEND == "wayland" and _has_wl_copy():
                result = subprocess.run(
                    ["wl-paste", "--type", "text/html"],
                    capture_output=True, timeout=2,
                )
            elif _BACKEND == "x11" and _has_xclip():
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o", "-t", "text/html"],
                    capture_output=True, timeout=2,
                )
            else:
                return b""
            if result.returncode == 0 and result.stdout.strip():
                data = result.stdout
                if b"<" in data:
                    return data
        except Exception:
            logger.debug("Failed to read HTML from clipboard")
        return b""

    def _get_rtf(self) -> bytes:
        try:
            if _BACKEND == "wayland" and _has_wl_copy():
                result = subprocess.run(
                    ["wl-paste", "--type", "text/rtf"],
                    capture_output=True, timeout=2,
                )
            elif _BACKEND == "x11" and _has_xclip():
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o", "-t", "text/rtf"],
                    capture_output=True, timeout=2,
                )
            else:
                return b""
            if result.returncode == 0 and result.stdout.strip():
                data = result.stdout
                # Relaxed check matching macOS — some apps emit RTF with
                # a leading BOM or whitespace before the {\rtf header.
                head = data[:200]
                if b"\\rtf" in head or b"{\\rtf" in head:
                    return data
        except Exception:
            logger.debug("Failed to read RTF from clipboard")
        return b""

    def _get_image(self) -> bytes:
        try:
            from PIL import ImageGrab
        except ImportError:
            pass
        else:
            try:
                img = ImageGrab.grabclipboard()
                if img:
                    buf = BytesIO()
                    img.save(buf, format="PNG")
                    self._image_fmt = "png"
                    return buf.getvalue()
            except Exception:
                logger.debug("ImageGrab.grabclipboard failed")

        # Fallback: try clipboard CLI tools with image/png target
        try:
            if _BACKEND == "wayland" and _has_wl_copy():
                result = subprocess.run(
                    ["wl-paste", "--type", "image/png"],
                    capture_output=True, timeout=2,
                )
            elif _BACKEND == "x11" and _has_xclip():
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o", "-t", "image/png"],
                    capture_output=True, timeout=2,
                )
            else:
                return b""
            if result.returncode == 0 and result.stdout:
                if result.stdout[:8] == b"\x89PNG\r\n\x1a\n":
                    self._image_fmt = "png"
                    return result.stdout
        except Exception:
            logger.debug("Failed to read image from clipboard via %s", _BACKEND)

        return b""


class _ClipboardWriter(ClipboardWriter):
    def write(self, content: ClipboardContent):
        # Write ALL available formats so the receiving application
        # can choose the richest one it supports.
        for fmt_type, data in content.types.items():
            logger.debug("Writing %s to clipboard (%d bytes)", fmt_type.name, len(data))
            if fmt_type == ContentType.TEXT:
                self._set_text(data)
            elif fmt_type == ContentType.HTML:
                self._set_html(data)
            elif fmt_type == ContentType.RTF:
                self._set_rtf(data)
            elif fmt_type == ContentType.IMAGE_PNG:
                self._set_image(data, content.image_fmt)
            # IMAGE_EMF is Windows-only, skip on Linux

    def _set_text(self, data: bytes):
        try:
            if _BACKEND == "wayland" and _has_wl_copy():
                subprocess.run(["wl-copy"], input=data, timeout=2)
            else:
                subprocess.run(
                    ["xclip", "-selection", "clipboard", "-in"],
                    input=data, timeout=2,
                )
        except Exception:
            logger.debug("Failed to write text to clipboard")

    def _set_html(self, data: bytes):
        try:
            if _BACKEND == "wayland" and _has_wl_copy():
                subprocess.run(
                    ["wl-copy", "--type", "text/html"],
                    input=data, timeout=2,
                )
            elif _BACKEND == "x11" and _has_xclip():
                subprocess.run(
                    ["xclip", "-selection", "clipboard", "-in", "-t", "text/html"],
                    input=data, timeout=2,
                )
        except Exception:
            logger.debug("Failed to write HTML to clipboard via %s", _BACKEND)

    def _set_rtf(self, data: bytes):
        try:
            if _BACKEND == "wayland" and _has_wl_copy():
                subprocess.run(
                    ["wl-copy", "--type", "text/rtf"],
                    input=data, timeout=2,
                )
            elif _BACKEND == "x11" and _has_xclip():
                subprocess.run(
                    ["xclip", "-selection", "clipboard", "-in", "-t", "text/rtf"],
                    input=data, timeout=2,
                )
        except Exception:
            logger.debug("Failed to write RTF to clipboard via %s", _BACKEND)

    def _set_image(self, data: bytes, image_fmt: str = ""):
        png_data = data
        if image_fmt in ("bmp", "tiff"):
            # Convert non-PNG formats to PNG for Linux clipboard
            try:
                from PIL import Image
                img = Image.open(BytesIO(data))
                buf = BytesIO()
                img.save(buf, format="PNG")
                png_data = buf.getvalue()
            except Exception:
                logger.debug("Failed to convert %s image to PNG for Linux", image_fmt)
                return

        try:
            if _BACKEND == "x11" and _has_xclip():
                subprocess.run(
                    ["xclip", "-selection", "clipboard", "-in", "-t", "image/png"],
                    input=png_data, timeout=2,
                )
            elif _BACKEND == "wayland" and _has_wl_copy():
                subprocess.run(
                    ["wl-copy", "--type", "image/png"],
                    input=png_data, timeout=2,
                )
        except Exception:
            logger.debug("Failed to write image to clipboard")


class LinuxClipboardMonitor(ClipboardMonitor):
    """Poll-based clipboard monitor for Linux."""

    def __init__(self, poll_interval: float = POLL_INTERVAL):
        self._running = False
        self._thread = None
        self._callback = None
        self._poll_interval = poll_interval

    def start(self, callback):
        self._callback = callback
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("Clipboard monitor started")

    def stop(self):
        self._running = False
        logger.info("Clipboard monitor stopped")

    def _poll_loop(self):
        last_hash = self._get_content_hash()
        while self._running:
            time.sleep(self._poll_interval)
            current = self._get_content_hash()
            if current and last_hash and current != last_hash:
                last_hash = current
                if self._callback:
                    try:
                        self._callback()
                    except Exception:
                        pass
            elif current and not last_hash:
                last_hash = current

    def _get_content_hash(self) -> str:
        try:
            if _BACKEND == "wayland" and _has_wl_copy():
                result = subprocess.run(
                    ["wl-paste", "--no-newline"], capture_output=True, timeout=2,
                )
            else:
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o"],
                    capture_output=True, timeout=2,
                )
            if result.returncode == 0:
                return hashlib.sha256(result.stdout).hexdigest()
        except Exception:
            pass
        return ""


def create_monitor(poll_interval: float = POLL_INTERVAL) -> ClipboardMonitor:
    return LinuxClipboardMonitor(poll_interval=poll_interval)


def create_reader() -> ClipboardReader:
    return _ClipboardReader()


def create_writer() -> ClipboardWriter:
    return _ClipboardWriter()
