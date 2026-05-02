"""Settings window for CopyBoard — Network, Content Filter, and About.

This is a secondary window accessed from the dashboard. It contains
configuration that users rarely change: network settings, content
filtering preferences, and version information.
"""

import logging
import os
import tkinter as tk
from tkinter import messagebox
from typing import Callable

import customtkinter as ctk

from internal.clipboard.filter import ALL_CATEGORIES, CATEGORY_LABELS

logger = logging.getLogger(__name__)

class SettingsWindow:
    """Settings window with sidebar navigation — separate from the main dashboard."""

    def __init__(
        self,
        root: tk.Tk,
        get_config: Callable,
        save_config: Callable,
        on_closed: Callable | None = None,
        on_export_logs: Callable | None = None,
        get_filter_categories: Callable | None = None,
        set_filter_categories: Callable | None = None,
        get_log_text: Callable | None = None,
    ):
        self._root = root
        self._get_config = get_config
        self._save_config = save_config
        self._on_closed = on_closed
        self._on_export_logs = on_export_logs
        self._get_filter_categories = get_filter_categories
        self._set_filter_categories = set_filter_categories
        self._get_log_text = get_log_text

        self._window: ctk.CTkToplevel | None = None
        self._dark_mode = False
        self._current_panel = "network"
        self._refresh_job: str | None = None

        # Widget references
        self._sidebar_buttons: dict[str, ctk.CTkButton] = {}
        self._panels: dict[str, ctk.CTkFrame] = {}
        self._content_frame: ctk.CTkFrame | None = None
        self._status_label: ctk.CTkLabel | None = None

        # Widget refs
        self._log_text: ctk.CTkTextbox | None = None

        # Form vars
        self._port_var: tk.StringVar | None = None
        self._svc_var: tk.StringVar | None = None
        self._relay_var: tk.StringVar | None = None
        self._filter_vars: dict[str, tk.BooleanVar] = {}
        # Advanced panel vars
        self._history_max_var: tk.StringVar | None = None
        self._file_receive_dir_var: tk.StringVar | None = None
        self._sync_debounce_var: tk.StringVar | None = None
        self._poll_interval_var: tk.StringVar | None = None
        self._max_reconnect_var: tk.StringVar | None = None
        self._transfer_timeout_var: tk.StringVar | None = None
        self._log_level_var: tk.StringVar | None = None
        self._notifications_var: tk.BooleanVar | None = None

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
                self._window.attributes("-topmost", True)
                self._window.after(200, lambda: self._window.attributes("-topmost", False))
                if self._window.winfo_viewable():
                    self._switch_panel(self._current_panel)
                    return
                self._window.destroy()
                self._window = None
            except tk.TclError:
                self._window = None

        logger.info("Opening CopyBoard settings")
        ctk.set_appearance_mode("dark" if self._dark_mode else "light")
        ctk.set_default_color_theme("blue")

        self._window = ctk.CTkToplevel(self._root)
        self._window.title("CopyBoard Settings")
        self._window.geometry("740x620")
        self._window.minsize(680, 560)
        self._window.protocol("WM_DELETE_WINDOW", self._on_close)

        self._window.update_idletasks()
        sw = self._window.winfo_screenwidth()
        sh = self._window.winfo_screenheight()
        w, h = 740, 620
        self._window.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        self._build_ui()
        self._switch_panel("network")

        try:
            self._window.attributes("-topmost", True)
            self._window.after(200, lambda: self._window.attributes("-topmost", False))
        except tk.TclError:
            pass

    def _on_close(self):
        if self._window is not None:
            self._window.destroy()
            self._window = None
            self._sidebar_buttons.clear()
            self._panels.clear()
            self._filter_vars.clear()
        if self._on_closed is not None:
            self._on_closed()

    # ═══════════════════════════════════════════════════════════════
    # UI construction
    # ═══════════════════════════════════════════════════════════════

    def _build_ui(self):
        outer = ctk.CTkFrame(self._window, fg_color="transparent")
        outer.pack(fill="both", expand=True)

        # Header
        header = ctk.CTkFrame(outer, corner_radius=0, fg_color=("#1A5276", "#1B2A3A"))
        header.pack(fill="x")
        h_inner = ctk.CTkFrame(header, fg_color="transparent")
        h_inner.pack(fill="x", padx=20, pady=(14, 14))

        ctk.CTkLabel(
            h_inner, text="\U0001F527  Settings",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=("#FFFFFF", "#E0E0E0"),
        ).pack(side="left")

        self._theme_btn = ctk.CTkButton(
            h_inner, text="☾  Dark" if not self._dark_mode else "☀  Light",
            width=90, height=32, fg_color="transparent",
            border_width=1, border_color=("#7F8C8D", "#566573"),
            text_color=("#FFFFFF", "#E0E0E0"),
            hover_color=("#5D6D7E", "#4A5568"),
            command=self._toggle_theme,
        )
        self._theme_btn.pack(side="right")

        # Body: sidebar | content
        body = ctk.CTkFrame(outer, fg_color="transparent")
        body.pack(fill="both", expand=True)

        self._build_sidebar(body)

        sep = ctk.CTkFrame(body, width=1, fg_color=("gray75", "gray30"))
        sep.pack(side="left", fill="y")

        self._content_frame = ctk.CTkFrame(body, fg_color="transparent")
        self._content_frame.pack(side="left", fill="both", expand=True)

        # Build panels
        self._panels["network"] = self._build_network_panel()
        self._panels["filter"] = self._build_filter_panel()
        self._panels["security"] = self._build_security_panel()
        self._panels["advanced"] = self._build_advanced_panel()
        self._panels["logs"] = self._build_logs_panel()
        self._panels["about"] = self._build_about_panel()

        # Footer
        footer = ctk.CTkFrame(outer, height=44, corner_radius=0,
                              fg_color=("gray90", "gray15"))
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)
        f_inner = ctk.CTkFrame(footer, fg_color="transparent")
        f_inner.pack(fill="x", padx=20, pady=8)

        self._status_label = ctk.CTkLabel(
            f_inner, text="Ready", text_color=("gray50", "gray60"),
            font=ctk.CTkFont(size=11),
        )
        self._status_label.pack(side="left")

        ctk.CTkButton(
            f_inner, text="Close", width=60, height=28,
            fg_color="transparent", border_width=1,
            text_color=("gray40", "gray70"),
            border_color=("gray60", "gray50"),
            hover_color=("gray85", "gray25"),
            command=self._on_close,
        ).pack(side="right")

    # ── Sidebar ────────────────────────────────────────────────────

    def _build_sidebar(self, body):
        sidebar = ctk.CTkFrame(body, width=180, fg_color="transparent")
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        inner = ctk.CTkFrame(sidebar, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=8, pady=16)

        nav = [
            ("network", "\U0001F310  Network"),
            ("filter",  "\U0001F6E1  Content Filter"),
            ("security", "\U0001F512  Security"),
            ("advanced", "⚙  Advanced"),
            ("logs",    "\U0001F4C4  Logs"),
            ("about",   "ℹ️  About"),
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
            btn.pack(fill="x", pady=5)
            self._sidebar_buttons[key] = btn

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
        if key == "logs":
            self._refresh_log_text(self._log_text)

    # ═══════════════════════════════════════════════════════════════
    # Panel: Network
    # ═══════════════════════════════════════════════════════════════

    def _build_network_panel(self):
        panel = ctk.CTkFrame(self._content_frame, fg_color="transparent")
        cfg = self._get_config()

        ctk.CTkLabel(
            panel, text="Network Settings",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", pady=(0, 16))

        card = ctk.CTkFrame(panel, corner_radius=12)
        card.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            card, text="Connection",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(14, 10))

        row1 = ctk.CTkFrame(card, fg_color="transparent")
        row1.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(row1, text="TCP Port", width=100, anchor="w").pack(side="left")
        self._port_var = tk.StringVar(value=str(cfg.port))
        ctk.CTkEntry(row1, textvariable=self._port_var, width=80, height=32).pack(side="left", padx=(12, 8))
        ctk.CTkLabel(
            row1, text="(1024–65535, restart required)",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
        ).pack(side="left")

        row2 = ctk.CTkFrame(card, fg_color="transparent")
        row2.pack(fill="x", padx=16, pady=(0, 10))
        ctk.CTkLabel(row2, text="Service Type", width=100, anchor="w").pack(side="left")
        self._svc_var = tk.StringVar(value=cfg.service_type)
        ctk.CTkEntry(row2, textvariable=self._svc_var, height=32).pack(
            side="left", fill="x", expand=True, padx=(12, 0))

        # Relay card
        card2 = ctk.CTkFrame(panel, corner_radius=12)
        card2.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            card2, text="Relay (optional)",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(14, 10))

        ctk.CTkLabel(
            card2, text="Relay URL", anchor="w",
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=16)
        self._relay_var = tk.StringVar(value=cfg.relay_url)
        ctk.CTkEntry(card2, textvariable=self._relay_var, height=32).pack(
            fill="x", padx=16, pady=(4, 6))

        ctk.CTkLabel(
            card2,
            text="Leave blank for LAN-only sync. Set a relay server URL for cross-network sync.",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
        ).pack(anchor="w", padx=16, pady=(0, 14))

        ctk.CTkButton(
            panel, text="Save Network Settings",
            width=200, height=36, command=self._on_save_network,
        ).pack(anchor="w")

        return panel

    def _on_save_network(self):
        try:
            port = int(self._port_var.get())
            if not 1024 <= port <= 65535:
                raise ValueError("Port out of range")
        except ValueError:
            messagebox.showwarning("Invalid", "Port must be 1024–65535.")
            return

        cfg = self._get_config()
        cfg.port = port
        cfg.service_type = self._svc_var.get().strip()
        cfg.relay_url = self._relay_var.get().strip()
        self._save_config()
        messagebox.showinfo(
            "Saved",
            "Network settings saved.\nRestart CopyBoard for changes to take effect.",
        )

    # ═══════════════════════════════════════════════════════════════
    # Panel: Content Filter
    # ═══════════════════════════════════════════════════════════════

    def _build_filter_panel(self):
        panel = ctk.CTkFrame(self._content_frame, fg_color="transparent")

        ctk.CTkLabel(
            panel, text="Content Filter",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", pady=(0, 8))

        ctk.CTkLabel(
            panel,
            text="When enabled, sensitive content is replaced with [FILTERED] before syncing to other devices.",
            font=ctk.CTkFont(size=12),
            text_color=("gray50", "gray60"),
            justify="left",
        ).pack(anchor="w", pady=(0, 16))

        card = ctk.CTkFrame(panel, corner_radius=12)
        card.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            card, text="Sensitive Content Categories",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(14, 10))

        active = self._get_filter_categories() if self._get_filter_categories else []

        for category in ALL_CATEGORIES:
            label = CATEGORY_LABELS.get(category, category)
            var = tk.BooleanVar(value=category in active)
            self._filter_vars[category] = var
            ctk.CTkSwitch(
                card, text=label,
                variable=var,
                font=ctk.CTkFont(size=12),
            ).pack(anchor="w", padx=16, pady=(2, 6))

        # Spacer at bottom of card
        ctk.CTkFrame(card, height=8, fg_color="transparent").pack()

        ctk.CTkButton(
            panel, text="Save Filter Settings",
            width=200, height=36, command=self._on_save_filter,
        ).pack(anchor="w")

        return panel

    def _on_save_filter(self):
        enabled = [cat for cat in ALL_CATEGORIES if self._filter_vars.get(cat, tk.BooleanVar()).get()]
        if self._set_filter_categories:
            self._set_filter_categories(enabled)
        cfg = self._get_config()
        cfg.filter_enabled_categories = enabled
        self._save_config()
        messagebox.showinfo("Saved", "Content filter settings saved.")

    # ═══════════════════════════════════════════════════════════════
    # Panel: Advanced
    # ═══════════════════════════════════════════════════════════════

    def _build_security_panel(self):
        panel = ctk.CTkFrame(self._content_frame, fg_color="transparent")
        cfg = self._get_config()

        scroll = ctk.CTkScrollableFrame(panel, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        ctk.CTkLabel(
            scroll, text="Security",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            scroll, text="Encryption settings protect your data at rest and in transit.",
            font=ctk.CTkFont(size=11), text_color=("gray50", "gray60"),
        ).pack(anchor="w", pady=(0, 14))

        # ── Card 1: Encryption toggle ──────────────────────────────
        card1 = ctk.CTkFrame(scroll, corner_radius=12)
        card1.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(
            card1, text="Data Encryption",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(14, 10))

        features = [
            "Private key encrypted at rest in config file",
            "Clipboard history encrypted on disk",
            "App-layer encryption between paired devices (on top of TLS 1.3)",
        ]
        for i, desc in enumerate(features):
            ctk.CTkLabel(
                card1, text=f"  {i+1}. {desc}",
                font=ctk.CTkFont(size=11),
                text_color=("gray40", "gray70"),
                anchor="w", justify="left",
            ).pack(anchor="w", padx=20, pady=(2, 0))

        self._enc_enabled_var = tk.BooleanVar(value=cfg.encryption_enabled)
        ctk.CTkSwitch(
            card1, text="Enable encryption (all three features)",
            variable=self._enc_enabled_var,
            font=ctk.CTkFont(size=13),
        ).pack(anchor="w", padx=16, pady=(14, 14))

        # ── Card 2: Pre-shared password ────────────────────────────
        card2 = ctk.CTkFrame(scroll, corner_radius=12)
        card2.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(
            card2, text="Pre-Shared Password (Optional)",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(14, 10))
        ctk.CTkLabel(
            card2,
            text="Set the same password on both devices for stronger\n"
                 "app-layer encryption. Leave blank to auto-derive\n"
                 "keys from device certificates.",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray60"),
            anchor="w", justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 4))

        # Status indicator — password is never loaded from disk (hash only)
        _pw_status = "Password is set (hash stored)" if cfg.encryption_password_hash else "No password set"
        self._enc_pw_status = ctk.CTkLabel(
            card2, text=_pw_status,
            font=ctk.CTkFont(size=11),
            text_color=("#27AE60", "#2ECC71") if cfg.encryption_password_hash else ("gray50", "gray60"),
        )
        self._enc_pw_status.pack(anchor="w", padx=20, pady=(0, 8))

        pw_row = ctk.CTkFrame(card2, fg_color="transparent")
        pw_row.pack(fill="x", padx=16, pady=(0, 14))
        # Password field always starts empty — plaintext never stored on disk
        self._enc_password_var = tk.StringVar(value="")
        self._enc_password_entry = ctk.CTkEntry(
            pw_row, textvariable=self._enc_password_var,
            height=32, width=240, show="*",
            placeholder_text="Enter new password to set or change",
        )
        self._enc_password_entry.pack(side="left", padx=(0, 8))
        self._show_pw_btn = ctk.CTkButton(
            pw_row, text="Show", width=50, height=32,
            fg_color="transparent", border_width=1,
            text_color=("gray50", "gray60"),
            border_color=("gray70", "gray40"),
            font=ctk.CTkFont(size=11),
            command=self._toggle_password_visibility,
        )
        self._show_pw_btn.pack(side="left")

        # ── Save button ──────────────────────────────────────────
        ctk.CTkButton(
            scroll, text="Save Security Settings",
            width=200, height=36, command=self._on_save_security,
        ).pack(anchor="w", pady=(4, 16))

        return panel

    def _toggle_password_visibility(self):
        if self._enc_password_entry.cget("show") == "*":
            self._enc_password_entry.configure(show="")
            self._show_pw_btn.configure(text="Hide")
        else:
            self._enc_password_entry.configure(show="*")
            self._show_pw_btn.configure(text="Show")

    def _on_save_security(self):
        cfg = self._get_config()
        cfg.encryption_enabled = self._enc_enabled_var.get()
        cfg.encryption_password = self._enc_password_var.get()
        self._save_config()
        if self._status_label:
            self._status_label.configure(
                text="Security settings saved. Restart required for changes to take effect."
            )

    def _build_advanced_panel(self):
        panel = ctk.CTkFrame(self._content_frame, fg_color="transparent")
        cfg = self._get_config()

        scroll = ctk.CTkScrollableFrame(panel, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        ctk.CTkLabel(
            scroll, text="Advanced Settings",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(
            scroll, text="Some changes require a restart to take effect.",
            font=ctk.CTkFont(size=11), text_color=("gray50", "gray60"),
        ).pack(anchor="w", pady=(0, 14))

        def _desc(parent, text):
            ctk.CTkLabel(
                parent, text=text, wraplength=420,
                font=ctk.CTkFont(size=11), text_color=("gray50", "gray60"),
                anchor="w", justify="left",
            ).pack(anchor="w", padx=16, pady=(0, 10))

        def _row(parent):
            r = ctk.CTkFrame(parent, fg_color="transparent")
            r.pack(fill="x", padx=16, pady=(0, 2))
            return r

        # ── Card 1: Clipboard & Sync ──────────────────────────────
        card1 = ctk.CTkFrame(scroll, corner_radius=12)
        card1.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(
            card1, text="Clipboard & Sync",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(14, 10))

        r = _row(card1)
        ctk.CTkLabel(r, text="History Max Entries", anchor="w",
                     font=ctk.CTkFont(size=12)).pack(side="left")
        self._history_max_var = tk.StringVar(value=str(cfg.history_max_entries))
        ctk.CTkEntry(r, textvariable=self._history_max_var,
                     width=80, height=32).pack(side="right")
        _desc(card1, "Maximum clipboard history entries to keep (10–1000)")

        r = _row(card1)
        ctk.CTkLabel(r, text="Sync Debounce (seconds)", anchor="w",
                     font=ctk.CTkFont(size=12)).pack(side="left")
        self._sync_debounce_var = tk.StringVar(value=str(cfg.sync_debounce))
        ctk.CTkEntry(r, textvariable=self._sync_debounce_var,
                     width=80, height=32).pack(side="right")
        _desc(card1, "Minimum interval between outgoing syncs (0.1–5.0)")

        r = _row(card1)
        ctk.CTkLabel(r, text="Poll Interval (seconds)", anchor="w",
                     font=ctk.CTkFont(size=12)).pack(side="left")
        self._poll_interval_var = tk.StringVar(value=str(cfg.clipboard_poll_interval))
        ctk.CTkEntry(r, textvariable=self._poll_interval_var,
                     width=80, height=32).pack(side="right")
        _desc(card1, "Clipboard change detection frequency (0.1–5.0). macOS/Linux only. Restart required.")

        # ── Card 2: File Transfer ─────────────────────────────────
        card2 = ctk.CTkFrame(scroll, corner_radius=12)
        card2.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(
            card2, text="File Transfer",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(14, 10))

        ctk.CTkLabel(
            card2, text="Receive Directory", anchor="w",
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=16, pady=(0, 4))
        dir_row = ctk.CTkFrame(card2, fg_color="transparent")
        dir_row.pack(fill="x", padx=16, pady=(0, 2))
        self._file_receive_dir_var = tk.StringVar(value=cfg.file_receive_dir)
        ctk.CTkEntry(dir_row, textvariable=self._file_receive_dir_var,
                     height=32, placeholder_text="~/Downloads/CopyBoard").pack(
            side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            dir_row, text="Browse...", width=80, height=32,
            command=self._browse_receive_dir,
        ).pack(side="right")
        _desc(card2, "Where received files are saved. Leave blank for default. Restart required.")

        r = _row(card2)
        ctk.CTkLabel(r, text="Transfer Timeout (seconds)", anchor="w",
                     font=ctk.CTkFont(size=12)).pack(side="left")
        self._transfer_timeout_var = tk.StringVar(value=str(cfg.transfer_timeout))
        ctk.CTkEntry(r, textvariable=self._transfer_timeout_var,
                     width=80, height=32).pack(side="right")
        _desc(card2, "Maximum time for a file transfer before it's considered stale (30–3600)")

        # ── Card 3: Connection ────────────────────────────────────
        card3 = ctk.CTkFrame(scroll, corner_radius=12)
        card3.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(
            card3, text="Connection",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(14, 10))

        r = _row(card3)
        ctk.CTkLabel(r, text="Max Reconnect Attempts", anchor="w",
                     font=ctk.CTkFont(size=12)).pack(side="left")
        self._max_reconnect_var = tk.StringVar(value=str(cfg.max_reconnect_attempts))
        ctk.CTkEntry(r, textvariable=self._max_reconnect_var,
                     width=80, height=32).pack(side="right")
        _desc(card3, "How many times to retry connecting to a lost peer (1–100). Restart required.")

        # ── Card 4: Logging & Notifications ───────────────────────
        card4 = ctk.CTkFrame(scroll, corner_radius=12)
        card4.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(
            card4, text="Logging & Notifications",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(14, 10))

        r = _row(card4)
        ctk.CTkLabel(r, text="Log Level", anchor="w",
                     font=ctk.CTkFont(size=12)).pack(side="left")
        self._log_level_var = tk.StringVar(value=cfg.log_level)
        ctk.CTkOptionMenu(
            r, variable=self._log_level_var,
            values=["DEBUG", "INFO", "WARNING", "ERROR"],
            width=120, height=32,
        ).pack(side="right")

        self._notifications_var = tk.BooleanVar(value=cfg.notifications_enabled)
        ctk.CTkSwitch(
            card4, text="Enable desktop notifications",
            variable=self._notifications_var,
            font=ctk.CTkFont(size=13),
        ).pack(anchor="w", padx=16, pady=(8, 14))

        # ── Save button ──────────────────────────────────────────
        ctk.CTkButton(
            scroll, text="Save Advanced Settings",
            width=200, height=36, command=self._on_save_advanced,
        ).pack(anchor="w", pady=(4, 16))

        return panel

    def _browse_receive_dir(self):
        from tkinter import filedialog
        from pathlib import Path
        directory = filedialog.askdirectory(
            parent=self._window,
            title="Select File Receive Directory",
            initialdir=self._file_receive_dir_var.get() or str(Path.home() / "Downloads" / "CopyBoard"),
        )
        if directory:
            self._file_receive_dir_var.set(directory)

    def _on_save_advanced(self):
        from pathlib import Path
        errors = []

        try:
            history_max = int(self._history_max_var.get())
            if not 10 <= history_max <= 1000:
                raise ValueError
        except ValueError:
            errors.append("History Max Entries: must be 10–1000")
            history_max = None

        try:
            debounce = float(self._sync_debounce_var.get())
            if not 0.1 <= debounce <= 5.0:
                raise ValueError
        except ValueError:
            errors.append("Sync Debounce: must be 0.1–5.0 seconds")
            debounce = None

        try:
            poll = float(self._poll_interval_var.get())
            if not 0.1 <= poll <= 5.0:
                raise ValueError
        except ValueError:
            errors.append("Poll Interval: must be 0.1–5.0 seconds")
            poll = None

        receive_dir = self._file_receive_dir_var.get().strip()
        if receive_dir and not Path(receive_dir).parent.exists():
            errors.append("Receive Directory: parent folder does not exist")

        try:
            timeout = float(self._transfer_timeout_var.get())
            if not 30 <= timeout <= 3600:
                raise ValueError
        except ValueError:
            errors.append("Transfer Timeout: must be 30–3600 seconds")
            timeout = None

        try:
            max_reconnect = int(self._max_reconnect_var.get())
            if not 1 <= max_reconnect <= 100:
                raise ValueError
        except ValueError:
            errors.append("Max Reconnect Attempts: must be 1–100")
            max_reconnect = None

        if errors:
            messagebox.showwarning("Validation Error", "\n".join(errors))
            return

        cfg = self._get_config()
        cfg.history_max_entries = history_max
        cfg.file_receive_dir = receive_dir
        cfg.sync_debounce = debounce
        cfg.clipboard_poll_interval = poll
        cfg.max_reconnect_attempts = max_reconnect
        cfg.transfer_timeout = timeout
        cfg.log_level = self._log_level_var.get()
        cfg.notifications_enabled = self._notifications_var.get()
        self._save_config()

        if self._status_label:
            self._status_label.configure(text="Advanced settings saved")
        messagebox.showinfo(
            "Saved",
            "Advanced settings saved.\n\n"
            "Settings marked 'Restart required' will take effect\n"
            "after restarting CopyBoard.",
        )

    # ═══════════════════════════════════════════════════════════════
    # Panel: Logs
    # ═══════════════════════════════════════════════════════════════

    def _build_logs_panel(self):
        panel = ctk.CTkFrame(self._content_frame, fg_color="transparent")

        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.pack(fill="x", pady=(0, 12))

        ctk.CTkLabel(
            header, text="Application Logs",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left")

        btn_row = ctk.CTkFrame(header, fg_color="transparent")
        btn_row.pack(side="right")

        ctk.CTkButton(
            btn_row, text="⟳  Refresh", width=90, height=30,
            fg_color="transparent", border_width=1,
            text_color=("gray40", "gray70"),
            border_color=("gray60", "gray50"),
            hover_color=("gray85", "gray25"),
            font=ctk.CTkFont(size=11),
            command=lambda: self._refresh_log_text(self._log_text),
        ).pack(side="left", padx=(0, 6))

        if self._on_export_logs:
            ctk.CTkButton(
                btn_row, text="\U0001F4BE  Export", width=80, height=30,
                font=ctk.CTkFont(size=11),
                command=self._on_export_logs,
            ).pack(side="left")

        card = ctk.CTkFrame(panel, corner_radius=12)
        card.pack(fill="both", expand=True)

        self._log_text = ctk.CTkTextbox(card, font=ctk.CTkFont(size=11), wrap="word")
        self._log_text.pack(fill="both", expand=True, padx=8, pady=8)
        self._refresh_log_text(self._log_text)

        return panel

    def _refresh_log_text(self, widget):
        if self._get_log_text:
            try:
                text = self._get_log_text()
                widget.configure(state="normal")
                widget.delete("1.0", "end")
                if not text:
                    text = "No log entries yet."
                widget.insert("1.0", text)
                widget.see("end")
                widget.configure(state="disabled")
            except Exception:
                pass

    # ═══════════════════════════════════════════════════════════════
    # Panel: About
    # ═══════════════════════════════════════════════════════════════

    def _build_about_panel(self):
        panel = ctk.CTkFrame(self._content_frame, fg_color="transparent")

        center = ctk.CTkFrame(panel, fg_color="transparent")
        center.pack(expand=True, fill="both")

        ctk.CTkLabel(
            center, text="\U0001F4CB", font=ctk.CTkFont(size=40),
        ).pack(pady=(0, 8))

        ctk.CTkLabel(
            center, text="CopyBoard",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(pady=(0, 4))

        ctk.CTkLabel(
            center, text="v1.0.0",
            font=ctk.CTkFont(size=12),
            text_color=("gray50", "gray60"),
        ).pack()

        ctk.CTkLabel(
            center,
            text="Cross-platform clipboard sharing\nbetween Windows, macOS, and Linux.",
            font=ctk.CTkFont(size=13),
            justify="center",
        ).pack(pady=(20, 18))

        feat_card = ctk.CTkFrame(center, corner_radius=12,
                                fg_color=("gray95", "gray17"))
        feat_card.pack(fill="x", padx=20)

        features = [
            ("✅", "Automatic peer discovery on LAN (mDNS/Bonjour)"),
            ("\U0001F512", "TLS 1.3 encrypted transport + certificate pinning"),
            ("\U0001F4C4", "Text, HTML, RTF, and image clipboard support"),
            ("⚡", "Zero configuration — just run it"),
            ("\U0001F6AB", "8-digit pairing code, rate-limited"),
            ("\U0001F4E4", "File sharing between devices"),
        ]

        for icon, desc in features:
            row = ctk.CTkFrame(feat_card, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=3)
            ctk.CTkLabel(row, text=icon, font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 8))
            ctk.CTkLabel(row, text=desc, font=ctk.CTkFont(size=12)).pack(side="left")

        ctk.CTkButton(
            center, text="\U0001F4C2  Show Data Folder", width=200, height=34,
            fg_color="transparent", border_width=1,
            border_color=("#2980B9", "#3498DB"),
            text_color=("#2980B9", "#3498DB"),
            hover_color=("#D6EAF8", "#1A3A4A"),
            font=ctk.CTkFont(size=12),
            command=self._open_data_folder,
        ).pack(pady=(18, 20))

        return panel

    def _open_data_folder(self):
        """Open the config directory in the system file explorer."""
        from internal.config.config import _config_dir
        import platform
        import subprocess
        path = str(_config_dir())
        try:
            system = platform.system()
            if system == "Windows":
                os.startfile(path)
            elif system == "Darwin":
                subprocess.run(["open", path], check=False)
            else:
                subprocess.run(["xdg-open", path], check=False)
        except Exception as e:
            logger.warning("Failed to open data folder: %s", e)

    # ═══════════════════════════════════════════════════════════════
    # Theme toggle
    # ═══════════════════════════════════════════════════════════════

    def _toggle_theme(self):
        self._dark_mode = not self._dark_mode
        ctk.set_appearance_mode("dark" if self._dark_mode else "light")
        self._theme_btn.configure(
            text="☀  Light" if self._dark_mode else "☾  Dark"
        )
