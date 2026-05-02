"""macOS clipboard implementation.

Uses subprocess to call macOS clipboard commands:
- pbpaste / pbcopy for text
- pbpaste -Prefer html / rtf for reading rich formats
- osascript with temp files for writing rich formats (avoids escaping issues)
- PIL.ImageGrab for image handling

Clipboard monitoring polls NSPasteboard.general.changeCount via osascript,
since macOS provides no event-driven clipboard API.
"""

import hashlib
import logging
import os
import re
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
        # Method 1: pbpaste -Prefer html (fast, works on most macOS versions)
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

        # Method 2: NSPasteboard via osascript (bypasses pbpaste limitations)
        return self._get_html_via_nspasteboard()

    def _get_html_via_nspasteboard(self) -> bytes:
        """Read HTML from NSPasteboard via AppleScript-ObjC bridge.

        This is more reliable than pbpaste -Prefer html because it
        accesses the pasteboard directly and reads the public.html UTI.
        """
        try:
            script = (
                'use framework "AppKit"\n'
                'set pb to current application\'s NSPasteboard\'s generalPasteboard()\n'
                'set htmlData to pb\'s dataForType:"public.html"\n'
                'if htmlData = missing value then\n'
                '    return "COPYBOARD_NO_HTML"\n'
                'end if\n'
                'set htmlStr to current application\'s NSString\'s alloc()\'s '
                'initWithData:htmlData encoding:current application\'s NSUTF8StringEncoding\n'
                'if htmlStr = missing value then\n'
                '    return "COPYBOARD_NO_HTML"\n'
                'end if\n'
                'return htmlStr as text'
            )
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, timeout=3,
            )
            if result.returncode == 0 and result.stdout:
                data = result.stdout
                if data != b"COPYBOARD_NO_HTML" and b"<" in data and b">" in data:
                    logger.debug("Read HTML via NSPasteboard fallback (%d bytes)", len(data))
                    return data
        except Exception:
            logger.debug("NSPasteboard html read failed", exc_info=True)
        return b""

    def _get_rtf(self) -> bytes:
        try:
            result = subprocess.run(
                ["pbpaste", "-Prefer", "rtf"],
                capture_output=True, timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                data = result.stdout
                # Some apps emit RTF with a leading BOM or whitespace.
                # Check for \rtf anywhere in the first 200 bytes.
                head = data[:200]
                if b"\\rtf" in head or b"{\\rtf" in head:
                    return data
        except Exception:
            logger.debug("pbpaste rtf read failed", exc_info=True)
        return b""

    def _get_image(self) -> bytes:
        # Method 1: Plain AppleScript (no AppKit — avoids TCC permissions)
        data = self._get_image_via_applescript()
        if data:
            return data

        # Method 2: PIL.ImageGrab.grabclipboard()
        try:
            from PIL import ImageGrab
            img = ImageGrab.grabclipboard()
            if img is not None:
                buf = BytesIO()
                img.save(buf, format="PNG")
                logger.info("Read image via ImageGrab.grabclipboard (%d bytes)", buf.tell())
                return buf.getvalue()
        except NotImplementedError:
            logger.debug("ImageGrab.grabclipboard not implemented on this platform")
        except ImportError:
            logger.debug("PIL import failed, image read unavailable")
        except Exception:
            logger.debug("ImageGrab read failed", exc_info=True)

        # Method 3: NSPasteboard via AppleScript-ObjC (may need permissions)
        return self._get_image_via_nspasteboard()

    @staticmethod
    def _get_image_via_applescript() -> bytes:
        """Read clipboard image using plain AppleScript — no AppKit needed.

        This avoids macOS TCC (Transparency, Consent, and Control)
        permission issues that ``use framework "AppKit"`` can trigger.
        Writes the pasteboard TIFF data to a temp file, then converts
        to PNG via PIL.
        """
        try:
            from PIL import Image
        except ImportError:
            return b""

        tmp_path = None
        try:
            import tempfile as _tf
            tmp_fd, tmp_path = _tf.mkstemp(suffix=".tiff")
            os.close(tmp_fd)

            script = (
                f'set f to open for access (POSIX file "{tmp_path}") '
                'with write permission\n'
                'set eof f to 0\n'
                'write (the clipboard as «class TIFF») to f\n'
                'close access f\n'
                'return "OK"'
            )
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, timeout=5,
            )
            if result.returncode != 0:
                stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
                if "-1700" in stderr:
                    logger.debug("AppleScript: no image on clipboard (expected)")
                else:
                    logger.warning("AppleScript image read failed (permissions?): %s",
                                  stderr[:200] if stderr else "unknown error")
                return b""

            file_size = os.path.getsize(tmp_path)
            if file_size == 0:
                logger.debug("AppleScript wrote empty image file — no image on clipboard")
                return b""

            img = Image.open(tmp_path)
            buf = BytesIO()
            img.save(buf, format="PNG")
            logger.info("Read image via plain AppleScript (%d bytes)", buf.tell())
            return buf.getvalue()
        except Exception:
            logger.warning("AppleScript image read exception", exc_info=True)
            return b""
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    @staticmethod
    def _get_image_via_nspasteboard() -> bytes:
        """Read image from NSPasteboard via AppleScript-ObjC bridge.

        Uses readObjectsForClasses: to get an NSImage directly, then
        converts to PNG via NSBitmapImageRep.  May require macOS
        Accessibility permissions for the terminal / Python launcher.
        """
        try:
            from PIL import Image
        except ImportError:
            return b""

        tmp_path = None
        try:
            import tempfile as _tf
            tmp_fd, tmp_path = _tf.mkstemp(suffix=".png")
            os.close(tmp_fd)

            script = (
                'use framework "AppKit"\n'
                'set pb to current application\'s NSPasteboard\'s generalPasteboard()\n'
                'set theClasses to current application\'s NSArray\'s '
                'arrayWithObject:(current application\'s NSImage\'s class)\n'
                'set results to pb\'s readObjectsForClasses:theClasses '
                'options:(missing value)\n'
                'if results\'s |count|() = 0 then\n'
                '    return "NO_IMAGE"\n'
                'end if\n'
                'set img to results\'s firstObject()\n'
                'set tiffRep to img\'s TIFFRepresentation()\n'
                'set pngRep to current application\'s NSBitmapImageRep\'s '
                'imageRepWithData:tiffRep\n'
                'if pngRep = missing value then\n'
                '    return "NO_IMAGE"\n'
                'end if\n'
                'set pngData to pngRep\'s representationUsingType:'
                '(current application\'s NSPNGFileType) |properties|:(missing value)\n'
                'if pngData = missing value then\n'
                '    return "NO_IMAGE"\n'
                'end if\n'
                f'set tmpPath to "{tmp_path}"\n'
                'pngData\'s writeToFile:tmpPath atomically:true\n'
                'return "OK"'
            )
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, timeout=5,
            )
            if result.returncode != 0:
                stderr = (result.stderr or b"").decode("utf-8", errors="replace").strip()
                logger.warning("NSPasteboard osascript failed (permissions?): %s",
                              stderr[:200] if stderr else "unknown error")
                return b""
            if b"NO_IMAGE" in (result.stdout or b""):
                return b""

            file_size = os.path.getsize(tmp_path)
            if file_size == 0:
                logger.debug("NSPasteboard wrote empty image file")
                return b""

            img = Image.open(tmp_path)
            buf = BytesIO()
            img.save(buf, format="PNG")
            logger.info("Read image via NSPasteboard (%d bytes)", buf.tell())
            return buf.getvalue()
        except Exception:
            logger.warning("NSPasteboard image read exception", exc_info=True)
            return b""
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


