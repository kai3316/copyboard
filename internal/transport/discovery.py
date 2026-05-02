"""mDNS service discovery for finding ClipSync peers on the LAN.

Registers this device as a _clipsync._tcp service and discovers
other devices running ClipSync on the same local network.
"""

import hashlib
import json
import logging
import platform
import socket
import subprocess
import threading
from typing import Callable

from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf

logger = logging.getLogger(__name__)


def _get_all_local_addresses():
    addresses = []
    seen = set()
    try:
        for info in socket.getaddrinfo(
            socket.gethostname(), None, family=socket.AF_INET
        ):
            ip = info[4][0]
            if ip.startswith("127.") or ip.startswith("169.254."):
                continue
            if ip not in seen:
                seen.add(ip)
                addresses.append(ip)
    except Exception:
        logger.warning("Failed to enumerate local addresses")
    return addresses


def _get_interface_priorities():
    priorities = {}
    system = platform.system()
    try:
        if system == "Windows":
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    "Get-NetIPAddress -AddressFamily IPv4 "
                    "| Select-Object IPAddress, InterfaceAlias "
                    "| ConvertTo-Json",
                ],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return priorities
            entries = json.loads(result.stdout)
            if isinstance(entries, dict):
                entries = [entries]
            for e in entries:
                ip = e.get("IPAddress", "")
                iface = e.get("InterfaceAlias", "").lower()
                if not ip:
                    continue
                if any(k in iface for k in ("ethernet", "eth", "local area")):
                    priorities[ip] = 0
                elif any(k in iface for k in ("wi-fi", "wlan", "wireless", "wifi")):
                    priorities[ip] = 1
                elif any(k in iface for k in ("vpn", "tunnel", "tap", "ppp", "teredo")):
                    priorities[ip] = 2
                else:
                    priorities[ip] = 3
        else:
            result = subprocess.run(
                ["ifconfig"], capture_output=True, text=True, timeout=5
            )
            iface = ""
            for line in result.stdout.splitlines():
                if line and line[0] not in ("\t", " "):
                    iface = line.split(":")[0].split()[0].lower()
                elif "inet " in line and iface:
                    parts = line.strip().split()
                    try:
                        idx = parts.index("inet")
                        ip = parts[idx + 1]
                        if any(k in iface for k in ("eth", "en")):
                            priorities[ip] = 0
                        elif any(k in iface for k in ("wlan", "wl", "wi-fi")):
                            priorities[ip] = 1
                        elif any(k in iface for k in ("tun", "tap", "vpn", "ppp", "utun")):
                            priorities[ip] = 2
                        else:
                            priorities[ip] = 3
                    except (ValueError, IndexError):
                        pass
    except Exception:
        pass
    return priorities


def _get_local_address():
    all_ips = _get_all_local_addresses()
    if not all_ips:
        logger.warning(
            "No non-loopback IP found, falling back to 127.0.0.1 "
            "-- this device will not be reachable from other hosts"
        )
        return "127.0.0.1"
    priorities = _get_interface_priorities()
    sorted_ips = sorted(all_ips, key=lambda ip: priorities.get(ip, 99))
    best = sorted_ips[0]
    logger.info("Local IPs: %s (selected %s)", all_ips, best)
    return best


