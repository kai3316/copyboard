"""Windows clipboard implementation using ctypes + Win32 API.

Event-driven monitoring via AddClipboardFormatListener — zero CPU when idle.
"""

import ctypes
import ctypes.wintypes
import re
import struct
import threading
import time
import logging
from io import BytesIO

from internal.clipboard.clipboard import ClipboardMonitor, ClipboardReader, ClipboardWriter
from internal.clipboard.format import ClipboardContent, ContentType

logger = logging.getLogger(__name__)

# Win32 API bindings
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Set up function signatures
kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.restype = ctypes.c_int
kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p

user32.OpenClipboard.argtypes = [ctypes.c_void_p]
user32.OpenClipboard.restype = ctypes.c_int
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = ctypes.c_int
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = ctypes.c_int
user32.GetClipboardData.argtypes = [ctypes.c_uint]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.EnumClipboardFormats.argtypes = [ctypes.c_uint]
user32.EnumClipboardFormats.restype = ctypes.c_uint
user32.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
user32.RegisterClipboardFormatW.restype = ctypes.c_uint
user32.AddClipboardFormatListener.argtypes = [ctypes.c_void_p]
user32.AddClipboardFormatListener.restype = ctypes.c_int
user32.RemoveClipboardFormatListener.argtypes = [ctypes.c_void_p]
user32.RemoveClipboardFormatListener.restype = ctypes.c_int
user32.CreateWindowExW.argtypes = [
    ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
]
user32.CreateWindowExW.restype = ctypes.c_void_p
user32.DestroyWindow.argtypes = [ctypes.c_void_p]
user32.DestroyWindow.restype = ctypes.c_int
user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_ulonglong, ctypes.c_longlong]
user32.DefWindowProcW.restype = ctypes.c_longlong
user32.GetMessageW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.c_void_p]
user32.TranslateMessage.restype = ctypes.c_int
user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
user32.DispatchMessageW.restype = ctypes.c_longlong
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostQuitMessage.restype = None
user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_ulonglong, ctypes.c_longlong]
user32.PostMessageW.restype = ctypes.c_int
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.RegisterClassW.restype = ctypes.c_uint

# Clipboard format constants
CF_TEXT = 1
CF_BITMAP = 2
CF_DIB = 8
CF_UNICODETEXT = 13
CF_HDROP = 15

# Registered format for HTML
CF_HTML = user32.RegisterClipboardFormatW("HTML Format")
CF_RTF = user32.RegisterClipboardFormatW("Rich Text Format")

# Message constants
WM_CLIPBOARDUPDATE = 0x031D
WM_DESTROY = 0x0002

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_void_p, ctypes.c_uint, ctypes.c_ulonglong, ctypes.c_longlong)


