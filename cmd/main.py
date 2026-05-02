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
from tkinter import filedialog, simpledialog

# Add project root to Python path so 'internal' package can be found
# (not needed in a PyInstaller-frozen bundle)
if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from internal.clipboard.filter import ContentFilter
from internal.clipboard.history import ClipboardHistory
from internal.config.config import Config, PeerInfo, load, save
from internal.platform.autostart import enable_autostart, disable_autostart, is_autostart_enabled
from internal.platform.notify import notification_mgr
from internal.protocol.codec import FILE_TRANSFER_MSG_TYPES, encode_message
from internal.security.encryption import EncryptionManager
from internal.security.pairing import PairingManager
from internal.sync.file_transfer import FileTransferManager
from internal.sync.manager import SyncManager
from internal.transport.connection import TransportManager
from internal.transport.discovery import Discovery
from internal.ui.dashboard import DashboardWindow
from internal.ui.dialogs import show_error, show_info, show_warning
from internal.ui.settings_window import SettingsWindow
from internal.ui.systray import SystrayApp

logger = logging.getLogger(__name__)


def _mask_file_name(file_name: str) -> str:
    """Return a privacy-safe file name: only the extension is preserved."""
    if not file_name or file_name == "?":
        return file_name
    ext = os.path.splitext(file_name)[1]
    return f"*{ext}" if ext else "*"