class _ClipboardWriter(ClipboardWriter):
    def write(self, content: ClipboardContent):
        # Write ALL available formats so the receiving application
        # can choose the richest one it supports.
        for fmt_type, data in content.types.items():
            if fmt_type == ContentType.TEXT:
                self._set_text(data)
            elif fmt_type == ContentType.HTML:
                self._set_html(data)
            elif fmt_type == ContentType.RTF:
                self._set_rtf(data)
            elif fmt_type == ContentType.IMAGE_PNG:
                self._set_image(data)
            # IMAGE_EMF is Windows-only, skip on macOS

    def _set_text(self, data: bytes):
        try:
            result = subprocess.run(["pbcopy"], input=data, timeout=2)
            if result.returncode != 0:
                logger.warning("pbcopy returned non-zero exit code: %d", result.returncode)
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
            logger.debug("osascript html write failed", exc_info=True)
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
    """Poll-based clipboard monitor for macOS.

    Uses content hashing (like the Linux implementation) rather than
    AppleScript change-count polling.  AppleScript ``«class ccnt»`` is
    unreliable on macOS ≥14 (Sonoma) where osascript clipboard access
    may fail silently or require entitlements the bundled app lacks.
    Hashing ``pbpaste`` output is fast for text, has no permission
    requirements, and has been battle-tested on Linux.
    """

    def __init__(self, poll_interval: float = POLL_INTERVAL):
        self._running = False
        self._thread = None
        self._callback = None
        self._poll_interval = poll_interval

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
                        logger.warning("Clipboard change callback failed", exc_info=True)
            elif current and not last_hash:
                last_hash = current

    def _get_content_hash(self) -> str:
        """Hash text + HTML pasteboard content for change detection.

        Falls back to osascript for image detection when no text/HTML
        is on the pasteboard, so image-only copies are not missed.
        """
        try:
            text = subprocess.run(
                ["pbpaste", "-Prefer", "txt"],
                capture_output=True, timeout=3,
            )
            html = subprocess.run(
                ["pbpaste", "-Prefer", "html"],
                capture_output=True, timeout=3,
            )
            txt_data = text.stdout if text.returncode == 0 else b""
            html_data = html.stdout if html.returncode == 0 else b""
            combined = txt_data + html_data
            if combined:
                return hashlib.sha256(combined).hexdigest()

            # No text — check for image-only content so image copies
            # are detected.  Uses plain AppleScript (no AppKit)
            # to avoid macOS TCC permission issues.
            try:
                img_check = subprocess.run(
                    ["osascript", "-e",
                     'try\n'
                     '    set imgData to (the clipboard as «class TIFF»)\n'
                     '    if imgData is not missing value and length of imgData > 0 then\n'
                     '        return "1"\n'
                     '    end if\n'
                     'end try\n'
                     'return "0"'],
                    capture_output=True, timeout=2,
                )
                if img_check.returncode == 0 and img_check.stdout.strip() == b"1":
                    return hashlib.sha256(str(time.time()).encode()).hexdigest()
            except Exception:
                pass
        except Exception:
            pass
        return ""


def create_monitor(poll_interval: float = POLL_INTERVAL) -> ClipboardMonitor:
    return DarwinClipboardMonitor(poll_interval=poll_interval)


def create_reader() -> ClipboardReader:
    return _ClipboardReader()


def create_writer() -> ClipboardWriter:
    return _ClipboardWriter()
