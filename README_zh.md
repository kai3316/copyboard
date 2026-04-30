<p align="center">
  <a href="README.md">🇬🇧 English</a> &nbsp;|&nbsp;
  <a href="README_zh.md">🇨🇳 中文</a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/kai3316/copyboard/master/assets/icon.svg" alt="CopyBoard" width="80" height="80">
</p>

<h1 align="center">CopyBoard</h1>

<p align="center">
  <strong>跨平台剪贴板共享 —— 像 Apple 通用剪贴板，但面向所有平台。</strong>
  <br>
  Windows &harr; macOS &harr; Linux · 局域网 · TLS 1.3 加密
</p>

<p align="center">
  <a href="https://github.com/kai3316/copyboard/actions"><img src="https://github.com/kai3316/copyboard/actions/workflows/test.yml/badge.svg" alt="测试状态"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12-blue" alt="Python 3.12"></a>
  <a href="https://github.com/kai3316/copyboard/blob/master/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="许可证"></a>
</p>

---

## 为什么选择 CopyBoard？

你在 Windows 台式机上写代码，复制了一段代码片段。现在你想在 MacBook 上粘贴它。通常你会打开 Slack、邮件或笔记应用来中转。

**CopyBoard 省去了这个步骤。** 它自动在局域网内同步你的剪贴板 —— 不需要云服务、不需要注册、不需要配置。

- 一台设备复制 &rarr; 另一台粘贴 &mdash; 即时同步
- 文本、HTML、RTF、表格、图片 &mdash; 所有格式完整保留
- 系统托盘静默运行 &mdash; 空闲时零 CPU 占用
- 完全运行在局域网 &mdash; 数据永不经过云端

## 功能特性

| | 功能 |
|---|---|
| 🔌 | **零配置** — 设备通过 mDNS 自动发现对方 |
| 🔒 | **TLS 1.3 加密** — 所有流量加密，证书锁定防中间人攻击 |
| 📋 | **丰富内容** — 文本、HTML、RTF、表格、PNG 图片 |
| 🌍 | **真正的跨平台** — Windows、macOS、Linux |
| 🎨 | **现代界面** — 明暗主题、设备状态卡片、系统托盘 |
| ⚡ | **资源高效** — Windows 事件驱动，其他平台最小化轮询 |
| 📦 | **单文件可执行** — PyInstaller 打包，CI 自动构建 |
| 🧪 | **全面测试** — 99 项测试，CI 覆盖 3 个操作系统 |

## 工作原理

```
┌─────────── Windows ───────────┐          ┌──────────── Mac ────────────┐
│                               │          │                             │
│  📋  剪贴板监听器              │          │  📋  NSPasteboard 轮询器    │
│      (AddClipboardFormat-     │          │      (changeCount, 400ms)   │
│       Listener, 事件驱动)      │          │                             │
│              │                │          │              │              │
│              ▼                │          │              ▼              │
│  🔄  同步管理器                │   TLS    │  🔄  同步管理器             │
│      哈希去重 · 防抖动          │◄────────►│      哈希去重 · 防抖动       │
│              │                │  1.3     │              │              │
│              ▼                │          │              ▼              │
│  📡  传输层 (TCP:19990)       │          │  📡  传输层 (TCP:19990)     │
│              │                │          │              │              │
│              ▼                │          │              ▼              │
│  🔍  mDNS 服务浏览器          │           │  🔍  mDNS 服务浏览器        │
│      "_copyboard._tcp"        │          │      "_copyboard._tcp"      │
└───────────────────────────────┘          └─────────────────────────────┘
```

1. **发现** — 每台设备通过 mDNS 广播自身 (`_copyboard._tcp`)
2. **配对** — 首次连接：验证 8 位配对码，交换证书
3. **同步** — 检测到剪贴板变化 → 哈希比对 → TLS 广播 → 远端粘贴
4. **防回环** — 基于 SHA-256 内容哈希防回环；64 条目去重环

## 快速开始

### 方式一：下载预构建可执行文件

