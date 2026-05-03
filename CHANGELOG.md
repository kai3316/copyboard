# Changelog

All notable changes to ClipSync are documented in this file.

## [1.2.0] — 2026-05-03

### Added
- **Web Companion** — built-in HTTP server for mobile phone access on the same LAN
  - QR code scanning to connect (no app install needed)
  - View clipboard history, push text to desktop, transfer files
  - PWA support with app icon for "Add to Home Screen" on iOS/Android
  - Pin/unpin history items from the web page
  - Delete history items from the web page
  - File upload from phone to desktop
  - File download from desktop to phone
  - Image download (tap image items to save, not copy)
  - iOS install banner with instructions
  - Animations (fade-in cards, refresh spin, push button pulse)
- Clipboard history: pin/unpin entries, pinned-first sorting
- History entry IDs for stable item identification across the API

### Changed
- Dashboard: buttons moved to right side of cards for compact layout (history, devices, transfers)
- Device panel: notes merged into status row (right-aligned)
- Web page: swipe gestures replaced with visible Pin/Delete buttons

### Fixed
- Deadlock in ClipboardHistory (Lock → RLock) when calling get_all from within locked methods
- Non-ASCII filename download crash (RFC 5987 Content-Disposition encoding)
- Web server: device list now shows only connected devices
- Firewall port check uses regex instead of brittle string matching

## [1.0.0] — 2026-05-02

### Added
- Cross-platform clipboard sync (Windows, macOS, Linux)
- mDNS/Zeroconf automatic device discovery on LAN
- TLS 1.3 encrypted transport with Ed25519 certificates
- AES-256-GCM app-layer encryption per peer-pair
- At-rest encryption for private keys and clipboard history
- Optional pre-shared password for additional key entropy
- Trust-on-first-use (TOFU) device pairing with 8-digit codes
- System tray application with sync toggle and device status
- Dashboard with Overview, Devices, History, and Transfers panels
- Settings window with Network, Content Filter, Security, Advanced, Logs, and About sections
- File transfer between paired devices with progress tracking
- Speed test for measuring LAN throughput
- Content filtering for sensitive data (credit cards, SSNs, API keys, etc.)
- Clipboard history with search, copy, and delete
- Dark mode support (light/dark/system)
- Auto-start on system login
- Desktop notifications for connect/disconnect and sync events
- Log viewer and export within the app
- PyInstaller standalone builds for all platforms

### Security
- PBKDF2 password verification (password never stored in plaintext)
- Certificate pinning with change detection (potential MITM alert)
- Rate-limited pairing code attempts (5 per 5-minute window)
- Path traversal prevention in file transfers