class _ClipboardReader(ClipboardReader):
    def read(self) -> ClipboardContent:
        content = ClipboardContent(timestamp=time.time())
        if not user32.OpenClipboard(None):
            return content

        try:
            fmt = 0
            while True:
                fmt = user32.EnumClipboardFormats(fmt)
                if fmt == 0:
                    break
                data = self._get_format_data(fmt)
                if data:
                    content_type = self._map_format(fmt)
                    if content_type:
                        if content_type == ContentType.TEXT and fmt == CF_TEXT and ContentType.TEXT in content.types:
                            continue  # prefer CF_UNICODETEXT already read, skip ANSI
                        content.types[content_type] = data
        finally:
            user32.CloseClipboard()

        fmt_count = len(content.types)
        if fmt_count > 0:
            logger.debug("Read %d format(s) from clipboard", fmt_count)
        return content

    def _get_format_data(self, fmt: int) -> bytes | None:
        handle = user32.GetClipboardData(fmt)
        if not handle:
            return None

        if fmt in (CF_TEXT, CF_UNICODETEXT):
            return self._read_text_handle(handle, fmt == CF_UNICODETEXT)
        elif fmt in (CF_HTML, CF_RTF):
            return self._read_text_handle(handle, wide=False)
        elif fmt == CF_DIB:
            return self._read_dib_handle(handle)
        return None

    def _read_text_handle(self, handle, wide: bool) -> bytes:
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return b""
        size = kernel32.GlobalSize(handle)
        try:
            if wide:
                text = ctypes.string_at(ptr, size).decode("utf-16-le")
                return text.rstrip("\x00").encode("utf-8")
            else:
                return ctypes.string_at(ptr, size).rstrip(b"\x00")
        finally:
            kernel32.GlobalUnlock(handle)

    def _read_dib_handle(self, handle) -> bytes:
        """Read DIB from global memory handle and convert to PNG bytes.

        Avoids ImageGrab.grabclipboard() which would try to open the clipboard
        while it's already open from read().
        """
        try:
            from PIL import Image
        except ImportError:
            return b""

        try:
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return b""
            try:
                size = kernel32.GlobalSize(handle)
                if size < 40:
                    return b""
                dib = ctypes.string_at(ptr, size)
            finally:
                kernel32.GlobalUnlock(handle)

            # Read BITMAPINFOHEADER to calculate the BMP file header
            bi_size = struct.unpack_from("<I", dib, 0)[0]
            bi_bit_count = struct.unpack_from("<H", dib, 14)[0]
            bi_clr_used = struct.unpack_from("<I", dib, 32)[0]

            # Color table size
            if bi_clr_used != 0:
                color_table_size = bi_clr_used * 4
            elif bi_bit_count <= 8:
                color_table_size = (1 << bi_bit_count) * 4
            else:
                color_table_size = 0

            bf_off_bits = 14 + bi_size + color_table_size
            bf_size = 14 + size

            buf = BytesIO()
            buf.write(struct.pack("<HIHHI", 0x4D42, bf_size, 0, 0, bf_off_bits))
            buf.write(dib)
            buf.seek(0)

            img = Image.open(buf)
            out = BytesIO()
            img.save(out, format="PNG")
            return out.getvalue()
        except Exception:
            logger.debug("Failed to read DIB from clipboard", exc_info=True)
            return b""

    def _map_format(self, fmt: int) -> ContentType | None:
        if fmt == CF_UNICODETEXT:
            return ContentType.TEXT
        elif fmt == CF_TEXT:
            # Only use CF_TEXT as fallback if no CF_UNICODETEXT was present
            return ContentType.TEXT
        elif fmt == CF_HTML:
            return ContentType.HTML
        elif fmt == CF_RTF:
            return ContentType.RTF
        elif fmt == CF_DIB:
            return ContentType.IMAGE_PNG
        return None


class _ClipboardWriter(ClipboardWriter):
    def write(self, content: ClipboardContent):
        if not user32.OpenClipboard(None):
            return
        try:
            user32.EmptyClipboard()

            best = content.best_format()
            if best is None:
                return
            fmt_type, data = best

            logger.debug("Writing %s to clipboard", fmt_type.name)
            if fmt_type == ContentType.TEXT:
                self._set_text(data)
            elif fmt_type == ContentType.HTML:
                self._set_html(data)
            elif fmt_type == ContentType.RTF:
                self._set_custom_format(CF_RTF, data)
            elif fmt_type == ContentType.IMAGE_PNG:
                self._set_image(data)
        finally:
            user32.CloseClipboard()

    def _set_text(self, data: bytes):
        text = data.decode("utf-8")
        wide_text = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(0x0002, len(wide_text))  # GMEM_MOVEABLE
        if handle:
            ptr = kernel32.GlobalLock(handle)
            ctypes.memmove(ptr, wide_text, len(wide_text))
            kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                logger.warning("SetClipboardData(CF_UNICODETEXT) failed")

    def _set_html(self, data: bytes):
        cf_html = self._build_cf_html(data)
        self._set_custom_format(CF_HTML, cf_html)
        text = re.sub(r'<[^>]*>', '', data.decode('utf-8', errors='replace'))
        self._set_text(text.encode('utf-8'))

    @staticmethod
    def _build_cf_html(html_bytes: bytes) -> bytes:
        """Wrap raw HTML in the CF_HTML envelope Windows expects."""
        html = html_bytes.decode("utf-8", errors="replace")
        MARKER = "<!--StartFragment-->"
        END_MARKER = "<!--EndFragment-->"
        if MARKER not in html:
            html = f"{MARKER}{html}{END_MARKER}"
        header_tmpl = (
            "Version:0.9\r\n"
            "StartHTML:{start_html:010d}\r\n"
            "EndHTML:{end_html:010d}\r\n"
            "StartFragment:{start_frag:010d}\r\n"
            "EndFragment:{end_frag:010d}\r\n"
        )
        dummy_header = header_tmpl.format(
            start_html=0, end_html=0, start_frag=0, end_frag=0,
        )
        prefix = "<html><body>\r\n"
        suffix = "\r\n</body></html>"
        header_len = len(dummy_header.encode("utf-8"))
        start_html = header_len
        html_encoded = html.encode("utf-8")
        prefix_encoded = prefix.encode("utf-8")
        suffix_encoded = suffix.encode("utf-8")
        frag_start_idx = html.find(MARKER)
        frag_end_idx = html.find(END_MARKER)
        start_frag = header_len + len(prefix_encoded) + len(html[:frag_start_idx].encode("utf-8")) + len(MARKER.encode("utf-8"))
        end_frag = header_len + len(prefix_encoded) + len(html[:frag_end_idx].encode("utf-8"))
        end_html = header_len + len(prefix_encoded) + len(html_encoded) + len(suffix_encoded)
        header = header_tmpl.format(
            start_html=start_html, end_html=end_html,
            start_frag=start_frag, end_frag=end_frag,
        )
        return (header + prefix + html + suffix).encode("utf-8")

    def _set_image(self, data: bytes):
        try:
            from PIL import Image
        except ImportError:
            return
        try:
            img = Image.open(BytesIO(data))
            from io import BytesIO as Bio
            dib = Bio()
            img.convert("RGB").save(dib, format="BMP")
            dib_data = dib.getvalue()
            # Skip BMP header (14 bytes) to get DIB
            dib_data = dib_data[14:]
            handle = kernel32.GlobalAlloc(0x0002, len(dib_data))
            if handle:
                ptr = kernel32.GlobalLock(handle)
                ctypes.memmove(ptr, dib_data, len(dib_data))
                kernel32.GlobalUnlock(handle)
                if not user32.SetClipboardData(CF_DIB, handle):
                    logger.warning("SetClipboardData(CF_DIB) failed")
        except Exception:
            logger.debug("Failed to write image to clipboard", exc_info=True)

    def _set_custom_format(self, fmt: int, data: bytes):
        handle = kernel32.GlobalAlloc(0x0002, len(data) + 1)
        if handle:
            ptr = kernel32.GlobalLock(handle)
            ctypes.memmove(ptr, data, len(data))
            ctypes.memset(ptr + len(data), 0, 1)
            kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(fmt, handle):
                logger.warning("SetClipboardData(%d) failed", fmt)