从 [GitHub Actions](https://github.com/kai3316/copyboard/actions/workflows/build.yml) 获取最新构建：

- **Windows** — `copyboard.exe`（构件：`copyboard-windows`）
- **macOS** — `copyboard.app`（构件：`copyboard-macos`）
- **Linux** — `copyboard` 二进制文件（构件：`copyboard-linux`）

点击最近一次成功的构建 → 滚动到底部 **Artifacts** → 下载对应你操作系统的文件。无需安装 Python。

### 方式二：从源码运行

**环境要求**

- Python 3.12
- Windows：无需额外依赖
- macOS：无需额外依赖（使用系统内置 `pbpaste`/`pbcopy`）
- Linux：需要 `xclip`（X11）或 `wl-clipboard`（Wayland）

### 从源码安装

```bash
# 克隆仓库
git clone https://github.com/kai3316/copyboard.git
cd copyboard

# 创建虚拟环境（推荐）
python -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows

# 安装依赖
pip install -r requirements.txt

# 运行
python cmd/main.py
```

CopyBoard 图标会出现在系统托盘中。同一局域网内，其他 CopyBoard 实例会被自动发现。

### 日志

```
# 设置日志级别 (DEBUG, INFO, WARNING, ERROR)
export COPYBOARD_LOG_LEVEL=DEBUG    # macOS/Linux
set COPYBOARD_LOG_LEVEL=DEBUG       # Windows
```

日志保存位置：
| 平台 | 路径 |
|---|---|
| Windows | `%APPDATA%\CopyBoard\copyboard.log` |
| macOS | `~/Library/Logs/CopyBoard/copyboard.log` |
| Linux | `~/.local/share/copyboard/copyboard.log` |

日志轮转：单文件最大 5 MB，保留 3 个备份。可从设置窗口或系统托盘导出日志。

### 配置

所有设置存储在平台对应的 `config.json` 中。设置窗口提供图形界面来配置：
- 设备名称
- 同步开关
- 开机自启动
- TCP 端口和服务类型
- 可选的中继服务器地址

## 开发

```bash
# 安装开发依赖
pip install pytest pytest-timeout

# 运行测试
python -m pytest tests/ -v

# 运行特定测试套件
python -m pytest tests/test_cross_platform_integration.py -v
```

### 项目结构

```
copyboard/
├── cmd/main.py              # 入口、日志设置、模块 wiring
├── internal/
│   ├── clipboard/           # 各平台剪贴板 I/O
│   │   ├── clipboard_windows.py   # Win32 API，事件驱动
│   │   ├── clipboard_darwin.py    # NSPasteboard 子进程方式
│   │   ├── clipboard_linux.py     # xclip / wl-paste
│   │   └── format.py              # 内容类型定义
│   ├── config/config.py     # JSON 配置、原子保存、损坏恢复
│   ├── protocol/codec.py    # 二进制 TLV 传输格式 (魔数: 0x4342)
│   ├── security/pairing.py  # Ed25519、X.509、证书锁定
│   ├── sync/manager.py      # 中央同步协调器
│   ├── transport/
│   │   ├── discovery.py     # mDNS/DNS-SD (zeroconf)
│   │   └── connection.py    # TCP + TLS 1.3 连接管理
│   └── ui/
│       ├── settings_window.py   # ttkbootstrap 现代化界面
│       └── systray.py           # pystray 系统托盘
└── tests/
    ├── test_codec.py                # 协议编解码
    ├── test_pairing.py              # 身份与配对逻辑
    ├── test_sync_manager.py         # 同步去重与节流
    ├── test_config.py               # 配置读写与原子保存
    ├── test_clipboard_sim.py        # 三平台剪贴板模拟
    └── test_cross_platform_integration.py  # 跨平台端到端同步
```

## 安全性

CopyBoard 使用类似蓝牙的配对模型：

| 阶段 | 机制 |
|---|---|
| **身份** | Ed25519 密钥对 + 自签名 X.509 证书 |
| **传输** | TLS 1.3，配对后验证证书 |
| **配对** | 8 位数字码（10⁸ 空间），频率限制（5 次 / 5 分钟） |
| **信任** | 证书锁定（TOFU）— 任何变更都会被检测并拒绝 |
| **首次联系** | 交换证书，支持指纹验证 |
| **中间人防护** | 配对后，证书变化会触发错误报警 |

## 许可证

MIT — 详见 [LICENSE](LICENSE)

---

<p align="center">
  <sub>使用 Python · ttkbootstrap · pystray · zeroconf · cryptography 构建</sub>
</p>
