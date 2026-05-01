class NotificationManager:
    def __init__(self):
        self._tray_icon = None  # set later via set_tray()
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value

    def set_tray(self, tray_icon):
        """Set the pystray Icon reference for notifications."""
        self._tray_icon = tray_icon

    def show(self, title: str, message: str):
        """Show a desktop notification if tray is available."""
        if not self._enabled:
            return
        if self._tray_icon:
            try:
                self._tray_icon.notify(message, title=title)
            except Exception:
                pass  # notifications are best-effort


notification_mgr = NotificationManager()
