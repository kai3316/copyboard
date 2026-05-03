#!/usr/bin/env python3
"""ClipSync — Cross-platform clipboard sharing.

Real-time clipboard sync between Windows, macOS, and Linux on the same local network.
Runs as a system tray application with an optional settings GUI.
"""

import base64 as _b64
import logging
import os
import shutil
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, simpledialog

# Add project root to Python path so 'internal' package can be found
# (not needed in a PyInstaller-frozen bundle)
if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from internal.clipboard.filter import ContentFilter
from internal.clipboard.format import ClipboardContent, ContentType as _CT
from internal.clipboard.history import ClipboardHistory
from internal.clipboard.platform import create_monitor, create_reader, create_writer
from internal.config.config import Config, PeerInfo, load, save
from internal.i18n import T, set_locale
from internal.platform.autostart import disable_autostart, enable_autostart, is_autostart_enabled
from internal.platform.notify import notification_mgr
from internal.protocol.codec import FILE_TRANSFER_MSG_TYPES, encode_message
from internal.security.encryption import (
    EncryptionManager,
    make_password_hash as _make_password_hash,
    verify_password as _verify_password,
)
from internal.security.pairing import (
    CertificateChangedError,
    PairingManager,
    fingerprint_pem as _fingerprint_pem,
)
from internal.sync.file_transfer import FileTransferManager
from internal.sync.manager import SyncManager
from internal.transport.connection import MAX_FRAME_SIZE, TransportManager
from internal.transport.discovery import Discovery
from internal.ui.dashboard import DashboardWindow
from internal.ui.dialogs import show_error, show_info, show_warning
from internal.ui.settings_window import SettingsWindow
from internal.ui.systray import SystrayApp

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Module-level helpers (must be picklable for macOS multiprocessing)
# ═══════════════════════════════════════════════════════════════════════════════


def _mask_file_name(file_name: str) -> str:
    if not file_name or file_name == "?":
        return file_name
    ext = os.path.splitext(file_name)[1]
    return f"*{ext}" if ext else "*"


def _mask_path(path: str) -> str:
    parent = os.path.basename(os.path.dirname(path))
    return f"{parent}/***" if parent else "***"


def _get_log_dir() -> "Path":
    import platform as _p
    from pathlib import Path

    system = _p.system()
    if system == "Windows":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "ClipSync"
    elif system == "Darwin":
        return Path.home() / "Library" / "Logs" / "ClipSync"
    else:
        return Path.home() / ".local" / "share" / "clipsync"


def _get_log_path() -> "Path":
    return _get_log_dir() / "clipsync.log"


def _hide_dock():
    """Hide the app from the macOS Dock, keeping only the menu bar icon."""
    if sys.platform != "darwin":
        return
    try:
        from rubicon.objc import ObjCClass

        NSApp = ObjCClass("NSApplication").sharedApplication()
        NSApp.setActivationPolicy_(2)
        return
    except Exception:
        pass
    try:
        import ctypes
        import ctypes.util

        lib = ctypes.util.find_library("objc")
        if not lib:
            return
        objc = ctypes.cdll.LoadLibrary(lib)
        objc.objc_getClass.argtypes = (ctypes.c_char_p,)
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = (ctypes.c_char_p,)
        objc.sel_registerName.restype = ctypes.c_void_p

        cls = objc.objc_getClass(b"NSApplication")
        sel_shared = objc.sel_registerName(b"sharedApplication")
        sel_policy = objc.sel_registerName(b"setActivationPolicy:")

        proto0 = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        app = proto0(("objc_msgSend", objc))(cls, sel_shared)

        proto1 = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long,
        )
        proto1(("objc_msgSend", objc))(app, sel_policy, 2)
    except Exception:
        pass


def _run_tray(device_name: str, pipe):
    """Run the system tray in a subprocess (macOS only). Must be module-level for multiprocessing."""
    Application.setup_logging()
    _hide_dock()
    child_systray = SystrayApp(
        device_name=device_name,
        on_enable_toggle=lambda v: pipe.send(("toggle_sync", v)),
        on_open_dashboard=lambda: pipe.send(("open_dashboard",)),
        on_open_settings=lambda: pipe.send(("open_settings",)),
        on_export_logs=lambda: pipe.send(("export_logs",)),
        on_quit=lambda: pipe.send(("quit",)),
    )

    def _recv_notifications():
        while True:
            try:
                if pipe.poll(1):
                    msg = pipe.recv()
                    if msg[0] == "show_notification" and child_systray._tray:
                        try:
                            child_systray._tray.notify(msg[2], title=msg[1])
                        except Exception:
                            pass
            except (EOFError, BrokenPipeError, OSError):
                break
            except Exception:
                pass

    threading.Thread(target=_recv_notifications, daemon=True).start()
    child_systray.run()


# ═══════════════════════════════════════════════════════════════════════════════
# Application class
# ═══════════════════════════════════════════════════════════════════════════════


