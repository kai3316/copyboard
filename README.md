<p align="center">
  <a href="README.md">🇬🇧 English</a> &nbsp;|&nbsp;
  <a href="README_zh.md">🇨🇳 中文</a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/kai3316/copyboard/master/assets/icon.svg" alt="CopyBoard" width="80" height="80">
</p>

<h1 align="center">CopyBoard</h1>

<p align="center">
  <strong>Cross-platform clipboard sharing — like Apple Universal Clipboard, but for everyone.</strong>
  <br>
  Windows &harr; macOS &harr; Linux · LAN · TLS 1.3 encrypted
</p>

<p align="center">
  <a href="https://github.com/kai3316/copyboard/actions"><img src="https://github.com/kai3316/copyboard/actions/workflows/test.yml/badge.svg" alt="Test"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue" alt="Python 3.10+"></a>
  <a href="https://github.com/kai3316/copyboard/blob/master/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License"></a>
</p>

---

## Why CopyBoard?

You're working on your Windows desktop. You copy a code snippet. Now you want to paste it on your MacBook. You reach for Slack, email, or a notes app to transfer it.

**CopyBoard eliminates that step.** It syncs your clipboard across your devices automatically — over your local network, with no cloud, no sign-up, no configuration.

- Copy on one device &rarr; paste on the other &mdash; instantly
- Text, HTML, RTF, tables, images &mdash; all formats preserved
- Runs quietly in the system tray &mdash; zero CPU when idle
- Works entirely on your LAN &mdash; never touches the cloud

## Features

| | Feature |
|---|---|
| 🔌 | **Zero configuration** — devices discover each other automatically (mDNS) |
| 🔒 | **TLS 1.3 encrypted** — all traffic encrypted, certificate pinning prevents MITM |
| 📋 | **Rich content** — text, HTML, RTF, tables, PNG images |
| 🌍 | **True cross-platform** — Windows, macOS, Linux |
| 🎨 | **Modern UI** — light/dark theme, device status cards, system tray |
| ⚡ | **Resource efficient** — event-driven on Windows, minimal polling elsewhere |
| 📦 | **Single executable** — PyInstaller packaging, auto-built via CI |
| 🧪 | **Comprehensive tests** — 96 tests, CI on all 3 OS × 3 Python versions |

## How It Works

```
┌─────────── Windows ───────────┐          ┌──────────── Mac ────────────┐
│                               │          │                             │
│  📋  Clipboard Monitor        │          │  📋  NSPasteboard Poller   │
│      (AddClipboardFormat-     │          │      (changeCount, 400ms)   │
│       Listener, event-driven) │          │                             │
│              │                │          │              │              │
│              ▼                │          │              ▼              │
│  🔄  Sync Manager             │   TLS    │  🔄  Sync Manager          │
│      hash dedup · debounce    │◄────────►│      hash dedup · debounce  │
│              │                │  1.3     │              │              │
│              ▼                │          │              ▼              │
│  📡  Transport (TCP:19990)    │          │  📡  Transport (TCP:19990) │
│              │                │          │              │              │
│              ▼                │          │              ▼              │
│  🔍  mDNS Service Browser     │          │  🔍  mDNS Service Browser  │
│      "_copyboard._tcp"        │          │      "_copyboard._tcp"      │
└───────────────────────────────┘          └─────────────────────────────┘
```

1. **Discovery** — Each device advertises itself via mDNS (`_copyboard._tcp`)
2. **Pairing** — First contact: verify 8-digit pairing codes, exchange certificates
3. **Sync** — Clipboard change detected → hash comparison → TLS broadcast → remote paste
4. **Loop prevention** — Content-based SHA-256 hashing prevents echo; 64-entry dedup ring

## Quick Start

### Option A: Download pre-built executable

