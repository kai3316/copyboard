#!/usr/bin/env python3
"""CopyBoard — Cross-platform clipboard sharing.

Real-time clipboard sync between Windows, macOS, and Linux on the same local network.
Runs as a system tray application with an optional settings GUI.
"""

import logging
import os
import shutil
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

# Add project root to Python path so 'internal' package can be found
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from internal.config.config import Config, PeerInfo, load, save
from internal.protocol.codec import encode_message
from internal.security.pairing import PairingManager
from internal.sync.manager import SyncManager
from internal.transport.connection import TransportManager
from internal.transport.discovery import Discovery
from internal.ui.settings_window import SettingsWindow
from internal.ui.systray import SystrayApp

logger = logging.getLogger(__name__)


def _get_log_dir():
    """Return the platform-specific log directory."""
    import platform
    from pathlib import Path

    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "CopyBoard"
    elif system == "Darwin":
        return Path.home() / "Library" / "Logs" / "CopyBoard"
    else:
        return Path.home() / ".local" / "share" / "copyboard"


def _get_log_path():
    return _get_log_dir() / "copyboard.log"


def setup_logging():
    import logging.handlers

    log_dir = _get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    # Log level from environment variable (COPYBOARD_LOG_LEVEL=DEBUG/INFO/WARNING/ERROR)
    raw = os.environ.get("COPYBOARD_LOG_LEVEL", "").upper()
    level_map = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
                 "WARNING": logging.WARNING, "ERROR": logging.ERROR}
    app_level = level_map.get(raw, logging.INFO)

    # Formatters
    file_fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)-8s] %(threadName)-12s "
        "%(name)s:%(lineno)d  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)-28s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # Root logger
    root = logging.getLogger()
    root.setLevel(app_level)

    # Rotating file handler (5 MB, 3 backups)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "copyboard.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_fmt)
    root.addHandler(file_handler)

    # Console (stderr) handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(app_level)
    console_handler.setFormatter(console_fmt)
    root.addHandler(console_handler)

    # Quiet down noisy third-party libraries
    for noisy in ("zeroconf", "PIL", "cryptography", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main():
    setup_logging()
    logger.info("=" * 72)
    logger.info("  CopyBoard v1.0.0 — session start  %s",
                time.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("  Platform: %s  |  PID: %d",
                sys.platform, os.getpid())
    logger.info("=" * 72)
    logger.info("CopyBoard starting...")

    # ── Load config ─────────────────────────────────────────────
    cfg = load()
    logger.info("Device: %s (%s)", cfg.device_name, cfg.device_id)

    # ── Pairing / Identity ──────────────────────────────────────
    pairing_mgr = PairingManager(cfg.device_id, cfg.device_name)
    identity = pairing_mgr.load_or_create_identity(
        cfg.private_key_pem, cfg.certificate_pem,
    )
    if cfg.private_key_pem != identity.private_key_pem:
        cfg.private_key_pem = identity.private_key_pem
        cfg.certificate_pem = identity.certificate_pem
        save(cfg)
    logger.info("Certificate fingerprint: %s", identity.fingerprint_short)

    for peer in cfg.peers.values():
        try:
            pairing_mgr.add_peer(
                peer.device_id, peer.device_name,
                peer.public_key_pem, peer.paired,
            )
        except Exception as e:
            logger.warning("Skipping peer %s: %s", peer.device_name, e)

    # ── Sync Manager ────────────────────────────────────────────
    sync_mgr = SyncManager(cfg.device_id, cfg.device_name)
    sync_mgr.set_enabled(cfg.sync_enabled)

    # ── Transport ───────────────────────────────────────────────
    transport_mgr = TransportManager(
        cfg.device_id, cfg.device_name, cfg.port, pairing_mgr,
    )

    def on_local_sync(msg):
        data = encode_message(msg)
        transport_mgr.broadcast(data)

    sync_mgr.on_send = on_local_sync

    def on_peer_message(msg):
        sync_mgr.handle_remote_message(msg)

    transport_mgr.set_on_peer_message(on_peer_message)

    # ── Discovery ───────────────────────────────────────────────
    discovery = Discovery(cfg.device_id, cfg.device_name, cfg.port, cfg.service_type)

    def on_peer_found(peer_id, peer_name, address, port):
        transport_mgr.connect_to_peer(peer_id, peer_name, address, port)

    def on_peer_lost(peer_id):
        transport_mgr.disconnect_peer(peer_id)

    discovery.set_callbacks(on_peer_found, on_peer_lost)

    # ── Start services ──────────────────────────────────────────
    sync_mgr.start()
    transport_mgr.start_server()
    discovery.start()

    # ── Tkinter root (hidden, for settings window) ──────────────
    root = tk.Tk()
    root.withdraw()  # hide the root window

    # ── Export logs ─────────────────────────────────────────────
    def on_export_logs():
        log_path = _get_log_path()
        dest = filedialog.asksaveasfilename(
            parent=root,
            title="Save Log File As",
            initialfile=f"copyboard_{time.strftime('%Y%m%d_%H%M%S')}.log",
            filetypes=[("Log files", "*.log"), ("Text files", "*.txt"), ("All files", "*.*")],
            defaultextension=".log",
        )
        if not dest:
            return
        try:
            shutil.copy2(log_path, dest)
            messagebox.showinfo("Exported", f"Log saved to:\n{dest}")
            logger.info("Log exported to %s", dest)
        except FileNotFoundError:
            messagebox.showwarning(
                "Not Found",
                f"No log file found at:\n{log_path}\n\n"
                "CopyBoard may not have been running long enough to generate logs.",
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export log:\n{e}")
            logger.error("Failed to export log: %s", e)

    # ── Settings window factory ─────────────────────────────────
    settings_win: SettingsWindow | None = None

    def _create_settings_window():
        nonlocal settings_win
        if settings_win is None:

            def get_cfg():
                return cfg

            def save_cfg():
                save(cfg)
                # Also save peers from pairing manager
                for peer in pairing_mgr.get_known_peers():
                    if peer.device_id not in cfg.peers:
                        cfg.peers[peer.device_id] = PeerInfo(
                            device_id=peer.device_id,
                            device_name=peer.device_name,
                            public_key_pem=peer.certificate_pem,
                            paired=peer.paired,
                        )
                save(cfg)

            def get_peers():
                result = []
                for p in pairing_mgr.get_known_peers():
                    connected = p.device_id in transport_mgr.get_connected_peers()
                    result.append((p.device_id, p.device_name, p.paired, connected))
                return result

            def get_pending():
                return pairing_mgr.get_pending_pairings()

            def on_pair(peer_id, code):
                return pairing_mgr.confirm_pairing(peer_id, code)

            def on_unpair(peer_id):
                pairing_mgr.reject_pairing(peer_id)
                transport_mgr.disconnect_peer(peer_id)
                # Update peer in config
                if peer_id in cfg.peers:
                    cfg.peers[peer_id].paired = False
                save(cfg)

            def on_remove(peer_id):
                pairing_mgr.remove_peer(peer_id)
                transport_mgr.disconnect_peer(peer_id)
                cfg.peers.pop(peer_id, None)
                save(cfg)

            def _on_win_closed():
                nonlocal settings_win
                settings_win = None

            settings_win = SettingsWindow(
                root=root,
                get_config=get_cfg,
                save_config=save_cfg,
                get_peers=get_peers,
                get_pending_pairings=get_pending,
                get_sync_enabled=lambda: cfg.sync_enabled,
                set_sync_enabled=lambda v: sync_mgr.set_enabled(v),
                on_pair=on_pair,
                on_unpair=on_unpair,
                on_remove_peer=on_remove,
                on_export_logs=on_export_logs,
                on_closed=_on_win_closed,
            )
            settings_win.show()

    def open_settings():
        """Called from systray menu — schedule on tkinter's main thread."""
        root.after(0, _create_settings_window)

    # ── Systray ─────────────────────────────────────────────────
    def on_enable_toggle(enabled: bool):
        sync_mgr.set_enabled(enabled)
        cfg.sync_enabled = enabled
        save(cfg)

    _shutting_down = False

    def on_quit():
        nonlocal _shutting_down
        if _shutting_down:
            return
        _shutting_down = True
        _stop_updater.set()
        logger.info("Shutting down...")
        sync_mgr.stop()
        discovery.stop()
        transport_mgr.stop_server()

        for peer in pairing_mgr.get_known_peers():
            if peer.device_id not in cfg.peers:
                cfg.peers[peer.device_id] = PeerInfo(
                    device_id=peer.device_id,
                    device_name=peer.device_name,
                    public_key_pem=peer.certificate_pem,
                    paired=peer.paired,
                )
        save(cfg)
        root.quit()

    systray = SystrayApp(
        device_name=cfg.device_name,
        on_enable_toggle=on_enable_toggle,
        on_open_settings=open_settings,
        on_export_logs=on_export_logs,
        on_quit=on_quit,
    )

    _stop_updater = threading.Event()

    def update_peers_loop():
        while not _stop_updater.is_set():
            peer_ids = transport_mgr.get_connected_peers()
            peer_names = []
            for pid in peer_ids:
                peers = pairing_mgr.get_known_peers()
                found = next((p for p in peers if p.device_id == pid), None)
                peer_names.append(found.device_name if found else pid)
            systray.set_peers(peer_names)
            _stop_updater.wait(3)

    updater = threading.Thread(target=update_peers_loop, daemon=True)
    updater.start()

    logger.info("CopyBoard is ready. System tray icon should appear.")

    # Run pystray in a background thread, tkinter in the main thread
    tray_thread = threading.Thread(target=systray.run, daemon=True)
    tray_thread.start()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        on_quit()


if __name__ == "__main__":
    main()
