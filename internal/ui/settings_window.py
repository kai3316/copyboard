"""Modern settings window using ttkbootstrap.

Sidebar navigation + card-based content panels.
Supports light/dark theme toggle, colour-coded device status,
and real-time peer list updates.

Maintains the original constructor API so cmd/main.py works unchanged:
    SettingsWindow(root, get_config, save_config, get_peers, ...)
"""

import logging
import tkinter as tk
from tkinter import messagebox
from typing import Callable

import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from ttkbootstrap.style import Style

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Colour constants for device status indicators
# ---------------------------------------------------------------------------
STATUS_COLORS = {
    "Connected": "#28a745",  # green
    "Paired":    "#f0ad4e",  # amber
    "Pending":   "#868e96",  # gray
}
STATUS_BOOTSTYLE = {
    "Connected": "success",
    "Paired":    "warning",
    "Pending":   "secondary",
}

LIGHT_THEME = "flatly"
DARK_THEME  = "darkly"


class SettingsWindow:
    """Modern settings dialog with sidebar navigation."""

    # ═══════════════════════════════════════════════════════════════
    # Constructor (API unchanged)
    # ═══════════════════════════════════════════════════════════════

    def __init__(
        self,
        root: tk.Tk,
        get_config: Callable,
        save_config: Callable,
        get_peers: Callable,
        get_pending_pairings: Callable,
        get_sync_enabled: Callable,
        set_sync_enabled: Callable,
        on_pair: Callable,
        on_unpair: Callable,
        on_remove_peer: Callable,
        on_export_logs: Callable | None = None,
        on_closed: Callable | None = None,
    ):
        """
        Args:
            root:                 Hidden tkinter root.
            get_config:           () -> Config
            save_config:          () -> None
            get_peers:            () -> list[(device_id, device_name, paired, connected)]
            get_pending_pairings: () -> list[(peer_id, code)]
            get_sync_enabled:     () -> bool
            set_sync_enabled:     (bool) -> None
            on_pair:              (peer_id, code) -> bool
            on_unpair:            (peer_id) -> None
            on_remove_peer:       (peer_id) -> None
            on_export_logs:       () -> None
            on_closed:            Called when the window is closed (for cleanup)
        """
        self._root = root
        self._get_config = get_config
        self._save_config = save_config
        self._get_peers = get_peers
        self._get_pending = get_pending_pairings
        self._get_sync = get_sync_enabled
        self._set_sync = set_sync_enabled
        self._on_pair = on_pair
        self._on_unpair = on_unpair
        self._on_remove_peer = on_remove_peer
        self._on_export_logs = on_export_logs
        self._on_closed = on_closed

        # Internal state
        self._window: ttk.Toplevel | None = None
        self._style: Style | None = None
        self._refresh_job: str | None = None
        self._dark_mode: bool = False
        self._current_panel: str = "general"

        # Widget references (populated in show)
        self._content_frame: ttk.Frame | None = None
        self._sidebar_buttons: dict[str, ttk.Button] = {}
        self._panels: dict[str, ttk.Frame] = {}
        self._theme_btn: ttk.Button | None = None
        self._status_label: ttk.Label | None = None
        self._sync_meter: ttk.Floodgauge | None = None
        self._device_cards_inner: ttk.Frame | None = None
        self._device_canvas: tk.Canvas | None = None
        self._canvas_window: int | None = None
        self._pending_label: ttk.Label | None = None

        # Data-bound tk vars
        self._dev_name_var: tk.StringVar | None = None
        self._sync_var: tk.BooleanVar | None = None
        self._autostart_var: tk.BooleanVar | None = None
        self._port_var: tk.StringVar | None = None
        self._svc_var: tk.StringVar | None = None
        self._relay_var: tk.StringVar | None = None
        self._pair_code_var: tk.StringVar | None = None

    # ═══════════════════════════════════════════════════════════════
    # Public API (must match original)
    # ═══════════════════════════════════════════════════════════════

    def show(self):
        """Create and show the settings window (or lift if already open)."""
        if self._window is not None:
            self._window.lift()
            self._window.focus_force()
            return

        logger.info("Opening settings window")
        # Bootstrap the ttkbootstrap style
        self._style = Style(theme=LIGHT_THEME)

        self._window = ttk.Toplevel(self._root)
        self._window.transient(self._root)
        self._window.title("CopyBoard Settings")
        self._window.geometry("660x580")
        self._window.minsize(560, 480)
        self._window.protocol("WM_DELETE_WINDOW", self._on_close)

        try:
            self._window.iconphoto(False, self._make_tk_icon())
        except Exception:
            pass

        self._build_ui()
        self._switch_panel("general")
        self._schedule_refresh()

    # ═══════════════════════════════════════════════════════════════
    # Lifecycle
    # ═══════════════════════════════════════════════════════════════

    def _on_close(self):
        """Clean up and destroy the window."""
        if self._refresh_job is not None:
            self._root.after_cancel(self._refresh_job)
            self._refresh_job = None
        if self._window is not None:
            self._window.destroy()
            self._window = None
            self._device_cards_inner = None
            self._device_canvas = None
            self._canvas_window = None
            self._sidebar_buttons.clear()
            self._panels.clear()
        if self._on_closed is not None:
            self._on_closed()

    def _schedule_refresh(self):
        """Periodically refresh the device list and sync meter."""
        self._refresh_device_list()
        self._refresh_sync_meter()
        if self._window is not None:
            self._refresh_job = self._root.after(2000, self._schedule_refresh)

    # ═══════════════════════════════════════════════════════════════
    # UI construction
    # ═══════════════════════════════════════════════════════════════

    def _build_ui(self):
        """Assemble the full window layout: header / sidebar / content / footer."""
        outer = ttk.Frame(self._window, padding=0)
        outer.pack(fill=BOTH, expand=YES)

        self._build_header(outer)

        # Body row: sidebar | separator | content
        body = ttk.Frame(outer)
        body.pack(fill=BOTH, expand=YES)

        self._build_sidebar(body)
        ttk.Separator(body, orient=VERTICAL).pack(side=LEFT, fill=Y)

        self._build_content_area(body)

        self._build_footer(outer)

    # ── Header ────────────────────────────────────────────────────

    def _build_header(self, parent: ttk.Frame):
        """Top bar with brand name and theme toggle."""
        header = ttk.Frame(parent, padding=(16, 10, 12, 10))
        header.configure(bootstyle="primary")
        header.pack(fill=X)

        # Brand
        brand = ttk.Frame(header)
        brand.pack(side=LEFT)

        ttk.Label(
            brand,
            text=u"\U0001F4CB  ",
            font=("Segoe UI", 14),
            bootstyle="inverse-primary",
            padding=(0, 0, 2, 0),
        ).pack(side=LEFT)

        ttk.Label(
            brand,
            text="CopyBoard",
            font=("Segoe UI", 14, "bold"),
            bootstyle="inverse-primary",
        ).pack(side=LEFT)

        # Theme toggle
        self._theme_btn = ttk.Button(
            header,
            text=u"\U0001F319  Dark",
            bootstyle="light-outline",
            command=self._toggle_theme,
            width=10,
        )
        self._theme_btn.pack(side=RIGHT)

    # ── Sidebar ───────────────────────────────────────────────────

    def _build_sidebar(self, body: ttk.Frame):
        """Vertical navigation rail on the left."""
        sidebar = ttk.Frame(body, width=160, bootstyle="light")
        sidebar.pack(side=LEFT, fill=Y)
        sidebar.pack_propagate(False)

        inner = ttk.Frame(sidebar, padding=(8, 20, 8, 12))
        inner.pack(fill=BOTH, expand=YES)

        # Navigation items: (key, icon, label)
        nav_items = [
            ("general", u"\U0001F3E0", "General"),
            ("devices", u"\U0001F4F1", "Devices"),
            ("network", u"\U0001F310", "Network"),
            ("about",    u"ℹ️", "About"),
        ]

        self._sidebar_buttons = {}
        for key, icon, label in nav_items:
            btn = ttk.Button(
                inner,
                text=f"  {icon}   {label}",
                bootstyle="light-link",
                command=lambda k=key: self._switch_panel(k),
                padding=(8, 8),
            )
            btn.pack(fill=X, pady=3, ipady=6)
            self._sidebar_buttons[key] = btn

    # ── Content area ──────────────────────────────────────────────

    def _build_content_area(self, body: ttk.Frame):
        """Container that hosts the switchable panels."""
        self._content_frame = ttk.Frame(body, padding=(20, 18, 20, 10))
        self._content_frame.pack(side=LEFT, fill=BOTH, expand=YES)

        # Build each panel
        self._panels["general"] = self._build_general_panel()
        self._panels["devices"] = self._build_devices_panel()
        self._panels["network"] = self._build_network_panel()
        self._panels["about"]    = self._build_about_panel()

    # ── Footer ────────────────────────────────────────────────────

    def _build_footer(self, parent: ttk.Frame):
        """Bottom bar with status text and close button."""
        ttk.Separator(parent, orient=HORIZONTAL).pack(fill=X)

        footer = ttk.Frame(parent, padding=(16, 8))
        footer.pack(fill=X)

        self._status_label = ttk.Label(
            footer, text="Ready",
            bootstyle="secondary",
        )
        self._status_label.pack(side=LEFT)

        ttk.Button(
            footer,
            text="Close",
            bootstyle="outline-secondary",
            command=self._on_close,
            width=8,
        ).pack(side=RIGHT)

    # ═══════════════════════════════════════════════════════════════
    # Panel switching
    # ═══════════════════════════════════════════════════════════════

    def _switch_panel(self, key: str):
        """Show the panel for *key* and hide all others.

        Updates sidebar button highlighting and refreshes
        data-driven panels on entry.
        """
        if self._content_frame is None:
            return

        # Hide / show panels
        for pk, panel in self._panels.items():
            if pk == key:
                panel.pack(in_=self._content_frame, fill=BOTH, expand=YES)
            else:
                panel.pack_forget()

        # Highlight active sidebar button
        for pk, btn in self._sidebar_buttons.items():
            if pk == key:
                btn.configure(bootstyle="primary-link")
            else:
                btn.configure(bootstyle="light-link")

        self._current_panel = key

        # Refresh content on entry
        if key == "devices":
            self._refresh_device_list()
        elif key == "general":
            self._refresh_sync_meter()

    # ═══════════════════════════════════════════════════════════════
    # Panel: General
    # ═══════════════════════════════════════════════════════════════

    def _build_general_panel(self) -> ttk.Frame:
        panel = ttk.Frame(self._content_frame)
        cfg = self._get_config()

        # Section heading
        ttk.Label(
            panel, text="General Settings",
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor=W, pady=(0, 14))

        # ── Device Information card ──────────────────────────────
        card = ttk.Labelframe(panel, text="  Device Information  ", padding=12)
        card.pack(fill=X, pady=(0, 12))

        row1 = ttk.Frame(card)
        row1.pack(fill=X, pady=(0, 8))
        ttk.Label(row1, text="Device Name", width=14, anchor=W).pack(side=LEFT)
        self._dev_name_var = tk.StringVar(value=cfg.device_name)
        ttk.Entry(row1, textvariable=self._dev_name_var).pack(
            side=LEFT, fill=X, expand=YES, padx=(12, 0),
        )

        row2 = ttk.Frame(card)
        row2.pack(fill=X, pady=(0, 8))
        ttk.Label(row2, text="Device ID", width=14, anchor=W).pack(side=LEFT)
        id_var = tk.StringVar(value=cfg.device_id)
        ttk.Entry(row2, textvariable=id_var, state="readonly").pack(
            side=LEFT, fill=X, expand=YES, padx=(12, 0),
        )

        save_row = ttk.Frame(card)
        save_row.pack(fill=X)
        ttk.Button(
            save_row, text="Save Device Name",
            bootstyle="primary-outline",
            command=self._on_save_general,
        ).pack(side=LEFT)
        if self._on_export_logs is not None:
            ttk.Button(
                save_row, text=u"\U0001F4BE  Export Logs",
                bootstyle="info-outline",
                command=self._on_export_logs,
                padding=(10, 4),
            ).pack(side=LEFT, padx=(10, 0))

        # ── Sync Settings card ───────────────────────────────────
        card2 = ttk.Labelframe(panel, text="  Sync & Startup  ", padding=12)
        card2.pack(fill=X, pady=(0, 12))

        # Sync health meter
        self._sync_meter = ttk.Floodgauge(
            card2,
            text="Sync Health",
            value=0,
            maximum=100,
            length=320,
            mode=DETERMINATE,
            bootstyle="success",
        )
        self._sync_meter.pack(fill=X, pady=(0, 12))

        # Enable sync
        self._sync_var = tk.BooleanVar(value=self._get_sync())
        ttk.Checkbutton(
            card2,
            text="Enable real-time clipboard sync",
            variable=self._sync_var,
            command=self._on_toggle_sync,
            bootstyle="success-round-toggle",
        ).pack(anchor=W, pady=(0, 8))

        # Auto-start
        self._autostart_var = tk.BooleanVar(value=cfg.auto_start)
        ttk.Checkbutton(
            card2,
            text="Start CopyBoard on login",
            variable=self._autostart_var,
            command=self._on_toggle_autostart,
            bootstyle="success-round-toggle",
        ).pack(anchor=W)

        return panel

    # ── General callbacks ────────────────────────────────────────

    def _on_toggle_sync(self):
        enabled = self._sync_var.get()
        self._set_sync(enabled)
        cfg = self._get_config()
        cfg.sync_enabled = enabled
        self._save_config()
        self._status_label.config(
            text="Sync " + ("enabled" if enabled else "paused"),
        )
        logger.info("Sync %s", "enabled" if enabled else "paused")

    def _on_toggle_autostart(self):
        cfg = self._get_config()
        cfg.auto_start = self._autostart_var.get()
        self._save_config()
        logger.info("Auto-start %s", "enabled" if cfg.auto_start else "disabled")

    def _on_save_general(self):
        new_name = self._dev_name_var.get().strip()
        if not new_name:
            logger.warning("Device name save attempted with empty name")
            messagebox.showwarning("Invalid", "Device name cannot be empty.")
            return
        old_name = self._get_config().device_name
        cfg = self._get_config()
        cfg.device_name = new_name
        self._save_config()
        logger.info("Device name changed: %s -> %s", old_name, new_name)
        messagebox.showinfo("Saved", f"Device name updated to: {new_name}")

    # ═══════════════════════════════════════════════════════════════
    # Sync health meter
    # ═══════════════════════════════════════════════════════════════

    def _refresh_sync_meter(self):
        """Drive the Floodgauge from current peer connection state."""
        if self._sync_meter is None:
            return
        try:
            peers = self._get_peers()
            if not peers:
                self._sync_meter.configure(value=0)
                return

            connected = sum(1 for _, _, _, c in peers if c)
            paired = sum(1 for _, _, p, _ in peers if p)
            total = len(peers)

            # Weighted health: connected peers at 100 %, paired-only at 50 %
            health = int(((connected + 0.5 * max(paired - connected, 0))
                          / max(total, 1)) * 100)
            health = min(health, 100)

            self._sync_meter.configure(value=health)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    # Panel: Devices  (card-based, replaces Treeview)
    # ═══════════════════════════════════════════════════════════════

    def _build_devices_panel(self) -> ttk.Frame:
        panel = ttk.Frame(self._content_frame)

        ttk.Label(
            panel, text="Device Management",
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor=W, pady=(0, 14))

        # ── Known Devices ────────────────────────────────────────
        card = ttk.Labelframe(panel, text="  Known Devices  ", padding=8)
        card.pack(fill=BOTH, expand=YES, pady=(0, 12))

        # Scrollable area for device cards (Canvas + Scrollbar)
        sf_outer = ttk.Frame(card)
        sf_outer.pack(fill=BOTH, expand=YES)

        self._device_canvas = tk.Canvas(sf_outer, height=140, highlightthickness=0,
                                         borderwidth=0)
        scrollbar = ttk.Scrollbar(sf_outer, orient=VERTICAL,
                                  command=self._device_canvas.yview)
        self._device_canvas.configure(yscrollcommand=scrollbar.set)

        self._device_cards_inner = ttk.Frame(self._device_canvas, padding=2)
        self._canvas_window = self._device_canvas.create_window(
            (0, 0), window=self._device_cards_inner, anchor=NW,
        )

        self._device_canvas.pack(side=LEFT, fill=BOTH, expand=YES)
        scrollbar.pack(side=RIGHT, fill=Y)

        # Bind scroll region update and mousewheel
        self._device_cards_inner.bind("<Configure>", self._on_cards_inner_configure)
        self._device_canvas.bind("<Configure>", self._on_canvas_configure)
        self._device_canvas.bind("<Enter>", self._bind_mousewheel)
        self._device_canvas.bind("<Leave>", self._unbind_mousewheel)

        # ── Pairing ──────────────────────────────────────────────
        card2 = ttk.Labelframe(panel, text="  Pairing  ", padding=12)
        card2.pack(fill=X, pady=(0, 8))

        row = ttk.Frame(card2)
        row.pack(fill=X)

        ttk.Label(row, text="Pairing Code:").pack(side=LEFT)

        self._pair_code_var = tk.StringVar(value="")
        ttk.Entry(
            row,
            textvariable=self._pair_code_var,
            width=10,
            font=("Segoe UI", 15),
            justify=CENTER,
        ).pack(side=LEFT, padx=(10, 8))

        ttk.Button(
            row,
            text="Confirm Pairing",
            bootstyle="success",
            command=self._on_confirm_pairing,
        ).pack(side=LEFT)

        self._pending_label = ttk.Label(card2, text="", bootstyle="info")
        self._pending_label.pack(anchor=W, pady=(8, 0))

        return panel

    # ── Device card factory ──────────────────────────────────────

    @staticmethod
    def _status_dot_color(status: str) -> str:
        """Return hex colour for a device status."""
        if status == "Connected":
            return STATUS_COLORS["Connected"]
        elif status == "Paired":
            return STATUS_COLORS["Paired"]
        else:
            return STATUS_COLORS["Pending"]

    @staticmethod
    def _status_bootstyle(status: str) -> str:
        """Return ttkbootstrap bootstyle for a device status badge."""
        return STATUS_BOOTSTYLE.get(status, "secondary")

    def _create_device_card(self, dev_id: str, dev_name: str,
                            paired: bool, connected: bool):
        """Build a single device card with colour-coded status dot."""
        if connected:
            status = "Connected"
        elif paired:
            status = "Paired"
        else:
            status = "Pending"

        color = self._status_dot_color(status)
        badge_bs = self._status_bootstyle(status)

        # Card frame
        card = ttk.Frame(self._device_cards_inner, padding=10)
        card.pack(fill=X, pady=2)

        # ── Status dot (tk.Label with coloured circle glyph) ─────
        dot = tk.Label(
            card,
            text=u"●",          # filled circle
            fg=color,
            font=("", 16),
            bg=self._get_card_bg(card),
            bd=0,
            padx=0,
            pady=0,
        )
        dot.pack(side=LEFT, padx=(0, 10))

        # ── Middle: name + id ────────────────────────────────────
        middle = ttk.Frame(card)
        middle.pack(side=LEFT, fill=X, expand=YES)

        name_row = ttk.Frame(middle)
        name_row.pack(fill=X)

        ttk.Label(
            name_row,
            text=dev_name,
            font=("Segoe UI", 11, "bold"),
        ).pack(side=LEFT)

        # Status badge
        ttk.Label(
            name_row,
            text=f"  {status}  ",
            bootstyle=badge_bs,
            font=("Segoe UI", 10),
        ).pack(side=LEFT, padx=(8, 0))

        ttk.Label(
            middle,
            text=dev_id,
            bootstyle="secondary",
            font=("Segoe UI", 10),
        ).pack(anchor=W)

        # ── Right: action buttons ────────────────────────────────
        btns = ttk.Frame(card)
        btns.pack(side=RIGHT, padx=(8, 0))

        ttk.Button(
            btns,
            text="Unpair",
            bootstyle="warning-outline",
            command=lambda d=dev_id: self._do_unpair(d),
            padding=(8, 3),
        ).pack(side=LEFT, padx=(0, 5))

        ttk.Button(
            btns,
            text="Remove",
            bootstyle="danger-outline",
            command=lambda d=dev_id: self._do_remove(d),
            padding=(8, 3),
        ).pack(side=LEFT)

        return card

    def _get_card_bg(self, _widget: ttk.Frame | None = None) -> str:
        """Pull the current theme background colour."""
        try:
            # Use ttkbootstrap's colour utility for the standard background
            return self._style.colors.bg  # type: ignore[union-attr]
        except Exception:
            return "#ffffff"

    # ── Device list refresh ──────────────────────────────────────

    def _refresh_device_list(self):
        """Rebuild all device cards and update the pending label."""
        if self._device_cards_inner is None:
            return

        # Destroy old cards
        for child in self._device_cards_inner.winfo_children():
            child.destroy()

        peers = self._get_peers()
        for dev_id, dev_name, paired, connected in peers:
            self._create_device_card(dev_id, dev_name, paired, connected)

        # Empty state
        if not peers:
            ttk.Label(
                self._device_cards_inner,
                text=(
                    "No devices discovered yet.\n"
                    "Devices on your LAN will appear here automatically."
                ),
                bootstyle="secondary",
                justify=CENTER,
                padding=(0, 24),
            ).pack(fill=X, expand=YES)

        # Pending pairings label
        if self._pending_label is not None:
            pending = self._get_pending()
            if pending:
                lines = "\n".join(
                    f"    {pid} : {code}" for pid, code in pending
                )
                self._pending_label.configure(
                    text=f"Pairing codes to share:\n{lines}",
                )
            else:
                self._pending_label.configure(text="")

        # Defer scroll region update until after layout settles
        if self._window is not None:
            self._window.after(50, self._on_cards_inner_configure)

    # ── Canvas scrolling helpers ──────────────────────────────────

    def _on_cards_inner_configure(self, _event=None):
        """Update scrollregion when inner frame size changes."""
        if self._device_canvas is not None:
            self._device_canvas.configure(
                scrollregion=self._device_canvas.bbox("all"),
            )

    def _on_canvas_configure(self, event):
        """Keep inner frame width matching canvas width."""
        if self._device_canvas is not None and self._canvas_window is not None:
            self._device_canvas.itemconfig(
                self._canvas_window, width=event.width,
            )

    def _bind_mousewheel(self, _event=None):
        if self._device_canvas is not None:
            self._device_canvas.bind_all(
                "<MouseWheel>",
                lambda e: self._device_canvas.yview_scroll(
                    int(-1 * (e.delta / 120)), "units"),
            )

    def _unbind_mousewheel(self, _event=None):
        if self._device_canvas is not None:
            self._device_canvas.unbind_all("<MouseWheel>")

    # ── Device actions ───────────────────────────────────────────

    def _do_unpair(self, peer_id: str):
        if messagebox.askyesno("Unpair", f"Unpair this device?\n\n{peer_id}"):
            logger.info("Unpairing peer: %s", peer_id)
            self._on_unpair(peer_id)
            self._refresh_device_list()
            logger.info("Peer unpaired: %s", peer_id)

    def _do_remove(self, peer_id: str):
        if messagebox.askyesno("Remove", f"Remove this device?\n\n{peer_id}"):
            logger.info("Removing peer: %s", peer_id)
            self._on_remove_peer(peer_id)
            self._refresh_device_list()
            logger.info("Peer removed: %s", peer_id)

    def _on_confirm_pairing(self):
        code = self._pair_code_var.get().strip()
        if not code or not code.isdigit() or len(code) != 6:
            logger.warning("Pairing attempt with invalid code: %r", code)
            messagebox.showwarning(
                "Invalid Code",
                "Please enter a valid 6-digit pairing code.",
            )
            return

        pending = self._get_pending()
        if not pending:
            logger.info("No pending pairing requests")
            messagebox.showinfo("No Pending", "No pending pairing requests.")
            return

        logger.info("Confirming pairing (code=%s) against %d pending peer(s)", code, len(pending))
        # Try each pending peer with the entered code
        success = False
        for peer_id, _ in pending:
            if self._on_pair(peer_id, code):
                logger.info("Pairing succeeded for %s", peer_id)
                success = True
                break

        if success:
            messagebox.showinfo("Success", "Device paired successfully!")
            self._pair_code_var.set("")
        else:
            logger.warning("Pairing failed for all pending peers (code=%s)", code)
            messagebox.showerror(
                "Failed",
                "Invalid pairing code. Check and try again.",
            )

        self._refresh_device_list()

    # ═══════════════════════════════════════════════════════════════
    # Panel: Network
    # ═══════════════════════════════════════════════════════════════

    def _build_network_panel(self) -> ttk.Frame:
        panel = ttk.Frame(self._content_frame)
        cfg = self._get_config()

        ttk.Label(
            panel, text="Network Settings",
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor=W, pady=(0, 14))

        # ── Connection card ──────────────────────────────────────
        card = ttk.Labelframe(panel, text="  Connection  ", padding=12)
        card.pack(fill=X, pady=(0, 12))

        # Port
        row1 = ttk.Frame(card)
        row1.pack(fill=X, pady=(0, 8))
        ttk.Label(row1, text="TCP Port", width=14, anchor=W).pack(side=LEFT)
        self._port_var = tk.StringVar(value=str(cfg.port))
        ttk.Entry(row1, textvariable=self._port_var, width=10).pack(side=LEFT, padx=(12, 0))
        ttk.Label(
            row1, text="(1024 – 65535, restart required)",
            bootstyle="secondary", font=("Segoe UI", 10),
        ).pack(side=LEFT, padx=(8, 0))

        # Service type
        row2 = ttk.Frame(card)
        row2.pack(fill=X, pady=(0, 8))
        ttk.Label(row2, text="Service Type", width=14, anchor=W).pack(side=LEFT)
        self._svc_var = tk.StringVar(value=cfg.service_type)
        ttk.Entry(row2, textvariable=self._svc_var).pack(
            side=LEFT, fill=X, expand=YES, padx=(12, 0),
        )

        # ── Relay card ───────────────────────────────────────────
        card2 = ttk.Labelframe(panel, text="  Relay (optional)  ", padding=12)
        card2.pack(fill=X, pady=(0, 12))

        ttk.Label(card2, text="Relay URL", anchor=W).pack(anchor=W)
        self._relay_var = tk.StringVar(value=cfg.relay_url)
        ttk.Entry(card2, textvariable=self._relay_var).pack(fill=X, pady=(6, 4))
        ttk.Label(
            card2,
            text="Leave blank for LAN-only sync. Set a relay server URL to sync across different networks.",
            bootstyle="secondary", font=("Segoe UI", 10), wraplength=380,
        ).pack(anchor=W)

        ttk.Button(
            panel, text="Save Network Settings",
            bootstyle="primary-outline",
            command=self._on_save_network,
        ).pack(anchor=W)

        return panel

    def _on_save_network(self):
        try:
            port = int(self._port_var.get())
            if not 1024 <= port <= 65535:
                raise ValueError("Port out of range")
        except ValueError:
            logger.warning("Invalid port entered: %s", self._port_var.get())
            messagebox.showwarning("Invalid", "Port must be 1024 – 65535.")
            return

        cfg = self._get_config()
        old_port = cfg.port
        old_svc = cfg.service_type
        old_relay = cfg.relay_url
        cfg.port = port
        cfg.service_type = self._svc_var.get().strip()
        cfg.relay_url = self._relay_var.get().strip()
        self._save_config()
        logger.info("Network settings saved (port=%d->%d, svc=%s->%s, relay=%s->%s)",
                    old_port, port, old_svc, cfg.service_type,
                    old_relay or "(none)", cfg.relay_url or "(none)")
        messagebox.showinfo(
            "Saved",
            "Network settings saved.\n\n"
            "Restart CopyBoard for port changes to take effect.",
        )

    # ═══════════════════════════════════════════════════════════════
    # Panel: About
    # ═══════════════════════════════════════════════════════════════

    def _build_about_panel(self) -> ttk.Frame:
        panel = ttk.Frame(self._content_frame)

        # Centered content
        centre = ttk.Frame(panel)
        centre.pack(expand=YES, fill=BOTH)

        ttk.Label(
            centre,
            text=u"\U0001F4CB",
            font=("Segoe UI", 32),
        ).pack(pady=(0, 6))

        ttk.Label(
            centre,
            text="CopyBoard",
            font=("Segoe UI", 18, "bold"),
        ).pack(pady=(0, 4))

        ttk.Label(
            centre,
            text="v1.0.0",
            bootstyle="secondary",
            font=("Segoe UI", 11),
        ).pack()

        ttk.Label(
            centre,
            text="\nCross-platform clipboard sharing\n"
                 "between Windows, macOS, and Linux.",
            justify=CENTER,
            font=("Segoe UI", 11),
        ).pack(pady=(16, 12))

        # Feature list
        card = ttk.Labelframe(centre, text="  Features  ", padding=12)
        card.pack(fill=X, pady=(4, 0))

        features = [
            (u"✅", "Automatic peer discovery on LAN (mDNS/Bonjour)"),
            (u"\U0001F512", "TLS 1.3 encrypted transport"),
            (u"\U0001F4C4", "Supports text, HTML, RTF, and images"),
            (u"⚡", "Zero configuration needed"),
            (u"\U0001F4A1", "Certificate pinning — detects MITM attacks"),
            (u"\U0001F6AB", "8-digit pairing code, rate-limited"),
        ]

        for icon, desc in features:
            row = ttk.Frame(card)
            row.pack(fill=X, pady=2)
            ttk.Label(row, text=icon, font=("Segoe UI", 11)).pack(side=LEFT, padx=(0, 6))
            ttk.Label(row, text=desc, font=("Segoe UI", 10)).pack(side=LEFT)

        return panel

    # ═══════════════════════════════════════════════════════════════
    # Theme toggle
    # ═══════════════════════════════════════════════════════════════

    def _toggle_theme(self):
        """Flip between light (flatly) and dark (darkly) themes."""
        if self._style is None:
            return

        self._dark_mode = not self._dark_mode
        if self._dark_mode:
            self._style.theme_use(DARK_THEME)
            self._theme_btn.configure(text=u"☀️  Light")
            logger.info("Theme switched to dark (darkly)")
        else:
            self._style.theme_use(LIGHT_THEME)
            self._theme_btn.configure(text=u"\U0001F319  Dark")
            logger.info("Theme switched to light (flatly)")

        # Redraw device cards so status-dot backgrounds match new theme
        self._refresh_device_list()

    # ═══════════════════════════════════════════════════════════════
    # Window icon (PIL-based, identical to original)
    # ═══════════════════════════════════════════════════════════════

    def _make_tk_icon(self) -> tk.PhotoImage:
        """Create a small clipboard icon for the title bar."""
        from PIL import Image, ImageDraw

        size = 32
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        m = 4
        y0 = m + 5
        draw.rounded_rectangle(
            [m, y0, size - m, size - m], radius=4, fill=(80, 140, 220),
        )
        bw = size // 3
        bx0 = (size - bw) // 2
        draw.rounded_rectangle(
            [bx0, m, bx0 + bw, y0 + 3], radius=2, fill=(60, 110, 180),
        )
        for i in range(3):
            ly = y0 + 6 + i * 5
            draw.line(
                [m + 8, ly, size - m - 8, ly],
                fill=(220, 230, 245), width=1,
            )

        return tk.PhotoImage(master=self._root, data=img.tobytes("raw", "RGBA"))
