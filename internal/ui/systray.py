"""System tray UI for CopyBoard.

Cross-platform system tray icon with menu:
- Device name and status
- Enable/disable sync
- Settings...
- Connected peers list
- About / Quit

Uses pystray with Pillow for icon rendering.
"""

import ctypes
import logging
import sys
from typing import Callable

import pystray
from PIL import Image, ImageDraw

from internal.platform.notify import notification_mgr

logger = logging.getLogger(__name__)


def _create_icon_image(size: int = 32) -> Image.Image:
    """Create a simple clipboard icon."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = size // 8
    padding = size // 5

    # Clipboard body
    x0 = margin
    y0 = margin + size // 6
    x1 = size - margin
    y1 = size - margin
    draw.rounded_rectangle([x0, y0, x1, y1], radius=size // 8, fill=(80, 140, 220))

    # Clipboard top bar
    bar_width = size // 3
    bar_x0 = (size - bar_width) // 2
    bar_y0 = margin
    bar_x1 = bar_x0 + bar_width
    bar_y1 = y0 + padding // 2
    draw.rounded_rectangle(
        [bar_x0, bar_y0, bar_x1, bar_y1],
        radius=size // 12, fill=(60, 110, 180),
    )

    # Paper lines
    line_color = (220, 230, 245)
    line_margin = size // 4
    line_spacing = size // 8
    for i in range(3):
        ly = y0 + padding + i * line_spacing
        draw.line(
            [x0 + line_margin, ly, x1 - line_margin, ly],
            fill=line_color, width=max(1, size // 20),
        )

    return img


class SystrayApp:
    """System tray application wrapper."""

    def __init__(
        self,
        device_name: str,
        on_enable_toggle: Callable | None = None,
        on_open_dashboard: Callable | None = None,
        on_open_settings: Callable | None = None,
        on_export_logs: Callable | None = None,
        on_quit: Callable | None = None,
    ):
        self._device_name = device_name
        self._on_enable_toggle = on_enable_toggle
        self._on_open_dashboard = on_open_dashboard
        self._on_open_settings = on_open_settings
        self._on_export_logs = on_export_logs
        self._on_quit_cb = on_quit
        self._syncing = True
        self._tray = None
        self._icon_image = _create_icon_image()
        self._peers: list[str] = []

        # Custom message for thread-safe menu updates on Windows.
        # pystray._update_menu() destroys the current menu handle via
        # DestroyMenu, which conflicts with TrackPopupMenuEx when the
        # context menu is displayed.  Posting a message guarantees
        # _update_menu() runs on the tray thread after the menu closes.
        if sys.platform == "win32":
            self._WM_UPDATE_MENU = 0x8000 + 0x100  # WM_APP + 256

    def set_syncing(self, enabled: bool):
        self._syncing = enabled

    def set_peers(self, peers: list[str]):
        self._peers = peers
        if self._tray is None:
            return
        if sys.platform == "win32" and getattr(self, "_WM_UPDATE_MENU", None):
            hwnd = getattr(self._tray, "_hwnd", None)
            if hwnd:
                ctypes.windll.user32.PostMessageW(
                    hwnd, self._WM_UPDATE_MENU, 0, 0)
                return
        self._tray.update_menu()

    def run(self):
        """Run the system tray. Blocks until quit."""
        menu = pystray.Menu(
            pystray.MenuItem(
                "CopyBoard",
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                f"Device: {self._device_name}",
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Syncing: ON",
                self._on_toggle_sync,
                checked=lambda item: self._syncing,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Show Dashboard", self._on_open_dashboard_click),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Connected Devices",
                pystray.Menu(self._build_peer_menu),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Settings...", self._on_open_settings_click),
            pystray.MenuItem("Export Logs...", self._on_export_logs_click),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("About CopyBoard", self._on_about),
            pystray.MenuItem("Quit", self._on_quit),
        )

        logger.info("Starting system tray")
        self._tray = pystray.Icon(
            "copyboard",
            self._icon_image,
            "CopyBoard",
            menu,
        )

        # Register a custom message handler so set_peers() can safely
        # trigger menu updates from the peer updater thread.  The
        # handler runs on the pystray thread (via the Windows message
        # pump), avoiding DestroyMenu / TrackPopupMenuEx races.
        if sys.platform == "win32" and getattr(self, "_WM_UPDATE_MENU", None):
            self._tray._message_handlers[self._WM_UPDATE_MENU] = (
                lambda w, l: self._tray._update_menu() or 0
            )

        notification_mgr.set_tray(self._tray)

        self._tray.run()

    def stop(self):
        if self._tray:
            self._tray.stop()

    def _build_peer_menu(self):
        """Build submenu for connected peers."""
        items = []
        if not self._peers:
            items.append(
                pystray.MenuItem("(no devices connected)", None, enabled=False),
            )
        else:
            for peer in self._peers:
                items.append(
                    pystray.MenuItem(peer, None, enabled=False),
                )
        return items

    def _on_toggle_sync(self, icon, item):
        self._syncing = not self._syncing
        if self._on_enable_toggle:
            self._on_enable_toggle(self._syncing)
        logger.info("Sync %s via tray", "enabled" if self._syncing else "paused")
        if self._tray:
            self._tray.update_menu()

    def _on_open_dashboard_click(self, icon, item):
        if self._on_open_dashboard:
            self._on_open_dashboard()

    def _on_open_settings_click(self, icon, item):
        if self._on_open_settings:
            self._on_open_settings()

    def _on_export_logs_click(self, icon, item):
        if self._on_export_logs:
            self._on_export_logs()

    def _on_about(self, icon, item):
        # Show a simple notification
        if self._tray:
            self._tray.notify(
                "CopyBoard — Cross-platform clipboard sharing\n"
                "Share your clipboard between Windows and Mac in real time.",
                title="About CopyBoard",
            )

    def _on_quit(self, icon, item):
        if self._on_quit_cb:
            self._on_quit_cb()
        self.stop()
