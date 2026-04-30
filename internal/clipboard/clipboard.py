"""Abstract clipboard monitor, reader, and writer interface."""

from abc import ABC, abstractmethod

from internal.clipboard.format import ClipboardContent


class ClipboardReader(ABC):
    """Read clipboard contents."""

    @abstractmethod
    def read(self) -> ClipboardContent:
        """Read all available formats from the clipboard."""


class ClipboardWriter(ABC):
    """Write content to clipboard."""

    @abstractmethod
    def write(self, content: ClipboardContent):
        """Write content to the clipboard in the best available format."""


class ClipboardMonitor(ABC):
    """Monitor clipboard for changes."""

    @abstractmethod
    def start(self, callback):
        """
        Start monitoring. Calls `callback()` whenever the clipboard changes.
        The callback is called from a background thread.
        """

    @abstractmethod
    def stop(self):
        """Stop monitoring."""
