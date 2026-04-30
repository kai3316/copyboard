"""TLS-encrypted TCP connection management for peer-to-peer sync."""

import logging
import os
import socket
import ssl
import struct
import threading
from pathlib import Path
from typing import Callable

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import Encoding

from internal.protocol.codec import decode_message
from internal.security.pairing import PairingManager

logger = logging.getLogger(__name__)

MAX_FRAME_SIZE = 10 * 1024 * 1024  # 10 MB
FRAME_HEADER_SIZE = 4
DATA_TIMEOUT = 30.0  # socket read timeout


class PeerConnection:
    """Represents a TLS connection to a single peer."""

    def __init__(self, device_id: str, device_name: str, sock: socket.socket):
        self.device_id = device_id
        self.device_name = device_name
        self._sock = sock
        self._sock.settimeout(DATA_TIMEOUT)
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
            self._sock.sendall(frame)
            return True
        except Exception as e:
            logger.debug("Send to %s failed: %s", self.device_name, e)
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
        data = b""
        while len(data) < n:
            try:
                chunk = self._sock.recv(n - len(data))
                if not chunk:
                    return None
                data += chunk
            except socket.timeout:
                if not self._running:
                    return None
                continue
            except Exception:
                return None
        return data


class TransportManager:
    """Manages peer connections — server and client side."""

    def __init__(
        self,
        device_id: str,
        device_name: str,
        port: int,
        pairing_mgr: PairingManager,
    ):
        self._device_id = device_id
        self._device_name = device_name
        self._port = port
        self._pairing_mgr = pairing_mgr
        self._server_sock: socket.socket | None = None
        self._server_thread: threading.Thread | None = None
        self._peers: dict[str, PeerConnection] = {}
        self._running = False
        self._on_peer_message: Callable | None = None
        self._lock = threading.Lock()

    def set_on_peer_message(self, callback: Callable):
        self._on_peer_message = callback

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

        key_path = scratch / "identity_key.pem"
        cert_path = scratch / "identity_cert.pem"
        ca_path = scratch / "peer_cert.pem" if verify_peer_id else None

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

            if verify_peer_id:
                ssl_context.verify_mode = ssl.CERT_REQUIRED
                peer_cert = self._pairing_mgr.get_peer_certificate(verify_peer_id)
                if peer_cert:
                    ca_path.write_text(peer_cert, encoding="ascii")
                    ssl_context.load_verify_locations(cafile=str(ca_path))
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
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass

    def connect_to_peer(self, peer_id: str, peer_name: str, address: str, port: int):
        with self._lock:
            if peer_id in self._peers:
                return  # already connected

        def _connect():
            sock = None
            try:
                sock = socket.create_connection((address, port), timeout=10)

                verify_id = peer_id if self._pairing_mgr.is_peer_paired(peer_id) else None
                ssl_context = self._build_ssl_context(
                    server_side=False, verify_peer_id=verify_id)

                ssl_sock = ssl_context.wrap_socket(sock, server_hostname=peer_id)

                # Extract peer certificate
                peer_cert_der = ssl_sock.getpeercert(binary_form=True)
                if peer_cert_der:
                    peer_cert = x509.load_der_x509_certificate(peer_cert_der)
                    peer_cert_pem = peer_cert.public_bytes(Encoding.PEM).decode()
                    self._pairing_mgr.add_peer(
                        peer_id, peer_name, peer_cert_pem,
                        paired=self._pairing_mgr.is_peer_paired(peer_id),
                    )

                conn = PeerConnection(peer_id, peer_name, ssl_sock)
                conn.set_on_message(self._on_peer_message)
                conn.set_on_disconnect(self._on_peer_disconnected)
                conn.start()

                # Remove stale connection if exists
                with self._lock:
                    old = self._peers.pop(peer_id, None)
                    if old:
                        old.stop()
                    self._peers[peer_id] = conn

                logger.info("Connected to peer %s (%s:%d)", peer_name, address, port)

            except Exception as e:
                logger.warning("Failed to connect to %s: %s", peer_name, e)
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

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
        """Clean up when a peer connection drops."""
        with self._lock:
            if peer_id in self._peers:
                del self._peers[peer_id]

    def _accept_loop(self, ssl_context: ssl.SSLContext):
        while self._running:
            client_sock = None
            try:
                client_sock, addr = self._server_sock.accept()
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

                    self._pairing_mgr.add_peer(
                        peer_id, peer_name,
                        peer_cert_pem,
                        paired=self._pairing_mgr.is_peer_paired(peer_id),
                    )

                conn = PeerConnection(peer_id or "unknown", peer_name or str(addr), ssl_sock)
                conn.set_on_message(self._on_peer_message)
                conn.set_on_disconnect(self._on_peer_disconnected)
                conn.start()

                with self._lock:
                    if peer_id:
                        old = self._peers.pop(peer_id, None)
                        if old:
                            old.stop()
                        self._peers[peer_id] = conn

                logger.info("Accepted connection from %s (peer_id=%s)", addr, peer_id)

            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.debug("Accept error: %s", e)
                if client_sock:
                    try:
                        client_sock.close()
                    except Exception:
                        pass

