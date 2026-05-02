import logging

logger = logging.getLogger(__name__)


class NotificationManager:
    def __init__(self):
        self._tray_icon = None  # set later via set_tray()
        self._enabled = True
        self._pipe = None  # multiprocessing pipe (macOS subprocess)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value

    def set_tray(self, tray_icon):
        """Set the pystray Icon reference for notifications."""
        self._tray_icon = tray_icon

    def set_pipe(self, pipe):
        """Set a multiprocessing pipe for macOS subprocess notifications.

        When set, ``show()`` sends the notification through the pipe
        instead of calling ``_tray_icon.notify()`` directly.  This is
        needed on macOS where the tray runs in a subprocess and the
        main process has no pystray Icon reference.
        """
        self._pipe = pipe

    def show(self, title: str, message: str):
        """Show a desktop notification if tray is available."""
        if not self._enabled:
            return
        if self._pipe:
            try:
                self._pipe.send(("show_notification", title, message))
            except Exception:
                logger.debug("Notification via pipe failed", exc_info=True)
            return
        if self._tray_icon:
            try:
                self._tray_icon.notify(message, title=title)
            except Exception:
                logger.debug("Desktop notification failed", exc_info=True)


notification_mgr = NotificationManager()
