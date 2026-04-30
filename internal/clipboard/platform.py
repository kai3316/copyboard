"""Platform-specific clipboard factory."""

import logging
import platform

logger = logging.getLogger(__name__)

_system = platform.system()

if _system == "Windows":
    from internal.clipboard.clipboard_windows import (  # noqa: F401
        create_monitor,
        create_reader,
        create_writer,
    )
elif _system == "Darwin":
    from internal.clipboard.clipboard_darwin import (  # noqa: F401
        create_monitor,
        create_reader,
        create_writer,
    )
elif _system == "Linux":
    from internal.clipboard.clipboard_linux import (  # noqa: F401
        create_monitor,
        create_reader,
        create_writer,
    )
else:
    raise RuntimeError(f"Unsupported platform: {_system}")

logger.info("Clipboard backend: %s", _system)
