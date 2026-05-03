<p align="center">
  <a href="README.md">English</a> &nbsp;|&nbsp;
  <a href="README_zh.md">中文</a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/kai3316/clipsync/master/assets/icon.svg" alt="ClipSync" width="96" height="96">
</p>

<h1 align="center">ClipSync</h1>

<p align="center">
  <strong>一台设备复制，另一台即刻粘贴。</strong>
  <br>
  全平台 &middot; 局域网 &middot; TLS 1.3 + AES-256-GCM &middot; 零配置
</p>

<p align="center">
  <a href="https://github.com/kai3316/clipsync/releases"><img src="https://img.shields.io/github/v/release/kai3316/clipsync?color=3498DB" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.12+-blue" alt="Python">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey" alt="Platforms">
</p>

---

## 快速开始

1. [下载](https://github.com/kai3316/clipsync/releases/latest) 对应平台的应用，直接运行
2. 在**同一局域网**的另一台设备上也运行 ClipSync
3. 确认两端显示相同的 8 位配对码
4. 一台复制，另一台粘贴

> **macOS：** 如遇 Gatekeeper 拦截，运行 `xattr -cr clipsync.app`，然后右键 → 打开。

---

## 功能特性

- **文本、HTML、RTF、图片** — 完整保真同步，不只是纯文本
- **文件传输** — 加密的点对点文件发送
- **Web 伴侣** — 内置 HTTP 服务器，手机扫码即可访问（PWA，无需安装 App）
- **自动发现** — mDNS/Zeroconf 自动发现局域网内设备
- **TOFU 配对** — 首次连接 8 位验证码确认，之后 Ed25519 证书锁定自动信任
- **双层加密** — TLS 1.3 传输层 + AES-256-GCM 逐帧加密；落盘数据同样加密
- **可选预共享密码** — PBKDF2（60 万次迭代）为密钥派生增加额外熵值
- **内容过滤** — 基于正则的敏感内容过滤（信用卡、身份证号、API 密钥等）
- **系统托盘** — 后台静默运行，右键控制同步开关、查看设备状态

---

## 下载

| 平台 | 文件 | 备注 |
|---|---|---|
| Windows 10/11 | `clipsync.exe` | 便携版 |
| macOS 12+ | `clipsync.app` (zip) | 通用二进制 |
| Linux (X11/Wayland) | `clipsync` (tar.gz) | 需安装 `xclip` 或 `wl-clipboard` |

[最新版本](https://github.com/kai3316/clipsync/releases/latest)

---

## 从源码运行

需要 **Python 3.12+**。

```bash
git clone https://github.com/kai3316/clipsync.git
cd clipsync
python -m venv .venv
source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
python src/main.py
```

**Linux 用户先装剪贴板工具：**

```bash
sudo apt install xclip          # X11
sudo apt install wl-clipboard   # Wayland
```

---

## 构建

```bash
pip install pyinstaller
pyinstaller clipsync.spec
```

构建结果在 `dist/`：`clipsync.exe`（Windows）、`clipsync.app`（macOS）、`clipsync`（Linux）。

---

## 常见问题

**设备互相发现不了：** 确认同一子网、无客户端隔离、防火墙放行 UDP 5353 (mDNS) 和 TCP 19990。

**同步不生效：** 确认两端同步开关已开启，设备面板显示"已连接"。如果一方开启了加密，双方密码需一致。

**证书变更警告：** 对方设备身份已变化 — 除非你最近重置过对方，否则应移除并重新配对。

---

## 许可证

MIT — 详见 [LICENSE](LICENSE)
