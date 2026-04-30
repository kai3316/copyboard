<p align="center">
  <a href="README.md">English</a> &nbsp;|&nbsp;
  <a href="README_zh.md">中文</a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/kai3316/copyboard/master/assets/icon.svg" alt="CopyBoard" width="80" height="80">
</p>

<h1 align="center">CopyBoard</h1>

<p align="center">
  <strong>一台设备复制，另一台粘贴。即时同步。</strong>
  <br>
  Windows &middot; macOS &middot; Linux &middot; 局域网 &middot; 加密传输
</p>

<p align="center">
  <a href="https://github.com/kai3316/copyboard/releases"><img src="https://img.shields.io/badge/平台-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey" alt="平台"></a>
  <a href="https://github.com/kai3316/copyboard/blob/master/LICENSE"><img src="https://img.shields.io/badge/许可证-MIT-green" alt="许可证"></a>
</p>

---

## 这是什么？

你在 Windows 电脑上复制了一段文字、一张图片或一个表格。几秒后，在 MacBook 上直接粘贴。不需要发邮件、不需要聊天工具中转、不需要上传云端。

CopyBoard 通过局域网自动在多台设备间同步剪贴板内容。

- **即时同步** — 一台设备复制，另一台几秒内即可粘贴
- **全格式支持** — 纯文本、富文本（HTML/RTF）、表格、图片
- **后台静默运行** — 常驻系统托盘，不打扰工作
- **隐私优先** — 数据只在局域网内传输，不经任何云端

## 下载

从 [Releases 页面](https://github.com/kai3316/copyboard/releases) 获取最新版本：

| 平台 | 文件 |
|---|---|
| Windows | `copyboard.exe` |
| macOS | `copyboard.app`（zip 压缩包）|
| Linux | `copyboard`（tar.gz 压缩包）|

下载后直接运行即可，无需安装、无需 Python 环境。

> **macOS 用户请注意：** 应用未经过公证。首次启动时，请右键点击应用并选择 *打开*。

## 从源码运行

如果你更倾向于从源码运行，需要 Python 3.12 环境。

```bash
git clone https://github.com/kai3316/copyboard.git
cd copyboard
python -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate        # Windows
pip install -r requirements.txt
python cmd/main.py
```

**Linux 用户：** 请先安装 `xclip`（X11）或 `wl-clipboard`（Wayland）。

## 构建独立可执行文件

将 CopyBoard 打包为单个可执行文件：

```bash
pip install pyinstaller
pyinstaller copyboard.spec
```

构建结果在 `dist/` 目录：
- Windows: `dist/copyboard.exe`
- macOS: `dist/copyboard`
- Linux: `dist/copyboard`

## 工作原理

1. **发现** — CopyBoard 通过 mDNS 自动发现局域网内的其他设备
2. **配对** — 首次连接时，两端显示相同的 8 位配对码，确认后完成配对
3. **同步** — 检测剪贴板变化 → 哈希去重 → 加密传输 → 远端写入剪贴板
4. **信任** — 配对后的设备互相记住，后续自动连接无需再次确认

所有流量均通过 TLS 1.3 加密。配对采用证书锁定机制 —— 设备身份一旦发生变化，会立即发出警告。

## 设置

右键系统托盘图标可进行以下配置：

| 设置 | 说明 |
|---|---|
| 设备名称 | 自定义显示给其他设备的名称 |
| 同步开关 | 临时暂停剪贴板共享 |
| 开机自启 | 登录系统时自动启动 |
| 主题 | 浅色 / 深色模式切换 |

## 常见问题

**设备互相发现不了？**
确保两台设备连接在同一个局域网（同一 WiFi / 子网）。企业网络如果开启了客户端隔离，可能会阻止 mDNS。

**同步不生效？**
检查两台设备上的同步开关是否都已开启（右键托盘图标查看）。

**需要查看日志？**
右键托盘图标 → *导出日志*，或直接打开：
- Windows: `%APPDATA%\CopyBoard\copyboard.log`
- macOS: `~/Library/Logs/CopyBoard/copyboard.log`
- Linux: `~/.local/share/copyboard/copyboard.log`

## 许可证

MIT — 详见 [LICENSE](LICENSE)