def _mask_path(path: str) -> str:
    """Return a privacy-safe path: only the parent directory name is shown."""
    parent = os.path.basename(os.path.dirname(path))
    return f"{parent}/***" if parent else "***"


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

    # ── Auto-start ─────────────────────────────────────────────
    if cfg.auto_start and not is_autostart_enabled():
        try:
            enable_autostart()
            logger.info("Auto-start enabled on boot")
        except Exception as e:
            logger.warning("Failed to enable auto-start: %s", e)

    # ── Content filter ─────────────────────────────────────────
    content_filter = ContentFilter(enabled_categories=cfg.filter_enabled_categories)

    # ── Encryption password (prompt if needed, before identity load) ─
    # The certificate is public and stored in plaintext — derive the
    # fingerprint from it to bootstrap decryption of the private key.
    from internal.security.encryption import verify_password as _verify_password
    from internal.security.pairing import fingerprint_pem as _fingerprint_pem

    _device_fingerprint = ""
    if cfg.encryption_enabled and cfg.certificate_pem:
        try:
            _device_fingerprint = _fingerprint_pem(cfg.certificate_pem)
        except Exception as exc:
            logger.warning("Failed to derive fingerprint from cert: %s", exc)

    # If a password hash was persisted, prompt for the password.
    # The plaintext password is never stored on disk — only a
    # PBKDF2 verification token that we check here.
    if (cfg.encryption_enabled and cfg.encryption_password_hash
            and not cfg.encryption_password):
        _tmp_root = tk.Tk()
        _tmp_root.withdraw()
        try:
            _entered = simpledialog.askstring(
                "Encryption Password",
                "Enter the pre-shared encryption password:",
                show="*", parent=_tmp_root,
            )
        finally:
            _tmp_root.destroy()
        if _entered and _verify_password(
            _entered, _device_fingerprint, cfg.encryption_password_hash,
        ):
            cfg.encryption_password = _entered
            logger.info("Encryption password verified")
        elif _entered:
            logger.warning("Encryption password verification FAILED — wrong password")
            cfg.encryption_password = _entered  # try to decrypt anyway (will likely fail)

    logger.info(
        "Encryption config: enabled=%s, password=%s",
        cfg.encryption_enabled,
        "set" if cfg.encryption_password else "not set",
    )

    enc_mgr = EncryptionManager(
        _device_fingerprint,
        password=cfg.encryption_password if cfg.encryption_enabled else "",
    )

    # Decrypt private key before loading identity
    if cfg.encryption_enabled and cfg.private_key_pem:
        pt = enc_mgr.decrypt_storage(cfg.private_key_pem)
        if pt is not None:
            if pt != cfg.private_key_pem:
                logger.info("Private key decrypted from encrypted storage (%d chars)",
                          len(pt))
            cfg.private_key_pem = pt
        else:
            logger.warning(
                "Private key decryption FAILED — possibly wrong password "
                "or corrupted data. Trying as plaintext."
            )

    # ── Pairing / Identity ──────────────────────────────────────
    pairing_mgr = PairingManager(cfg.device_id, cfg.device_name)
    identity = pairing_mgr.load_or_create_identity(
        cfg.private_key_pem, cfg.certificate_pem,
    )
    _is_new_identity = cfg.private_key_pem != identity.private_key_pem
    if _is_new_identity:
        cfg.private_key_pem = identity.private_key_pem
        cfg.certificate_pem = identity.certificate_pem
    logger.info("Certificate fingerprint: %s", identity.fingerprint_short)

    # Re-create EncryptionManager with correct fingerprint if it changed
    if identity.fingerprint != _device_fingerprint:
        enc_mgr = EncryptionManager(
            identity.fingerprint,
            password=cfg.encryption_password if cfg.encryption_enabled else "",
        )

    # Migrate old plaintext password to verification hash
    from internal.security.encryption import make_password_hash as _make_password_hash
    _password_just_migrated = False
    if (cfg.encryption_enabled and cfg.encryption_password
            and not cfg.encryption_password_hash):
        cfg.encryption_password_hash = _make_password_hash(
            cfg.encryption_password, identity.fingerprint,
        )
        _password_just_migrated = True
        logger.info("Migrated plaintext encryption password to verification hash")

    # Save new identity with encryption (now that enc_mgr has correct fingerprint)
    if _is_new_identity:
        save(cfg, enc_mgr if cfg.encryption_enabled else None)

    # ── Clipboard history ──────────────────────────────────────
    clipboard_history = ClipboardHistory(
        max_entries=cfg.history_max_entries,
        enc_mgr=enc_mgr if cfg.encryption_enabled else None,
    )

    def _make_save_enc():
        """Create an EncryptionManager for saving, using current cfg values.

        Also updates the persisted password hash so the plaintext password
        is never written to disk.
        """
        if not cfg.encryption_enabled:
            return None
        if cfg.encryption_password:
            cfg.encryption_password_hash = _make_password_hash(
                cfg.encryption_password,
                pairing_mgr.get_identity().fingerprint,
            )
        return EncryptionManager(
            pairing_mgr.get_identity().fingerprint,
            password=cfg.encryption_password,
        )

    def _save_cfg_encrypted():
        """Save config with encryption if enabled."""
        save(cfg, _make_save_enc())

    for peer in cfg.peers.values():
        try:
            pairing_mgr.add_peer(
                peer.device_id, peer.device_name,
                peer.public_key_pem, peer.paired,
            )
        except Exception as e:
            logger.warning("Skipping peer %s: %s", peer.device_name, e)

    # Callback for new pairing requests: notify the user and auto-open
    # the dashboard so the pairing code is visible on BOTH devices.
    # Track notified codes to avoid spamming on connection storms.
    _notified_pairings: dict[str, str] = {}  # peer_id -> last notified code

    def _on_new_pairing(peer_id, code, peer_name):
        prev = _notified_pairings.get(peer_id)
        if prev == code:
            return  # same code, already notified
        _notified_pairings[peer_id] = code
        notification_mgr.show(
            "Pairing Request",
            f"Device \"{peer_name}\" wants to pair — code: {code}",
        )
        root.after(0, open_dashboard)

    pairing_mgr.set_on_new_pairing(_on_new_pairing)

    # ── Sync Manager ────────────────────────────────────────────
    from internal.clipboard.platform import create_monitor, create_reader, create_writer
    monitor = create_monitor(poll_interval=cfg.clipboard_poll_interval)
    reader = create_reader()
    writer = create_writer()
    sync_mgr = SyncManager(cfg.device_id, cfg.device_name,
                           reader=reader, writer=writer, monitor=monitor,
                           history=clipboard_history,
                           sync_debounce=cfg.sync_debounce)
    sync_mgr.set_enabled(cfg.sync_enabled)

    # ── Transport ───────────────────────────────────────────────
    transport_mgr = TransportManager(
        cfg.device_id, cfg.device_name, cfg.port, pairing_mgr,
        max_reconnect_attempts=cfg.max_reconnect_attempts,
    )
    if cfg.encryption_enabled:
        transport_mgr.set_encryption_manager(enc_mgr)

    def on_local_sync(msg):
        # Apply content filter if enabled
        if content_filter.is_active and content_filter.is_sensitive(msg.content):
            sensitivity = content_filter.describe_sensitivity(msg.content)
            logger.info("Filtering sensitive content: %s", sensitivity)
            msg.content = content_filter.filter_content(msg.content)
        data = encode_message(msg)
        transport_mgr.broadcast(data)

    sync_mgr.on_send = on_local_sync

    # ── File Transfer ─────────────────────────────────────────
    file_receive_dir = cfg.file_receive_dir if cfg.file_receive_dir else None
    file_transfer_mgr = FileTransferManager(
        cfg.device_id,
        output_dir=file_receive_dir,
        transfer_timeout=cfg.transfer_timeout,
    )

    def on_transfer_progress(transfer_id: str, progress: float):
        logger.debug("File transfer %s: %.0f%%", transfer_id[:8], progress * 100)

    def on_transfer_complete(transfer_id: str, success: bool):
        logger.info("File transfer %s: %s",
                    transfer_id[:8], "complete" if success else "failed")
        if success:
            notification_mgr.show("File Transfer", "File sent successfully")
        else:
            notification_mgr.show("File Transfer", "File transfer failed")

    def on_file_received(transfer_id: str, saved_path: str, file_name: str):
        logger.info("File received: %s -> %s", _mask_file_name(file_name), _mask_path(saved_path))
        notification_mgr.show("File Received", f"Received: {file_name}")

    def on_transfer_request(transfer_id: str, file_name: str, file_size: int,
                            mime_type: str, send_fn) -> None:
        # Auto-accept files from paired peers for now
        logger.info("File request: %s (%d bytes, %s) -- auto-accepting",
                    file_name, file_size, mime_type)
        file_transfer_mgr.accept_transfer(transfer_id, send_fn)

    file_transfer_mgr.set_on_transfer_progress(on_transfer_progress)
    file_transfer_mgr.set_on_transfer_complete(on_transfer_complete)
    file_transfer_mgr.set_on_file_received(on_file_received)
    file_transfer_mgr.set_on_transfer_request(on_transfer_request)

    # ── Message routing ───────────────────────────────────────
    def on_peer_message(msg):
        msg_type = getattr(msg, "msg_type", "clipboard")
        if msg_type in FILE_TRANSFER_MSG_TYPES:
            raw_payload = getattr(msg, "_raw_payload", {})
            file_transfer_mgr.handle_message(
                msg_type, raw_payload, transport_mgr.broadcast,
            )
        else:
            sync_mgr.handle_remote_message(msg)

    transport_mgr.set_on_peer_message(on_peer_message)

    # ── Discovery ───────────────────────────────────────────────
    discovery = Discovery(cfg.device_id, cfg.device_name, cfg.port, cfg.service_type)

    # Discovered (but not yet connected) peers: peer_id -> {name, address, port}
    _discovered_peers: dict[str, dict] = {}
    _discovered_lock = threading.Lock()

    def on_peer_found(peer_id, peer_name, address, port):
        with _discovered_lock:
            _discovered_peers[peer_id] = {
                "name": peer_name, "address": address, "port": port,
            }
        logger.info("Peer discovered: %s (%s) at %s:%d — waiting for pairing",
                    peer_name, peer_id, address, port)

    def on_peer_lost(peer_id):
        with _discovered_lock:
            _discovered_peers.pop(peer_id, None)
        transport_mgr.disconnect_peer(peer_id)

    discovery.set_callbacks(on_peer_found, on_peer_lost)

    # ── Start services ──────────────────────────────────────────
    sync_mgr.start()
    transport_mgr.start_server()
    transport_mgr.set_on_wake(discovery._wake_recovery)
    discovery.start()

    # ── Apply advanced config ──────────────────────────────────
    notification_mgr.enabled = cfg.notifications_enabled
    if cfg.log_level:
        _level = getattr(logging, cfg.log_level.upper(), None)
        if _level is not None:
            logging.getLogger().setLevel(_level)

    # ── Tkinter root (hidden, for settings window) ──────────────
    root = tk.Tk()
    root.title("CopyBoard")  # avoid showing the default "tk" title on macOS
    root.geometry("1x1+0+0")  # minimal size so file-dialog parent is near-invisible
    root.withdraw()  # hide the root window
    # macOS: prevent closing the briefly-visible root window (e.g. during
    # file dialogs) from destroying the root and quitting the whole app.
    root.protocol("WM_DELETE_WINDOW", root.withdraw)

    # ── Export logs ─────────────────────────────────────────────
    def on_export_logs():
        root.after(0, _do_export_logs)

    def _do_export_logs():
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
            show_info(root, "Exported", f"Log saved to:\n{dest}")
            logger.info("Log exported to %s", dest)
        except FileNotFoundError:
            show_warning(
                root,
                "Not Found",
                f"No log file found at:\n{log_path}\n\n"
                "CopyBoard may not have been running long enough to generate logs.",
            )
        except Exception as e:
            show_error(root, "Error", f"Failed to export log:\n{e}")
            logger.error("Failed to export log: %s", e)

    # ── Send file ──────────────────────────────────────────────
    def on_send_file():
        root.after(0, _do_send_file)

    def _do_send_file():
        file_path = filedialog.askopenfilename(
            parent=root,
            title="Select File to Send",
            filetypes=[
                ("All files", "*"),
            ],
        )
        if not file_path:
            return
        try:
            transfer_id = file_transfer_mgr.send_file(file_path, transport_mgr.broadcast)
            logger.info("File transfer initiated: %s", transfer_id[:8])
            show_info(
                root,
                "File Transfer",
                f"Sending: {os.path.basename(file_path)}",
            )
        except FileNotFoundError:
            show_error(root, "Error", f"File not found:\n{file_path}")

    # ── Window factories ─────────────────────────────────

    # Shared callbacks used by both windows
    def get_cfg():
        return cfg

    def save_cfg():
        for peer in pairing_mgr.get_known_peers():
            if peer.device_id not in cfg.peers:
                cfg.peers[peer.device_id] = PeerInfo(
                    device_id=peer.device_id,
                    device_name=peer.device_name,
                    public_key_pem=peer.certificate_pem,
                    paired=peer.paired,
                )
        save(cfg, _make_save_enc())

    def get_peers():
        known = []
        discovered = []
        seen_ids = set()
        known_names = set()
        connected_ids = set(transport_mgr.get_connected_peers())
        resolved = transport_mgr.get_resolved_hashes()  # hash_id -> real_id
        # Reverse mapping: real_id -> set of hash_ids
        rev_resolved: dict[str, set] = {}
        for h_id, r_id in resolved.items():
            rev_resolved.setdefault(r_id, set()).add(h_id)
        for p in pairing_mgr.get_known_peers():
            connected = p.device_id in connected_ids
            if not connected:
                # Also check if any hash ID mapped to this peer is connected
                for h_id in rev_resolved.get(p.device_id, []):
                    if h_id in connected_ids:
                        connected = True
                        break
            known.append((p.device_id, p.device_name, p.paired, connected))
            seen_ids.add(p.device_id)
            known_names.add(p.device_name.lower())
        # Resolved hash IDs: skip discovered entries whose real ID already listed
        for hash_id, real_id in resolved.items():
            if real_id in seen_ids:
                seen_ids.add(hash_id)
        # Build a set of known name fragments for fuzzy matching
        # (mDNS may truncate names to 8 chars, so check prefixes)
        def _name_matches_known(disc_name: str) -> bool:
            dl = disc_name.lower()
            for kn in known_names:
                if dl == kn or dl.startswith(kn) or kn.startswith(dl):
                    return True
            return False
        with _discovered_lock:
            for peer_id, info in list(_discovered_peers.items()):
                if peer_id in seen_ids:
                    continue
                if _name_matches_known(info["name"]):
                    continue
                discovered.append((peer_id, info["name"], False, False))
        # Known devices first, then discovered
        return known + discovered

    def get_pending():
        return pairing_mgr.get_pending_pairings()  # list of (peer_id, code, peer_name)

    def on_pair(peer_id, code):
        result = pairing_mgr.confirm_pairing(peer_id, code)
        if result:
            save_cfg()
            if peer_id not in transport_mgr.get_connected_peers():
                on_connect(peer_id)
        return result

    def on_unpair(peer_id):
        pairing_mgr.unpair_peer(peer_id)
        pairing_mgr.reject_pairing(peer_id)
        transport_mgr.disconnect_peer(peer_id)
        if peer_id in cfg.peers:
            cfg.peers[peer_id].paired = False
        _save_cfg_encrypted()

    def on_connect(peer_id):
        info = None
        with _discovered_lock:
            info = _discovered_peers.get(peer_id)
        if not info:
            # peer_id may be a real device_id from the pairing manager,
            # while _discovered_peers is keyed by hashed mDNS IDs.
            # Compute the hash directly and look up.
            hashed = Discovery._hash_device_id(peer_id)
            with _discovered_lock:
                info = _discovered_peers.get(hashed)
        if not info:
            # Try reverse lookup via the hash→real mapping.
            resolved = transport_mgr.get_resolved_hashes()
            hash_id = None
            for h_id, r_id in resolved.items():
                if r_id == peer_id:
                    hash_id = h_id
                    break
            if hash_id:
                with _discovered_lock:
                    info = _discovered_peers.get(hash_id)
            if not info:
                # Fallback: match by device name (mDNS truncates to 8 chars)
                peers = pairing_mgr.get_known_peers()
                target = next((p for p in peers if p.device_id == peer_id), None)
                if target:
                    with _discovered_lock:
                        for pid, pinfo in _discovered_peers.items():
                            pname = pinfo["name"].lower()
                            tname = target.device_name.lower()
                            if pname == tname or tname.startswith(pname):
                                info = pinfo
                                break
        if info:
            logger.info("User initiated pairing with %s (peer_id=%s)", info['name'], peer_id[:12])
            transport_mgr.connect_to_peer(
                peer_id, info["name"], info["address"], info["port"],
            )
        else:
            logger.warning("Cannot connect: peer %s not in discovered list", peer_id[:12])

    def on_remove(peer_id):
        pairing_mgr.remove_peer(peer_id)
        transport_mgr.disconnect_peer(peer_id)
        with _discovered_lock:
            _discovered_peers.pop(peer_id, None)
        cfg.peers.pop(peer_id, None)
        _save_cfg_encrypted()

    def _get_history():
        return clipboard_history.get_all()

    def _search_history(query: str):
        return clipboard_history.search(query)

    def _copy_from_history(index: int):
        entry = clipboard_history.get(index)
        if entry is None or "types" not in entry:
            return False
        import base64 as _b64
        from internal.clipboard.format import ClipboardContent, ContentType as _CT
        types: dict = {}
        _type_map = {"TEXT": _CT.TEXT, "HTML": _CT.HTML, "IMAGE": _CT.IMAGE_PNG, "RTF": _CT.RTF}
        for key, b64_data in entry["types"].items():
            ct = _type_map.get(key)
            if ct is not None:
                types[ct] = _b64.b64decode(b64_data)
        if types:
            content = ClipboardContent(types=types)
            writer.write(content)
            return True
        return False

    def _clear_history():
        clipboard_history.clear()

    def _delete_history_item(index: int):
        return clipboard_history.delete(index)

    def _clear_transfer_history():
        file_transfer_mgr.clear_history()

    # ── Settings window ──────────────────────────────────────────
    settings_win: SettingsWindow | None = None

    def _create_settings_window():
        nonlocal settings_win
        if settings_win is not None:
            settings_win.show()
            return

        def _on_settings_closed():
            nonlocal settings_win
            settings_win = None

        settings_win = SettingsWindow(
            root=root,
            get_config=get_cfg,
            save_config=save_cfg,
            on_closed=_on_settings_closed,
            on_export_logs=on_export_logs,
            get_filter_categories=lambda: content_filter.enabled_categories,
            set_filter_categories=lambda cats: (
                setattr(content_filter, 'enabled_categories', cats),
                setattr(cfg, 'filter_enabled_categories', cats),
            ),
            get_log_text=lambda: _get_log_path().read_text(encoding="utf-8") if _get_log_path().exists() else "No log file yet.",
        )
        settings_win.show()

    def open_settings():
        root.after(0, _create_settings_window)

    # ── Dashboard window ─────────────────────────────────────────
    dashboard_win: DashboardWindow | None = None

    def _create_dashboard_window():
        nonlocal dashboard_win
        if dashboard_win is not None:
            dashboard_win.show()
            return

        dashboard_win = DashboardWindow(
            root=root,
            get_config=get_cfg,
            save_config=save_cfg,
            get_peers=get_peers,
            get_sync_enabled=lambda: cfg.sync_enabled,
            set_sync_enabled=lambda v: (sync_mgr.set_enabled(v), systray.set_syncing(v)),
            on_open_settings=open_settings,
            on_send_file=on_send_file,
            on_toggle_autostart=lambda enabled: (
                enable_autostart() if enabled else disable_autostart()
            ),
            get_transfers=lambda: file_transfer_mgr.get_transfers(),
            on_cancel_transfer=lambda tid: file_transfer_mgr.cancel_transfer(tid, transport_mgr.broadcast),
            get_pending_pairings=get_pending,
            on_pair=on_pair,
            on_unpair=on_unpair,
            on_connect_peer=on_connect,
            on_remove_peer=on_remove,
            get_history=_get_history,
            search_history=_search_history,
            copy_from_history=_copy_from_history,
            clear_history=_clear_history,
            delete_history_item=_delete_history_item,
            get_transfer_history=lambda: file_transfer_mgr.get_history(),
            on_speed_test=lambda: file_transfer_mgr.start_speed_test(transport_mgr.broadcast),
            get_speed_test_result=lambda: file_transfer_mgr.get_speed_test(),
            clear_transfer_history=_clear_transfer_history,
        )
        dashboard_win.show()

    def open_dashboard():
        root.after(0, _create_dashboard_window)

    # ── Systray ─────────────────────────────────────────────────
    def on_enable_toggle(enabled: bool):
        sync_mgr.set_enabled(enabled)
        cfg.sync_enabled = enabled
        _save_cfg_encrypted()
        systray.set_syncing(enabled)
        logger.info("Sync %s", "enabled" if enabled else "paused")

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
        _save_cfg_encrypted()
        root.quit()

    systray = SystrayApp(
        device_name=cfg.device_name,
        on_enable_toggle=on_enable_toggle,
        on_open_dashboard=open_dashboard,
        on_open_settings=open_settings,
        on_export_logs=on_export_logs,
        on_quit=on_quit,
    )

    _stop_updater = threading.Event()

    def update_peers_loop():
        prev_display: list[str] = []
        prev_connected: set[str] = set()
        prev_pending: list[tuple] = []
        cleanup_counter = 0
        while not _stop_updater.is_set():
            connected_ids = transport_mgr.get_connected_peers()
            peer_display = []
            seen = set()
            for pid in connected_ids:
                peers = pairing_mgr.get_known_peers()
                found = next((p for p in peers if p.device_id == pid), None)
                name = found.device_name if found else pid
                peer_display.append(f"{name}  (connected)")
                seen.add(pid)
            # Also show discovered but not connected peers
            with _discovered_lock:
                for pid, info in _discovered_peers.items():
                    if pid not in seen:
                        peer_display.append(f"{info['name']}  (found)")
            if peer_display != prev_display:
                prev_display = peer_display
                systray.set_peers(peer_display)

            # Notify on connect/disconnect
            connected_set = set(connected_ids)
            for pid in connected_set - prev_connected:
                peers = pairing_mgr.get_known_peers()
                found = next((p for p in peers if p.device_id == pid), None)
                name = found.device_name if found else pid[:12]
                notification_mgr.show("Device Connected", f"{name} is now connected")
            for pid in prev_connected - connected_set:
                peers = pairing_mgr.get_known_peers()
                found = next((p for p in peers if p.device_id == pid), None)
                name = found.device_name if found else pid[:12]
                notification_mgr.show("Device Disconnected", f"{name} has disconnected")
            prev_connected = connected_set

            pending = get_pending()
            prev_pending = pending

            # Periodically clean up stale file transfers (~every 30s)
            cleanup_counter += 1
            if cleanup_counter >= 10:
                cleanup_counter = 0
                try:
                    file_transfer_mgr.cleanup_stale_transfers()
                except Exception:
                    pass

            _stop_updater.wait(3)

    updater = threading.Thread(target=update_peers_loop, daemon=True)
    updater.start()

    logger.info("CopyBoard is ready. System tray icon should appear.")

    # Auto-open the dashboard on startup
    root.after(500, open_dashboard)

    if sys.platform == "darwin":
        # macOS: run pystray in a subprocess to avoid Apple Silicon GIL crash
        # (pystray #138 — run_detached fails on M-series chips)
        import multiprocessing

        # freeze_support must be called early for PyInstaller-frozen apps
        multiprocessing.freeze_support()

        parent_conn, child_conn = multiprocessing.Pipe()
        # Route main-process notifications through the pipe to the tray
        # subprocess, which owns the pystray Icon reference.
        notification_mgr.set_pipe(parent_conn)
        tray_proc = multiprocessing.Process(
            target=_run_tray, args=(cfg.device_name, child_conn),
            daemon=True,
        )
        tray_proc.start()

        def _handle_tray_msg(msg):
            cmd = msg[0]
            if cmd == "toggle_sync":
                on_enable_toggle(msg[1])
            elif cmd == "open_dashboard":
                open_dashboard()
            elif cmd == "open_settings":
                open_settings()
            elif cmd == "export_logs":
                on_export_logs()
            elif cmd == "quit":
                on_quit()

        def _poll_tray():
            while parent_conn.poll():
                _handle_tray_msg(parent_conn.recv())
            root.after(100, _poll_tray)

        root.after(100, _poll_tray)
    else:
        tray_thread = threading.Thread(target=systray.run, daemon=True)
        tray_thread.start()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        on_quit()


def _run_tray(device_name: str, pipe):
    """Run the system tray in a subprocess (macOS only).

    Must be at module level so it is picklable for multiprocessing with 'spawn'.
    """
    setup_logging()
    child_systray = SystrayApp(
        device_name=device_name,
        on_enable_toggle=lambda v: pipe.send(("toggle_sync", v)),
        on_open_dashboard=lambda: pipe.send(("open_dashboard",)),
        on_open_settings=lambda: pipe.send(("open_settings",)),
        on_export_logs=lambda: pipe.send(("export_logs",)),
        on_quit=lambda: pipe.send(("quit",)),
    )

    # Thread to receive notification requests from the main process.
    # On macOS the main process has no pystray Icon reference; it sends
    # notification requests through the pipe instead.
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

    import threading as _th
    _notif_thread = _th.Thread(target=_recv_notifications, daemon=True)
    _notif_thread.start()

    child_systray.run()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
