<p align="center">
  <a href="README.md">English</a> &nbsp;|&nbsp;
  <a href="README_zh.md">中文</a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/kai3316/clipsync/master/assets/icon.svg" alt="ClipSync" width="96" height="96">
</p>

<h1 align="center">ClipSync</h1>

<p align="center">
  <strong>Copy on one device. Paste on another. Instantly.</strong>
  <br>
  Cross-platform &middot; LAN &middot; TLS 1.3 + AES-256-GCM &middot; Zero config
</p>

<p align="center">
  <a href="https://github.com/kai3316/clipsync/releases"><img src="https://img.shields.io/github/v/release/kai3316/clipsync?color=3498DB" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.12+-blue" alt="Python">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey" alt="Platforms">
</p>

---

## Quick Start

1. [Download](https://github.com/kai3316/clipsync/releases/latest) the app for your platform
2. Run it — no install needed
3. Run it on another device on the **same network**
4. Confirm the 8-digit pairing code on both devices
5. Copy on one, paste on the other!

> **macOS users:** If Gatekeeper blocks the app, run `xattr -cr clipsync.app` then right-click → Open.

---

## Contents

- [Overview](#overview)
- [Why ClipSync?](#why-clipsync)
- [Features](#features)
- [Download](#download)
- [Install from Source](#install-from-source)
- [Build from Source](#build-from-source)
- [How It Works](#how-it-works)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Project Structure](#project-structure)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

ClipSync syncs your clipboard across devices on the same local network. Copy text, images, HTML, or rich text on your Windows PC — paste it seconds later on your MacBook. No cloud, no accounts, no setup.

### Why ClipSync?

- **No cloud dependency** — all data stays on your LAN; nothing ever leaves your network
- **Instant** — sub-second sync after paste detection, with smart debouncing to prevent echo
- **Full fidelity** — preserves text encoding, HTML structure, RTF formatting, and image data byte-for-byte
- **Secure by default** — dual-layer encryption: TLS 1.3 transport + AES-256-GCM per frame, at-rest encryption for stored data
- **Zero config** — devices discover each other automatically via mDNS; pair once, trusted forever

---

## Comparison

Most clipboard sync tools fall into two camps: **cloud-based services** (convenient but your data leaves your network) and **OS built-in solutions** (limited to one ecosystem, plain text only). ClipSync takes a third path.

| Capability | ClipSync | Cloud Clipboard Tools | OS Built-in (Universal Clipboard, etc.) |
|---|---|---|---|
| **Plain Text** | ✅ UTF-8, full Unicode | ✅ | ✅ |
| **Rich Text / HTML** | ✅ Links, tables, formatting preserved | ❌ Plain text only | ❌ Plain text only |
| **Images (PNG)** | ✅ Full resolution, any size | ❌ Not supported | ❌ Not supported |
| **File Transfer** | ✅ Encrypted, any file type | ❌ Not supported | ❌ |
| **Mobile Access** | ✅ Web Companion — browser on phone | ❌ Separate app required | ❌ |
| **Data Location** | ✅ Pure LAN — never leaves | ❌ Uploaded to cloud servers | ✅ Local only |
| **Account Required** | ✅ None — just run it | ❌ Account + sign-in | Varies |
| **Cross-Platform** | ✅ Windows / macOS / Linux | Varies by tool | ❌ Single ecosystem |
| **Encryption** | ✅ TLS 1.3 + AES-256-GCM | ⚠️ TLS only (if at all) | ❌ None or basic |
| **Zero Config** | ✅ mDNS auto-discovery | ❌ Manual setup | ✅ |
| **Open Source** | ✅ MIT License | ❌ Proprietary | ❌ |
| **Price** | ✅ Free forever | ❌ Freemium / Paid | ✅ Free |

**Bottom line:** If you only copy plain text within one ecosystem, built-in tools work fine. If you need rich text, images, files, cross-platform sync, and privacy — ClipSync is purpose-built for that.

---

## Features

### Clipboard Sync
| Format | Type | Notes |
|---|---|---|
| Plain Text | `TEXT` | UTF-8, full Unicode support |
| Rich Text | `HTML` | Preserves links, tables, formatting |
| Rich Text | `RTF` | Microsoft Office compatible |
| Images | `IMAGE_PNG` | PNG format, any resolution |

### Device Management
- **Auto-discovery** — mDNS/Zeroconf finds peers on the LAN without any IP configuration
- **Trust-on-first-use (TOFU)** — each device has a unique Ed25519 identity; pinned on first pairing
- **Certificate pinning** — if a device's certificate changes, you're alerted immediately
- **Pairing codes** — 8-digit verification codes prevent MITM attacks during initial handshake

### Security Architecture

| Layer | Technology | Detail |
|---|---|---|
| **Application** | AES-256-GCM per frame | Per-peer encryption keys derived via HKDF from Ed25519 certificates; optional pre-shared password for extra entropy (PBKDF2, 600K iterations) |
| **Transport** | TLS 1.3 | Self-signed Ed25519 X.509 certificates with certificate pinning (TOFU); MITM detection on identity change |
| **At-Rest** | AES-256-GCM on disk | Private keys, clipboard history, and sensitive config fields encrypted at rest; password stored as PBKDF2 verification token (never persisted) |

- **Dual-layer encryption** — TLS 1.3 secures the transport; AES-256-GCM encrypts each frame at the application layer. Different keys per peer-pair, automatically derived
- **Storage encryption** — private keys, clipboard history, and sensitive config fields are AES-256-GCM encrypted at rest
- **Optional pre-shared password** — add an out-of-band password for additional entropy; verified via PBKDF2 hash on startup

### Additional Features
- **Web Companion** — built-in HTTP server lets phones on the same LAN view clipboard history, push text, and transfer files. Scan a QR code, no app install needed. PWA support for "Add to Home Screen" on iOS/Android.
- **File transfer** — send files between paired devices over the encrypted channel
- **Content filtering** — optional regex-based filters for credit cards, SSNs, API keys, passwords
- **System tray** — runs quietly in the background; right-click for settings
- **Notifications** — optional desktop notifications on connect, disconnect, and sync events
- **Dark mode** — follows system theme or manual toggle
- **Auto-start** — optionally launch on login

---

## Download

Get the latest release from the [Releases page](https://github.com/kai3316/clipsync/releases):

| Platform | File | Notes |
|---|---|---|
| Windows 10/11 | `clipsync.exe` | Portable — no install needed |
| macOS 12+ | `clipsync.app` (zip) | Universal binary (Apple Silicon + Intel) |
| Linux (X11/Wayland) | `clipsync` (tar.gz) | Requires `xclip` or `wl-clipboard` |

No Python installation required. Download, run, done.

> **macOS users:** The app is not notarized. If Gatekeeper blocks it:
> ```bash
> xattr -cr clipsync.app
> ```
> Then right-click the app and select **Open**. If issues persist, run the binary directly for diagnostics:
> ```bash
> ./clipsync.app/Contents/MacOS/clipsync
> ```

---

## Install from Source

Requires **Python 3.12+**.

```bash
# Clone the repository
git clone https://github.com/kai3316/clipsync.git
cd clipsync

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate       # Windows

# Install dependencies
pip install -r requirements.txt

# Run
python src/main.py
```

**Linux prerequisites:**
```bash
# Debian/Ubuntu
sudo apt install xclip
# Fedora
sudo dnf install xclip
# Arch
sudo pacman -S xclip
# Wayland users: install wl-clipboard instead
```

---

## Build from Source

Build a standalone executable with PyInstaller:

```bash
pip install pyinstaller
pyinstaller clipsync.spec
```

Output in `dist/`:
- Windows: `dist/clipsync.exe`
- macOS: `dist/clipsync.app`
- Linux: `dist/clipsync`

The `.spec` file auto-collects all internal modules and required dependencies (`zeroconf`, `cryptography`, `PIL`, `pystray`, `customtkinter`).

---

## How It Works

```
Device A                              Device B
   │                                     │
   ├─ 1. mDNS broadcast ────────────────►│  "I'm here: clipsync._tcp.local"
   │                                     │
   ├─ 2. TCP connection ◄───────────────►│  TLS 1.3 handshake
   │                                     │
   ├─ 3. Identity exchange ─────────────►│  Ed25519 cert fingerprints
   │                                     │
   ├─ 4. Pairing (first time) ◄─────────►│  8-digit code confirmation
   │     Certificate pinned              │  Certificate pinned
   │                                     │
   ├─ 5. Clipboard change detected──────►│  Hash → dedup → encrypt → send
   │     AES-256-GCM encrypted frame     │  Decrypt → write to clipboard
   │                                     │
   ├─ 6. Trusted on reconnect ──────────►│  Pinned cert verified, auto-connect
```

1. **Discovery** — mDNS/Zeroconf broadcasts device presence on the LAN. The service type `_clipsync._tcp.local` enables automatic peer detection without IP configuration.

2. **Connection** — TCP connection established, TLS 1.3 handshake with self-signed Ed25519 certificates. Certificate fingerprints are exchanged at the application layer for identity verification.

3. **Pairing** — On first contact, both devices display the same 8-digit code (derived from cert fingerprints). Confirming the code pins the peer's certificate — all future connections are trust-on-first-use.

4. **Sync** — The clipboard monitor detects content changes. Before sending, the content is hashed for deduplication (prevents echo loops). The frame is encrypted with AES-256-GCM (peer-specific key), then sent over TLS 1.3.

5. **Reconnect** — Paired devices reconnect automatically. If a peer's certificate has changed since pairing, the user is alerted (potential MITM).

---

## Configuration

| Setting | Location | Description |
|---|---|---|
| Device Name | Dashboard → Overview | Custom name shown to other devices |
| Sync Toggle | Dashboard → Overview | Pause/resume clipboard sharing |
| Auto-start | Settings | Launch on system login |
| Theme | Settings | Light / Dark / System |
| Port | Settings → Network | Default: 19990 |
| Relay URL | Settings → Network | Optional relay server for cross-subnet sync |
| Content Filter | Settings → Filter | Regex categories: credit card, SSN, API key, etc. |
| Encryption | Settings → Security | Toggle at-rest + frame encryption |
| Pre-shared Password | Settings → Security | Optional shared secret for extra key entropy |
| History | Settings → Advanced | Max entries (default: 50) |
| Web Companion | Settings → Web Companion | Built-in HTTP server for mobile access (off by default) |
| Web Port | Settings → Web Companion | HTTP port for web companion (default: 19991) |
| Web History Limit | Settings → Web Companion | Number of history items shown on the web page (default: 5) |
| File Receive Dir | Settings → Advanced | Where received files are saved |
| Poll Interval | Settings → Advanced | Clipboard check frequency (default: 0.4s) |
| Sync Debounce | Settings → Advanced | Minimum interval between syncs (default: 0.3s) |

### Data Storage

All application data is stored locally:

| OS | Config & History | Logs |
|---|---|---|
| Windows | `%APPDATA%\ClipSync\` | `%APPDATA%\ClipSync\clipsync.log` |
| macOS | `~/Library/Application Support/ClipSync/` | `~/Library/Logs/ClipSync/clipsync.log` |
| Linux | `~/.config/clipsync/` | `~/.local/share/clipsync/clipsync.log` |

- `config.json` — device identity, peer list, settings (private key encrypted)
- `clipboard_history.json` — last N clipboard entries (all content encrypted at rest)

---

## Troubleshooting

### Devices not discovering each other
1. Verify both devices are on the **same subnet** (same WiFi network)
2. Corporate networks may have **client isolation** blocking mDNS — try a personal hotspot
3. Check that your firewall allows **UDP port 5353** (mDNS) and **TCP port 19990** (ClipSync)
4. Try the **Relay URL** setting if crossing subnets

### Sync not working
1. Confirm **Sync is enabled** on both devices (Dashboard → Overview toggle)
2. Check the **Devices panel** — the peer should show "Connected" with a lock icon
3. If "Paired" but not connected, click the **Reconnect** button
4. Check the **Settings → Security** panel — if encryption is enabled on one device, both must have matching passwords if set

### Connection issues
- Look at the **Devices panel** status indicators:
  - Green dot + lock = Connected and encrypted
  - Orange dot + "Paired" = Trusted but offline
  - Blue dot + "Discovered" = Found but not yet paired
- If a device shows as "Discovered" but won't connect, try **Forget** and re-discover
- Restarting ClipSync on both devices often resolves transient mDNS issues

### Getting logs
- Right-click the system tray icon → **Export Logs**
- Or open the log file directly (see [Data Storage](#data-storage) paths above)
- Log level can be set in Settings → Advanced (DEBUG, INFO, WARNING, ERROR)

---

## Project Structure

```
clipsync/
├── src/
│   └── main.py                      # Application entry point
├── internal/
│   ├── clipboard/                   # Clipboard I/O per platform
│   │   ├── clipboard.py             # Factory + common logic
│   │   ├── clipboard_windows.py     # Windows via win32clipboard
│   │   ├── clipboard_darwin.py      # macOS via AppKit
│   │   ├── clipboard_linux.py       # Linux via xclip/wl-paste
│   │   ├── filter.py                # Content filtering (regex)
│   │   ├── format.py                # Content type + sync message
│   │   └── history.py               # Encrypted local history
│   ├── config/
│   │   └── config.py                # JSON config load/save
│   ├── platform/
│   │   ├── autostart.py             # OS-specific auto-launch
│   │   └── notify.py                # Desktop notifications
│   ├── protocol/
│   │   └── codec.py                 # Frame encoding/decoding
│   ├── security/
│   │   ├── encryption.py            # AES-256-GCM + HKDF + PBKDF2
│   │   └── pairing.py               # Ed25519 identity, TOFU, pairing codes
│   ├── sync/
│   │   ├── manager.py               # Sync orchestration + dedup
│   │   └── file_transfer.py         # File transfer protocol
│   ├── transport/
│   │   ├── connection.py            # TLS 1.3 TCP connections
│   │   └── discovery.py             # mDNS/Zeroconf discovery
│   ├── ui/
│   │   ├── dashboard.py             # Main window with 5 panels
│   │   ├── settings_window.py       # Settings with sidebar nav
│   │   ├── dialogs.py               # Themed CTk dialog replacements
│   │   └── systray.py               # System tray icon + menu
│   └── web/
│       ├── __init__.py
│       └── server.py                # Built-in HTTP server + mobile PWA
├── tests/                           # 204 tests covering all modules
├── assets/
│   └── icon.svg                     # Application icon
├── clipsync.spec                   # PyInstaller build spec
├── requirements.txt                 # Python dependencies
├── README.md
├── README_zh.md
└── LICENSE
```

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Make your changes and ensure all tests pass: `python -m pytest tests/ -v`
4. Submit a pull request

Please keep changes focused — one PR, one purpose.

---

## License

MIT — see [LICENSE](LICENSE)