class Discovery:
    """mDNS-based peer discovery."""

    @staticmethod
    def _hash_device_id(device_id: str) -> str:
        return hashlib.sha256(device_id.encode()).hexdigest()[:12]

    def __init__(self, device_id: str, device_name: str, port: int, service_type: str):
        self._device_id = device_id
        self._device_name = device_name
        self._device_id_hash = self._hash_device_id(device_id)
        self._display_name = device_name[:8] if device_name else "ClipSync"
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

        # Build properties – use hashed device_id to avoid exposing the
        # real device identity in plaintext mDNS TXT records.
        props = {
            b"device_id_hash": self._device_id_hash.encode("utf-8"),
        }

        all_ips = _get_all_local_addresses()
        for i, ip in enumerate(all_ips):
            props[f"alt_ip_{i}".encode()] = ip.encode()

        local_ip = _get_local_address()
        logger.info(
            "Registering mDNS on %s (all IPs: %s)", local_ip, all_ips
        )

        # Register our service – use a truncated display name so the
        # real hostname is not broadcast in plaintext on the LAN.
        self._service_info = ServiceInfo(
            type_=self._service_type,
            name=f"{self._display_name}.{self._service_type}",
            addresses=[socket.inet_aton(local_ip)],
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
        self.stop_browsing()
        self.stop_advertising()
        if self._zc:
            self._zc.close()
            self._zc = None
        logger.info("Discovery stopped")

    # ── Granular control ───────────────────────────────────────

    @property
    def is_browsing(self) -> bool:
        return self._browser is not None

    @property
    def is_advertising(self) -> bool:
        return self._zc is not None and self._service_info is not None

    def stop_browsing(self):
        """Stop discovering new peers without affecting advertising."""
        if self._browser:
            self._browser.cancel()
            self._browser = None
            logger.info("Stopped browsing for peers")

    def start_browsing(self):
        """Resume discovering new peers. Requires start() to have been called."""
        if self._browser is not None:
            return
        if self._zc is None:
            return
        self._browser = ServiceBrowser(
            self._zc,
            self._service_type,
            handlers=[self._on_service_state_change],
        )
        logger.info("Resumed browsing for peers")

    def stop_advertising(self):
        """Unregister mDNS service without affecting browsing."""
        if self._zc and self._service_info:
            self._zc.unregister_service(self._service_info)
            self._service_info = None
            logger.info("Stopped advertising this device")

    def start_advertising(self):
        """Re-register mDNS service. Requires start() to have been called."""
        if self._service_info is not None:
            return
        if self._zc is None:
            return
        # Rebuild service info (IPs may have changed, and ServiceInfo
        # can't be re-registered after unregistration).
        props = {b"device_id_hash": self._device_id_hash.encode("utf-8")}
        all_ips = _get_all_local_addresses()
        for i, ip in enumerate(all_ips):
            props[f"alt_ip_{i}".encode()] = ip.encode()
        local_ip = _get_local_address()
        self._service_info = ServiceInfo(
            type_=self._service_type,
            name=f"{self._display_name}.{self._service_type}",
            addresses=[socket.inet_aton(local_ip)],
            port=self._port,
            properties=props,
        )
        try:
            self._zc.register_service(self._service_info)
            logger.info("Resumed advertising this device on port %d", self._port)
        except Exception as e:
            logger.warning("Failed to re-register mDNS: %s", e)

    def _wake_recovery(self):
        """Re-register the mDNS service after wake-from-sleep.

        After sleep, network interfaces may have changed and the mDNS
        registration may be stale. Re-registering ensures other devices
        can discover us again.
        """
        if not self._zc or not self._service_info:
            return
        try:
            self._zc.unregister_service(self._service_info)
        except Exception:
            pass
        try:
            self._zc.register_service(self._service_info)
            logger.info("Re-registered mDNS service after wake")
        except Exception as e:
            logger.warning("Failed to re-register mDNS after wake: %s", e)

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
        # Read the hashed device_id from TXT records.
        peer_id_hash = b""
        if props and b"device_id_hash" in props:
            peer_id_hash = props[b"device_id_hash"].decode("utf-8")

        # Skip our own service by comparing the hashed identity.
        if peer_id_hash == self._device_id_hash:
            return

        if not info.addresses:
            return

        address = socket.inet_ntoa(info.addresses[0])
        port = info.port

        # Derive a privacy-safe display name from the service name.
        # The service name is e.g. "<display_name>._clipsync._tcp.local."
        try:
            peer_display = name.split(".")[0]
        except (IndexError, TypeError):
            peer_display = peer_id_hash

        with self._lock:
            if peer_id_hash in self._known_peers:
                return
            self._known_peers[peer_id_hash] = {
                "name": peer_display,
                "address": address,
                "port": port,
            }
            self._service_to_peer[name] = peer_id_hash

        logger.info("Discovered peer: %s at %s:%d", peer_display, address, port)

        with self._lock:
            on_found = self._on_peer_found
        if on_found:
            on_found(peer_id_hash, peer_display, address, port)

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
        return _get_local_address()
