"""Main dashboard window for CopyBoard — sidebar navigation with rich panels.

Panels: Overview, Devices (with pairing), History, Transfers.
Settings is a separate window accessed via the sidebar button.
"""

import datetime
import logging
import tkinter as tk
from tkinter import messagebox
from typing import Callable

import customtkinter as ctk

logger = logging.getLogger(__name__)

STATUS_COLOR = "#2ECC71"
OFFLINE_COLOR = "#95A5A6"
WARN_COLOR = "#F39C12"
ACCENT = "#3498DB"

STATUS_COLORS = {
    "Connected":  "#2ECC71",
    "Paired":     "#F39C12",
    "Discovered": "#3498DB",
    "Pending":    "#95A5A6",
}


class DashboardWindow:
    """Main application window with sidebar + panels."""

    def __init__(
        self,
        root: tk.Tk,
        get_config: Callable,
        save_config: Callable,
        get_peers: Callable,
        get_sync_enabled: Callable,
        set_sync_enabled: Callable,
        on_open_settings: Callable | None = None,
        on_send_file: Callable | None = None,
        on_toggle_autostart: Callable | None = None,
        get_transfers: Callable | None = None,
        on_cancel_transfer: Callable | None = None,
        # Pairing
        get_pending_pairings: Callable | None = None,
        on_pair: Callable | None = None,
        on_unpair: Callable | None = None,
        on_connect_peer: Callable | None = None,
        on_remove_peer: Callable | None = None,
        # History
        get_history: Callable | None = None,
        search_history: Callable | None = None,
        copy_from_history: Callable | None = None,
        clear_history: Callable | None = None,
        # Lifecycle
        on_hidden: Callable | None = None,
    ):
        self._root = root
        self._get_config = get_config
        self._save_config = save_config
        self._get_peers = get_peers
        self._get_sync = get_sync_enabled
        self._set_sync = set_sync_enabled
        self._on_open_settings = on_open_settings
        self._on_send_file = on_send_file
        self._on_toggle_autostart_cb = on_toggle_autostart
        self._get_transfers = get_transfers
        self._on_cancel_transfer = on_cancel_transfer
        self._get_pending = get_pending_pairings
        self._on_pair = on_pair
        self._on_unpair = on_unpair
        self._on_connect_peer = on_connect_peer
        self._on_remove_peer = on_remove_peer
        self._get_history = get_history
        self._search_history = search_history
        self._copy_from_history = copy_from_history
        self._clear_history = clear_history
        self._on_hidden = on_hidden

        self._window: ctk.CTkToplevel | None = None
        self._dark_mode = False
        self._current_panel = "overview"
        self._refresh_job: str | None = None
        self._anim_frame = 0

        # Sidebar
        self._sidebar_buttons: dict[str, ctk.CTkButton] = {}
        self._panels: dict[str, ctk.CTkFrame] = {}
        self._content_frame: ctk.CTkFrame | None = None

        # Form vars
        self._sync_var: tk.BooleanVar | None = None
        self._autostart_var: tk.BooleanVar | None = None
        self._history_search_var: tk.StringVar | None = None

        # Widget refs
        self._status_dot: ctk.CTkFrame | None = None
        self._status_label: ctk.CTkLabel | None = None
        self._stat_peers: ctk.CTkLabel | None = None
        self._stat_crypto: ctk.CTkLabel | None = None
        self._stat_history: ctk.CTkLabel | None = None
        self._device_name_label: ctk.CTkLabel | None = None
        self._device_scroll: ctk.CTkScrollableFrame | None = None
        self._transfer_scroll: ctk.CTkScrollableFrame | None = None
        self._history_scroll: ctk.CTkScrollableFrame | None = None
        self._pending_label: ctk.CTkLabel | None = None
        self._pending_frame: ctk.CTkFrame | None = None
        self._footer_label: ctk.CTkLabel | None = None
        self._status_footer: ctk.CTkLabel | None = None
        self._overview_device_name: ctk.CTkLabel | None = None
        self._last_history_count: int = 0

    # ═══════════════════════════════════════════════════════════════
    # Public API
    # ═══════════════════════════════════════════════════════════════

    def show(self):
        if self._window is not None:
            try:
                self._window.deiconify()
                self._window.lift()
                self._window.focus_force()
                self._window.update_idletasks()
                if self._window.winfo_viewable():
                    self._switch_panel(self._current_panel)
                    self._schedule_refresh()
                    return
                self._window.destroy()
                self._window = None
            except tk.TclError:
                self._window = None

        logger.info("Opening CopyBoard dashboard")
        ctk.set_appearance_mode("dark" if self._dark_mode else "light")
        ctk.set_default_color_theme("blue")

        self._window = ctk.CTkToplevel(self._root)
        self._window.title("CopyBoard")
        self._window.geometry("800x640")
        self._window.minsize(720, 560)
        self._window.protocol("WM_DELETE_WINDOW", self._on_hide)

        self._window.update_idletasks()
        sw = self._window.winfo_screenwidth()
        sh = self._window.winfo_screenheight()
        w, h = 800, 640
        self._window.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        self._build_ui()
        self._switch_panel("overview")
        self._schedule_refresh()

    def _on_hide(self):
        if self._window is not None:
            self._window.withdraw()
        if self._on_hidden:
            self._on_hidden()

    def _on_close(self):
        if self._refresh_job is not None:
            self._root.after_cancel(self._refresh_job)
            self._refresh_job = None
        if self._window is not None:
            self._window.destroy()
            self._window = None

    def _schedule_refresh(self):
        self._refresh_overview()
        if self._current_panel == "devices":
            self._refresh_devices()
        elif self._current_panel == "transfers":
            self._refresh_transfers()
        elif self._current_panel == "history":
            # Lightweight check: only rebuild if count changed
            if self._get_history is not None:
                try:
                    entries = self._get_history()
                    if len(entries) != self._last_history_count:
                        self._last_history_count = len(entries)
                        self._refresh_history_list()
                except Exception:
                    pass
        if self._window is not None:
            self._refresh_job = self._root.after(2000, self._schedule_refresh)

    # ═══════════════════════════════════════════════════════════════
    # UI construction
    # ═══════════════════════════════════════════════════════════════

    def _build_ui(self):
        cfg = self._get_config()
        outer = ctk.CTkFrame(self._window, fg_color="transparent")
        outer.pack(fill="both", expand=True)

        # ── Header ──────────────────────────────────────────────────
        header = ctk.CTkFrame(outer, corner_radius=0, fg_color=("#1A5276", "#1B2A3A"))
        header.pack(fill="x")
        h_inner = ctk.CTkFrame(header, fg_color="transparent")
        h_inner.pack(fill="x", padx=20, pady=(14, 14))

        ctk.CTkLabel(
            h_inner, text="\U0001F4CB  CopyBoard",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=("#FFFFFF", "#E0E0E0"),
        ).pack(side="left")

        self._device_name_label = ctk.CTkLabel(
            h_inner, text=cfg.device_name,
            font=ctk.CTkFont(size=12),
            text_color=("#B0C4DE", "#8A9BA8"),
        )
        self._device_name_label.pack(side="left", padx=(16, 0))

        self._theme_btn = ctk.CTkButton(
            h_inner, text="☾  Dark" if not self._dark_mode else "☀  Light",
            width=90, height=32, fg_color="transparent",
            border_width=1, border_color=("#7F8C8D", "#566573"),
            text_color=("#FFFFFF", "#E0E0E0"),
            hover_color=("#5D6D7E", "#4A5568"),
            command=self._toggle_theme,
        )
        self._theme_btn.pack(side="right")

        # ── Body: sidebar | content ─────────────────────────────────
        body = ctk.CTkFrame(outer, fg_color="transparent")
        body.pack(fill="both", expand=True)

        self._build_sidebar(body)

        sep = ctk.CTkFrame(body, width=1, fg_color=("gray75", "gray30"))
        sep.pack(side="left", fill="y")

        self._content_frame = ctk.CTkFrame(body, fg_color="transparent")
        self._content_frame.pack(side="left", fill="both", expand=True)

        # Build panels
        self._panels["overview"] = self._build_overview_panel()
        self._panels["devices"] = self._build_devices_panel()
        self._panels["history"] = self._build_history_panel()
        self._panels["transfers"] = self._build_transfers_panel()

        # ── Footer ──────────────────────────────────────────────────
        footer = ctk.CTkFrame(outer, height=44, corner_radius=0,
                              fg_color=("gray90", "gray15"))
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)
        f_inner = ctk.CTkFrame(footer, fg_color="transparent")
        f_inner.pack(fill="x", padx=20, pady=8)

        self._status_footer = ctk.CTkLabel(
            f_inner, text="Ready",
            text_color=("gray50", "gray60"),
            font=ctk.CTkFont(size=11),
        )
        self._status_footer.pack(side="left")

        ctk.CTkButton(
            f_inner, text="Hide to Tray", width=100, height=28,
            fg_color="transparent", border_width=1,
            text_color=("gray40", "gray70"),
            border_color=("gray60", "gray50"),
            hover_color=("gray85", "gray25"),
            command=self._on_hide,
        ).pack(side="right")

    # ── Sidebar ────────────────────────────────────────────────────

    def _build_sidebar(self, body):
        sidebar = ctk.CTkFrame(body, width=160, fg_color="transparent")
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        inner = ctk.CTkFrame(sidebar, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=8, pady=16)

        nav = [
            ("overview",  "\U0001F3E0  Overview"),
            ("devices",   "\U0001F4F1  Devices"),
            ("history",   "\U0001F4CB  History"),
            ("transfers", "\U0001F4E4  Transfers"),
        ]

        for key, label in nav:
            btn = ctk.CTkButton(
                inner, text=label, anchor="w",
                height=40, corner_radius=8,
                fg_color="transparent",
                text_color=("gray30", "gray80"),
                hover_color=("gray85", "gray25"),
                font=ctk.CTkFont(size=13),
                command=lambda k=key: self._switch_panel(k),
            )
            btn.pack(fill="x", pady=1)
            self._sidebar_buttons[key] = btn

        # Settings button at bottom
        spacer = ctk.CTkFrame(inner, fg_color="transparent")
        spacer.pack(fill="y", expand=True)

        if self._on_open_settings:
            ctk.CTkButton(
                inner, text="⚙  Settings", anchor="w",
                height=36, corner_radius=8,
                fg_color="transparent", border_width=1,
                text_color=("gray40", "gray70"),
                border_color=("gray60", "gray50"),
                hover_color=("gray85", "gray25"),
                font=ctk.CTkFont(size=12),
                command=self._on_open_settings,
            ).pack(fill="x", pady=(0, 2))

    # ═══════════════════════════════════════════════════════════════
    # Panel switching
    # ═══════════════════════════════════════════════════════════════

    def _switch_panel(self, key: str):
        if self._content_frame is None:
            return
        for pk, panel in self._panels.items():
            if pk == key:
                panel.pack(in_=self._content_frame, fill="both", expand=True,
                          padx=20, pady=16)
            else:
                panel.pack_forget()
        for pk, btn in self._sidebar_buttons.items():
            if pk == key:
                btn.configure(
                    fg_color=("#2A82C7", "#1F6AA5"),
                    text_color=("#FFFFFF", "#FFFFFF"),
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=("gray30", "gray80"),
                )
        self._current_panel = key
        if key == "devices":
            self._refresh_devices()
        elif key == "transfers":
            self._refresh_transfers()
        elif key == "history":
            self._refresh_history_list()
        elif key == "overview":
            self._refresh_overview()

    # ═══════════════════════════════════════════════════════════════
    # Panel: Overview
    # ═══════════════════════════════════════════════════════════════

    def _build_overview_panel(self):
        panel = ctk.CTkFrame(self._content_frame, fg_color="transparent")
        cfg = self._get_config()

        ctk.CTkLabel(
            panel, text="System Overview",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", pady=(0, 16))

        # Status card
        card = ctk.CTkFrame(panel, corner_radius=12)
        card.pack(fill="x", pady=(0, 12))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=(14, 14))

        status_row = ctk.CTkFrame(inner, fg_color="transparent")
        status_row.pack(fill="x")

        self._status_dot = ctk.CTkFrame(status_row, width=16, height=16,
                                        corner_radius=8, fg_color=STATUS_COLOR)
        self._status_dot.pack(side="left", padx=(0, 12))

        left = ctk.CTkFrame(status_row, fg_color="transparent")
        left.pack(side="left")

        self._status_label = ctk.CTkLabel(
            left, text="Sync Active",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=("gray20", "gray90"),
        )
        self._status_label.pack(anchor="w")

        ctk.CTkLabel(
            left, text="TLS 1.3 encrypted  •  mDNS auto-discovery",
            font=ctk.CTkFont(size=11),
            text_color=("#27AE60", "#2ECC71"),
        ).pack(anchor="w")

        # Device info card
        card2 = ctk.CTkFrame(panel, corner_radius=12)
        card2.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            card2, text="This Device",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(14, 6))

        info_grid = ctk.CTkFrame(card2, fg_color="transparent")
        info_grid.pack(fill="x", padx=16, pady=(0, 12))

        # Device Name row (editable)
        name_row = ctk.CTkFrame(info_grid, fg_color="transparent")
        name_row.pack(fill="x", pady=2)
        ctk.CTkLabel(name_row, text="Device Name:", width=100, anchor="w",
                    font=ctk.CTkFont(size=12)).pack(side="left")
        self._overview_device_name = ctk.CTkLabel(
            name_row, text=cfg.device_name,
            font=ctk.CTkFont(size=12),
            text_color=("gray50", "gray60"),
        )
        self._overview_device_name.pack(side="left")
        ctk.CTkButton(
            name_row, text="✎", width=28, height=24,
            fg_color="transparent", border_width=1,
            text_color=("gray50", "gray60"),
            border_color=("gray65", "gray50"),
            hover_color=("gray85", "gray25"),
            font=ctk.CTkFont(size=11),
            command=self._edit_device_name,
        ).pack(side="left", padx=(8, 0))

        rows = [
            ("Device ID", cfg.device_id),
            ("Port", str(cfg.port)),
            ("Service", cfg.service_type),
        ]
        for label, value in rows:
            row = ctk.CTkFrame(info_grid, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=label + ":", width=100, anchor="w",
                        font=ctk.CTkFont(size=12)).pack(side="left")
            ctk.CTkLabel(row, text=value,
                        font=ctk.CTkFont(size=12),
                        text_color=("gray50", "gray60")).pack(side="left")

        # Stats card
        card3 = ctk.CTkFrame(panel, corner_radius=12)
        card3.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            card3, text="Activity",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(14, 6))

        stat_row = ctk.CTkFrame(card3, fg_color="transparent")
        stat_row.pack(fill="x", padx=16, pady=(0, 14))

        stats = [
            ("Devices", "\U0001F4F1", "0", "_stat_peers"),
            ("Security", "\U0001F512", "TLS 1.3", "_stat_crypto"),
            ("History", "\U0001F4CB", "0", "_stat_history"),
        ]
        for title, icon, default, ref_name in stats:
            box = ctk.CTkFrame(stat_row, corner_radius=8,
                             fg_color=("gray95", "gray17"))
            box.pack(side="left", fill="x", expand=True, padx=3)

            ctk.CTkLabel(
                box, text=f"{icon}  {title}",
                font=ctk.CTkFont(size=11),
                text_color=("gray50", "gray60"),
            ).pack(anchor="center", padx=8, pady=(8, 2))

            val = ctk.CTkLabel(
                box, text=default,
                font=ctk.CTkFont(size=20, weight="bold"),
                text_color=("gray20", "gray85"),
            )
            val.pack(anchor="center", padx=8, pady=(0, 8))

            if title == "Devices":
                self._stat_peers = val
            elif title == "Security":
                self._stat_crypto = val
            elif title == "History":
                self._stat_history = val

        # Quick controls card
        card4 = ctk.CTkFrame(panel, corner_radius=12)
        card4.pack(fill="x")

        ctk.CTkLabel(
            card4, text="Quick Controls",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(14, 6))

        self._sync_var = tk.BooleanVar(value=self._get_sync())
        ctk.CTkSwitch(
            card4, text="Enable clipboard sync",
            variable=self._sync_var, command=self._on_toggle_sync,
            font=ctk.CTkFont(size=13),
        ).pack(anchor="w", padx=16, pady=(0, 6))

        self._autostart_var = tk.BooleanVar(value=cfg.auto_start)
        ctk.CTkSwitch(
            card4, text="Start on login",
            variable=self._autostart_var, command=self._on_toggle_autostart,
            font=ctk.CTkFont(size=13),
        ).pack(anchor="w", padx=16, pady=(0, 10))

        return panel

    def _refresh_overview(self):
        if self._status_dot is None:
            return

        syncing = self._get_sync()
        self._anim_frame = (self._anim_frame + 1) % 20
        if syncing:
            size = 15 + (1 if self._anim_frame < 10 else 0)
            self._status_dot.configure(width=size, height=size,
                                       corner_radius=size // 2,
                                       fg_color=STATUS_COLOR)
            self._status_label.configure(text="Sync Active")
        else:
            self._status_dot.configure(width=16, height=16, corner_radius=8,
                                       fg_color=OFFLINE_COLOR)
            self._status_label.configure(text="Sync Paused")

        try:
            peers = self._get_peers()
            connected = sum(1 for _, _, _, c in peers if c)
            total = len(peers)
            if self._stat_peers:
                if total == 0:
                    self._stat_peers.configure(text="Searching...")
                elif connected > 0:
                    self._stat_peers.configure(text=f"{connected}/{total}")
                else:
                    self._stat_peers.configure(text=f"{total}")
        except Exception:
            pass

        if self._stat_crypto:
            self._stat_crypto.configure(text="TLS 1.3")

        if self._stat_history:
            try:
                history = self._get_history() if self._get_history else []
                count = len(history)
                self._stat_history.configure(text=str(count))
            except Exception:
                self._stat_history.configure(text="--")

    # ═══════════════════════════════════════════════════════════════
    # Panel: Devices (with pairing)
    # ═══════════════════════════════════════════════════════════════

    def _build_devices_panel(self):
        panel = ctk.CTkFrame(self._content_frame, fg_color="transparent")

        ctk.CTkLabel(
            panel, text="Network Devices",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", pady=(0, 4))

        ctk.CTkLabel(
            panel, text="Auto-discovered via mDNS/Bonjour on your LAN",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
        ).pack(anchor="w", pady=(0, 14))

        # Device list card
        card = ctk.CTkFrame(panel, corner_radius=12)
        card.pack(fill="both", expand=True, pady=(0, 10))

        self._device_scroll = ctk.CTkScrollableFrame(card, fg_color="transparent")
        self._device_scroll.pack(fill="both", expand=True, padx=8, pady=8)

        # Section labels (created once, hidden/shown in _refresh_devices)
        self._known_header: ctk.CTkLabel | None = None
        self._discovered_header: ctk.CTkLabel | None = None

        # Pairing section
        card2 = ctk.CTkFrame(panel, corner_radius=12)
        card2.pack(fill="x")

        ctk.CTkLabel(
            card2, text="Pairing Requests",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(14, 4))

        instr = ctk.CTkLabel(
            card2,
            text=(
                "Click 'Connect' on a discovered device. "
                "Both devices will show the same 8-digit code.\n"
                "Verify the codes match, then click Confirm."
            ),
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
            justify="left",
        )
        instr.pack(anchor="w", padx=16, pady=(0, 8))

        self._pending_frame = ctk.CTkFrame(card2, fg_color="transparent")
        self._pending_frame.pack(fill="x", padx=16, pady=(0, 12))

        self._pending_label = ctk.CTkLabel(
            self._pending_frame, text="",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray60"),
        )
        self._pending_label.pack(anchor="w")

        return panel

    def _refresh_devices(self):
        if self._device_scroll is None:
            return
        for child in self._device_scroll.winfo_children():
            child.destroy()

        try:
            peers = self._get_peers()
        except Exception:
            peers = []

        if not peers:
            ctk.CTkLabel(
                self._device_scroll,
                text="Searching for devices on your LAN...\n\n"
                     "Devices running CopyBoard will appear here.\n"
                     "Make sure they are on the same network.",
                font=ctk.CTkFont(size=12),
                text_color=("gray50", "gray60"),
                justify="center",
            ).pack(fill="x", expand=True, pady=40)
        else:
            known = [(i, d, p, c) for i, d, p, c in peers if p or c]
            discovered = [(i, d, p, c) for i, d, p, c in peers if not p and not c]

            if known:
                self._add_section_header("📋  Known Devices", len(known))
                for dev_id, dev_name, paired, connected in known:
                    self._create_device_row(dev_id, dev_name, paired, connected)

            if discovered:
                self._add_section_header("🔍  Discovered Devices", len(discovered))
                for dev_id, dev_name, paired, connected in discovered:
                    self._create_device_row(dev_id, dev_name, paired, connected)

        # Pending pairing codes — per-device confirm / reject buttons
        if self._pending_frame is not None:
            for child in self._pending_frame.winfo_children():
                child.destroy()
            pending = self._get_pending() if self._get_pending else []
            if pending:
                self._pending_label.pack_forget()
                for peer_id, code, peer_name in pending:
                    self._create_pending_row(peer_id, code, peer_name)
            else:
                self._pending_label.configure(text="No pending pairing requests.")
                self._pending_label.pack(anchor="w")

    def _add_section_header(self, text: str, count: int):
        header = ctk.CTkFrame(self._device_scroll, fg_color="transparent")
        header.pack(fill="x", pady=(8, 2), padx=4)
        ctk.CTkLabel(
            header, text=text,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("gray40", "gray70"),
        ).pack(side="left")
        ctk.CTkLabel(
            header, text=str(count),
            font=ctk.CTkFont(size=11),
            text_color=("gray60", "gray50"),
        ).pack(side="left", padx=(6, 0))

    def _create_device_row(self, dev_id, dev_name, paired, connected):
        if connected:
            color, status, icon = STATUS_COLOR, "Connected", "\U0001F7E2"
        elif paired:
            color, status, icon = WARN_COLOR, "Paired (offline)", "\U0001F7E1"
        else:
            color, status, icon = ACCENT, "Discovered", "\U0001F535"

        display_name = dev_name or dev_id[:12]
        display_id = dev_id[:12] if dev_name else dev_id[:16]

        row = ctk.CTkFrame(self._device_scroll, fg_color=("gray95", "gray17"),
                          corner_radius=8)
        row.pack(fill="x", pady=2, padx=2)

        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=10)

        dot = ctk.CTkFrame(inner, width=10, height=10, corner_radius=5,
                          fg_color=color)
        dot.pack(side="left", padx=(0, 8))

        mid = ctk.CTkFrame(inner, fg_color="transparent")
        mid.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            mid, text=display_name,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w")

        detail = status
        if connected:
            detail += "  \U0001F512  TLS 1.3 encrypted"
        elif paired:
            detail += " — reconnect to sync"

        ctk.CTkLabel(
            mid, text=detail,
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
        ).pack(anchor="w")

        # Right side: device ID + action buttons
        right = ctk.CTkFrame(inner, fg_color="transparent")
        right.pack(side="right")

        ctk.CTkLabel(
            right, text=display_id,
            font=ctk.CTkFont(size=9),
            text_color=("gray60", "gray50"),
        ).pack(side="bottom", anchor="e")

        btns = ctk.CTkFrame(right, fg_color="transparent")
        btns.pack(side="bottom", anchor="e", pady=(0, 4))

        if not connected and not paired and self._on_connect_peer:
            ctk.CTkButton(
                btns, text="Connect", width=70, height=26,
                fg_color=ACCENT,
                hover_color=("#2980B9", "#2471A3"),
                font=ctk.CTkFont(size=11),
                command=lambda d=dev_id: self._do_connect(d),
            ).pack(side="left", padx=(0, 4))

        if paired and not connected and self._on_connect_peer:
            ctk.CTkButton(
                btns, text="Reconnect", width=80, height=26,
                fg_color=ACCENT,
                hover_color=("#2980B9", "#2471A3"),
                font=ctk.CTkFont(size=11),
                command=lambda d=dev_id: self._do_reconnect(d),
            ).pack(side="left", padx=(0, 4))

        if paired:
            ctk.CTkButton(
                btns, text="Unpair", width=60, height=26,
                fg_color="transparent", border_width=1,
                text_color=WARN_COLOR,
                border_color=WARN_COLOR,
                hover_color=("#FDEBD0", "#7D5A0B"),
                font=ctk.CTkFont(size=11),
                command=lambda d=dev_id: self._do_unpair(d),
            ).pack(side="left", padx=(0, 4))

        ctk.CTkButton(
            btns, text="Forget", width=60, height=26,
            fg_color="transparent", border_width=1,
            text_color=("#E74C3C", "#C0392B"),
            border_color=("#E74C3C", "#C0392B"),
            hover_color=("#FADBD8", "#5B2C2C"),
            font=ctk.CTkFont(size=11),
            command=lambda d=dev_id: self._do_remove(d),
        ).pack(side="left")

    # ── Device actions ────────────────────────────────────────────

    def _do_connect(self, peer_id):
        logger.info("User initiated connection to %s", peer_id)
        if self._on_connect_peer:
            self._on_connect_peer(peer_id)
            messagebox.showinfo(
                "Connecting",
                "Connection initiated.\n\n"
                "A pairing code will appear in the Pairing section below.\n"
                "The SAME code should appear on BOTH devices.\n"
                "Enter the code and click 'Confirm Pairing' to complete.",
            )

    def _do_reconnect(self, peer_id):
        logger.info("User initiated reconnect to %s", peer_id)
        if self._on_connect_peer:
            self._on_connect_peer(peer_id)

    def _do_unpair(self, peer_id):
        if messagebox.askyesno("Unpair", f"Unpair this device?\n\n{peer_id}"):
            self._on_unpair(peer_id)
            self._refresh_devices()

    def _do_remove(self, peer_id):
        if messagebox.askyesno("Forget Device", f"Remove this device from known list?\n\n{peer_id}"):
            self._on_remove_peer(peer_id)
            self._refresh_devices()

    def _create_pending_row(self, peer_id: str, code: str, peer_name: str):
        row = ctk.CTkFrame(self._pending_frame, fg_color=("gray90", "gray20"),
                          corner_radius=8)
        row.pack(fill="x", pady=2)

        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=8)

        ctk.CTkLabel(
            inner, text=peer_name,
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w")

        ctk.CTkLabel(
            inner, text=f"Code: {code}",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=ACCENT,
        ).pack(anchor="w", pady=(2, 6))

        btn_row = ctk.CTkFrame(inner, fg_color="transparent")
        btn_row.pack(fill="x")

        ctk.CTkButton(
            btn_row, text="Confirm", width=80, height=28,
            fg_color=STATUS_COLOR,
            hover_color=("#27AE60", "#1E8449"),
            font=ctk.CTkFont(size=12),
            command=lambda pid=peer_id, c=code: self._on_confirm_pairing(pid, c),
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_row, text="Reject", width=60, height=28,
            fg_color="transparent", border_width=1,
            text_color=("#E74C3C", "#C0392B"),
            border_color=("#E74C3C", "#C0392B"),
            hover_color=("#FADBD8", "#5B2C2C"),
            font=ctk.CTkFont(size=11),
            command=lambda pid=peer_id: self._on_reject_pairing(pid),
        ).pack(side="left")

    def _on_confirm_pairing(self, peer_id: str, code: str):
        if not self._on_pair:
            return
        success = self._on_pair(peer_id, code)
        if success:
            if self._status_footer:
                self._status_footer.configure(text="Device paired successfully")
            messagebox.showinfo("Paired", "Device paired successfully!")
        else:
            messagebox.showerror(
                "Failed",
                "Pairing failed. The code may have expired.\n"
                "Try connecting again.",
            )
        self._refresh_devices()

    def _on_reject_pairing(self, peer_id: str):
        if self._on_unpair:
            self._on_unpair(peer_id)
        self._refresh_devices()

    # ═══════════════════════════════════════════════════════════════
    # Panel: History
    # ═══════════════════════════════════════════════════════════════

    def _build_history_panel(self):
        panel = ctk.CTkFrame(self._content_frame, fg_color="transparent")

        header_row = ctk.CTkFrame(panel, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            header_row, text="Clipboard History",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left")

        if self._clear_history:
            ctk.CTkButton(
                header_row, text="Clear All", width=80, height=28,
                fg_color="transparent", border_width=1,
                text_color=("#E74C3C", "#C0392B"),
                border_color=("#E74C3C", "#C0392B"),
                hover_color=("#FADBD8", "#5B2C2C"),
                font=ctk.CTkFont(size=11),
                command=self._on_clear_history,
            ).pack(side="right")

        # Search bar
        search_frame = ctk.CTkFrame(panel, corner_radius=8, fg_color=("gray95", "gray17"))
        search_frame.pack(fill="x", pady=(0, 8))

        self._history_search_var = tk.StringVar()
        self._history_search_timer: str | None = None
        search_entry = ctk.CTkEntry(
            search_frame, textvariable=self._history_search_var,
            height=32, placeholder_text="Search history...",
        )
        search_entry.pack(fill="x", padx=12, pady=8)
        search_entry.bind("<KeyRelease>", self._on_search_keyrelease)

        # History list
        card = ctk.CTkFrame(panel, corner_radius=12)
        card.pack(fill="both", expand=True)

        self._history_scroll = ctk.CTkScrollableFrame(card, fg_color="transparent")
        self._history_scroll.pack(fill="both", expand=True, padx=8, pady=8)

        return panel

    def _refresh_history_list(self):
        if self._history_scroll is None or self._get_history is None:
            return

        for child in self._history_scroll.winfo_children():
            child.destroy()

        self._last_history_count = 0  # reset, will be updated on next auto-refresh

        query = (self._history_search_var.get().strip()
                 if self._history_search_var else "")

        entries = (self._search_history(query) if query and self._search_history
                   else self._get_history())

        if not entries:
            ctk.CTkLabel(
                self._history_scroll,
                text="No clipboard history yet.\nCopied items will appear here.",
                font=ctk.CTkFont(size=12),
                text_color=("gray50", "gray60"),
                justify="center",
            ).pack(fill="x", expand=True, pady=40)
            return

        for i, entry in enumerate(entries):
            self._create_history_card(i, entry)

    def _create_history_card(self, index: int, entry: dict):
        timestamp = entry.get("timestamp", 0)
        content_type = entry.get("content_type", "?")
        preview = entry.get("text_preview", "")[:120]

        time_str = datetime.datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")

        card = ctk.CTkFrame(self._history_scroll, corner_radius=8,
                           fg_color=("gray95", "gray17"))
        card.pack(fill="x", pady=2, padx=2)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=8)

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")

        type_labels = {"TEXT": "Text", "HTML": "HTML", "IMAGE": "Image", "RTF": "Rich Text"}
        ctk.CTkLabel(
            top, text=type_labels.get(content_type, content_type),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("#2A82C7", "#5DADE2"),
        ).pack(side="left")

        ctk.CTkLabel(
            top, text=time_str,
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
        ).pack(side="right")

        if preview:
            ctk.CTkLabel(
                inner, text=preview,
                font=ctk.CTkFont(size=12),
                text_color=("gray40", "gray60"),
                anchor="w", justify="left",
            ).pack(fill="x", pady=(4, 6))

        copy_btn = ctk.CTkButton(
            inner, text="Copy to Clipboard", width=130, height=26,
            font=ctk.CTkFont(size=11),
            command=lambda i=index: self._do_copy_history(i),
        )
        copy_btn.pack(anchor="w")

    def _do_copy_history(self, index: int):
        if self._copy_from_history:
            success = self._copy_from_history(index)
            if self._status_footer:
                self._status_footer.configure(
                    text="Copied to clipboard" if success else "Failed to copy"
                )

    def _on_search_keyrelease(self, event):
        if self._history_search_timer is not None:
            self._root.after_cancel(self._history_search_timer)
        self._history_search_timer = self._root.after(300, self._refresh_history_list)

    def _on_clear_history(self):
        if self._clear_history is None:
            return
        if messagebox.askyesno("Clear History", "Delete all clipboard history?"):
            self._clear_history()
            self._refresh_history_list()

    # ═══════════════════════════════════════════════════════════════
    # Panel: Transfers
    # ═══════════════════════════════════════════════════════════════

    def _build_transfers_panel(self):
        panel = ctk.CTkFrame(self._content_frame, fg_color="transparent")

        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            header, text="File Transfers",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left")

        if self._on_send_file:
            ctk.CTkButton(
                header, text="\U0001F4E4  Send File",
                width=120, height=32,
                command=self._on_send_file,
            ).pack(side="right")

        card = ctk.CTkFrame(panel, corner_radius=12)
        card.pack(fill="both", expand=True)

        ctk.CTkLabel(
            card, text="Active Transfers",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(14, 8))

        self._transfer_scroll = ctk.CTkScrollableFrame(card, fg_color="transparent")
        self._transfer_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        return panel

    def _refresh_transfers(self):
        if self._transfer_scroll is None:
            return
        for child in self._transfer_scroll.winfo_children():
            child.destroy()

        transfers = self._get_transfers() if self._get_transfers else []
        if not transfers:
            ctk.CTkLabel(
                self._transfer_scroll,
                text="No active transfers.\nSend a file to get started.",
                font=ctk.CTkFont(size=12),
                text_color=("gray50", "gray60"),
                justify="center",
            ).pack(fill="x", expand=True, pady=30)
            return

        for t in transfers:
            self._create_transfer_card(t)

    def _create_transfer_card(self, transfer: dict):
        direction = transfer.get("direction", "down")
        file_name = transfer.get("file_name", "?")
        file_size = transfer.get("file_size", 0)
        progress = transfer.get("progress", 0.0)
        state = transfer.get("state", "unknown")

        arrow = "\U0001F4E4" if direction == "up" else "\U0001F4E5"
        size_str = self._format_size(file_size)

        card = ctk.CTkFrame(self._transfer_scroll, corner_radius=8,
                           fg_color=("gray95", "gray17"))
        card.pack(fill="x", pady=2, padx=2)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(
            inner, text=f"{arrow}  {file_name}",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w")

        info = ctk.CTkFrame(inner, fg_color="transparent")
        info.pack(fill="x", pady=(4, 0))

        state_labels = {
            "awaiting_ack": "Waiting for peer...",
            "pending": "Waiting for acceptance...",
            "receiving": f"Receiving...  {int(progress * 100)}%",
            "sending": "Sending...",
            "cancelled": "Cancelled",
        }
        status_text = state_labels.get(state, state.replace("_", " ").title())

        ctk.CTkLabel(
            info, text=f"{status_text}  |  {size_str}",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
        ).pack(side="left")

        if state in ("receiving", "sending") and progress > 0:
            bar = ctk.CTkProgressBar(inner, height=6)
            bar.pack(fill="x", pady=(6, 0))
            bar.set(progress)

        # Cancel button for active transfers
        if self._on_cancel_transfer and state not in ("complete", "failed", "cancelled", "error"):
            cancel_btn = ctk.CTkButton(
                inner, text="✕  Cancel", width=70, height=22,
                fg_color="transparent", border_width=1,
                text_color=("#E74C3C", "#C0392B"),
                border_color=("#E74C3C", "#C0392B"),
                hover_color=("#FADBD8", "#5B2C2C"),
                font=ctk.CTkFont(size=10),
                command=lambda tid=transfer.get("transfer_id", ""): self._on_cancel_transfer(tid),
            )
            cancel_btn.pack(anchor="e", pady=(4, 0))

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.1f} GB"

    # ═══════════════════════════════════════════════════════════════
    # Toggle handlers
    # ═══════════════════════════════════════════════════════════════

    def _on_toggle_sync(self):
        enabled = self._sync_var.get()
        self._set_sync(enabled)
        cfg = self._get_config()
        cfg.sync_enabled = enabled
        self._save_config()
        self._refresh_overview()

    def _on_toggle_autostart(self):
        cfg = self._get_config()
        enabled = self._autostart_var.get()
        cfg.auto_start = enabled
        self._save_config()
        if self._on_toggle_autostart_cb:
            try:
                self._on_toggle_autostart_cb(enabled)
            except Exception:
                logger.debug("Auto-start toggle failed", exc_info=True)

    def _edit_device_name(self):
        from tkinter import simpledialog

        cfg = self._get_config()
        new_name = simpledialog.askstring(
            "Device Name",
            "Enter a name for this device:",
            initialvalue=cfg.device_name,
            parent=self._window,
        )
        if new_name and new_name.strip():
            cfg.device_name = new_name.strip()
            self._save_config()
            if self._device_name_label:
                self._device_name_label.configure(text=cfg.device_name)
            if self._overview_device_name:
                self._overview_device_name.configure(text=cfg.device_name)
            if self._status_footer:
                self._status_footer.configure(text="Device name updated (restart for network discovery)")

    def _toggle_theme(self):
        self._dark_mode = not self._dark_mode
        ctk.set_appearance_mode("dark" if self._dark_mode else "light")
        self._theme_btn.configure(
            text="☀  Light" if self._dark_mode else "☾  Dark"
        )
