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

1. [下载](https://github.com/kai3316/clipsync/releases/latest) 对应平台的应用
2. 直接运行 — 无需安装
3. 在**同一网络**的另一台设备上也运行 ClipSync
4. 两端确认相同的 8 位配对码
5. 一台复制，另一台粘贴！

> **macOS 用户：** 如遇 Gatekeeper 拦截，运行 `xattr -cr clipsync.app`，然后右键 → 打开。

---

## 目录

- [项目简介](#项目简介)
- [为什么选择 ClipSync？](#为什么选择-clipsync)
- [功能特性](#功能特性)
- [下载](#下载)
- [从源码运行](#从源码运行)
- [构建独立可执行文件](#构建独立可执行文件)
- [工作原理](#工作原理)
- [配置项](#配置项)
- [常见问题](#常见问题)
- [项目结构](#项目结构)
- [参与贡献](#参与贡献)
- [许可证](#许可证)

---

## 项目简介

ClipSync 在局域网内自动同步多台设备的剪贴板内容。你在 Windows 电脑上复制一段文字、一张图片或一个表格，几秒后在 MacBook 上直接粘贴。不需要发邮件、不需要通过聊天工具中转、不需要上传云端。

### 为什么选择 ClipSync？

- **数据不出局域网** — 所有数据仅在本地网络中传输，绝不经过任何云端服务器
- **即时同步** — 亚秒级响应，智能去重，杜绝重复粘贴与同步回环
- **完整保真** — 保留文本编码、HTML 结构、RTF 格式、图片数据，字节级精确还原
- **默认安全** — 双层加密：TLS 1.3 传输层 + AES-256-GCM 逐帧加密，落盘数据同样加密存储
- **零配置** — mDNS 自动发现局域网内设备；首次配对后，后续自动信任连接

---

## 方案对比

市面上的剪贴板工具大致分为两类：**云端服务**（方便，但数据经过第三方服务器）和**系统内置方案**（仅限单一生态，只支持纯文本）。ClipSync 走了第三条路。

| 能力 | ClipSync | 云端剪贴板工具 | 系统内置（通用剪贴板等） |
|---|---|---|---|
| **纯文本** | ✅ UTF-8，完整 Unicode | ✅ | ✅ |
| **富文本 / HTML** | ✅ 链接、表格、排版格式完整保留 | ❌ 仅纯文本 | ❌ 仅纯文本 |
| **图片 (PNG)** | ✅ 原始画质，任意分辨率 | ❌ 不支持 | ❌ 不支持 |
| **文件传输** | ✅ 加密通道，任意文件类型 | ❌ 不支持 | ❌ |
| **手机访问** | ✅ Web 伴侣 — 手机浏览器扫码即用 | ❌ 需另外安装 App | ❌ |
| **数据位置** | ✅ 纯局域网 — 永不出网 | ❌ 上传到云端服务器 | ✅ 仅本地 |
| **需要账号** | ✅ 不需要 — 直接运行 | ❌ 需要注册登录 | 视情况 |
| **跨平台** | ✅ Windows / macOS / Linux | 因工具而异 | ❌ 单一生态 |
| **加密** | ✅ TLS 1.3 + AES-256-GCM | ⚠️ 仅 TLS（若有） | ❌ 无或基础 |
| **零配置** | ✅ mDNS 自动发现 | ❌ 需要手动配置 | ✅ |
| **开源** | ✅ MIT 开源协议 | ❌ 闭源 | ❌ |
| **价格** | ✅ 永久免费 | ❌ 免费增值 / 付费 | ✅ 免费 |

**总结：** 如果你只需要在同一生态内同步纯文本，系统自带工具够用。如果你需要同步富文本、图片、文件，跨平台使用，并且注重隐私 — ClipSync 正是为此而生。

---

## 功能特性

### 剪贴板同步

| 格式 | 类型 | 说明 |
|---|---|---|
| 纯文本 | `TEXT` | UTF-8 编码，完整 Unicode 支持 |
| 富文本 | `HTML` | 保留链接、表格、排版格式 |
| 富文本 | `RTF` | Microsoft Office 兼容 |
| 图片 | `IMAGE_PNG` | PNG 格式，支持任意分辨率 |

### 设备管理

- **自动发现** — 通过 mDNS/Zeroconf 自动发现局域网内的其他设备，无需配置 IP 地址
- **首次信任 (TOFU)** — 每台设备拥有唯一的 Ed25519 身份证书；首次配对时锁定
- **证书锁定** — 设备证书一旦发生变化，立即发出安全警告
- **配对码验证** — 首次连接时两端显示 8 位配对码，防止中间人攻击

### 安全架构

| 层级 | 技术方案 | 详情 |
|---|---|---|
| **应用层** | AES-256-GCM 逐帧加密 | 每对设备通过 HKDF 从 Ed25519 证书派生独立密钥；可选预共享密码进一步增强加密强度（PBKDF2, 60 万次迭代） |
| **传输层** | TLS 1.3 | Ed25519 自签名 X.509 证书 + 证书锁定（TOFU）；身份变更即时预警，防止中间人攻击 |
| **静态存储** | AES-256-GCM 落盘加密 | 私钥、剪贴板历史、敏感配置均加密存储；密码仅保存 PBKDF2 验证哈希，永不落盘 |

- **双层加密** — TLS 1.3 保护传输通道；AES-256-GCM 在应用层逐帧加密。每对设备自动派生独立密钥
- **静态加密** — 私钥、剪贴板历史、敏感配置字段均以 AES-256-GCM 加密存储
- **可选预共享密码** — 可为密钥派生增加带外约定的密码，启动时通过 PBKDF2 哈希验证

### 更多功能

- **Web 伴侣** — 内置 HTTP 服务器，手机在同一局域网扫码即可查看剪贴板历史、推送文字、传输文件。无需安装 App。支持 PWA "添加到主屏幕"，像原生应用一样使用。
- **文件传输** — 通过加密通道在已配对设备间传输文件
- **内容过滤** — 可选的基于正则表达式的敏感内容过滤（信用卡号、身份证号、API 密钥、密码等）
- **系统托盘** — 在后台静默运行，右键即可访问设置
- **桌面通知** — 可选的设备连接、断开、同步事件通知
- **深色模式** — 跟随系统主题或手动切换
- **开机自启** — 可选登录时自动启动

---

## 下载

从 [Releases 页面](https://github.com/kai3316/clipsync/releases) 获取最新版本：

| 平台 | 文件 | 备注 |
|---|---|---|
| Windows 10/11 | `clipsync.exe` | 便携版 — 无需安装 |
| macOS 12+ | `clipsync.app` (zip) | 通用二进制（Apple Silicon + Intel） |
| Linux (X11/Wayland) | `clipsync` (tar.gz) | 需安装 `xclip` 或 `wl-clipboard` |

无需 Python 环境，下载后直接运行即可。

> **macOS 用户注意：** 应用未经过公证。如果 Gatekeeper 阻止运行：
> ```bash
> xattr -cr clipsync.app
> ```
> 然后右键点击应用并选择 **打开**。如果仍然无法打开，从终端运行内部二进制文件查看错误详情：
> ```bash
> ./clipsync.app/Contents/MacOS/clipsync
> ```

---

## 从源码运行

需要 **Python 3.12+**。

```bash
# 克隆仓库
git clone https://github.com/kai3316/clipsync.git
cd clipsync

# 创建并激活虚拟环境
python -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate       # Windows

# 安装依赖
pip install -r requirements.txt

# 运行
python src/main.py
```

**Linux 用户请先安装依赖：**
```bash
# Debian/Ubuntu
sudo apt install xclip
# Fedora
sudo dnf install xclip
# Arch
sudo pacman -S xclip
# Wayland 用户请安装 wl-clipboard
```

---

## 构建独立可执行文件

使用 PyInstaller 打包：

```bash
pip install pyinstaller
pyinstaller clipsync.spec
```

构建结果在 `dist/` 目录：
- Windows: `dist/clipsync.exe`
- macOS: `dist/clipsync.app`
- Linux: `dist/clipsync`

`.spec` 文件自动收集所有内部模块和依赖（`zeroconf`、`cryptography`、`PIL`、`pystray`、`customtkinter`）。

---

## 工作原理

```
设备 A                              设备 B
   │                                     │
   ├─ 1. mDNS 广播 ──────────────────►    │  "我在这里: clipsync._tcp.local"
   │                                     │
   ├─ 2. TCP 连接 ◄──────────────────►    │  TLS 1.3 握手
   │                                     │
   ├─ 3. 身份交换 ───────────────────►    │  Ed25519 证书指纹
   │                                     │
   ├─ 4. 配对（首次）◄────────────────►   │  8 位配对码确认
   │     证书已锁定                       │  证书已锁定
   │                                     │
   ├─ 5. 检测剪贴板变化 ─────────────►     │  哈希去重 → 加密 → 发送
   │     AES-256-GCM 加密帧               │  解密 → 写入剪贴板
   │                                     │
   ├─ 6. 信任重连 ◄──────────────────►    │  锁定证书验证通过，自动连接
```

1. **发现** — mDNS/Zeroconf 在局域网内广播设备存在。服务类型 `_clipsync._tcp.local` 使设备无需配置 IP 即可自动互相发现。

2. **连接** — 建立 TCP 连接，使用自签名 Ed25519 证书进行 TLS 1.3 握手。在应用层交换证书指纹以进行身份验证。

3. **配对** — 首次接触时，两端显示相同的 8 位配对码（从证书指纹派生）。确认配对码即锁定对方证书 — 后续所有连接均为首次信任。

4. **同步** — 剪贴板监控器检测内容变化。发送前对内容进行哈希去重（防止回声循环）。使用 AES-256-GCM 逐帧加密（每对设备独立密钥），通过 TLS 1.3 发送。

5. **重连** — 已配对设备自动重连。如果对方证书自配对以来发生变化，用户会收到安全告警（可能是中间人攻击）。

---

## 配置项

| 设置 | 位置 | 说明 |
|---|---|---|
| 设备名称 | 控制面板 → 总览 | 自定义显示给其他设备的名称 |
| 同步开关 | 控制面板 → 总览 | 暂停/恢复剪贴板共享 |
| 开机自启 | 设置 | 系统登录时自动启动 |
| 主题 | 设置 | 浅色 / 深色 / 跟随系统 |
| 端口 | 设置 → 网络 | 默认 19990 |
| 中继 URL | 设置 → 网络 | 可选中继服务器，用于跨子网同步 |
| 内容过滤 | 设置 → 过滤器 | 正则过滤类别：信用卡、SSN、API 密钥等 |
| 加密开关 | 设置 → 安全 | 开启/关闭静态加密和逐帧加密 |
| 预共享密码 | 设置 → 安全 | 可选的共享密钥，为加密增加额外熵值 |
| 历史条目数 | 设置 → 高级 | 最大剪贴板历史条数（默认 50）|
| Web 伴侣 | 设置 → Web 伴侣 | 内置 HTTP 服务器，手机扫码即可访问（默认关闭） |
| Web 端口 | 设置 → Web 伴侣 | Web 服务端口（默认 19991） |
| Web 历史条数 | 设置 → Web 伴侣 | 网页上显示的剪贴板历史条数（默认 5） |
| 文件接收目录 | 设置 → 高级 | 接收文件的保存位置 |
| 轮询间隔 | 设置 → 高级 | 剪贴板检测频率（默认 0.4 秒）|
| 同步去抖 | 设置 → 高级 | 两次同步之间的最小间隔（默认 0.3 秒）|

### 数据存储位置

所有应用数据均存储在本地：

| 系统 | 配置和历史 | 日志 |
|---|---|---|
| Windows | `%APPDATA%\ClipSync\` | `%APPDATA%\ClipSync\clipsync.log` |
| macOS | `~/Library/Application Support/ClipSync/` | `~/Library/Logs/ClipSync/clipsync.log` |
| Linux | `~/.config/clipsync/` | `~/.local/share/clipsync/clipsync.log` |

- `config.json` — 设备身份、设备列表、设置（私钥加密存储）
- `clipboard_history.json` — 最近 N 条剪贴板记录（所有内容静态加密）

---

## 常见问题

### 设备互相发现不了？

1. 确保两台设备连接在**同一子网**（同一 WiFi 网络）
2. 企业网络可能开启了**客户端隔离**，阻止了 mDNS —— 可尝试使用手机热点
3. 检查防火墙是否放行 **UDP 5353 端口**（mDNS）和 **TCP 19990 端口**（ClipSync）
4. 如跨越子网，可尝试在设置中配置**中继 URL**

### 同步不生效？

1. 确认两台设备上的**同步开关**均已开启（控制面板 → 总览）
2. 检查**设备面板** — 对端设备应显示"已连接"并带有锁图标
3. 如果显示"已配对"但未连接，点击**重连**按钮
4. 检查**设置 → 安全**面板 — 如果一方开启了加密，双方必须保持一致；如设置了密码，双方必须使用相同密码

### 连接问题

- 查看**设备面板**中的状态指示器：
  - 绿色圆点 + 锁图标 = 已连接且加密
  - 橙色圆点 + "已配对" = 已信任但离线
  - 蓝色圆点 + "已发现" = 已发现但尚未配对
- 如果设备显示"已发现"但无法连接，尝试**移除**后重新发现
- 重启两台设备上的 ClipSync 通常能解决临时性的 mDNS 问题

### 如何获取日志？

- 右键系统托盘图标 → **导出日志**
- 或直接打开日志文件（路径见上方[数据存储位置](#数据存储位置)）
- 日志级别可在设置 → 高级中调整（DEBUG、INFO、WARNING、ERROR）

---

## 项目结构

```
clipsync/
├── src/
│   └── main.py                      # 应用入口
├── internal/
│   ├── clipboard/                   # 各平台剪贴板 I/O
│   │   ├── clipboard.py             # 工厂类 + 公共逻辑
│   │   ├── clipboard_windows.py     # Windows（win32clipboard）
│   │   ├── clipboard_darwin.py      # macOS（AppKit）
│   │   ├── clipboard_linux.py       # Linux（xclip/wl-paste）
│   │   ├── filter.py                # 内容过滤（正则）
│   │   ├── format.py                # 内容类型和同步消息
│   │   └── history.py               # 加密本地历史记录
│   ├── config/
│   │   └── config.py                # JSON 配置读写
│   ├── platform/
│   │   ├── autostart.py             # 各平台开机自启
│   │   └── notify.py                # 桌面通知
│   ├── protocol/
│   │   └── codec.py                 # 帧编解码
│   ├── security/
│   │   ├── encryption.py            # AES-256-GCM + HKDF + PBKDF2
│   │   └── pairing.py               # Ed25519 身份、TOFU、配对码
│   ├── sync/
│   │   ├── manager.py               # 同步编排 + 去重
│   │   └── file_transfer.py         # 文件传输协议
│   ├── transport/
│   │   ├── connection.py            # TLS 1.3 TCP 连接
│   │   └── discovery.py             # mDNS/Zeroconf 发现
│   ├── ui/
│   │   ├── dashboard.py             # 主窗口（5 个面板）
│   │   ├── settings_window.py       # 设置窗口（侧边栏导航）
│   │   ├── dialogs.py               # 主题化 CTk 对话框
│   │   └── systray.py               # 系统托盘图标和菜单
│   └── web/
│       ├── __init__.py
│       └── server.py                # 内置 HTTP 服务器 + 移动端 PWA
├── tests/                           # 204 个测试覆盖所有模块
├── assets/
│   └── icon.svg                     # 应用图标
├── docs/                            # GitHub Pages 站点
│   ├── index.html                   # 英文 Landing Page
│   └── index_zh.html                # 中文 Landing Page
├── clipsync.spec                   # PyInstaller 构建配置
├── requirements.txt                 # Python 依赖
├── README.md
├── README_zh.md
└── LICENSE
```

---

## 参与贡献

1. Fork 本仓库
2. 创建特性分支：`git checkout -b feat/my-feature`
3. 修改代码并确保所有测试通过：`python -m pytest tests/ -v`
4. 提交 Pull Request

请保持改动聚焦 — 一个 PR 只做一件事。

---

## 许可证

MIT — 详见 [LICENSE](LICENSE)