class WindowsClipboardMonitor(ClipboardMonitor):
    """Event-driven clipboard monitor using AddClipboardFormatListener."""

    def __init__(self):
        self._running = False
        self._thread = None
        self._callback = None
        self._hwnd = None

    def start(self, callback):
        logger.info("WindowsClipboardMonitor starting")
        self._callback = callback
        self._running = True
        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()

    def stop(self):
        logger.info("WindowsClipboardMonitor stopping")
        self._running = False
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_DESTROY, 0, 0)

    def _message_loop(self):
        hinstance = kernel32.GetModuleHandleW(None)

        # Register window class
        class_name = "CopyBoardClipWatcher"
        wndproc = WNDPROC(self._window_proc)

        wndclass = _WNDCLASSW()
        wndclass.lpfnWndProc = wndproc
        wndclass.hInstance = hinstance
        wndclass.lpszClassName = class_name

        atom = user32.RegisterClassW(ctypes.byref(wndclass))
        if not atom:
            logger.error("Failed to register window class")
            return

        # Create message-only window
        HWND_MESSAGE = -3
        self._hwnd = user32.CreateWindowExW(
            0, class_name, class_name, 0,
            0, 0, 0, 0, HWND_MESSAGE, None, hinstance, None,
        )

        if not self._hwnd:
            logger.error("Failed to create message-only window")
            return

        logger.info("Message-only window created successfully")
        # Register for clipboard updates
        if not user32.AddClipboardFormatListener(self._hwnd):
            logger.error("Failed to register clipboard format listener")
        else:
            logger.info("Clipboard format listener registered")

        # Message pump
        msg = _MSG()
        while self._running:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        user32.RemoveClipboardFormatListener(self._hwnd)
        user32.DestroyWindow(self._hwnd)

    def _window_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_CLIPBOARDUPDATE:
            if self._callback:
                try:
                    self._callback()
                except Exception:
                    logger.warning("Clipboard change callback failed", exc_info=True)
            return 0
        elif msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
    ]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_ulonglong),
        ("lParam", ctypes.c_longlong),
        ("time", ctypes.c_uint),
        ("pt_x", ctypes.c_long),
        ("pt_y", ctypes.c_long),
    ]


def create_monitor(poll_interval: float = 0.4) -> ClipboardMonitor:
    return WindowsClipboardMonitor()


def create_reader() -> ClipboardReader:
    return _ClipboardReader()


def create_writer() -> ClipboardWriter:
    return _ClipboardWriter()
