# Changelog

All notable changes to ClipSync are documented in this file.

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
