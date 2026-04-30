"""Configuration management.

Config is stored as JSON in the user's config directory:
  Windows: %APPDATA%/CopyBoard/config.json
  macOS:   ~/Library/Application Support/CopyBoard/config.json
"""

import json
import logging
import os
import platform
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class PeerInfo:
    device_id: str
    device_name: str
    public_key_pem: str = ""  # pinned after pairing
    paired: bool = False


@dataclass
class Config:
    device_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    device_name: str = field(default_factory=platform.node)
    port: int = 19990
    service_type: str = "_copyboard._tcp.local."
    peers: dict[str, PeerInfo] = field(default_factory=dict)
    sync_enabled: bool = True
    auto_start: bool = False
    relay_url: str = ""
    private_key_pem: str = ""
    certificate_pem: str = ""

    def add_peer(self, peer: PeerInfo):
        self.peers[peer.device_id] = peer


def _config_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return Path(base) / "CopyBoard"
    elif system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "CopyBoard"
    else:
        return Path.home() / ".config" / "copyboard"


def _config_path() -> Path:
    return _config_dir() / "config.json"


def _cleanup_stale_temps():
    """Remove stale .config_tmp_*.json files from a previous crashed save."""
    try:
        config_dir = _config_dir()
        if config_dir.exists():
            for f in config_dir.glob(".config_tmp_*.json"):
                try:
                    f.unlink()
                    logger.debug("Cleaned up stale temp config: %s", f.name)
                except OSError:
                    pass
    except Exception:
        pass


def load() -> Config:
    _cleanup_stale_temps()
    path = _config_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError):
            logger.warning("Failed to parse config, using defaults", exc_info=True)
            return Config()
        cfg = Config()
        for key in (
            "device_id", "device_name", "port", "service_type",
            "sync_enabled", "auto_start", "relay_url",
            "private_key_pem", "certificate_pem",
        ):
            if key in data:
                setattr(cfg, key, data[key])
        for peer_data in data.get("peers", []):
            try:
                peer = PeerInfo(
                    device_id=peer_data["device_id"],
                    device_name=peer_data["device_name"],
                    public_key_pem=peer_data.get("public_key_pem", ""),
                    paired=peer_data.get("paired", False),
                )
                cfg.peers[peer.device_id] = peer
            except (KeyError, TypeError):
                continue
        return cfg
    return Config()


def save(cfg: Config):
    config_dir = _config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = _config_path()
    data = {
        "device_id": cfg.device_id,
        "device_name": cfg.device_name,
        "port": cfg.port,
        "service_type": cfg.service_type,
        "sync_enabled": cfg.sync_enabled,
        "auto_start": cfg.auto_start,
        "relay_url": cfg.relay_url,
        "private_key_pem": cfg.private_key_pem,
        "certificate_pem": cfg.certificate_pem,
        "peers": [
            {
                "device_id": p.device_id,
                "device_name": p.device_name,
                "public_key_pem": p.public_key_pem,
                "paired": p.paired,
            }
            for p in cfg.peers.values()
        ],
    }
    # Atomic save: write to temp file then rename
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(config_dir), prefix=".config_tmp_", suffix=".json",
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, config_path)  # atomic on same filesystem
        logger.debug("Config saved to %s", config_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
