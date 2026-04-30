<p align="center">
  <a href="README.md">English</a> &nbsp;|&nbsp;
  <a href="README_zh.md">中文</a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/kai3316/copyboard/master/assets/icon.svg" alt="CopyBoard" width="80" height="80">
</p>

<h1 align="center">CopyBoard</h1>

<p align="center">
  <strong>Copy on one device. Paste on another. Instantly.</strong>
  <br>
  Windows &middot; macOS &middot; Linux &middot; LAN &middot; Encrypted
</p>

<p align="center">
  <a href="https://github.com/kai3316/copyboard/releases"><img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey" alt="Platforms"></a>
  <a href="https://github.com/kai3316/copyboard/blob/master/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License"></a>
</p>

---

## What is CopyBoard?

You copy text, an image, or a table on your Windows PC. A moment later, you paste it on your MacBook. No emailing yourself. No messaging apps. No cloud uploads.

CopyBoard syncs your clipboard across your devices automatically over your local network.

- **Instant** — copy on one device, paste on another within seconds
- **All formats** — text, rich text (HTML/RTF), spreadsheets, images
- **Runs in the background** — lives in your system tray, stays out of your way
- **Private by design** — everything stays on your local network, nothing goes to the cloud

## Download

Get the latest version from the [Releases page](https://github.com/kai3316/copyboard/releases):

| Platform | File |
|---|---|
| Windows | `copyboard.exe` |
| macOS | `copyboard.app` (zip) |
| Linux | `copyboard` (tar.gz) |

Download, run, and you're ready. No installation, no Python required.

> **Note for macOS users:** The app is not notarized. Right-click the app and select *Open* for the first launch.

## Install from Source

If you prefer to run from source, you'll need Python 3.12.

```bash
git clone https://github.com/kai3316/copyboard.git
cd copyboard
python -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate        # Windows
pip install -r requirements.txt
python cmd/main.py
```

**Linux users:** install `xclip` (X11) or `wl-clipboard` (Wayland) first.

## Build a Standalone Executable

To bundle CopyBoard into a single executable file:

```bash
pip install pyinstaller
pyinstaller copyboard.spec
```

Find the output in `dist/`:
- Windows: `dist/copyboard.exe`
- macOS: `dist/copyboard`
- Linux: `dist/copyboard`

## How It Works

1. **Discover** — CopyBoard finds other devices on your LAN automatically (mDNS)
2. **Pair** — First time two devices meet, confirm the 8-digit pairing code on both sides
3. **Sync** — Copy on one device, paste on the other. Content is compared by hash to avoid duplicates and echo loops
4. **Trust** — Once paired, devices remember each other. No confirmation needed next time

All traffic is encrypted with TLS 1.3. Pairing uses certificate pinning — if a device's identity ever changes, you'll be alerted.

## Settings

Right-click the system tray icon to access settings:

| Setting | Description |
|---|---|
| Device name | How your device appears to others |
| Sync on/off | Temporarily pause clipboard sharing |
| Auto-start | Launch CopyBoard when you log in |
| Theme | Light or dark mode |

## Troubleshooting

**Devices can't find each other?**
Make sure both devices are on the same local network (same WiFi / subnet). Corporate networks with client isolation may block mDNS.

**Sync isn't working?**
Check that sync is enabled on both devices (right-click tray icon).

**Need logs?**
Right-click the tray icon → *Export Logs*, or check:
- Windows: `%APPDATA%\CopyBoard\copyboard.log`
- macOS: `~/Library/Logs/CopyBoard/copyboard.log`
- Linux: `~/.local/share/copyboard/copyboard.log`

## License

MIT — see [LICENSE](LICENSE)
