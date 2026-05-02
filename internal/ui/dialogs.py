"""Themed dialog replacements for tkinter.messagebox.

Uses CTkToplevel for consistent look with the rest of the app.
"""

import customtkinter as ctk

# ── Icon + color scheme per dialog type ──────────────────────────
_INFO_ICON = "ℹ️"     # ℹ
_WARN_ICON = "⚠️"     # ⚠
_ERROR_ICON = "❌"          # ❌

_INFO_COLOR = "#3498DB"
_WARN_COLOR = "#F39C12"
_ERROR_COLOR = "#E74C3C"


def _dialog(parent, title, message, icon, accent_color, buttons):
    """Shared dialog builder. Returns True if first button was clicked."""
    dlg = ctk.CTkToplevel(parent)
    dlg.title(title)
    dlg.resizable(False, False)
    dlg.transient(parent)
    dlg.grab_set()

    # Position centered over parent
    dlg.update_idletasks()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    w, h = 420, 180
    x = px + (pw - w) // 2
    y = py + (ph - h) // 2
    dlg.geometry(f"{w}x{h}+{x}+{y}")

    result = [False]

    # ── Content ──────────────────────────────────────────────
    body = ctk.CTkFrame(dlg, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=24, pady=(24, 16))

    # Icon + title row
    title_row = ctk.CTkFrame(body, fg_color="transparent")
    title_row.pack(fill="x", pady=(0, 10))

    ctk.CTkLabel(
        title_row, text=icon, font=ctk.CTkFont(size=22),
    ).pack(side="left", padx=(0, 10))

    ctk.CTkLabel(
        title_row, text=title,
        font=ctk.CTkFont(size=15, weight="bold"),
        text_color=accent_color,
    ).pack(side="left")

    # Message
    ctk.CTkLabel(
        body, text=message, justify="left",
        font=ctk.CTkFont(size=12),
        text_color=("gray30", "gray80"),
        wraplength=370,
    ).pack(fill="x")

    # ── Buttons ──────────────────────────────────────────────
    btn_row = ctk.CTkFrame(dlg, fg_color="transparent")
    btn_row.pack(fill="x", padx=24, pady=(0, 20))

    for i, (label, color) in enumerate(buttons):
        if isinstance(color, tuple):
            # Tuple (light, dark) — use slightly darker variants for hover
            hover = color
        elif color == "transparent":
            hover = ("gray85", "gray25")
        elif color.startswith("#"):
            hover = _darken(color, 0.15)
        else:
            hover = color
        btn = ctk.CTkButton(
            btn_row, text=label, width=90, height=32,
            fg_color=color,
            hover_color=hover,
            font=ctk.CTkFont(size=12),
            command=lambda v=i: _close(v),
        )
        btn.pack(side="right", padx=(6 if i > 0 else 0, 0))

    def _close(value):
        result[0] = (value == 0)
        dlg.destroy()

    dlg.protocol("WM_DELETE_WINDOW", lambda: _close(1))
    dlg.wait_window()
    return result[0]


def _darken(hex_color, amount):
    """Darken a hex color by the given amount (0-1)."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    r = max(0, int(r * (1 - amount)))
    g = max(0, int(g * (1 - amount)))
    b = max(0, int(b * (1 - amount)))
    return f"#{r:02x}{g:02x}{b:02x}"


def show_info(parent, title, message):
    """Show an info dialog with an OK button."""
    return _dialog(parent, title, message, _INFO_ICON, _INFO_COLOR,
                   [("OK", _INFO_COLOR)])


def show_warning(parent, title, message):
    """Show a warning dialog with an OK button."""
    return _dialog(parent, title, message, _WARN_ICON, _WARN_COLOR,
                   [("OK", _WARN_COLOR)])


def show_error(parent, title, message):
    """Show an error dialog with an OK button."""
    return _dialog(parent, title, message, _ERROR_ICON, _ERROR_COLOR,
                   [("OK", _ERROR_COLOR)])


def ask_yesno(parent, title, message):
    """Show a confirmation dialog with Yes/No buttons. Returns True if Yes."""
    return _dialog(parent, title, message, _WARN_ICON, _WARN_COLOR,
                   [("Yes", _INFO_COLOR), ("No", ("gray65", "gray45"))])
