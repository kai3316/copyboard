"""mDNS service discovery for finding CopyBoard peers on the LAN.

Registers this device as a _copyboard._tcp service and discovers
other devices running CopyBoard on the same local network.
"""

import logging
import socket
import threading
from typing import Callable

from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf

logger = logging.getLogger(__name__)


class Discovery:
    """mDNS-based peer discovery."""

    def __init__(self, device_id: str, device_name: str, port: int, service_type: str):
        self._device_id = device_id
        self._device_name = device_name
        self._port = port
        self._service_type = service_type
        self._zc: Zeroconf | None = None
        self._service_info: ServiceInfo | None = None
        self._browser: ServiceBrowser | None = None
        self._on_peer_found: Callable | None = None
        self._on_peer_lost: Callable | None = None
        self._lock = threading.Lock()
        self._known_peers: dict[str, dict] = {}  # peer_id -> info
        self._service_to_peer: dict[str, str] = {}  # service_name -> peer_id

    def set_callbacks(self, on_found: Callable, on_lost: Callable):
        """Set callbacks for peer discovery events.
        on_found(device_id, device_name, address, port)
        on_lost(device_id)
        """
        with self._lock:
            self._on_peer_found = on_found
            self._on_peer_lost = on_lost

    def start(self):
        """Register our service and start browsing for peers."""
        try:
            self._zc = Zeroconf()
        except Exception as e:
            logger.error("Failed to initialize mDNS: %s", e)
            return

        # Build properties
        props = {
            b"device_id": self._device_id.encode("utf-8"),
            b"device_name": self._device_name.encode("utf-8"),
        }

        # Register our service
        self._service_info = ServiceInfo(
            type_=self._service_type,
            name=f"{self._device_name}.{self._service_type}",
            addresses=[socket.inet_aton(self._get_local_ip())],
            port=self._port,
            properties=props,
        )

        try:
            self._zc.register_service(self._service_info)
            logger.info("Registered mDNS service on port %d", self._port)
        except Exception as e:
            logger.warning("Failed to register mDNS: %s", e)

        # Browse for peers
        self._browser = ServiceBrowser(
            self._zc,
            self._service_type,
            handlers=[self._on_service_state_change],
        )
        logger.info("Started browsing for peers")

    def stop(self):
        if self._browser:
            self._browser.cancel()
        if self._zc:
            if self._service_info:
                self._zc.unregister_service(self._service_info)
            self._zc.close()
        logger.info("Discovery stopped")

    def _on_service_state_change(self, zeroconf, service_type, name, state_change):
        """Handle mDNS service add/remove events."""
        if state_change.name == "Added":
            self._handle_service_added(zeroconf, service_type, name)
        elif state_change.name == "Removed":
            self._handle_service_removed(name)

    def _handle_service_added(self, zeroconf, service_type, name):
        info = zeroconf.get_service_info(service_type, name)
        if info is None:
            return

        props = info.properties
        peer_id = b""
        if props and b"device_id" in props:
            peer_id = props[b"device_id"].decode("utf-8")

        # Skip our own service
        if peer_id == self._device_id:
            return

        if not info.addresses:
            return

        address = socket.inet_ntoa(info.addresses[0])
        port = info.port

        peer_name = peer_id
        if props and b"device_name" in props:
            peer_name = props[b"device_name"].decode("utf-8")

        with self._lock:
            if peer_id in self._known_peers:
                return
            self._known_peers[peer_id] = {
                "name": peer_name,
                "address": address,
                "port": port,
            }
            self._service_to_peer[name] = peer_id

        logger.info("Discovered peer: %s at %s:%d", peer_name, address, port)

        with self._lock:
            on_found = self._on_peer_found
        if on_found:
            on_found(peer_id, peer_name, address, port)

    def _handle_service_removed(self, name):
        with self._lock:
            peer_id = self._service_to_peer.pop(name, None)
            if peer_id is None:
                return
            if peer_id in self._known_peers:
                del self._known_peers[peer_id]
            on_lost = self._on_peer_lost
        if peer_id:
            logger.info("Peer lost: %s", peer_id)
            if on_lost:
                on_lost(peer_id)

    def _get_local_ip(self) -> str:
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            return ip
        except Exception:
            return "127.0.0.1"
        finally:
            if s:
                try:
                    s.close()
                except Exception:
                    pass
