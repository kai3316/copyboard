"""Tests for Config — load, save, atomic writes, recovery."""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# We need to patch _config_dir and _config_path
import internal.config.config as config_module


class TestConfigDefaults:
    def test_default_values(self):
        cfg = config_module.Config()
        assert len(cfg.device_id) == 12  # uuid4 hex[:12]
        assert isinstance(cfg.device_name, str)
        assert cfg.port == 19990
        assert cfg.service_type == "_clipsync._tcp.local."
        assert cfg.sync_enabled is True
        assert cfg.auto_start is False
        assert cfg.relay_url == ""
        assert cfg.private_key_pem == ""
        assert cfg.certificate_pem == ""
        assert isinstance(cfg.peers, dict)

    def test_add_peer(self):
        cfg = config_module.Config()
        peer = config_module.PeerInfo(
            device_id="abc",
            device_name="Test",
            public_key_pem="key-data",
            paired=True,
        )
        cfg.add_peer(peer)
        assert "abc" in cfg.peers
        assert cfg.peers["abc"].device_name == "Test"
        assert cfg.peers["abc"].paired is True


class TestConfigSaveLoad:
    def test_roundtrip(self):
        """Save a config, load it back, verify all fields match."""
        tmp_dir = Path(tempfile.mkdtemp())
        config_path = tmp_dir / "config.json"

        # Patch _config_dir and _config_path
        original_dir = config_module._config_dir
        original_path = config_module._config_path
        config_module._config_dir = lambda: tmp_dir
        config_module._config_path = lambda: config_path

        try:
            cfg = config_module.Config()
            cfg.device_name = "Roundtrip Test"
            cfg.port = 23456
            cfg.sync_enabled = False
            cfg.auto_start = True
            cfg.peers["peer1"] = config_module.PeerInfo(
                device_id="peer1",
                device_name="Peer One",
                public_key_pem="pem-data-here",
                paired=True,
            )

            config_module.save(cfg)

            # Verify file exists
            assert config_path.exists()

            # Load it back
            loaded = config_module.load()
            assert loaded.device_name == "Roundtrip Test"
            assert loaded.port == 23456
            assert loaded.sync_enabled is False
            assert loaded.auto_start is True
            assert "peer1" in loaded.peers
            assert loaded.peers["peer1"].device_name == "Peer One"
            assert loaded.peers["peer1"].public_key_pem == "pem-data-here"
            assert loaded.peers["peer1"].paired is True
        finally:
            config_module._config_dir = original_dir
            config_module._config_path = original_path

    def test_peers_save_as_list(self):
        """Peers dict should serialize as a JSON array."""
        tmp_dir = Path(tempfile.mkdtemp())
        config_path = tmp_dir / "config.json"

        original_dir = config_module._config_dir
        original_path = config_module._config_path
        config_module._config_dir = lambda: tmp_dir
        config_module._config_path = lambda: config_path

        try:
            cfg = config_module.Config()
            cfg.peers["a"] = config_module.PeerInfo(device_id="a", device_name="A")
            cfg.peers["b"] = config_module.PeerInfo(device_id="b", device_name="B")
            config_module.save(cfg)

            raw = json.loads(config_path.read_text(encoding="utf-8"))
            assert isinstance(raw["peers"], list)
            assert len(raw["peers"]) == 2
        finally:
            config_module._config_dir = original_dir
            config_module._config_path = original_path


class TestConfigRecovery:
    def test_corrupted_json(self):
        """Corrupted config file should fall back to defaults."""
        tmp_dir = Path(tempfile.mkdtemp())
        config_path = tmp_dir / "config.json"
        config_path.write_text("this is not valid json {{{", encoding="utf-8")

        original_dir = config_module._config_dir
        original_path = config_module._config_path
        config_module._config_dir = lambda: tmp_dir
        config_module._config_path = lambda: config_path

        try:
            cfg = config_module.load()
            # Should get defaults, not crash
            assert cfg.port == 19990
            assert isinstance(cfg.device_id, str)
        finally:
            config_module._config_dir = original_dir
            config_module._config_path = original_path

    def test_partial_json(self):
        """Valid JSON but missing fields should use defaults."""
        tmp_dir = Path(tempfile.mkdtemp())
        config_path = tmp_dir / "config.json"
        config_path.write_text('{"device_name": "Partial", "port": 30000}', encoding="utf-8")

        original_dir = config_module._config_dir
        original_path = config_module._config_path
        config_module._config_dir = lambda: tmp_dir
        config_module._config_path = lambda: config_path

        try:
            cfg = config_module.load()
            assert cfg.device_name == "Partial"
            assert cfg.port == 30000
            # Other fields should be defaults
            assert cfg.sync_enabled is True  # default
            assert cfg.auto_start is False  # default
        finally:
            config_module._config_dir = original_dir
            config_module._config_path = original_path


class TestAtomicSave:
    def test_no_stale_temp_files(self):
        """After a successful save, there should be no leftover .config_tmp_ files."""
        tmp_dir = Path(tempfile.mkdtemp())

        original_dir = config_module._config_dir
        original_path = config_module._config_path
        config_module._config_dir = lambda: tmp_dir
        config_module._config_path = lambda: tmp_dir / "config.json"

        try:
            cfg = config_module.Config()
            config_module.save(cfg)

            # Check no temp files remain
            temps = list(tmp_dir.glob(".config_tmp_*.json"))
            assert len(temps) == 0, f"Stale temp files found: {temps}"
        finally:
            config_module._config_dir = original_dir
            config_module._config_path = original_path


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
