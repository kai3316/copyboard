"""Main dashboard window for CopyBoard — sidebar navigation with rich panels.

Panels: Overview, Devices (with pairing), History, Transfers.
Settings is a separate window accessed via the sidebar button.
"""

import datetime
import logging
import platform
import socket
import time
import tkinter as tk
from internal.ui.dialogs import ask_yesno, show_error, show_info
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
        delete_history_item: Callable | None = None,
        # Lifecycle
        on_hidden: Callable | None = None,
        # Transfers
        get_transfer_history: Callable | None = None,
        on_speed_test: Callable | None = None,
        get_speed_test_result: Callable | None = None,
        clear_transfer_history: Callable | None = None,
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
        self._delete_history_item = delete_history_item
        self._on_hidden = on_hidden
        self._get_transfer_history = get_transfer_history
        self._on_speed_test = on_speed_test
        self._get_speed_test_result = get_speed_test_result
        self._clear_transfer_history = clear_transfer_history

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
        self._stat_paired: ctk.CTkLabel | None = None
        self._stat_history: ctk.CTkLabel | None = None
        self._stat_transfers: ctk.CTkLabel | None = None
        self._sub_peers: ctk.CTkLabel | None = None
        self._sub_paired: ctk.CTkLabel | None = None
        self._sub_history: ctk.CTkLabel | None = None
        self._sub_transfers: ctk.CTkLabel | None = None
        self._recent_activity: ctk.CTkLabel | None = None
        self._theme_var: tk.BooleanVar | None = None
        self._uptime_label: ctk.CTkLabel | None = None
        self._local_ip_label: ctk.CTkLabel | None = None
        self._start_time: float = 0.0
        self._device_name_label: ctk.CTkLabel | None = None
        self._device_scroll: ctk.CTkScrollableFrame | None = None
        self._transfer_scroll: ctk.CTkScrollableFrame | None = None
        self._transfer_history_scroll: ctk.CTkScrollableFrame | None = None
        self._speed_test_label: ctk.CTkLabel | None = None
        self._history_scroll: ctk.CTkScrollableFrame | None = None
        self._pending_frame: ctk.CTkFrame | None = None
        self._footer_label: ctk.CTkLabel | None = None
        self._status_footer: ctk.CTkLabel | None = None
        self._clear_history_btn: ctk.CTkButton | None = None
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
        self._window.geometry("900x640")
        self._window.minsize(780, 560)
        self._window.protocol("WM_DELETE_WINDOW", self._on_hide)

        self._window.update_idletasks()
        sw = self._window.winfo_screenwidth()
        sh = self._window.winfo_screenheight()
        w, h = 900, 640
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
                font=ctk.CTkFont(size=15),
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
                font=ctk.CTkFont(size=14),
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

    @staticmethod
    def _detect_local_ip() -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0)
            s.connect(("10.254.254.254", 1))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    @staticmethod
    def _detect_network_info() -> dict:
        """Detect current network: WiFi (SSID + signal) or Ethernet. Returns
        {'type': 'wifi'|'ethernet'|'unknown', 'label': str, 'detail': str}."""
        import subprocess
        import re

        # Try WiFi detection via netsh (Windows)
        try:
            out = subprocess.check_output(
                ["netsh", "wlan", "show", "interfaces"],
                text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW
            )
            ssid_match = re.search(r"^\s*SSID\s*:\s*(.+)$", out, re.MULTILINE | re.IGNORECASE)
            signal_match = re.search(r"^\s*Signal\s*:\s*(\d+)%", out, re.MULTILINE | re.IGNORECASE)
            state_match = re.search(r"^\s*State\s*:\s*connected", out, re.MULTILINE | re.IGNORECASE)
            if ssid_match and state_match:
                ssid = ssid_match.group(1).strip()
                signal = int(signal_match.group(1)) if signal_match else 0
                bars = "▂▄▆█" if signal >= 75 else "▂▄▆ " if signal >= 50 else "▂▄  " if signal >= 25 else "▂   "
                return {
                    "type": "wifi",
                    "label": f"Wi-Fi · {ssid}",
                    "detail": f"Signal {signal}%  {bars}",
                }
        except Exception:
            pass

        # Try Ethernet detection (Windows)
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 "Get-NetAdapter -Physical | Where-Object Status -eq 'Up' | "
                 "Where-Object MediaType -eq '802.3' | Select-Object -First 1 "
                 "-ExpandProperty LinkSpeed"],
                text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW
            )
            out = out.strip()
            if out:
                # Convert to human-readable: e.g. "1000000000" → "1 Gbps"
                speed_bps = int(out)
                if speed_bps >= 1_000_000_000:
                    speed_str = f"{speed_bps / 1_000_000_000:.0f} Gbps"
                elif speed_bps >= 1_000_000:
                    speed_str = f"{speed_bps / 1_000_000:.0f} Mbps"
                else:
                    speed_str = f"{speed_bps / 1_000:.0f} Kbps"
                return {
                    "type": "ethernet",
                    "label": "Ethernet",
                    "detail": f"Link speed {speed_str}",
                }
        except Exception:
            pass

        return {"type": "unknown", "label": "LAN", "detail": ""}

    def _build_overview_panel(self):
        panel = ctk.CTkFrame(self._content_frame, fg_color="transparent")
        cfg = self._get_config()
        net = self._detect_network_info()
        local_ip = self._detect_local_ip()

        ctk.CTkLabel(
            panel, text="System Overview",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", pady=(0, 8))

        # ── Top row: 3 equal-height columns ────────────────────────────
        top = ctk.CTkFrame(panel, fg_color="transparent")
        top.pack(fill="x")
        top.rowconfigure(0, weight=1)
        for c in range(3):
            top.columnconfigure(c, weight=1, uniform="top3")

        # ── Col 0: Connection ──────────────────────────────────────────
        card_s = ctk.CTkFrame(top, corner_radius=12)
        card_s.grid(row=0, column=0, sticky="nsew", padx=(0, 5), pady=(0, 8))
        ctk.CTkLabel(card_s, text="Connection",
                    font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(12, 6))
        s_center = ctk.CTkFrame(card_s, fg_color="transparent")
        s_center.pack(fill="x", padx=16)

        # Sync status
        sr = ctk.CTkFrame(s_center, fg_color="transparent")
        sr.pack(fill="x")
        self._status_dot = ctk.CTkFrame(sr, width=14, height=14,
                                        corner_radius=7, fg_color=STATUS_COLOR)
        self._status_dot.pack(side="left", padx=(0, 8))
        self._status_label = ctk.CTkLabel(
            sr, text="Sync Active", font=ctk.CTkFont(size=15, weight="bold"),
            text_color=("gray30", "gray80"),
        )
        self._status_label.pack(side="left")

        # Network info
        self._net_label = ctk.CTkLabel(
            s_center, text=net["label"],
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=("gray50", "gray60"),
        )
        self._net_label.pack(anchor="w", pady=(10, 0))
        if net["detail"]:
            self._net_detail_label = ctk.CTkLabel(
                s_center, text=net["detail"],
                font=ctk.CTkFont(size=11),
                text_color=("gray50", "gray60"),
            )
            self._net_detail_label.pack(anchor="w", pady=(2, 0))

        # Local address
        self._local_ip_label = ctk.CTkLabel(
            s_center, text=f"Local address  {local_ip}:{cfg.port}",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
        )
        self._local_ip_label.pack(anchor="w", pady=(8, 0))

        # Protocol info
        proto_row = ctk.CTkFrame(s_center, fg_color="transparent")
        proto_row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(
            proto_row, text="Encrypted  TLS 1.3",
            font=ctk.CTkFont(size=11),
            text_color=("#27AE60", "#2ECC71"),
        ).pack(side="left")
        ctk.CTkLabel(
            proto_row, text="Discovery  mDNS",
            font=ctk.CTkFont(size=11),
            text_color=("#27AE60", "#2ECC71"),
        ).pack(side="left", padx=(8, 0))
        self._start_time = time.time()
        self._uptime_label = ctk.CTkLabel(
            proto_row, text="",
            font=ctk.CTkFont(size=11),
            text_color=("gray55", "gray55"),
        )
        self._uptime_label.pack(side="right")

        # ── Col 1: This Device ─────────────────────────────────────────
        card_d = ctk.CTkFrame(top, corner_radius=12)
        card_d.grid(row=0, column=1, sticky="nsew", padx=5, pady=(0, 8))
        ctk.CTkLabel(card_d, text="This Device",
                    font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(12, 6))
        d_grid = ctk.CTkFrame(card_d, fg_color="transparent")
        d_grid.pack(fill="x", padx=16, pady=(0, 10))

        # Name row (editable)
        nr = ctk.CTkFrame(d_grid, fg_color="transparent")
        nr.pack(fill="x", pady=2)
        ctk.CTkLabel(nr, text="Name:", width=72, anchor="w",
                    font=ctk.CTkFont(size=11)).pack(side="left")
        self._overview_device_name = ctk.CTkLabel(
            nr, text=cfg.device_name, font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
        )
        self._overview_device_name.pack(side="left")
        ctk.CTkButton(
            nr, text="✎", width=24, height=22,
            fg_color="transparent", border_width=1,
            text_color=("gray50", "gray60"),
            border_color=("gray65", "gray50"),
            hover_color=("gray85", "gray25"),
            font=ctk.CTkFont(size=10),
            command=self._edit_device_name,
        ).pack(side="left", padx=(4, 0))

        for label, value in [
            ("Device ID", cfg.device_id),
            ("Platform", platform.system() + " " + platform.machine()),
            ("Service", cfg.service_type.replace("_copyboard._tcp.local.", "copyboard")),
        ]:
            row = ctk.CTkFrame(d_grid, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=label + ":", width=72, anchor="w",
                        font=ctk.CTkFont(size=11)).pack(side="left")
            ctk.CTkLabel(row, text=value, font=ctk.CTkFont(size=11),
                        text_color=("gray50", "gray60")).pack(side="left")

        # ── Col 2: Quick Controls ──────────────────────────────────────
        card_c = ctk.CTkFrame(top, corner_radius=12)
        card_c.grid(row=0, column=2, sticky="nsew", padx=(5, 0), pady=(0, 8))
        c_center = ctk.CTkFrame(card_c, fg_color="transparent")
        c_center.pack(fill="x", padx=16)

        ctk.CTkLabel(c_center, text="Settings",
                    font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", pady=(0, 8))

        self._sync_var = tk.BooleanVar(value=self._get_sync())
        ctk.CTkSwitch(
            c_center, text="Clipboard sync", variable=self._sync_var,
            command=self._on_toggle_sync,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", pady=(0, 6))

        self._autostart_var = tk.BooleanVar(value=cfg.auto_start)
        ctk.CTkSwitch(
            c_center, text="Start at login", variable=self._autostart_var,
            command=self._on_toggle_autostart,
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", pady=(0, 6))

        self._theme_var = tk.BooleanVar(value=self._dark_mode)

        ctk.CTkButton(
            c_center, text="Manage devices →", width=120, height=30,
            fg_color="transparent", border_width=1,
            text_color=ACCENT, border_color=ACCENT,
            hover_color=("#D6EAF8", "#1A3A4A"),
            font=ctk.CTkFont(size=11),
            command=lambda: self._switch_panel("devices"),
        ).pack(anchor="w")

        # ── Bottom: Activity ───────────────────────────────────────────
        card_a = ctk.CTkFrame(panel, corner_radius=12)
        card_a.pack(fill="both", expand=True)
        top_bar = ctk.CTkFrame(card_a, fg_color="transparent")
        top_bar.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(top_bar, text="Activity",
                    font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")
        self._recent_activity = ctk.CTkLabel(
            top_bar, text="",
            font=ctk.CTkFont(size=11),
            text_color=("gray55", "gray55"),
        )
        self._recent_activity.pack(side="right")

        stat_row = ctk.CTkFrame(card_a, fg_color="transparent")
        stat_row.pack(fill="both", expand=True, padx=11, pady=(0, 12))
        for c in range(4):
            stat_row.columnconfigure(c, weight=1, uniform="stat")

        accent_colors = ["#2ECC71", "#F39C12", "#3498DB", "#9B59B6"]
        stats_def = [
            ("\U0001F7E2  Connected", "--", "online", "_stat_peers", "_sub_peers"),
            ("\U0001F4F1  Paired", "--", "trusted devices", "_stat_paired", "_sub_paired"),
            ("\U0001F4CB  History", "--", "items saved", "_stat_history", "_sub_history"),
            ("\U0001F4E4  Transfers", "--", "active", "_stat_transfers", "_sub_transfers"),
        ]
        for i, (title, default, subtitle, ref_name, sub_ref) in enumerate(stats_def):
            box = ctk.CTkFrame(stat_row, corner_radius=8,
                             fg_color=("gray95", "gray17"))
            box.grid(row=0, column=i, sticky="nsew", padx=3)
            accent_bar = ctk.CTkFrame(box, width=3, fg_color=accent_colors[i])
            accent_bar.pack(side="left", fill="y")
            content = ctk.CTkFrame(box, fg_color="transparent")
            content.pack(side="left", fill="both", expand=True, padx=(3, 0))
            ctk.CTkLabel(content, text=title,
                        font=ctk.CTkFont(size=11),
                        text_color=("gray50", "gray60"),
            ).pack(anchor="center", padx=6, pady=(14, 2))
            val = ctk.CTkLabel(content, text=default,
                              font=ctk.CTkFont(size=26, weight="bold"),
                              text_color=("gray20", "gray85"))
            val.pack(anchor="center", padx=6)
            sub = ctk.CTkLabel(content, text=subtitle,
                              font=ctk.CTkFont(size=11),
                              text_color=("gray55", "gray55"))
            sub.pack(anchor="center", padx=6, pady=(1, 14))

            if ref_name == "_stat_peers":
                self._stat_peers = val
                self._sub_peers = sub
            elif ref_name == "_stat_paired":
                self._stat_paired = val
                self._sub_paired = sub
            elif ref_name == "_stat_history":
                self._stat_history = val
                self._sub_history = sub
            elif ref_name == "_stat_transfers":
                self._stat_transfers = val
                self._sub_transfers = sub

        return panel

    def _refresh_overview(self):
        if self._status_dot is None:
            return

        syncing = self._get_sync()
        self._anim_frame = (self._anim_frame + 4) % 20
        if syncing:
            size = 15 + (1 if self._anim_frame < 10 else 0)
            self._status_dot.configure(width=size, height=size,
                                       corner_radius=size // 2,
                                       fg_color=STATUS_COLOR)
            self._status_label.configure(text="Sync Active")
        else:
            self._status_dot.configure(width=14, height=14, corner_radius=7,
                                       fg_color=OFFLINE_COLOR)
            self._status_label.configure(text="Sync Paused")

        # Uptime
        if self._uptime_label and self._start_time:
            uptime = int(time.time() - self._start_time)
            if uptime < 120:
                self._uptime_label.configure(text=f"up {uptime}s")
            elif uptime < 7200:
                self._uptime_label.configure(text=f"up {uptime // 60}m")
            else:
                self._uptime_label.configure(text=f"up {uptime // 3600}h {uptime % 3600 // 60:02d}m")

        # Peer stats
        try:
            peers = self._get_peers()
            connected = sum(1 for _, _, _, c in peers if c)
            paired = sum(1 for _, _, p, _ in peers if p)
            known = sum(1 for _, _, p, _ in peers if p)  # paired == known

            if self._stat_peers:
                self._stat_peers.configure(text=str(connected))
            if self._sub_peers:
                self._sub_peers.configure(text=f"of {len(peers)} visible")
            if self._stat_paired:
                self._stat_paired.configure(text=str(paired))
            if self._sub_paired:
                self._sub_paired.configure(text="trusted devices")
        except Exception:
            if self._stat_peers:
                self._stat_peers.configure(text="--")
            if self._sub_peers:
                self._sub_peers.configure(text="")
            if self._stat_paired:
                self._stat_paired.configure(text="--")
            if self._sub_paired:
                self._sub_paired.configure(text="")

        if self._stat_history:
            try:
                history = self._get_history() if self._get_history else []
                count = len(history)
                self._stat_history.configure(text=str(count))
                if self._sub_history:
                    self._sub_history.configure(text="items saved")
            except Exception:
                self._stat_history.configure(text="--")
                if self._sub_history:
                    self._sub_history.configure(text="")

        if self._stat_transfers:
            try:
                transfers = self._get_transfers() if self._get_transfers else []
                count = len(transfers)
                self._stat_transfers.configure(text=str(count) if count > 0 else "--")
                if self._sub_transfers:
                    if count > 0:
                        self._sub_transfers.configure(text="active")
                    else:
                        self._sub_transfers.configure(text="none active")
            except Exception:
                self._stat_transfers.configure(text="--")
                if self._sub_transfers:
                    self._sub_transfers.configure(text="")

        # Recent activity summary
        if self._recent_activity:
            try:
                parts = []
                history = self._get_history() if self._get_history else []
                if history:
                    parts.append(f"\U0001F4CB {len(history)} clips")
                transfers = self._get_transfers() if self._get_transfers else []
                if transfers:
                    parts.append(f"\U0001F4E4 {len(transfers)} transfers")
                if parts:
                    self._recent_activity.configure(text="  ·  ".join(parts))
                else:
                    self._recent_activity.configure(text="No recent activity")
            except Exception:
                self._recent_activity.configure(text="")

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
                for peer_id, code, peer_name in pending:
                    self._create_pending_row(peer_id, code, peer_name)
            else:
                empty = ctk.CTkLabel(
                    self._pending_frame, text="No pending pairing requests.",
                    font=ctk.CTkFont(size=11),
                    text_color=("gray40", "gray60"),
                )
                empty.pack(anchor="w")

    def _add_section_header(self, text: str, count: int):
        header = ctk.CTkFrame(self._device_scroll, fg_color="transparent")
        header.pack(fill="x", pady=(8, 2), padx=0)
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
            color, status = STATUS_COLOR, "Connected"
        elif paired:
            color, status = WARN_COLOR, "Paired (offline)"
        else:
            color, status = ACCENT, "Discovered"

        display_name = dev_name or dev_id[:12]
        display_id = dev_id[:12] if dev_name else dev_id[:16]

        row = ctk.CTkFrame(self._device_scroll, fg_color=("gray95", "gray17"),
                          corner_radius=8)
        row.pack(fill="x", pady=2, padx=2)

        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=10)

        # ── Row 1: dot + name + device ID ──────────────────────
        r1 = ctk.CTkFrame(inner, fg_color="transparent")
        r1.pack(fill="x")

        dot = ctk.CTkFrame(r1, width=12, height=12, corner_radius=6,
                          fg_color=color)
        dot.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(
            r1, text=display_name,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")

        ctk.CTkLabel(
            r1, text=display_id,
            font=ctk.CTkFont(size=10),
            text_color=("gray60", "gray50"),
        ).pack(side="right")

        # ── Row 2: status text ─────────────────────────────────
        detail = status
        if connected:
            detail += "  \U0001F512  encrypted"
        elif paired:
            detail += " — reconnect to sync"
        else:
            detail += " — connect to sync"

        ctk.CTkLabel(
            inner, text=detail,
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
        ).pack(anchor="w", pady=(2, 0))

        # ── Row 3: action buttons ──────────────────────────────
        btns = ctk.CTkFrame(inner, fg_color="transparent")
        btns.pack(fill="x", pady=(6, 0))

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

    def _do_reconnect(self, peer_id):
        logger.info("User initiated reconnect to %s", peer_id)
        if self._on_connect_peer:
            self._on_connect_peer(peer_id)

    def _do_unpair(self, peer_id):
        # Look up device name for the confirmation dialog
        device_name = peer_id[:12]
        try:
            for dev_id, dev_name, _, _ in self._get_peers():
                if dev_id == peer_id:
                    if dev_name:
                        device_name = dev_name
                    break
        except Exception:
            pass
        if ask_yesno(self._window, "Unpair", f"Unpair this device?\n\n{device_name}"):
            self._on_unpair(peer_id)
            self._refresh_devices()

    def _do_remove(self, peer_id):
        # Look up device name for the confirmation dialog
        device_name = peer_id[:12]
        try:
            for dev_id, dev_name, _, _ in self._get_peers():
                if dev_id == peer_id:
                    if dev_name:
                        device_name = dev_name
                    break
        except Exception:
            pass
        if ask_yesno(self._window, "Forget Device", f"Remove this device from known list?\n\n{device_name}"):
            self._on_remove_peer(peer_id)
            self._refresh_devices()

    def _do_speed_test(self):
        if not self._on_speed_test:
            return
        if self._speed_test_label:
            self._speed_test_label.configure(
                text="Speed test running... sending test data.",
                text_color=WARN_COLOR,
            )
        self._on_speed_test()

    def _create_pending_row(self, peer_id: str, code: str, peer_name: str):
        row = ctk.CTkFrame(self._pending_frame, fg_color=("gray90", "gray20"),
                          corner_radius=8)
        row.pack(fill="x", pady=2)

        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=8)

        ctk.CTkLabel(
            inner, text=peer_name,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w")

        code_frame = ctk.CTkFrame(inner, fg_color=("#D6EAF8", "#1A3A4A"),
                                  corner_radius=6)
        code_frame.pack(anchor="w", pady=(2, 6))
        ctk.CTkLabel(
            code_frame, text=f"Code: {code}",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=ACCENT,
        ).pack(padx=12, pady=6)

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
            font=ctk.CTkFont(size=12),
            command=lambda pid=peer_id: self._on_reject_pairing(pid),
        ).pack(side="left")

    def _on_confirm_pairing(self, peer_id: str, code: str):
        if not self._on_pair:
            return
        success = self._on_pair(peer_id, code)
        if success:
            if self._status_footer:
                self._status_footer.configure(text="Device paired successfully")
            show_info(self._window, "Paired", "Device paired successfully!")
        else:
            show_error(
                self._window,
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
        header_row.pack(fill="x", pady=(4, 12))

        ctk.CTkLabel(
            header_row, text="Clipboard History",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left")

        if self._clear_history:
            self._clear_history_btn = ctk.CTkButton(
                header_row, text="Clear All", width=80, height=28,
                fg_color="transparent", border_width=1,
                text_color=("#E74C3C", "#C0392B"),
                border_color=("#E74C3C", "#C0392B"),
                hover_color=("#FADBD8", "#5B2C2C"),
                font=ctk.CTkFont(size=11),
                command=self._on_clear_history,
            )
            self._clear_history_btn.pack(side="right")

        # Search bar
        search_frame = ctk.CTkFrame(panel, corner_radius=8, fg_color=("gray95", "gray17"))
        search_frame.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(
            search_frame, text="🔍",
            font=ctk.CTkFont(size=14),
        ).pack(side="left", padx=(12, 0), pady=4)

        self._history_search_var = tk.StringVar()
        self._history_search_timer: str | None = None
        search_entry = ctk.CTkEntry(
            search_frame, textvariable=self._history_search_var,
            height=32, placeholder_text="Search history...",
            fg_color="transparent",
        )
        search_entry.pack(side="left", fill="x", expand=True, padx=(4, 4), pady=8)
        search_entry.bind("<KeyRelease>", self._on_search_keyrelease)

        ctk.CTkButton(
            search_frame, text="✕", width=24, height=24,
            fg_color="transparent",
            text_color=("gray50", "gray60"),
            hover_color=("gray85", "gray25"),
            font=ctk.CTkFont(size=12),
            command=self._on_clear_search,
        ).pack(side="right", padx=(0, 8), pady=4)

        # History list
        card = ctk.CTkFrame(panel, corner_radius=12)
        card.pack(fill="both", expand=True)

        self._history_scroll = ctk.CTkScrollableFrame(card, fg_color="transparent")
        self._history_scroll.pack(fill="both", expand=True, padx=8, pady=8)

        return panel

    def _refresh_history_list(self):
        if self._history_scroll is None or self._get_history is None:
            return

        query = (self._history_search_var.get().strip()
                 if self._history_search_var else "")

        entries = (self._search_history(query) if query and self._search_history
                   else self._get_history())

        for child in self._history_scroll.winfo_children():
            child.destroy()

        # Toggle Clear All button state
        if self._clear_history_btn is not None:
            if len(entries) == 0:
                self._clear_history_btn.configure(state="disabled")
            else:
                self._clear_history_btn.configure(state="normal")

        if not entries:
            if query:
                empty_text = f"No results for '{query}'"
            else:
                empty_text = "No clipboard history yet.\nCopied items will appear here."
            ctk.CTkLabel(
                self._history_scroll,
                text=empty_text,
                font=ctk.CTkFont(size=12),
                text_color=("gray50", "gray60"),
                justify="center",
            ).pack(fill="x", expand=True, pady=40)
        else:
            for i, entry in enumerate(entries):
                self._create_history_card(i, entry)

    @staticmethod
    def _sanitize_preview(raw: str, max_len: int = 120) -> str:
        """Strip control and replacement characters for clean display."""
        import unicodedata
        if not raw:
            return ""
        cleaned = "".join(
            ch if (unicodedata.category(ch)[0] != "C"
                   and ch != "�") or ch in ("\t", "\n", "\r")
            else " "
            for ch in raw
        )
        cleaned = " ".join(cleaned.split())
        return cleaned[:max_len]

    def _create_history_card(self, index: int, entry: dict):
        timestamp = entry.get("timestamp", 0)
        content_type = entry.get("content_type", "Unknown")
        preview = self._sanitize_preview(entry.get("text_preview", ""))

        if timestamp and timestamp > 0:
            dt = datetime.datetime.fromtimestamp(timestamp)
            now = datetime.datetime.now()
            if dt.date() == now.date():
                time_str = dt.strftime("%H:%M")
            elif (now.date() - dt.date()).days == 1:
                time_str = f"Yesterday {dt.strftime('%H:%M')}"
            else:
                time_str = dt.strftime("%m-%d %H:%M")
        else:
            time_str = ""

        card = ctk.CTkFrame(self._history_scroll, corner_radius=8,
                           fg_color=("gray95", "gray17"))
        card.pack(fill="x", pady=3, padx=2)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=8)

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")

        type_colors = {
            "TEXT": ("#27AE60", "#2ECC71"),
            "HTML": ("#E67E22", "#F39C12"),
            "IMAGE": ("#8E44AD", "#9B59B6"),
            "RTF": ("#7F8C8D", "#95A5A6"),
        }
        type_labels = {"TEXT": "Text", "HTML": "HTML", "IMAGE": "Image", "RTF": "Rich Text"}
        ctk.CTkLabel(
            top, text=type_labels.get(content_type, content_type),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=type_colors.get(content_type, ("#2A82C7", "#5DADE2")),
        ).pack(side="left")

        if time_str:
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

        btn_row = ctk.CTkFrame(inner, fg_color="transparent")
        btn_row.pack(fill="x")
        ctk.CTkButton(
            btn_row, text="Copy", width=60, height=26,
            fg_color=("#27AE60", "#2ECC71"),
            font=ctk.CTkFont(size=11),
            command=lambda i=index: self._do_copy_history(i),
        ).pack(side="left")
        ctk.CTkButton(
            btn_row, text="Delete", width=60, height=26,
            fg_color="transparent", border_width=1,
            text_color=("#E74C3C", "#C0392B"),
            border_color=("#E74C3C", "#C0392B"),
            hover_color=("#FADBD8", "#5B2C2C"),
            font=ctk.CTkFont(size=11),
            command=lambda i=index: self._on_delete_history_item(i),
        ).pack(side="left", padx=(6, 0))

    def _do_copy_history(self, index: int):
        if self._copy_from_history:
            success = self._copy_from_history(index)
            if self._status_footer:
                if success:
                    self._status_footer.configure(
                        text="Copied to clipboard", text_color=("#27AE60", "#2ECC71")
                    )
                else:
                    self._status_footer.configure(
                        text="Failed to copy", text_color=("#E74C3C", "#C0392B")
                    )

    def _on_search_keyrelease(self, event):
        if self._history_search_timer is not None:
            self._root.after_cancel(self._history_search_timer)
        self._history_search_timer = self._root.after(300, self._refresh_history_list)

    def _on_clear_search(self):
        if self._history_search_var:
            self._history_search_var.set("")
        self._refresh_history_list()

    def _on_clear_history(self):
        if self._clear_history is None:
            return
        if ask_yesno(self._window, "Clear History", "Delete all clipboard history?"):
            self._clear_history()
            self._refresh_history_list()

    def _on_delete_history_item(self, index: int):
        if self._delete_history_item is None:
            return
        if ask_yesno(self._window, "Delete Item", "Delete this clipboard entry?"):
            self._delete_history_item(index)
            # Defer rebuild so the button animation completes first
            self._root.after(50, self._refresh_history_list)

    def _on_clear_transfer_history(self):
        if self._clear_transfer_history is None:
            return
        if ask_yesno(self._window, "Clear Transfer History", "Delete all transfer history?"):
            self._clear_transfer_history()
            self._root.after(50, self._refresh_transfers)

    # ═══════════════════════════════════════════════════════════════
    # Panel: Transfers
    # ═══════════════════════════════════════════════════════════════

    def _build_transfers_panel(self):
        panel = ctk.CTkFrame(self._content_frame, fg_color="transparent")

        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            header, text="File Transfers",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left")

        btn_row = ctk.CTkFrame(header, fg_color="transparent")
        btn_row.pack(side="right")

        if self._on_send_file:
            ctk.CTkButton(
                btn_row, text="Send File", width=90, height=30,
                command=self._on_send_file,
            ).pack(side="left", padx=(0, 6))

        # Speed Test button
        ctk.CTkButton(
            btn_row, text="Speed Test", width=90, height=30,
            fg_color="transparent", border_width=1,
            text_color=ACCENT, border_color=ACCENT,
            hover_color=("#D6EAF8", "#1A3A4A"),
            font=ctk.CTkFont(size=12),
            command=self._do_speed_test,
        ).pack(side="left")

        # ── Active + History stacked vertically ──────────────────────
        card = ctk.CTkFrame(panel, corner_radius=12)
        card.pack(fill="both", expand=True)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(0, weight=1)  # active section expands

        # Active (top)
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="nsew", padx=12, pady=(8, 4))
        ctk.CTkLabel(top, text="Active",
                    font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(anchor="w")
        self._transfer_scroll = ctk.CTkScrollableFrame(top, fg_color="transparent")
        self._transfer_scroll.pack(fill="x", expand=True, pady=(2, 0))

        # Separator (horizontal)
        hsep = ctk.CTkFrame(card, height=1, fg_color=("gray80", "gray30"))
        hsep.grid(row=1, column=0, sticky="ew", padx=12)

        # History (bottom)
        bottom = ctk.CTkFrame(card, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="nsew", padx=12, pady=(4, 8))
        history_header = ctk.CTkFrame(bottom, fg_color="transparent")
        history_header.pack(fill="x")
        ctk.CTkLabel(history_header, text="History",
                    font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left")
        if self._clear_transfer_history:
            ctk.CTkButton(
                history_header, text="Clear", width=50, height=22,
                fg_color="transparent", border_width=1,
                text_color=("#E74C3C", "#C0392B"),
                border_color=("#E74C3C", "#C0392B"),
                hover_color=("#FADBD8", "#5B2C2C"),
                font=ctk.CTkFont(size=10),
                command=self._on_clear_transfer_history,
            ).pack(side="right")
        self._transfer_history_stats = ctk.CTkLabel(
            bottom, text="",
            font=ctk.CTkFont(size=10),
            text_color=("gray55", "gray55"),
        )
        self._transfer_history_stats.pack(anchor="w", pady=(1, 0))
        self._transfer_history_scroll = ctk.CTkScrollableFrame(bottom, height=80, fg_color="transparent")
        self._transfer_history_scroll.pack(fill="x", expand=False, pady=(2, 0))

        # Speed test result below the card
        self._speed_test_label = ctk.CTkLabel(
            panel, text="",
            font=ctk.CTkFont(size=11),
            text_color=("gray55", "gray55"),
        )
        self._speed_test_label.pack(anchor="w", pady=(8, 0))

        return panel

    def _refresh_transfers(self):
        if self._transfer_scroll is None or self._transfer_history_scroll is None:
            return

        # ── Active transfers ───────────────────────────────────────
        for child in self._transfer_scroll.winfo_children():
            child.destroy()

        transfers = self._get_transfers() if self._get_transfers else []
        if not transfers:
            ctk.CTkLabel(
                self._transfer_scroll,
                text="No active transfers.",
                font=ctk.CTkFont(size=12),
                text_color=("gray50", "gray60"),
            ).pack(fill="x", pady=8)
        else:
            for t in transfers:
                self._create_transfer_card(t)

        # ── History ─────────────────────────────────────────────────
        for child in self._transfer_history_scroll.winfo_children():
            child.destroy()

        history = self._get_transfer_history() if self._get_transfer_history else []
        if not history:
            ctk.CTkLabel(
                self._transfer_history_scroll,
                text="No completed transfers yet.",
                font=ctk.CTkFont(size=12),
                text_color=("gray50", "gray60"),
            ).pack(fill="x", pady=8)
            if self._transfer_history_stats:
                self._transfer_history_stats.configure(text="")
        else:
            for h in history[:50]:  # show last 50
                self._create_transfer_history_card(h)
            # Stats
            total = len(history)
            success = sum(1 for h in history if h.get("success"))
            fail = total - success
            total_size = sum(h.get("file_size", 0) for h in history)
            parts = [f"{total} completed"]
            if success:
                parts.append(f"{success} ok")
            if fail:
                parts.append(f"{fail} fail")
            if total_size > 0:
                parts.append(self._format_size(total_size))
            if self._transfer_history_stats:
                self._transfer_history_stats.configure(text=" · ".join(parts))

        # ── Speed test result ──────────────────────────────────────
        if self._speed_test_label and self._get_speed_test_result:
            st = self._get_speed_test_result()
            if st:
                state = st.get("state", "")
                mbps = st.get("result_mbps", 0)
                if state == "sending":
                    sent = st.get("chunks_sent", 0)
                    total = st.get("total_chunks", 0)
                    self._speed_test_label.configure(
                        text=f"Speed test {sent}/{total}",
                        text_color=WARN_COLOR,
                    )
                elif state in ("done", "acknowledged") and mbps > 0:
                    quality = "fast" if mbps > 10 else "good" if mbps > 2 else "slow"
                    self._speed_test_label.configure(
                        text=f"{mbps:.1f} MB/s ({quality})",
                        text_color=STATUS_COLOR,
                    )
                else:
                    self._speed_test_label.configure(
                        text="Speed test failed",
                        text_color=("#E74C3C", "#C0392B"),
                    )

    def _format_speed(self, bytes_per_sec: float) -> str:
        if bytes_per_sec < 1024:
            return f"{bytes_per_sec:.0f} B/s"
        elif bytes_per_sec < 1024 * 1024:
            return f"{bytes_per_sec / 1024:.1f} KB/s"
        else:
            return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"

    def _format_eta(self, seconds: float) -> str:
        if seconds <= 0:
            return ""
        if seconds < 60:
            return f"{int(seconds)}s left"
        elif seconds < 3600:
            return f"{int(seconds / 60)}m {int(seconds % 60)}s left"
        else:
            return f"{int(seconds / 3600)}h left"

    def _create_transfer_card(self, transfer: dict):
        direction = transfer.get("direction", "down")
        file_name = transfer.get("file_name", "?")
        file_size = transfer.get("file_size", 0)
        progress = transfer.get("progress", 0.0)
        state = transfer.get("state", "unknown")
        speed = transfer.get("speed_bytes_per_sec", 0.0)
        eta = transfer.get("eta_seconds", 0.0)

        arrow = "\U0001F4E4" if direction == "up" else "\U0001F4E5"
        size_str = self._format_size(file_size)

        card = ctk.CTkFrame(self._transfer_scroll, corner_radius=8,
                           fg_color=("gray95", "gray17"))
        card.pack(fill="x", pady=2, padx=2)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=10)

        # Row 1: file name + size
        r1 = ctk.CTkFrame(inner, fg_color="transparent")
        r1.pack(fill="x")
        ctk.CTkLabel(
            r1, text=f"{arrow}  {file_name}",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            r1, text=size_str,
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
        ).pack(side="right")

        # Row 2: status + speed + ETA
        state_labels = {
            "awaiting_ack": "Waiting for peer...",
            "pending": "Waiting for acceptance...",
            "receiving": "Receiving...",
            "sending": "Sending...",
            "cancelled": "Cancelled",
        }
        status_text = state_labels.get(state, state.replace("_", " ").title())

        extras = []
        if state in ("receiving", "sending"):
            extras.append(f"{int(progress * 100)}%")
        if speed > 0:
            extras.append(self._format_speed(speed))
        if eta > 0:
            extras.append(self._format_eta(eta))

        r2 = ctk.CTkFrame(inner, fg_color="transparent")
        r2.pack(fill="x")
        ctk.CTkLabel(
            r2, text="  |  ".join([status_text] + extras) if extras else status_text,
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
        ).pack(side="left")

        # Progress bar
        if state in ("receiving", "sending") and progress >= 0:
            bar = ctk.CTkProgressBar(inner, height=6)
            bar.pack(fill="x", pady=(6, 0))
            bar.set(progress)

        # Cancel button
        if self._on_cancel_transfer and state not in ("complete", "failed", "cancelled", "error"):
            ctk.CTkButton(
                inner, text="Cancel", width=60, height=22,
                fg_color="transparent", border_width=1,
                text_color=("#E74C3C", "#C0392B"),
                border_color=("#E74C3C", "#C0392B"),
                hover_color=("#FADBD8", "#5B2C2C"),
                font=ctk.CTkFont(size=10),
                command=lambda tid=transfer.get("transfer_id", ""): self._on_cancel_transfer(tid),
            ).pack(anchor="e", pady=(4, 0))

    def _create_transfer_history_card(self, entry: dict):
        direction = entry.get("direction", "down")
        file_name = entry.get("file_name", "?")
        file_size = entry.get("file_size", 0)
        success = entry.get("success", False)
        timestamp = entry.get("timestamp", 0)

        arrow = "\U0001F4E4" if direction == "up" else "\U0001F4E5"
        status_icon = "✅" if success else "❌"
        size_str = self._format_size(file_size)
        if timestamp and timestamp > 0:
            time_str = datetime.datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
        else:
            time_str = ""

        card = ctk.CTkFrame(self._transfer_history_scroll, corner_radius=6,
                           fg_color=("gray95", "gray17"))
        card.pack(fill="x", pady=2, padx=2)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=8)

        r1 = ctk.CTkFrame(inner, fg_color="transparent")
        r1.pack(fill="x")
        ctk.CTkLabel(
            r1, text=f"{status_icon}  {arrow}  {file_name}",
            font=ctk.CTkFont(size=12),
        ).pack(side="left")
        ctk.CTkLabel(
            r1, text=f"{size_str}  ·  {time_str}",
            font=ctk.CTkFont(size=10),
            text_color=("gray50", "gray60"),
        ).pack(side="right")

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
        if self._theme_var is not None:
            self._theme_var.set(self._dark_mode)
