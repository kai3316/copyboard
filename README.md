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

1. [Download](https://github.com/kai3316/clipsync/releases/latest) and run — no install needed
2. Run it on another device on the **same LAN**
3. Confirm the 8-digit pairing code on both devices
4. Copy on one, paste on the other

> **macOS:** If Gatekeeper blocks the app, run `xattr -cr clipsync.app` then right-click → Open.

---

## Features

- **Text, HTML, RTF, images** — full-fidelity clipboard sync, not just plain text
- **File transfer** — encrypted peer-to-peer file sending
- **Web Companion** — built-in HTTP server for mobile phone access via QR code (PWA, no app install)
- **Auto-discovery** — mDNS/Zeroconf finds peers on the LAN automatically
- **TOFU pairing** — 8-digit code verification on first contact, Ed25519 certificate pinning thereafter
- **Dual-layer encryption** — TLS 1.3 transport + AES-256-GCM per frame; at-rest encryption for stored data
- **Optional pre-shared password** — extra key entropy via PBKDF2 (600K iterations)
- **Content filtering** — regex-based filters for credit cards, SSNs, API keys, etc.
- **System tray** — runs in background with sync toggle, device status, and notifications

---

## Download

| Platform | File | Notes |
|---|---|---|
| Windows 10/11 | `clipsync.exe` | Portable |
| macOS 12+ | `clipsync.app` (zip) | Universal binary |
| Linux (X11/Wayland) | `clipsync` (tar.gz) | Requires `xclip` or `wl-clipboard` |

[Latest release](https://github.com/kai3316/clipsync/releases/latest)

---

## Install from Source

Requires **Python 3.12+**.

```bash
git clone https://github.com/kai3316/clipsync.git
cd clipsync
python -m venv .venv
source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
python src/main.py
```

**Linux:** install a clipboard tool first:

```bash
sudo apt install xclip          # X11
sudo apt install wl-clipboard   # Wayland
```

---

## Build

```bash
pip install pyinstaller
pyinstaller clipsync.spec
```

Output in `dist/`: `clipsync.exe` (Windows), `clipsync.app` (macOS), or `clipsync` (Linux).

---

## Troubleshooting

**Devices not discovering each other:** ensure same subnet, no client isolation, firewall allows UDP 5353 (mDNS) and TCP 19990.

**Sync not working:** check sync toggle is on, peer shows "Connected" in Devices panel. If encryption is enabled on one device, both must use matching passwords.

**Certificate change alert:** a paired device's identity changed — Forget and re-pair unless you recently reset the peer.

---

## License

MIT — see [LICENSE](LICENSE)
