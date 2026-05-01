"""TLS-encrypted TCP connection management for peer-to-peer sync."""

import logging
import os
import socket
import ssl
import struct
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import Encoding

from internal.protocol.codec import decode_message
from internal.security.pairing import CertificateChangedError, PairingManager

logger = logging.getLogger(__name__)

MAX_FRAME_SIZE = 10 * 1024 * 1024  # 10 MB
FRAME_HEADER_SIZE = 4
DATA_TIMEOUT = 30.0  # socket read timeout
MAX_RECONNECT_ATTEMPTS = 10
MAX_RECONNECT_BACKOFF = 30


class PeerConnection:
    """Represents a TLS connection to a single peer."""

    def __init__(self, device_id: str, device_name: str, sock: socket.socket):
        self.device_id = device_id
        self.device_name = device_name
        self._sock = sock
        self._sock.settimeout(DATA_TIMEOUT)
        self._send_lock = threading.Lock()
        self._recv_thread: threading.Thread | None = None
        self._running = False
        self._on_message: Callable | None = None
        self._on_disconnect: Callable | None = None

    def set_on_message(self, callback: Callable):
        self._on_message = callback

    def set_on_disconnect(self, callback: Callable):
        self._on_disconnect = callback

    def start(self):
        self._running = True
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()

    def stop(self):
        self._running = False
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self._sock.close()
        except Exception:
            pass

    def send(self, data: bytes) -> bool:
        """Send a frame. Returns False if the send failed."""
        try:
            frame = struct.pack(">I", len(data)) + data
            with self._send_lock:
                self._sock.sendall(frame)
            return True
        except Exception as e:
            logger.warning("Send to %s failed: %s", self.device_name, e)
            return False

    def health_check(self) -> bool:
        """Check if the underlying TCP connection is still alive.

        Uses SO_ERROR to detect broken connections without consuming data.
        Returns False if the socket is in an error state.
        """
        try:
            error = self._sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            return error == 0
        except Exception:
            return False

    def _recv_loop(self):
        while self._running:
            try:
                header = self._recv_exact(FRAME_HEADER_SIZE)
                if header is None:
                    break
                frame_len = struct.unpack(">I", header)[0]
                if frame_len == 0 or frame_len > MAX_FRAME_SIZE:
                    logger.warning("Invalid frame size from %s: %d", self.device_name, frame_len)
                    break

                payload = self._recv_exact(frame_len)
                if payload is None:
                    break

                msg = decode_message(payload)
                if msg and self._on_message:
                    self._on_message(msg)

            except (ConnectionError, OSError) as e:
                logger.debug("Connection to %s closed: %s", self.device_name, e)
                break
            except Exception as e:
                logger.warning("Error receiving from %s: %s", self.device_name, e)
                break

        logger.info("Disconnected from %s", self.device_name)
        self._running = False
        if self._on_disconnect:
            self._on_disconnect(self.device_id)

    def _recv_exact(self, n: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self._sock.recv(n - len(buf))
                if not chunk:
                    return None
                buf.extend(chunk)
            except socket.timeout:
                if not self._running:
                    return None
                continue
            except Exception:
                return None
        return bytes(buf)


class TransportManager:
    """Manages peer connections — server and client side."""

    def __init__(
        self,
        device_id: str,
        device_name: str,
        port: int,
        pairing_mgr: PairingManager,
        max_reconnect_attempts: int = MAX_RECONNECT_ATTEMPTS,
    ):
        self._device_id = device_id
        self._device_name = device_name
        self._port = port
        self._pairing_mgr = pairing_mgr
        self._server_sock: socket.socket | None = None
        self._server_thread: threading.Thread | None = None
        self._health_thread: threading.Thread | None = None
        self._peers: dict[str, PeerConnection] = {}
        self._running = False
        self._on_peer_message: Callable | None = None
        self._on_wake: Callable | None = None
        self._lock = threading.Lock()
        self._last_health_tick = 0.0
        self._peer_addresses: dict[str, tuple[str, str, int]] = {}
        self._reconnect_attempts: dict[str, int] = {}
        self._reconnect_timers: dict[str, threading.Timer] = {}
        self._max_reconnect_attempts = max_reconnect_attempts

    def set_on_peer_message(self, callback: Callable):
        self._on_peer_message = callback

    def set_on_wake(self, callback: Callable):
        """Set a callback invoked when sleep/wake is detected.

        The callback receives no arguments. Use it to re-register
        mDNS or perform other post-wake recovery.
        """
        self._on_wake = callback

    @staticmethod
    def _secure_scratch_dir() -> Path:
        """Per-user scratch directory for temporary key material.

        Uses the platform config directory (not system /tmp) so files are
        never world-readable and survive only as long as the app runs.
        Stale files from previous crashes are cleaned on next startup.
        """
        import platform
        system = platform.system()
        if system == "Windows":
            base = os.environ.get("APPDATA", str(Path.home()))
            scratch = Path(base) / "CopyBoard" / ".scratch"
        elif system == "Darwin":
            scratch = Path.home() / "Library" / "Application Support" / "CopyBoard" / ".scratch"
        else:
            scratch = Path.home() / ".config" / "copyboard" / ".scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        # On Unix, restrict permissions so only the owner can read
        if system != "Windows":
            try:
                scratch.chmod(0o700)
            except Exception:
                pass
        return scratch

    @staticmethod
    def _cleanup_stale_scratch():
        """Remove any leftover key files from a previous crash."""
        try:
            scratch = TransportManager._secure_scratch_dir()
            for f in scratch.glob("*.pem"):
                try:
                    f.unlink()
                    logger.debug("Cleaned up stale temp file: %s", f.name)
                except OSError:
                    pass
        except Exception:
            pass

    def _build_ssl_context(self, server_side: bool = True,
                           verify_peer_id: str | None = None) -> ssl.SSLContext:
        """Build an SSL context from in-memory identity and optional peer cert.

        Writes key material to a secure per-user scratch directory (NOT system /tmp).
        Files are cleaned up immediately after loading into the SSL context.

        server_side: True for accept(), False for connect().  Must not be derived from
                     verify_peer_id — an unpaired client still needs a CLIENT context.
        verify_peer_id: if set, require and pin this peer's certificate.
        """
        identity = self._pairing_mgr.get_identity()
        scratch = self._secure_scratch_dir()

        # Unique suffix prevents races between concurrent connections
        uid = uuid.uuid4().hex[:8]
        key_path = scratch / f"identity_key_{uid}.pem"
        cert_path = scratch / f"identity_cert_{uid}.pem"
        ca_path = scratch / f"peer_cert_{uid}.pem" if verify_peer_id else None

        try:
            # Write identity files to secure scratch dir
            key_path.write_text(identity.private_key_pem, encoding="ascii")
            cert_path.write_text(identity.certificate_pem, encoding="ascii")

            if server_side:
                ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            else:
                ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)

            ssl_context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
            ssl_context.check_hostname = False
            ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3

            if verify_peer_id:
                ssl_context.verify_mode = ssl.CERT_REQUIRED
                peer_cert = self._pairing_mgr.get_peer_certificate(verify_peer_id)
                if peer_cert:
                    ca_path.write_text(peer_cert, encoding="ascii")
                    ssl_context.load_verify_locations(cafile=str(ca_path))
            elif server_side:
                # Request but don't require client cert so we can extract peer identity
                ssl_context.verify_mode = ssl.CERT_OPTIONAL
            else:
                ssl_context.verify_mode = ssl.CERT_NONE

            return ssl_context

        finally:
            # Immediately clean up — key material loaded into SSL context memory
            for p in [key_path, cert_path, ca_path]:
                if p and p.exists():
                    try:
                        p.unlink()
                    except OSError:
                        pass

    def start_server(self):
        self._cleanup_stale_scratch()
        ssl_context = self._build_ssl_context(server_side=True)

        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind(("0.0.0.0", self._port))
        self._server_sock.listen(5)
        self._server_sock.settimeout(1.0)

        self._running = True
        self._last_health_tick = time.monotonic()
        self._health_thread = threading.Thread(
            target=self._health_check_loop, daemon=True,
        )
        self._health_thread.start()
        self._server_thread = threading.Thread(
            target=self._accept_loop, args=(ssl_context,), daemon=True,
        )
        self._server_thread.start()
        logger.info("TCP server listening on port %d", self._port)

    def stop_server(self):
        self._running = False
        # Unblock accept() by connecting to our own port
        if self._server_sock:
            try:
                unblocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    unblocker.connect(("127.0.0.1", self._port))
                except Exception:
                    pass
                unblocker.close()
            except Exception:
                pass
        # Disconnect all peers (outside lock to avoid holding it during I/O)
        with self._lock:
            peers = list(self._peers.values())
            self._peers.clear()
        for conn in peers:
            conn.stop()
        with self._lock:
            timers = list(self._reconnect_timers.values())
            self._reconnect_timers.clear()
        for timer in timers:
            timer.cancel()
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass

    def connect_to_peer(self, peer_id: str, peer_name: str, address: str, port: int):
        with self._lock:
            if peer_id in self._peers:
                return  # already connected
            self._peer_addresses[peer_id] = (peer_name, address, port)

        def _connect():
            sock = None
            try:
                sock = socket.create_connection((address, port), timeout=10)

                verify_id = peer_id if self._pairing_mgr.is_peer_paired(peer_id) else None
                ssl_context = self._build_ssl_context(
                    server_side=False, verify_peer_id=verify_id)

                ssl_sock = ssl_context.wrap_socket(sock, server_hostname=peer_id)

                # Extract real device_id from peer certificate CN.
                # Discovery may pass a hashed ID for privacy; the cert
                # CN is the authoritative device_id for pairing/storage.
                real_peer_id = peer_id
                peer_cert_der = ssl_sock.getpeercert(binary_form=True)
                if peer_cert_der:
                    peer_cert = x509.load_der_x509_certificate(peer_cert_der)
                    peer_cert_pem = peer_cert.public_bytes(Encoding.PEM).decode()

                    try:
                        cn_attrs = peer_cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
                        if cn_attrs:
                            real_peer_id = cn_attrs[0].value
                    except Exception:
                        pass

                    was_paired = self._pairing_mgr.is_peer_paired(real_peer_id)
                    self._pairing_mgr.add_peer(
                        real_peer_id, peer_name, peer_cert_pem,
                        paired=was_paired,
                    )
                    if not was_paired:
                        try:
                            shared_code = self._pairing_mgr.generate_shared_pairing_code(real_peer_id)
                            logger.info(
                                "Pairing code for %s: %s — verify this code on both devices",
                                peer_name, shared_code,
                            )
                        except Exception as e:
                            logger.debug("Could not generate shared pairing code: %s", e)

                conn = PeerConnection(real_peer_id, peer_name, ssl_sock)
                conn.set_on_message(self._on_peer_message)
                conn.set_on_disconnect(self._on_peer_disconnected)
                conn.start()

                with self._lock:
                    if not self._running:
                        conn.stop()
                        return
                    # If discovery used a hashed ID, also clean up under that key
                    if real_peer_id != peer_id:
                        old_hash = self._peers.pop(peer_id, None)
                        if old_hash:
                            old_hash.stop()
                        # Re-key address tracking under the real ID
                        addr_info = self._peer_addresses.pop(peer_id, None)
                        if addr_info:
                            self._peer_addresses[real_peer_id] = addr_info
                    old = self._peers.pop(real_peer_id, None)
                    if old:
                        old.stop()
                    self._peers[real_peer_id] = conn

                self._reconnect_attempts.pop(peer_id, None)
                self._reconnect_attempts.pop(real_peer_id, None)
                logger.info("Connected to peer %s [%s] (%s:%d)", peer_name, real_peer_id, address, port)

            except CertificateChangedError:
                logger.error(
                    "SECURITY: Certificate for %s has changed — possible MITM attack! "
                    "Connection rejected.", peer_name,
                )
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
            except Exception as e:
                logger.warning("Failed to connect to %s: %s", peer_name, e)
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
                self._schedule_reconnect(peer_id)

        threading.Thread(target=_connect, daemon=True).start()

    def broadcast(self, data: bytes):
        with self._lock:
            peers = list(self._peers.values())
        for conn in peers:
            conn.send(data)

    def disconnect_peer(self, peer_id: str):
        with self._lock:
            conn = self._peers.pop(peer_id, None)
        if conn:
            conn.stop()

    def get_connected_peers(self) -> list[str]:
        with self._lock:
            return list(self._peers.keys())

    def _on_peer_disconnected(self, peer_id: str):
        with self._lock:
            if peer_id in self._peers:
                del self._peers[peer_id]
        self._schedule_reconnect(peer_id)

    def _schedule_reconnect(self, peer_id: str):
        with self._lock:
            if peer_id not in self._peer_addresses:
                return
            if not self._running:
                return
            attempts = self._reconnect_attempts.get(peer_id, 0)
            if attempts >= self._max_reconnect_attempts:
                saved = self._peer_addresses.pop(peer_id, None)
                self._reconnect_attempts.pop(peer_id, None)
                if saved:
                    logger.warning(
                        "Gave up reconnecting to %s (%s:%d) after %d attempts",
                        peer_id, saved[1], saved[2], self._max_reconnect_attempts,
                    )
                return
            delay = min(2 ** attempts, MAX_RECONNECT_BACKOFF)
            self._reconnect_attempts[peer_id] = attempts + 1
        timer = threading.Timer(delay, self._try_reconnect, args=(peer_id,))
        timer.daemon = True
        with self._lock:
            old = self._reconnect_timers.pop(peer_id, None)
        if old:
            old.cancel()
        with self._lock:
            self._reconnect_timers[peer_id] = timer
        timer.start()

    def _try_reconnect(self, peer_id: str):
        with self._lock:
            if peer_id in self._peers:
                self._reconnect_attempts.pop(peer_id, None)
                return
            if not self._running:
                return
            saved = self._peer_addresses.get(peer_id)
            if not saved:
                return
            peer_name, address, port = saved
        self.connect_to_peer(peer_id, peer_name, address, port)

    def _health_check_loop(self):
        """Periodically check connection health and detect sleep/wake events."""
        while self._running:
            time.sleep(15)
            if not self._running:
                break

            now = time.monotonic()
            gap = now - self._last_health_tick
            self._last_health_tick = now

            if gap > 60:
                logger.info(
                    "Sleep/wake detected (gap=%.0fs), recovering connections", gap,
                )
                self._handle_wake()
                if self._on_wake:
                    try:
                        self._on_wake()
                    except Exception as e:
                        logger.debug("on_wake callback error: %s", e)
            else:
                with self._lock:
                    peers = list(self._peers.items())
                for peer_id, conn in peers:
                    if not conn.health_check():
                        logger.warning(
                            "Health check failed for %s, disconnecting", peer_id,
                        )
                        conn.stop()

    def _handle_wake(self):
        """Disconnect stale connections and reconnect to previously known peers."""
        with self._lock:
            stale_peers = list(self._peers.values())
            self._peers.clear()
            saved_addresses = dict(self._peer_addresses)
            # Cancel stale reconnect timers so they don't race with
            # the wake-initiated reconnections below.
            for timer in self._reconnect_timers.values():
                timer.cancel()
            self._reconnect_timers.clear()
            self._reconnect_attempts.clear()
        for conn in stale_peers:
            try:
                conn.stop()
            except Exception:
                pass
        for peer_id, (name, address, port) in saved_addresses.items():
            logger.info("Reconnecting to %s after wake", name)
            self.connect_to_peer(peer_id, name, address, port)

    def _accept_loop(self, ssl_context: ssl.SSLContext):
        while self._running:
            client_sock = None
            try:
                client_sock, addr = self._server_sock.accept()
                client_sock.settimeout(15)  # TLS handshake timeout
                try:
                    ssl_sock = ssl_context.wrap_socket(client_sock, server_side=True)
                except ssl.SSLError:
                    client_sock.close()
                    continue

                peer_id = ""
                peer_name = ""
                peer_cert_der = ssl_sock.getpeercert(binary_form=True)

                if peer_cert_der:
                    peer_cert = x509.load_der_x509_certificate(peer_cert_der)
                    peer_cert_pem = peer_cert.public_bytes(Encoding.PEM).decode()

                    try:
                        cn_attrs = peer_cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
                        if cn_attrs:
                            peer_id = cn_attrs[0].value
                    except Exception:
                        pass

                    try:
                        ou_attrs = peer_cert.subject.get_attributes_for_oid(NameOID.ORGANIZATIONAL_UNIT_NAME)
                        if ou_attrs:
                            peer_name = ou_attrs[0].value
                    except Exception:
                        pass

                    was_paired = self._pairing_mgr.is_peer_paired(peer_id)
                    self._pairing_mgr.add_peer(
                        peer_id, peer_name,
                        peer_cert_pem,
                        paired=was_paired,
                    )
                    if not was_paired and peer_id:
                        try:
                            shared_code = self._pairing_mgr.generate_shared_pairing_code(peer_id)
                            logger.info(
                                "Pairing code for %s: %s — verify this code on both devices",
                                peer_name or peer_id, shared_code,
                            )
                        except Exception as e:
                            logger.debug("Could not generate shared pairing code: %s", e)

                display_id = peer_id or "unknown"
                conn = PeerConnection(display_id, peer_name or str(addr), ssl_sock)
                conn.set_on_message(self._on_peer_message)
                conn.set_on_disconnect(self._on_peer_disconnected)
                conn.start()

                with self._lock:
                    if not self._running:
                        # Server was stopped during TLS handshake — clean up
                        conn.stop()
                        return
                    if peer_id:
                        old = self._peers.pop(peer_id, None)
                        if old:
                            old.stop()
                        self._peers[peer_id] = conn
                    else:
                        # Track anonymous connections so they can be cleaned up
                        anon_key = f"__anon__{addr[0]}:{addr[1]}"
                        self._peers[anon_key] = conn

                logger.info("Accepted connection from %s (peer_id=%s)", addr, peer_id or "N/A")

            except socket.timeout:
                continue
            except CertificateChangedError:
                logger.error(
                    "SECURITY: Incoming connection presented changed certificate — "
                    "possible MITM attack! Connection rejected.",
                )
                if client_sock:
                    try:
                        client_sock.close()
                    except Exception:
                        pass
            except Exception as e:
                if self._running:
                    logger.debug("Accept error: %s", e)
                if client_sock:
                    try:
                        client_sock.close()
                    except Exception:
                        pass