Grab the latest build for your platform from [GitHub Actions](https://github.com/kai3316/copyboard/actions/workflows/build.yml):

- **Windows** — `copyboard.exe` (artifact: `copyboard-windows`)
- **macOS** — `copyboard.app` (artifact: `copyboard-macos`)
- **Linux** — `copyboard` binary (artifact: `copyboard-linux`)

Click the most recent successful workflow run → scroll to **Artifacts** at the bottom → download the one for your OS. No Python required.

### Option B: Run from source

**Prerequisites**

- Python 3.10 or newer
- Windows: no extra dependencies
- macOS: no extra dependencies (uses built-in `pbpaste`/`pbcopy`)
- Linux: `xclip` (X11) or `wl-clipboard` (Wayland)

### Install from source

```bash
# Clone
git clone https://github.com/kai3316/copyboard.git
cd copyboard

# Create virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows

# Install dependencies
pip install -r requirements.txt

# Run
python cmd/main.py
```

The CopyBoard icon appears in your system tray. On the same LAN, other CopyBoard instances are discovered automatically.

### Logging

```
# Set log level (DEBUG, INFO, WARNING, ERROR)
export COPYBOARD_LOG_LEVEL=DEBUG    # macOS/Linux
set COPYBOARD_LOG_LEVEL=DEBUG       # Windows
```

Logs are saved to:
| Platform | Path |
|---|---|
| Windows | `%APPDATA%\CopyBoard\copyboard.log` |
| macOS | `~/Library/Logs/CopyBoard/copyboard.log` |
| Linux | `~/.local/share/copyboard/copyboard.log` |

Log rotation: 5 MB per file, 3 backups. Export logs from the Settings window or system tray menu.

### Configuration

All settings are stored in `config.json` at the platform-appropriate location. The Settings window provides a GUI for:
- Device name
- Sync toggle
- Auto-start on login
- TCP port and service type
- Optional relay server URL

## Development

```bash
# Install dev dependencies
pip install pytest pytest-timeout

# Run tests
python -m pytest tests/ -v

# Run specific test suite
python -m pytest tests/test_cross_platform_integration.py -v
```

### Project structure

```
copyboard/
├── cmd/main.py              # Entry point, logging setup, wiring
├── internal/
│   ├── clipboard/           # Platform-specific clipboard I/O
│   │   ├── clipboard_windows.py   # Win32 API, event-driven
│   │   ├── clipboard_darwin.py    # NSPasteboard via subprocess
│   │   ├── clipboard_linux.py     # xclip / wl-paste
│   │   └── format.py              # Content type definitions
│   ├── config/config.py     # JSON config, atomic save, recovery
│   ├── protocol/codec.py    # Binary TLV wire format (magic: 0x4342)
│   ├── security/pairing.py  # Ed25519, X.509, certificate pinning
│   ├── sync/manager.py      # Central sync coordinator
│   ├── transport/
│   │   ├── discovery.py     # mDNS/DNS-SD (zeroconf)
│   │   └── connection.py    # TCP + TLS 1.3 connection management
│   └── ui/
│       ├── settings_window.py   # ttkbootstrap modern UI
│       └── systray.py           # pystray system tray
└── tests/
    ├── test_codec.py                # Protocol encode/decode
    ├── test_pairing.py              # Identity & pairing logic
    ├── test_sync_manager.py         # Sync dedup & throttle
    ├── test_config.py               # Config load/save/atomic
    ├── test_clipboard_sim.py        # 3-platform clipboard simulation
    └── test_cross_platform_integration.py  # End-to-end cross-platform sync
```

## Security

CopyBoard uses a Bluetooth-style pairing model:

| Phase | Mechanism |
|---|---|
| **Identity** | Ed25519 keypair + self-signed X.509 certificate |
| **Transport** | TLS 1.3, certificate verification after pairing |
| **Pairing** | 8-digit code (10⁸ space), rate-limited (5 attempts / 5 min) |
| **Trust** | Certificate pinning (TOFU) — any change is detected and rejected |
| **First contact** | Certificates exchanged, fingerprint verification available |
| **MITM protection** | Once paired, any certificate change raises an error |

## License

MIT — see [LICENSE](LICENSE)

---

<p align="center">
  <sub>Built with Python · ttkbootstrap · pystray · zeroconf · cryptography</sub>
</p>