class Application:
    """Central controller for ClipSync lifecycle.

    Lifecycle phases (called in order):
      1. setup_logging()   — static, configures root logger
      2. load_config()
      3. _bootstrap_crypto()
      4. _bootstrap_identity()
      5. _create_services()
      6. _wire_callbacks()
      7. _apply_config()
      8. _create_ui()
      9. _start_services()
     10. _start_threads()
     11. run()             — blocks on root.mainloop(); calls shutdown() on exit
    """

    def __init__(self) -> None:
        # ── Config ──────────────────────────────────────────────────
        self.cfg: Config | None = None

        # ── Services ────────────────────────────────────────────────
        self.content_filter: ContentFilter | None = None
        self.enc_mgr: EncryptionManager | None = None
        self.pairing_mgr: PairingManager | None = None
        self.clipboard_history: ClipboardHistory | None = None
        self.sync_mgr: SyncManager | None = None
        self.transport_mgr: TransportManager | None = None
        self.file_transfer_mgr: FileTransferManager | None = None
        self.discovery: Discovery | None = None

        # ── UI ──────────────────────────────────────────────────────
        self.root: tk.Tk | None = None
        self.systray: SystrayApp | None = None
        self.settings_win: SettingsWindow | None = None
        self.dashboard_win: DashboardWindow | None = None

        # ── Threading ───────────────────────────────────────────────
        self._stop_updater = threading.Event()
        self._shutting_down = False

        # ── Shared mutable state ────────────────────────────────────
        self._discovered_peers: dict[str, dict] = {}
        self._discovered_lock = threading.Lock()
        self._notified_pairings: dict[str, str] = {}

        # ── macOS multiprocessing state ─────────────────────────────
        self._parent_conn = None
        self._tray_proc = None

    # ═══════════════════════════════════════════════════════════════
    # Phase 1: Logging (static)
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def setup_logging() -> None:
        import logging.handlers

        log_dir = _get_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)

        raw = os.environ.get("CLIPSYNC_LOG_LEVEL", "").upper()
        level_map = {"DEBUG": logging.DEBUG, "INFO": logging.INFO,
                     "WARNING": logging.WARNING, "ERROR": logging.ERROR}
        app_level = level_map.get(raw, logging.INFO)

        file_fmt = logging.Formatter(
            "%(asctime)s.%(msecs)03d [%(levelname)-8s] %(threadName)-12s "
            "%(name)s:%(lineno)d  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        console_fmt = logging.Formatter(
            "%(asctime)s [%(levelname)-8s] %(name)-28s  %(message)s",
            datefmt="%H:%M:%S",
        )

        root_logger = logging.getLogger()
        root_logger.setLevel(app_level)

        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "clipsync.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(file_fmt)
        root_logger.addHandler(file_handler)

        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(app_level)
        console_handler.setFormatter(console_fmt)
        root_logger.addHandler(console_handler)

        for noisy in ("zeroconf", "PIL", "cryptography", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    # ═══════════════════════════════════════════════════════════════
    # Phase 2: Config
    # ═══════════════════════════════════════════════════════════════

    def load_config(self) -> None:
        self.cfg = load()
        set_locale(self.cfg.language)
        logger.info("=" * 72)
        logger.info("  ClipSync v1.1.0 — session start  %s",
                    time.strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("  Platform: %s  |  PID: %d", sys.platform, os.getpid())
        logger.info("=" * 72)
        logger.info("ClipSync starting...")
        logger.info("Device: %s (%s)", self.cfg.device_name, self.cfg.device_id)

    # ═══════════════════════════════════════════════════════════════
    # Phase 3: Crypto
    # ═══════════════════════════════════════════════════════════════

    def _bootstrap_crypto(self) -> None:
        cfg = self.cfg

        # ── Auto-start ──────────────────────────────────────────
        if cfg.auto_start and not is_autostart_enabled():
            try:
                enable_autostart()
                logger.info("Auto-start enabled on boot")
            except Exception as e:
                logger.warning("Failed to enable auto-start: %s", e)

        # ── Content filter ──────────────────────────────────────
        self.content_filter = ContentFilter(enabled_categories=cfg.filter_enabled_categories)

        # ── Derive device fingerprint from stored cert ──────────
        device_fingerprint = ""
        if cfg.encryption_enabled and cfg.certificate_pem:
            try:
                device_fingerprint = _fingerprint_pem(cfg.certificate_pem)
            except Exception as exc:
                logger.warning("Failed to derive fingerprint from cert: %s", exc)

        # ── Prompt for encryption password (if hash stored) ─────
        if (cfg.encryption_enabled and cfg.encryption_password_hash
                and not cfg.encryption_password):
            tmp_root = tk.Tk()
            tmp_root.withdraw()
            try:
                entered = simpledialog.askstring(
                    "Encryption Password",
                    "Enter the pre-shared encryption password:",
                    show="*", parent=tmp_root,
                )
            finally:
                tmp_root.destroy()
            if entered and _verify_password(
                entered, device_fingerprint, cfg.encryption_password_hash,
            ):
                cfg.encryption_password = entered
                logger.info("Encryption password verified")
            elif entered:
                logger.warning("Encryption password verification FAILED — wrong password")
                cfg.encryption_password = entered

        logger.info(
            "Encryption config: enabled=%s, password=%s",
            cfg.encryption_enabled,
            "set" if cfg.encryption_password else "not set",
        )

        self.enc_mgr = EncryptionManager(
            device_fingerprint,
            password=cfg.encryption_password if cfg.encryption_enabled else "",
        )

        # ── Decrypt private key if encrypted ────────────────────
        if cfg.encryption_enabled and cfg.private_key_pem:
            pt = self.enc_mgr.decrypt_storage(cfg.private_key_pem)
            if pt is not None:
                if pt != cfg.private_key_pem:
                    logger.info("Private key decrypted from encrypted storage (%d chars)", len(pt))
                cfg.private_key_pem = pt
            else:
                logger.warning(
                    "Private key decryption FAILED — possibly wrong password "
                    "or corrupted data. Trying as plaintext."
                )

    # ═══════════════════════════════════════════════════════════════
    # Phase 4: Identity / Pairing
    # ═══════════════════════════════════════════════════════════════

    def _bootstrap_identity(self) -> None:
        cfg = self.cfg

        self.pairing_mgr = PairingManager(cfg.device_id, cfg.device_name)
        identity = self.pairing_mgr.load_or_create_identity(
            cfg.private_key_pem, cfg.certificate_pem,
        )
        is_new = cfg.private_key_pem != identity.private_key_pem
        if is_new:
            cfg.private_key_pem = identity.private_key_pem
            cfg.certificate_pem = identity.certificate_pem
        logger.info("Certificate fingerprint: %s", identity.fingerprint_short)

        # Re-create EncryptionManager with correct fingerprint if changed
        if identity.fingerprint != self.enc_mgr._fingerprint:
            self.enc_mgr = EncryptionManager(
                identity.fingerprint,
                password=cfg.encryption_password if cfg.encryption_enabled else "",
            )

        # Migrate old plaintext password to verification hash
        if (cfg.encryption_enabled and cfg.encryption_password
                and not cfg.encryption_password_hash):
            cfg.encryption_password_hash = _make_password_hash(
                cfg.encryption_password, identity.fingerprint,
            )
            logger.info("Migrated plaintext encryption password to verification hash")

        # Save new identity
        if is_new:
            self._save_cfg_encrypted()

        # ── Clipboard history ───────────────────────────────────
        self.clipboard_history = ClipboardHistory(
            max_entries=cfg.history_max_entries,
            enc_mgr=self.enc_mgr if cfg.encryption_enabled else None,
        )

        # ── Register known peers ─────────────────────────────────
        self._cert_warnings: list[str] = []
        for peer in cfg.peers.values():
            try:
                self.pairing_mgr.add_peer(
                    peer.device_id, peer.device_name,
                    peer.public_key_pem, peer.paired,
                )
            except CertificateChangedError:
                self._cert_warnings.append(peer.device_name)
                logger.warning("Skipping peer %s: certificate changed", peer.device_name)
            except Exception as e:
                logger.warning("Skipping peer %s: %s", peer.device_name, e)

        # ── Pairing notification callback ───────────────────────
        self.pairing_mgr.set_on_new_pairing(self._on_new_pairing)

    # ═══════════════════════════════════════════════════════════════
    # Phase 5: Services
    # ═══════════════════════════════════════════════════════════════

    def _create_services(self) -> None:
        cfg = self.cfg

        # ── Sync Manager ────────────────────────────────────────
        monitor = create_monitor(poll_interval=cfg.clipboard_poll_interval)
        reader = create_reader()
        writer = create_writer()
        self.sync_mgr = SyncManager(
            cfg.device_id, cfg.device_name,
            reader=reader, writer=writer, monitor=monitor,
            history=self.clipboard_history,
            sync_debounce=cfg.sync_debounce,
        )
        self.sync_mgr.set_enabled(cfg.sync_enabled)

        # ── Transport ───────────────────────────────────────────
        self.transport_mgr = TransportManager(
            cfg.device_id, cfg.device_name, cfg.port, self.pairing_mgr,
            max_reconnect_attempts=cfg.max_reconnect_attempts,
        )
        if cfg.encryption_enabled:
            self.transport_mgr.set_encryption_manager(self.enc_mgr)

        # ── File Transfer ───────────────────────────────────────
        file_receive_dir = cfg.file_receive_dir if cfg.file_receive_dir else None
        self.file_transfer_mgr = FileTransferManager(
            cfg.device_id,
            output_dir=file_receive_dir,
            transfer_timeout=cfg.transfer_timeout,
        )

        # ── Discovery ───────────────────────────────────────────
        self.discovery = Discovery(
            cfg.device_id, cfg.device_name, cfg.port, cfg.service_type,
        )

    # ═══════════════════════════════════════════════════════════════
    # Phase 6: Callback wiring
    # ═══════════════════════════════════════════════════════════════

    def _wire_callbacks(self) -> None:
        # ── Sync → Transport ────────────────────────────────────
        self.sync_mgr.on_send = self._on_local_sync

        # ── Transport → Sync / File Transfer ────────────────────
        self.transport_mgr.set_on_peer_message(self._on_peer_message)

        # ── File transfer callbacks ─────────────────────────────
        self.file_transfer_mgr.set_on_transfer_progress(self._on_transfer_progress)
        self.file_transfer_mgr.set_on_transfer_complete(self._on_transfer_complete)
        self.file_transfer_mgr.set_on_file_received(self._on_file_received)
        self.file_transfer_mgr.set_on_transfer_request(self._on_transfer_request)

        # ── Discovery callbacks ─────────────────────────────────
        self.discovery.set_callbacks(self._on_peer_found, self._on_peer_lost)

        # ── Security alerts ──────────────────────────────────────
        self.transport_mgr.set_on_security_alert(self._on_security_alert)

        # ── Wake recovery ───────────────────────────────────────
        self.transport_mgr.set_on_wake(self.discovery._wake_recovery)

    # ── Callback implementations ──────────────────────────────────

    def _on_local_sync(self, msg) -> None:
        if self.content_filter.is_active and self.content_filter.is_sensitive(msg.content):
            sensitivity = self.content_filter.describe_sensitivity(msg.content)
            logger.info("Filtering sensitive content: %s", sensitivity)
            msg.content = self.content_filter.filter_content(msg.content)
        data = encode_message(msg)
        if len(data) > MAX_FRAME_SIZE:
            size_mb = len(data) / (1024 * 1024)
            logger.warning(
                "Clipboard content too large to sync: %.1f MB (limit: %d MB)",
                size_mb, MAX_FRAME_SIZE // (1024 * 1024),
            )
            notification_mgr.show(
                "Sync Skipped",
                T("sync.oversize", size=size_mb),
            )
            return
        self.transport_mgr.broadcast(data)

    def _on_peer_message(self, msg) -> None:
        msg_type = getattr(msg, "msg_type", "clipboard")
        if msg_type in FILE_TRANSFER_MSG_TYPES:
            raw_payload = getattr(msg, "_raw_payload", {})
            self.file_transfer_mgr.handle_message(
                msg_type, raw_payload, self.transport_mgr.broadcast,
            )
        else:
            self.sync_mgr.handle_remote_message(msg)

    def _on_transfer_progress(self, transfer_id: str, progress: float) -> None:
        logger.debug("File transfer %s: %.0f%%", transfer_id[:8], progress * 100)

    def _on_transfer_complete(self, transfer_id: str, success: bool) -> None:
        logger.info("File transfer %s: %s",
                    transfer_id[:8], "complete" if success else "failed")
        if success:
            notification_mgr.show("File Transfer", T("transfer.send_success"))
        else:
            notification_mgr.show("File Transfer", T("transfer.send_failed"))

    def _on_file_received(self, transfer_id: str, saved_path: str, file_name: str) -> None:
        logger.info("File received: %s -> %s",
                    _mask_file_name(file_name), _mask_path(saved_path))
        notification_mgr.show("File Received", T("transfer.received", name=file_name))

    def _on_transfer_request(self, transfer_id: str, file_name: str, file_size: int,
                             mime_type: str, send_fn) -> None:
        logger.info("File request: %s (%d bytes, %s) -- auto-accepting",
                    file_name, file_size, mime_type)
        self.file_transfer_mgr.accept_transfer(transfer_id, send_fn)

    def _on_peer_found(self, peer_id: str, peer_name: str, address: str, port: int) -> None:
        with self._discovered_lock:
            self._discovered_peers[peer_id] = {
                "name": peer_name, "address": address, "port": port,
            }
        logger.info("Peer discovered: %s (%s) at %s:%d — waiting for pairing",
                    peer_name, peer_id, address, port)

    def _on_peer_lost(self, peer_id: str) -> None:
        with self._discovered_lock:
            self._discovered_peers.pop(peer_id, None)
        self.transport_mgr.disconnect_peer(peer_id)

    def _on_new_pairing(self, peer_id: str, code: str, peer_name: str) -> None:
        prev = self._notified_pairings.get(peer_id)
        if prev == code:
            return
        self._notified_pairings[peer_id] = code
        notification_mgr.show(
            "Pairing Request",
            T("notify.pairing_request", name=peer_name, code=code),
        )
        self.root.after(0, self.open_dashboard)

    # ═══════════════════════════════════════════════════════════════
    # Phase 7: Apply config
    # ═══════════════════════════════════════════════════════════════

    def _apply_config(self) -> None:
        cfg = self.cfg
        notification_mgr.enabled = cfg.notifications_enabled
        if cfg.log_level:
            level = getattr(logging, cfg.log_level.upper(), None)
            if level is not None:
                logging.getLogger().setLevel(level)

    # ═══════════════════════════════════════════════════════════════
    # Phase 8: UI
    # ═══════════════════════════════════════════════════════════════

    def _create_ui(self) -> None:
        from customtkinter import set_appearance_mode

        set_appearance_mode(self.cfg.appearance_mode)

        self.root = tk.Tk()
        _hide_dock()
        self.root.title("ClipSync")
        self.root.geometry("1x1+0+0")
        self.root.withdraw()
        self.root.protocol("WM_DELETE_WINDOW", self.root.withdraw)

        # ── Systray ─────────────────────────────────────────────
        self.systray = SystrayApp(
            device_name=self.cfg.device_name,
            on_enable_toggle=self._on_systray_toggle,
            on_open_dashboard=self.open_dashboard,
            on_open_settings=self.open_settings,
            on_export_logs=self.export_logs,
            on_quit=self.shutdown,
        )

    # ═══════════════════════════════════════════════════════════════
    # Phase 9: Start services
    # ═══════════════════════════════════════════════════════════════

    def _start_services(self) -> None:
        self.sync_mgr.start()
        self.transport_mgr.start_server()
        self.discovery.start()

    # ═══════════════════════════════════════════════════════════════
    # Phase 10: Background threads
    # ═══════════════════════════════════════════════════════════════

    def _start_threads(self) -> None:
        updater = threading.Thread(target=self._update_peers_loop, daemon=True)
        updater.start()

        logger.info("ClipSync is ready. System tray icon should appear.")

        # Auto-open dashboard on startup
        self.root.after(500, self.open_dashboard)

        if sys.platform == "darwin":
            self._start_macos_tray()
        else:
            tray_thread = threading.Thread(target=self.systray.run, daemon=True)
            tray_thread.start()

    def _start_macos_tray(self) -> None:
        import multiprocessing

        multiprocessing.freeze_support()
        parent_conn, child_conn = multiprocessing.Pipe()
        notification_mgr.set_pipe(parent_conn)
        self._tray_proc = multiprocessing.Process(
            target=_run_tray, args=(self.cfg.device_name, child_conn),
            daemon=True,
        )
        self._tray_proc.start()
        self._parent_conn = parent_conn

        def _poll_tray():
            while parent_conn.poll():
                self._handle_tray_msg(parent_conn.recv())
            self.root.after(100, _poll_tray)

        self.root.after(100, _poll_tray)

    def _handle_tray_msg(self, msg: tuple) -> None:
        cmd = msg[0]
        if cmd == "toggle_sync":
            self._on_systray_toggle(msg[1])
        elif cmd == "open_dashboard":
            self.open_dashboard()
        elif cmd == "open_settings":
            self.open_settings()
        elif cmd == "export_logs":
            self.export_logs()
        elif cmd == "quit":
            self.shutdown()

    def _update_peers_loop(self) -> None:
        prev_display: list[str] = []
        prev_connected: set[str] = set()
        cleanup_counter = 0
        while not self._stop_updater.is_set():
            connected_ids = self.transport_mgr.get_connected_peers()
            peer_display = []
            seen = set()
            for pid in connected_ids:
                peers = self.pairing_mgr.get_known_peers()
                found = next((p for p in peers if p.device_id == pid), None)
                name = found.device_name if found else pid
                peer_display.append(f"{name}  (connected)")
                seen.add(pid)
            with self._discovered_lock:
                for pid, info in self._discovered_peers.items():
                    if pid not in seen:
                        peer_display.append(f"{info['name']}  (found)")
            if peer_display != prev_display:
                prev_display = peer_display
                self.systray.set_peers(peer_display)

            connected_set = set(connected_ids)
            for pid in connected_set - prev_connected:
                peers = self.pairing_mgr.get_known_peers()
                found = next((p for p in peers if p.device_id == pid), None)
                name = found.device_name if found else pid[:12]
                notification_mgr.show("Device Connected",
                                      T("notify.device_connected", name=name))
            for pid in prev_connected - connected_set:
                peers = self.pairing_mgr.get_known_peers()
                found = next((p for p in peers if p.device_id == pid), None)
                name = found.device_name if found else pid[:12]
                notification_mgr.show("Device Disconnected",
                                      T("notify.device_disconnected", name=name))
            prev_connected = connected_set

            cleanup_counter += 1
            if cleanup_counter >= 10:
                cleanup_counter = 0
                try:
                    self.file_transfer_mgr.cleanup_stale_transfers()
                except Exception:
                    pass

            self._stop_updater.wait(3)

    # ═══════════════════════════════════════════════════════════════
    # Phase 11: Event loop
    # ═══════════════════════════════════════════════════════════════

    def run(self) -> None:
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    # ═══════════════════════════════════════════════════════════════
    # Shutdown
    # ═══════════════════════════════════════════════════════════════

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._stop_updater.set()
        logger.info("Shutting down...")
        self.sync_mgr.stop()
        self.discovery.stop()
        self.transport_mgr.stop_server()

        for peer in self.pairing_mgr.get_known_peers():
            self.cfg.peers[peer.device_id] = PeerInfo(
                device_id=peer.device_id,
                device_name=peer.device_name,
                public_key_pem=peer.certificate_pem,
                paired=peer.paired,
            )
        self._save_cfg_encrypted()
        if self.root:
            self.root.quit()

    # ═══════════════════════════════════════════════════════════════
    # Config persistence helpers
    # ═══════════════════════════════════════════════════════════════

    def _make_save_enc(self) -> EncryptionManager | None:
        if not self.cfg.encryption_enabled:
            return None
        if self.cfg.encryption_password:
            self.cfg.encryption_password_hash = _make_password_hash(
                self.cfg.encryption_password,
                self.pairing_mgr.get_identity().fingerprint,
            )
        return EncryptionManager(
            self.pairing_mgr.get_identity().fingerprint,
            password=self.cfg.encryption_password,
        )

    def _save_cfg_encrypted(self) -> None:
        save(self.cfg, self._make_save_enc())

    # ═══════════════════════════════════════════════════════════════
    # UI action handlers
    # ═══════════════════════════════════════════════════════════════

    def export_logs(self) -> None:
        self.root.after(0, self._do_export_logs)

    def _do_export_logs(self) -> None:
        log_path = _get_log_path()
        dest = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save Log File As",
            initialfile=f"clipsync_{time.strftime('%Y%m%d_%H%M%S')}.log",
            filetypes=[("Log files", "*.log"), ("Text files", "*.txt"), ("All files", "*.*")],
            defaultextension=".log",
        )
        if not dest:
            return
        try:
            shutil.copy2(log_path, dest)
            show_info(self.root, "Exported", f"Log saved to:\n{dest}")
            logger.info("Log exported to %s", dest)
        except FileNotFoundError:
            show_warning(
                self.root, "Not Found",
                f"No log file found at:\n{log_path}\n\n"
                "ClipSync may not have been running long enough to generate logs.",
            )
        except PermissionError:
            show_error(self.root, "Error",
                       f"Permission denied writing to:\n{dest}")
            logger.error("Permission denied exporting log to %s", dest)
        except OSError as e:
            show_error(self.root, "Error", f"Failed to export log:\n{e}")
            logger.error("Failed to export log: %s", e)

    def send_file(self) -> None:
        self.root.after(0, self._do_send_file)

    def send_folder(self) -> None:
        self.root.after(0, self._do_send_folder)

    def _do_send_file(self) -> None:
        file_paths = filedialog.askopenfilenames(
            parent=self.root,
            title="Select Files to Send",
            filetypes=[("All files", "*")],
        )
        if not file_paths:
            return
        if len(file_paths) == 1:
            self._send_single_path(file_paths[0])
        else:
            self._send_as_zip(file_paths)

    def _do_send_folder(self) -> None:
        folder = filedialog.askdirectory(
            parent=self.root,
            title="Select Folder to Send",
        )
        if not folder:
            return
        self._send_as_zip([folder])

    def _send_single_path(self, file_path: str) -> None:
        """Send a single file directly (no zipping)."""
        try:
            transfer_id = self.file_transfer_mgr.send_file(
                file_path, self.transport_mgr.broadcast,
            )
            logger.info("File transfer initiated: %s", transfer_id[:8])
            notification_mgr.show("File Transfer",
                                  T("transfer.sending_file", name=os.path.basename(file_path)))
        except FileNotFoundError:
            show_error(self.root, "Error", f"File not found:\n{file_path}")
        except PermissionError:
            show_error(self.root, "Error",
                       f"Permission denied reading:\n{file_path}")
        except OSError as e:
            show_error(self.root, "Error", f"Failed to send file:\n{e}")
            logger.error("Failed to send file: %s", e)

    def _send_as_zip(self, paths: list[str]) -> None:
        """Zip one or more files/folders into a temp archive and send it.

        Shows a progress dialog so the user can track the archiving and
        cancel if needed.  Zipping runs in a background thread to keep
        the UI responsive.
        """
        import tempfile, zipfile
        import customtkinter as _ctk
        from pathlib import Path

        def _safe_remove(path):
            try:
                if path and path.exists():
                    path.unlink()
            except OSError:
                pass

        # ── Count files for progress tracking ──────────────────────
        total_files = 0
        for path in paths:
            p = Path(path)
            if p.is_file():
                total_files += 1
            elif p.is_dir():
                total_files += sum(1 for fp in p.rglob("*") if fp.is_file())
        if total_files == 0:
            show_error(self.root, "Error", "No files found to send")
            return

        cancel_event = threading.Event()
        names = [os.path.basename(p.rstrip(os.sep).rstrip("/")) for p in paths]
        base = names[0] if len(names) == 1 else f"files-{len(names)}"
        zip_name = f"{base}.zip"

        # ── Progress dialog ────────────────────────────────────────
        # Compute geometry before creating the window
        dw, dh = 420, 170
        if self.root.winfo_viewable():
            rw, rh = self.root.winfo_width(), self.root.winfo_height()
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            x = rx + (rw - dw) // 2
            y = ry + (rh - dh) // 2
        else:
            x = (self.root.winfo_screenwidth() - dw) // 2
            y = (self.root.winfo_screenheight() - dh) // 2

        import platform as _platform
        _is_macos = _platform.system() == "Darwin"

        if _is_macos:
            import tkinter as _tk
            from tkinter import ttk as _ttk

            _mac_dark = self.root._get_appearance_mode() == "dark"
            _bg = "#2E2E2E" if _mac_dark else "#F0F0F0"
            _fg = "#FFFFFF" if _mac_dark else "#000000"
            _sub = "#AAAAAA" if _mac_dark else "#666666"

            dlg = _tk.Toplevel(self.root)
            dlg.title(T("transfer.creating_archive"))
            dlg.resizable(False, False)
            dlg.configure(bg=_bg)
            dlg.geometry(f"{dw}x{dh}+{x}+{y}")
            dlg.protocol("WM_DELETE_WINDOW", lambda: cancel_event.set())

            _tk.Label(dlg, text=T("transfer.zipping", name=zip_name),
                      font=("Helvetica", 13, "bold"),
                      fg=_fg, bg=_bg).pack(pady=(20, 10))

            progress_bar = _ttk.Progressbar(dlg, length=370, mode="determinate")
            progress_bar.pack(pady=(0, 8))

            status_var = tk.StringVar(value=T("transfer.preparing"))
            _tk.Label(dlg, textvariable=status_var,
                      font=("Helvetica", 11),
                      fg=_sub, bg=_bg).pack()

            _tk.Button(dlg, text=T("ui.cancel"),
                       command=lambda: cancel_event.set()).pack(pady=(12, 16))

            def _set_progress(val):
                progress_bar["value"] = val * 100
            def _set_status(text):
                status_var.set(text)

            dlg.update()
            dlg.transient(self.root)
            dlg.grab_set()
        else:
            dlg = _ctk.CTkToplevel(self.root)
            dlg.title(T("transfer.creating_archive"))
            dlg.resizable(False, False)
            dlg.geometry(f"{dw}x{dh}+{x}+{y}")
            dlg.protocol("WM_DELETE_WINDOW", lambda: cancel_event.set())

            body = _ctk.CTkFrame(dlg, fg_color="transparent")
            body.pack(fill="both", expand=True, padx=24, pady=(20, 12))

            _ctk.CTkLabel(
                body, text=T("transfer.zipping", name=zip_name),
                font=_ctk.CTkFont(size=13, weight="bold"),
            ).pack(anchor="w", pady=(0, 12))

            progress_bar = _ctk.CTkProgressBar(body, width=370, height=14)
            progress_bar.pack(fill="x", pady=(0, 8))
            progress_bar.set(0)

            status_var = tk.StringVar(value=T("transfer.preparing"))
            _ctk.CTkLabel(
                body, textvariable=status_var,
                font=_ctk.CTkFont(size=11),
                text_color=("gray50", "gray60"),
            ).pack(anchor="w")

            _ctk.CTkButton(
                dlg, text=T("ui.cancel"), width=90, height=30,
                fg_color="transparent", border_width=1,
                text_color=("gray40", "gray60"),
                border_color=("gray60", "gray50"),
                hover_color=("gray85", "gray25"),
                font=_ctk.CTkFont(size=12),
                command=lambda: cancel_event.set(),
            ).pack(pady=(0, 16))

            def _set_progress(val):
                progress_bar.set(val)
            def _set_status(text):
                status_var.set(text)

            dlg.update()
            dlg.transient(self.root)
            dlg.grab_set()

        # Keep a reference to prevent premature garbage collection on macOS
        self._active_dialog = dlg

        # ── Background worker ──────────────────────────────────────
        def _worker():
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                    tmp_path = Path(tmp.name)

                with zipfile.ZipFile(str(tmp_path), "w", zipfile.ZIP_DEFLATED) as zf:
                    file_count = 0
                    for path in paths:
                        if cancel_event.is_set():
                            break
                        p = Path(path)
                        if p.is_file():
                            zf.write(str(p), p.name)
                            file_count += 1
                            frac = file_count / total_files
                            cur = file_count  # capture for closure
                            self.root.after(0, lambda f=frac, c=cur: (
                                _set_progress(f),
                                _set_status(
                                    T("transfer.zipping_progress", current=c, total=total_files)),
                            ))
                        elif p.is_dir():
                            for fpath in sorted(p.rglob("*")):
                                if cancel_event.is_set():
                                    break
                                if fpath.is_file():
                                    arcname = str(fpath.relative_to(p.parent))
                                    zf.write(str(fpath), arcname)
                                    file_count += 1
                                    frac = file_count / total_files
                                    cur = file_count
                                    self.root.after(0, lambda f=frac, c=cur: (
                                        _set_progress(f),
                                        _set_status(
                                            T("transfer.zipping_progress", current=c, total=total_files)),
                                    ))

                if cancel_event.is_set():
                    _safe_remove(tmp_path)
                    self.root.after(0, dlg.destroy)
                    return

                transfer_id = self.file_transfer_mgr.send_file(
                    str(tmp_path), self.transport_mgr.broadcast,
                )
                logger.info("Zip transfer initiated: %s (%d files)", transfer_id[:8], total_files)
                self.root.after(0, lambda: (
                    dlg.destroy(),
                    notification_mgr.show(
                        "File Transfer", T("transfer.sending_file", name=zip_name)),
                ))
            except FileNotFoundError:
                _safe_remove(tmp_path)
                self.root.after(0, lambda: (dlg.destroy(), show_error(
                    self.root, "Error", "File not found")))
            except PermissionError:
                _safe_remove(tmp_path)
                self.root.after(0, lambda: (dlg.destroy(), show_error(
                    self.root, "Error", "Permission denied")))
            except OSError as e:
                logger.error("Failed to zip and send: %s", e)
                _safe_remove(tmp_path)
                self.root.after(0, lambda e=e: (dlg.destroy(), show_error(
                    self.root, "Error", f"Failed to create archive:\n{e}")))

        threading.Thread(target=_worker, daemon=True, name="zip-sender").start()

    def open_settings(self) -> None:
        self.root.after(0, self._create_settings_window)

    def _create_settings_window(self) -> None:
        if self.settings_win is not None:
            self.settings_win.show()
            return

        def _on_closed():
            self.settings_win = None

        self.settings_win = SettingsWindow(
            root=self.root,
            get_config=self._get_cfg,
            save_config=self._save_cfg_and_peers,
            on_closed=_on_closed,
            on_export_logs=self.export_logs,
            get_filter_categories=lambda: self.content_filter.enabled_categories,
            set_filter_categories=lambda cats: (
                setattr(self.content_filter, 'enabled_categories', cats),
                setattr(self.cfg, 'filter_enabled_categories', cats),
            ),
            get_log_text=lambda: _get_log_path().read_text(encoding="utf-8")
            if _get_log_path().exists() else "No log file yet.",
        )
        self.settings_win.show()

    def open_dashboard(self) -> None:
        self.root.after(0, self._create_dashboard_window)

    def _create_dashboard_window(self) -> None:
        if self.dashboard_win is not None:
            self.dashboard_win.show()
            return

        self.dashboard_win = DashboardWindow(
            root=self.root,
            get_config=self._get_cfg,
            save_config=self._save_cfg_and_peers,
            get_peers=self._get_peers,
            get_sync_enabled=lambda: self.cfg.sync_enabled,
            set_sync_enabled=lambda v: (
                self.sync_mgr.set_enabled(v), self.systray.set_syncing(v)
            ),
            get_discovering=lambda: self.discovery.is_browsing,
            get_visible=lambda: self.discovery.is_advertising,
            on_toggle_discovery=self._on_toggle_discovery,
            on_toggle_visibility=self._on_toggle_visibility,
            on_open_settings=self.open_settings,
            on_send_file=self.send_file,
            on_send_folder=self.send_folder,
            on_toggle_autostart=lambda enabled: (
                enable_autostart() if enabled else disable_autostart()
            ),
            get_transfers=lambda: self.file_transfer_mgr.get_transfers(),
            on_cancel_transfer=lambda tid: self.file_transfer_mgr.cancel_transfer(
                tid, self.transport_mgr.broadcast,
            ),
            on_pause_transfer=lambda tid: self.file_transfer_mgr.pause_transfer(
                tid, self.transport_mgr.broadcast,
            ),
            on_resume_transfer=lambda tid: self.file_transfer_mgr.resume_transfer(
                tid, self.transport_mgr.broadcast,
            ),
            get_pending_pairings=self._get_pending,
            on_pair=self._on_pair,
            on_unpair=self._on_unpair,
            on_connect_peer=self._on_connect,
            on_remove_peer=self._on_remove,
            get_history=self._get_history,
            search_history=self._search_history,
            copy_from_history=self._copy_from_history,
            clear_history=self._clear_history,
            delete_history_item=self._delete_history_item,
            get_transfer_history=lambda: self.file_transfer_mgr.get_history(),
            on_speed_test=lambda: self.file_transfer_mgr.start_speed_test(
                self.transport_mgr.broadcast,
            ),
            get_speed_test_result=lambda: self.file_transfer_mgr.get_speed_test(),
            clear_transfer_history=self._clear_transfer_history,
            delete_transfer_history_item=lambda entry: (
                self.file_transfer_mgr.delete_history_item(entry)
                and self.dashboard_win._refresh_transfers()
            ),
            on_open_file=self._open_file,
            on_open_folder=self._open_folder,
            on_retry_transfer=self._retry_file_transfer,
            on_edit_note=self._on_edit_note,
        )
        self.dashboard_win.show()

        # Show startup cert warnings
        if self._cert_warnings:
            names = ", ".join(self._cert_warnings[:3])
            if len(self._cert_warnings) > 3:
                names += f" +{len(self._cert_warnings) - 3}"
            self.root.after(500, lambda: show_error(
                self.dashboard_win._window,
                T("security.cert_changed_title"),
                T("security.cert_changed_startup", names=names),
            ))

    # ═══════════════════════════════════════════════════════════════
    # Dashboard / Settings callbacks
    # ═══════════════════════════════════════════════════════════════

    def _get_cfg(self) -> Config:
        return self.cfg

    def _save_cfg_and_peers(self) -> None:
        for peer in self.pairing_mgr.get_known_peers():
            existing = self.cfg.peers.get(peer.device_id)
            self.cfg.peers[peer.device_id] = PeerInfo(
                device_id=peer.device_id,
                device_name=peer.device_name,
                public_key_pem=peer.certificate_pem,
                paired=peer.paired,
                notes=existing.notes if existing else "",
            )
        self._save_cfg_encrypted()

    def _get_peers(self) -> list[tuple]:
        known = []
        discovered = []
        seen_ids: set[str] = set()
        known_names: set[str] = set()
        connected_ids = set(self.transport_mgr.get_connected_peers())
        resolved = self.transport_mgr.get_resolved_hashes()

        rev_resolved: dict[str, set] = {}
        for h_id, r_id in resolved.items():
            rev_resolved.setdefault(r_id, set()).add(h_id)

        for p in self.pairing_mgr.get_known_peers():
            connected = p.device_id in connected_ids
            if not connected:
                for h_id in rev_resolved.get(p.device_id, []):
                    if h_id in connected_ids:
                        connected = True
                        break
            notes = ""
            if p.device_id in self.cfg.peers:
                notes = self.cfg.peers[p.device_id].notes
            known.append((p.device_id, p.device_name, p.paired, connected, notes))
            seen_ids.add(p.device_id)
            known_names.add(p.device_name.lower())

        for hash_id, real_id in resolved.items():
            if real_id in seen_ids:
                seen_ids.add(hash_id)

        def _name_matches_known(disc_name: str) -> bool:
            dl = disc_name.lower()
            for kn in known_names:
                if dl == kn or dl.startswith(kn) or kn.startswith(dl):
                    return True
            return False

        with self._discovered_lock:
            for peer_id, info in list(self._discovered_peers.items()):
                if peer_id in seen_ids:
                    continue
                if _name_matches_known(info["name"]):
                    continue
                discovered.append((peer_id, info["name"], False, False, ""))

        return known + discovered

    def _get_pending(self) -> list:
        return self.pairing_mgr.get_pending_pairings()

    def _on_pair(self, peer_id: str, code: str) -> bool:
        result = self.pairing_mgr.confirm_pairing(peer_id, code)
        if result:
            self._save_cfg_and_peers()
            if peer_id not in self.transport_mgr.get_connected_peers():
                self._on_connect(peer_id)
        return result

    def _on_unpair(self, peer_id: str) -> None:
        self.pairing_mgr.unpair_peer(peer_id)
        self.pairing_mgr.reject_pairing(peer_id)
        self.transport_mgr.forget_peer(peer_id)
        if peer_id in self.cfg.peers:
            self.cfg.peers[peer_id].paired = False
        self._save_cfg_encrypted()

    def _on_connect(self, peer_id: str) -> None:
        info = None
        with self._discovered_lock:
            info = self._discovered_peers.get(peer_id)
        if not info:
            hashed = Discovery._hash_device_id(peer_id)
            with self._discovered_lock:
                info = self._discovered_peers.get(hashed)
        if not info:
            resolved = self.transport_mgr.get_resolved_hashes()
            hash_id = None
            for h_id, r_id in resolved.items():
                if r_id == peer_id:
                    hash_id = h_id
                    break
            if hash_id:
                with self._discovered_lock:
                    info = self._discovered_peers.get(hash_id)
            if not info:
                peers = self.pairing_mgr.get_known_peers()
                target = next(
                    (p for p in peers if p.device_id == peer_id), None,
                )
                if target:
                    with self._discovered_lock:
                        for pid, pinfo in self._discovered_peers.items():
                            pname = pinfo["name"].lower()
                            tname = target.device_name.lower()
                            if pname == tname or tname.startswith(pname):
                                info = pinfo
                                break
        if info:
            logger.info("User initiated pairing with %s (peer_id=%s)",
                        info["name"], peer_id[:12])
            self.transport_mgr.connect_to_peer(
                peer_id, info["name"], info["address"], info["port"],
            )
        else:
            logger.warning("Cannot connect: peer %s not in discovered list",
                          peer_id[:12])

    def _on_remove(self, peer_id: str) -> None:
        self.pairing_mgr.remove_peer(peer_id)
        self.transport_mgr.disconnect_peer(peer_id)
        with self._discovered_lock:
            self._discovered_peers.pop(peer_id, None)
        self.cfg.peers.pop(peer_id, None)
        self._save_cfg_encrypted()

    def _on_edit_note(self, peer_id: str, note: str) -> None:
        if peer_id in self.cfg.peers:
            self.cfg.peers[peer_id].notes = note
            self._save_cfg_encrypted()

    # ═══════════════════════════════════════════════════════════════
    # History helpers
    # ═══════════════════════════════════════════════════════════════

    def _get_history(self) -> list:
        return self.clipboard_history.get_all()

    def _search_history(self, query: str) -> list:
        return self.clipboard_history.search(query)

    def _copy_from_history(self, index: int) -> bool:
        entry = self.clipboard_history.get(index)
        if entry is None or "types" not in entry:
            return False
        types: dict = {}
        _type_map = {
            "TEXT": _CT.TEXT, "HTML": _CT.HTML,
            "IMAGE": _CT.IMAGE_PNG, "IMAGE_EMF": _CT.IMAGE_EMF,
            "RTF": _CT.RTF,
        }
        for key, b64_data in entry["types"].items():
            ct = _type_map.get(key)
            if ct is not None:
                types[ct] = _b64.b64decode(b64_data)
        if types:
            content = ClipboardContent(types=types)
            # Clear dedup state so the monitor event from this write
            # is not suppressed — the restored content will sync to peers.
            self.sync_mgr.reset_dedup_for_restore()
            create_writer().write(content)
            return True
        return False

    def _clear_history(self) -> None:
        self.clipboard_history.clear()

    def _delete_history_item(self, index: int) -> bool:
        return self.clipboard_history.delete(index)

    def _clear_transfer_history(self) -> None:
        self.file_transfer_mgr.clear_history()

    def _open_file(self, file_path: str) -> None:
        """Open a file with the default OS application."""
        import subprocess, sys as _sys
        resolved = os.path.abspath(file_path) if file_path else ""
        if not os.path.isfile(resolved):
            show_error(self.root, T("ui.file_not_found_title"),
                       T("ui.file_not_found_msg", path=file_path))
            return
        try:
            if _sys.platform == "win32":
                os.startfile(resolved)
            elif _sys.platform == "darwin":
                subprocess.run(["open", resolved], check=True)
            else:
                subprocess.run(["xdg-open", resolved], check=True)
        except Exception as e:
            logger.error("Failed to open file %s: %s", resolved, e)
            show_error(self.root, T("ui.open_failed_title"),
                       T("ui.open_failed_msg", path=resolved))

    def _open_folder(self, file_path: str) -> None:
        """Open the containing folder in the OS file manager."""
        import subprocess, sys as _sys
        resolved = os.path.abspath(file_path) if file_path else ""
        if os.path.isfile(resolved):
            folder = os.path.dirname(resolved)
        elif os.path.isdir(resolved):
            folder = resolved
        else:
            show_error(self.root, T("ui.file_not_found_title"),
                       T("ui.file_not_found_msg", path=file_path))
            return
        if not os.path.isdir(folder):
            show_error(self.root, T("ui.folder_not_found_title"),
                       T("ui.folder_not_found_msg", path=folder))
            return
        try:
            if _sys.platform == "win32":
                # Use explorer.exe directly instead of os.startfile()
                # to avoid any file-association misrouting that could
                # launch a new instance of the app.
                subprocess.Popen(["explorer", folder])
            elif _sys.platform == "darwin":
                subprocess.run(["open", folder], check=True)
            else:
                subprocess.run(["xdg-open", folder], check=True)
        except Exception as e:
            logger.error("Failed to open folder %s: %s", folder, e)
            show_error(self.root, T("ui.open_failed_title"),
                       T("ui.open_failed_msg", path=folder))

    def _retry_file_transfer(self, file_path: str) -> None:
        """Retry sending a file that previously failed."""
        try:
            transfer_id = self.file_transfer_mgr.send_file(
                file_path, self.transport_mgr.broadcast,
            )
            logger.info("Retried file transfer: %s (%s)", file_path, transfer_id[:8])
            notification_mgr.show("File Transfer",
                                  T("transfer.sending_file", name=os.path.basename(file_path)))
        except OSError as e:
            logger.error("Failed to retry sending file %s: %s", file_path, e)

    # ═══════════════════════════════════════════════════════════════
    # Discovery / visibility toggles
    # ═══════════════════════════════════════════════════════════════

    def _on_toggle_discovery(self, enabled: bool) -> None:
        if enabled:
            self.discovery.start_browsing()
        else:
            self.discovery.stop_browsing()

    def _on_toggle_visibility(self, enabled: bool) -> None:
        if enabled:
            self.discovery.start_advertising()
        else:
            self.discovery.stop_advertising()

    def _on_security_alert(self, peer_name: str, expected: str, received: str) -> None:
        """Show a security alert dialog when a peer's certificate changes."""
        self.root.after(0, lambda: show_error(
            self.root if not self.dashboard_win else self.dashboard_win._window,
            T("security.cert_changed_title"),
            T("security.cert_changed_message", name=peer_name),
        ))

    # ═══════════════════════════════════════════════════════════════
    # Systray toggle
    # ═══════════════════════════════════════════════════════════════

    def _on_systray_toggle(self, enabled: bool) -> None:
        self.sync_mgr.set_enabled(enabled)
        self.cfg.sync_enabled = enabled
        self._save_cfg_encrypted()
        self.systray.set_syncing(enabled)
        logger.info("Sync %s", "enabled" if enabled else "paused")


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    Application.setup_logging()
    app = Application()
    app.load_config()
    app._bootstrap_crypto()
    app._bootstrap_identity()
    app._create_services()
    app._wire_callbacks()
    app._apply_config()
    app._create_ui()
    app._start_services()
    app._start_threads()
    app.run()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
