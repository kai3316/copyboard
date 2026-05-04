"""Built-in HTTP server for ClipSync Web Companion.

Serves a mobile-optimised web page that lets phones on the same LAN
view clipboard history, push text to the desktop, and transfer files.
Requires a valid token in the query string for all requests.
"""

import base64
import json
import logging
import os
import secrets
import socket
import sys
import time
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

logger = logging.getLogger(__name__)

# ── Upload directory ─────────────────────────────────────────────
def _get_upload_dir() -> str:
    d = os.path.join(os.path.expanduser("~"), "Downloads", "ClipSync")
    os.makedirs(d, exist_ok=True)
    return d

# ── Simple multipart form parser (no external deps) ──────────────

def _parse_multipart(body: bytes, content_type: str) -> dict:
    """Parse multipart/form-data. Returns {field_name: (filename, data)}."""
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("boundary="):
            boundary = part.split("=", 1)[1].strip().strip('"').strip("'")
            break
    if not boundary:
        return {}
    boundary_bytes = b"--" + boundary.encode("utf-8", errors="surrogateescape")
    parts = body.split(boundary_bytes)
    result = {}
    for part in parts:
        if not part or part in (b"--\r\n", b"--", b"\r\n"):
            continue
        part = part.lstrip(b"\r\n")
        # Strip trailing boundary markers
        for suffix in (b"\r\n--", b"--\r\n", b"\r\n--\r\n"):
            if part.endswith(suffix):
                part = part[:-len(suffix)]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        if b"\r\n\r\n" not in part:
            continue
        header_section, body_data = part.split(b"\r\n\r\n", 1)
        headers_text = header_section.decode("utf-8", errors="replace")
        field_name = None
        filename = None
        for line in headers_text.split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                for disp_part in line.split(";"):
                    disp_part = disp_part.strip()
                    key_lower = disp_part.lower().split("=", 1)[0].strip()
                    val = disp_part.split("=", 1)[1].strip().strip('"') if "=" in disp_part else ""
                    if key_lower == "name":
                        field_name = val
                    elif key_lower == "filename":
                        filename = val
        if field_name and body_data:
            result[field_name] = (filename or "", body_data)
    return result

# ── Embedded HTML template (single-page app, mobile-first) ────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="theme-color" content="#1A5276">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="ClipSync">
<link rel="manifest" href="/manifest.json?token=__TOKEN__">
<link rel="apple-touch-icon" href="/icon-192.png?token=__TOKEN__">
<title>ClipSync Web</title>
<style>
  /* ═══════════════════════════════════════════════════════════════
     ClipSync Web Companion — Design System
     ═══════════════════════════════════════════════════════════════ */

  /* ── Tokens ────────────────────────────────────────────────── */
  :root {
    --bg: #F0F2F5; --card: #FFFFFF; --text: #111827;
    --sub: #6B7280; --accent: #4F46E5; --accent2: #7C3AED;
    --accent-glow: rgba(79,70,229,.12);
    --border: #E5E7EB; --tag-bg: #EEF2FF; --tag-text: #4338CA;
    --success: #059669; --warn: #D97706; --danger: #DC2626;
    --shadow-sm: 0 1px 3px rgba(0,0,0,.04);
    --shadow: 0 1px 3px rgba(0,0,0,.06), 0 1px 2px rgba(0,0,0,.04);
    --shadow-md: 0 4px 16px rgba(0,0,0,.07);
    --shadow-lg: 0 12px 40px rgba(0,0,0,.10);
    --radius-sm: 10px; --radius: 14px; --radius-lg: 18px;
    --font: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, sans-serif;
  }
  .dark {
    --bg: #0A0E1A; --card: #131A2E; --text: #E2E8F0;
    --sub: #7C8AA0; --accent: #818CF8; --accent2: #A78BFA;
    --accent-glow: rgba(129,140,248,.15);
    --border: #1E293B; --tag-bg: #1E2040; --tag-text: #A5B4FC;
    --success: #10B981; --warn: #F59E0B; --danger: #EF4444;
    --shadow-sm: 0 1px 3px rgba(0,0,0,.15);
    --shadow: 0 1px 3px rgba(0,0,0,.2), 0 1px 2px rgba(0,0,0,.15);
    --shadow-md: 0 4px 16px rgba(0,0,0,.25);
    --shadow-lg: 0 12px 40px rgba(0,0,0,.35);
  }

  /* ── Reset & Base ──────────────────────────────────────────── */
  *, *::before, *::after { box-sizing:border-box; margin:0; padding:0; }
  html { height:100%; -webkit-text-size-adjust:100%; }
  body {
    font-family:var(--font); background:var(--bg); color:var(--text);
    min-height:100%; line-height:1.5; -webkit-tap-highlight-color:transparent;
    -webkit-font-smoothing:antialiased;
  }
  /* subtle bg texture */
  body::before {
    content:""; position:fixed; inset:0; pointer-events:none; z-index:-1;
    background: radial-gradient(ellipse 80% 50% at 50% -20%, var(--accent-glow), transparent);
  }

  /* ── Layout Container ──────────────────────────────────────── */
  .app { margin:0 auto; padding:0 16px 24px; }

  /* ── Header (glass) ────────────────────────────────────────── */
  header {
    position:sticky; top:0; z-index:50; padding:14px 0;
    display:flex; align-items:center; justify-content:space-between;
    backdrop-filter:blur(16px) saturate(180%);
    -webkit-backdrop-filter:blur(16px) saturate(180%);
    background:color-mix(in srgb, var(--bg) 75%, transparent);
    border-bottom:1px solid transparent;
    transition:border-color .3s, box-shadow .3s;
  }
  header.scrolled { border-bottom-color:var(--border); box-shadow:var(--shadow-sm); }
  .logo { display:flex; align-items:center; gap:10px; }
  .logo-icon {
    width:38px; height:38px; display:flex; align-items:center; justify-content:center;
    background:linear-gradient(135deg, var(--accent), var(--accent2));
    border-radius:11px; font-size:20px; color:#fff; box-shadow:0 2px 8px var(--accent-glow);
  }
  .logo-text { font-size:19px; font-weight:700; letter-spacing:-0.3px; }
  .header-actions { display:flex; gap:6px; }
  .icon-btn {
    background:var(--card); border:1px solid var(--border); border-radius:var(--radius-sm);
    width:36px; height:36px; display:flex; align-items:center; justify-content:center;
    cursor:pointer; font-size:15px; color:var(--text); transition:all .2s;
    box-shadow:var(--shadow-sm);
  }
  .icon-btn:hover { border-color:var(--accent); box-shadow:var(--shadow); }
  .icon-btn:active { transform:scale(.93); }
  .icon-btn.spin { animation:spin360 .6s ease-in-out; }
  @keyframes spin360 { to { transform:rotate(360deg); } }

  /* ── Devices Strip ─────────────────────────────────────────── */
  .devices-card {
    background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow);
    padding:12px 14px; margin-bottom:14px; border:1px solid var(--border);
  }
  .devices-title {
    font-size:11px; font-weight:700; color:var(--sub); text-transform:uppercase;
    letter-spacing:1px; margin-bottom:8px;
  }
  .device-row {
    display:flex; align-items:center; gap:10px; padding:6px 0;
    font-size:13px;
  }
  .device-row + .device-row { border-top:1px solid var(--border); }
  .status-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
  .status-dot.online {
    background:var(--success);
    box-shadow:0 0 6px color-mix(in srgb, var(--success) 50%, transparent);
    animation:pulse-dot 2s ease-in-out infinite;
  }
  @keyframes pulse-dot {
    0%, 100% { box-shadow:0 0 4px color-mix(in srgb, var(--success) 40%, transparent); }
    50% { box-shadow:0 0 10px color-mix(in srgb, var(--success) 70%, transparent); }
  }
  .status-dot.offline { background:var(--sub); }
  .device-name { flex:1; font-weight:500; }
  .device-status { font-size:11px; color:var(--sub); }

  /* ── Tabs ──────────────────────────────────────────────────── */
  .tabs {
    display:flex; gap:4px; margin-bottom:14px;
    background:var(--card); border-radius:var(--radius); padding:4px;
    box-shadow:var(--shadow-sm); border:1px solid var(--border);
  }
  .tab {
    flex:1; text-align:center; padding:9px 0; border-radius:var(--radius-sm);
    font-size:13px; font-weight:600; cursor:pointer; color:var(--sub);
    transition:all .25s cubic-bezier(.4,0,.2,1);
    border:none; background:transparent; font-family:var(--font);
  }
  .tab:hover { color:var(--text); }
  .tab.active {
    background:linear-gradient(135deg, var(--accent), var(--accent2));
    color:#fff; box-shadow:0 2px 8px var(--accent-glow);
  }

  /* ── Push Card ─────────────────────────────────────────────── */
  .push-card {
    background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow);
    padding:14px; margin-bottom:14px; border:1px solid var(--border);
  }
  .push-input {
    width:100%; min-height:80px; background:var(--bg); border:1.5px solid var(--border);
    border-radius:var(--radius-sm); padding:12px; font-size:14px; font-family:var(--font);
    color:var(--text); resize:vertical; line-height:1.5; transition:border-color .2s, box-shadow .2s;
  }
  .push-input::placeholder { color:var(--sub); }
  .push-input:focus {
    outline:none; border-color:var(--accent);
    box-shadow:0 0 0 3px var(--accent-glow);
  }
  .push-row { display:flex; align-items:center; justify-content:space-between; margin-top:10px; gap:10px; }
  .push-target { font-size:12px; color:var(--sub); flex:1; }
  .push-btn, .upload-btn {
    background:linear-gradient(135deg, var(--accent), var(--accent2));
    color:#fff; border:none; border-radius:var(--radius-sm);
    padding:9px 20px; font-size:13px; font-weight:600; font-family:var(--font);
    cursor:pointer; transition:all .2s; white-space:nowrap;
    box-shadow:0 2px 8px var(--accent-glow);
  }
  .push-btn:hover, .upload-btn:hover {
    transform:translateY(-1px); box-shadow:0 4px 14px var(--accent-glow);
  }
  .push-btn:active, .upload-btn:active { transform:scale(.96); }
  .push-btn:disabled, .upload-btn:disabled {
    opacity:.4; pointer-events:none; box-shadow:none; transform:none;
  }

  /* ── File Upload ───────────────────────────────────────────── */
  .upload-card {
    background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow);
    padding:14px; margin-bottom:14px; border:1px solid var(--border);
  }
  .upload-area {
    border:2px dashed var(--border); border-radius:var(--radius-sm); padding:28px 16px;
    text-align:center; cursor:pointer; transition:all .2s;
  }
  .upload-area:hover { border-color:var(--accent); background:var(--accent-glow); }
  .upload-area:active, .upload-area.drag-over { border-color:var(--accent); background:var(--accent-glow); }
  .upload-icon { font-size:36px; margin-bottom:8px; }
  .upload-text { font-size:13px; color:var(--sub); }
  .upload-file-name { font-size:13px; margin-top:8px; color:var(--accent); font-weight:600; }
  .file-input-hidden { display:none; }

  /* ── File List ─────────────────────────────────────────────── */
  .file-list { display:flex; flex-direction:column; gap:8px; }
  .file-item {
    background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow);
    padding:12px 14px; display:flex; align-items:center; gap:10px;
    border:1px solid var(--border); transition:all .2s;
    min-width:0; overflow:hidden;
  }
  .file-item:hover { box-shadow:var(--shadow-md); }
  .file-icon { font-size:26px; flex-shrink:0; }
  .file-info { flex:1; min-width:0; overflow:hidden; }
  .file-name { font-size:13px; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .file-meta { font-size:11px; color:var(--sub); margin-top:2px; }
  .file-dl {
    background:linear-gradient(135deg, var(--accent), var(--accent2));
    color:#fff; border:none; border-radius:8px;
    padding:6px 14px; font-size:12px; font-weight:600; cursor:pointer;
    font-family:var(--font); white-space:nowrap; flex-shrink:0;
    box-shadow:0 1px 4px var(--accent-glow);
  }
  .file-dl:active { transform:scale(.95); }

  /* ── History ───────────────────────────────────────────────── */
  .section-title {
    font-size:11px; font-weight:700; color:var(--sub); text-transform:uppercase;
    letter-spacing:1px; margin-bottom:10px; padding-left:2px;
  }
  .history-list { display:flex; flex-direction:column; gap:8px; }
  .clip-icon {
    font-size:20px; width:34px; height:34px; display:flex; align-items:center;
    justify-content:center; border-radius:9px; flex-shrink:0;
    background:var(--tag-bg);
  }
  .clip-body { flex:1; min-width:0; overflow:hidden; }
  .clip-preview {
    font-size:13px; line-height:1.5; display:-webkit-box;
    -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden;
    word-break:break-all;
  }
  .clip-meta { display:flex; gap:8px; margin-top:5px; font-size:11px; color:var(--sub); flex-wrap:wrap; }
  .clip-source {
    display:inline-flex; align-items:center; gap:4px;
    background:var(--tag-bg); color:var(--tag-text); border-radius:6px;
    padding:2px 8px; font-size:10px; font-weight:600;
  }
  .device-indicator { width:6px; height:6px; border-radius:50%; display:inline-block; }
  .device-indicator.local { background:var(--accent); }
  .device-indicator.remote { background:var(--warn); }

  /* ── History Item ──────────────────────────────────────────── */
  .clip-item {
    background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow);
    padding:13px; display:flex; gap:12px; cursor:pointer; transition:all .2s;
    align-items:flex-start; border:1px solid var(--border);
    animation:fadeUp .35s ease-out;
  }
  @keyframes fadeUp { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:translateY(0); } }
  .clip-item:hover { box-shadow:var(--shadow-md); border-color:color-mix(in srgb, var(--accent) 30%, var(--border)); }
  .clip-item:active { transform:scale(.985); border-color:var(--accent); }

  /* ── Action Buttons ────────────────────────────────────────── */
  .clip-actions { display:flex; flex-direction:column; gap:4px; flex-shrink:0; align-self:center; }
  .clip-action-btn {
    background:transparent; border:1px solid var(--border); border-radius:7px;
    padding:4px 8px; font-size:11px; font-weight:600; cursor:pointer;
    color:var(--sub); font-family:var(--font); transition:all .2s;
    white-space:nowrap; text-align:center;
  }
  .clip-action-btn:hover { border-color:currentColor; }
  .clip-action-btn:active { transform:scale(.94); }
  .clip-action-btn.btn-delete { color:var(--danger); border-color:color-mix(in srgb, var(--danger) 30%, transparent); }
  .clip-action-btn.btn-delete:hover { background:color-mix(in srgb, var(--danger) 8%, transparent); }
  .clip-action-btn.btn-pin { color:var(--accent); border-color:color-mix(in srgb, var(--accent) 30%, transparent); }
  .clip-action-btn.btn-pin:hover { background:var(--accent-glow); }
  .clip-action-btn.btn-nav { color:var(--accent2); border-color:color-mix(in srgb, var(--accent2) 30%, transparent); }
  .clip-action-btn.btn-nav:hover { background:var(--accent-glow); }
  .clip-action-btn.pinned { background:var(--accent); color:#fff; border-color:var(--accent); }

  /* ── Empty State ───────────────────────────────────────────── */
  .empty { text-align:center; padding:40px 24px; color:var(--sub); }
  .empty-icon { font-size:44px; margin-bottom:10px; opacity:.5; }
  .empty-text { font-size:13px; line-height:1.6; }
  .status-msg { text-align:center; padding:20px; color:var(--sub); font-size:13px; }

  /* ── Toast ─────────────────────────────────────────────────── */
  .toast {
    position:fixed; bottom:36px; left:50%; transform:translateX(-50%);
    background:var(--text); color:var(--bg); padding:10px 22px; border-radius:20px;
    font-size:13px; font-weight:600; opacity:0; pointer-events:none;
    transition:opacity .3s; z-index:100; box-shadow:var(--shadow-lg);
    font-family:var(--font);
  }
  .toast.show { opacity:1; }

  /* ── Install Banner ────────────────────────────────────────── */
  .install-banner {
    background:var(--card); border:1px solid var(--accent); border-radius:var(--radius-sm);
    padding:10px 14px; margin-bottom:12px; display:flex; align-items:center;
    gap:8px; font-size:12px; color:var(--sub); line-height:1.4;
  }
  .install-banner b { color:var(--accent); }

  /* ── Device Picker Overlay ─────────────────────────────────── */
  .picker-overlay {
    position:fixed; inset:0; background:rgba(0,0,0,.5);
    display:flex; align-items:center; justify-content:center;
    z-index:200; backdrop-filter:blur(4px); animation:fadeIn .2s ease-out;
  }
  @keyframes fadeIn { from { opacity:0; } to { opacity:1; } }
  .picker-panel {
    background:var(--card); border-radius:var(--radius-lg); padding:24px;
    min-width:260px; max-width:340px; box-shadow:var(--shadow-lg);
    animation:slideUp .25s ease-out;
  }
  @keyframes slideUp { from { opacity:0; transform:translateY(20px); } to { opacity:1; transform:translateY(0); } }
  .picker-title {
    font-size:16px; font-weight:700; margin-bottom:14px; text-align:center;
  }
  .picker-btn {
    display:block; width:100%; padding:12px 16px; margin-bottom:8px;
    background:var(--bg); border:1px solid var(--border); border-radius:var(--radius-sm);
    font-size:14px; font-weight:500; cursor:pointer; color:var(--text);
    font-family:var(--font); transition:all .2s; text-align:center;
  }
  .picker-btn:hover { border-color:var(--accent); background:var(--accent-glow); }
  .picker-btn:active { transform:scale(.97); background:var(--accent); color:#fff; border-color:var(--accent); }
  .picker-cancel {
    background:transparent; color:var(--sub); border-color:transparent;
    margin-top:4px; font-weight:400;
  }
  .picker-cancel:hover { color:var(--text); background:transparent; }

  /* ═══════════════════════════════════════════════════════════════
     Responsive Breakpoints
     ═══════════════════════════════════════════════════════════════ */

  /* ── Tablet (≥ 640px) ──────────────────────────────────────── */
  @media (min-width:640px) {
    .app { max-width:680px; padding:0 24px 36px; }
    header { padding:18px 0; }
    .logo-text { font-size:21px; }
    .logo-icon { width:42px; height:42px; border-radius:12px; font-size:22px; }
    .push-input { min-height:100px; font-size:15px; padding:14px; }
    .tab { padding:11px 0; font-size:14px; }
    .clip-item { padding:16px; }
    .clip-icon { font-size:24px; width:40px; height:40px; border-radius:10px; }
    .clip-preview { font-size:14px; }
    .clip-action-btn { padding:5px 10px; font-size:12px; }
    .clip-actions { gap:5px; }
    .file-list { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    .section-title { font-size:12px; margin-bottom:12px; }
    /* Horizontal device chips */
    #devicesList { display:flex; flex-wrap:wrap; gap:6px; }
    .device-row {
      flex:0 0 auto; min-width:0; font-size:12px; padding:4px 8px;
      background:var(--bg); border-radius:6px;
    }
    .device-row + .device-row { border-top:none; }
  }

  /* ── Desktop (≥ 1024px) ───────────────────────────────────── */
  @media (min-width:1024px) {
    .app { max-width:940px; padding:0 32px 40px; }
    header { padding:20px 0; }
    .logo-text { font-size:23px; }
    .push-input { min-height:110px; }
    /* 2-col history grid */
    .history-list { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
    /* clip item in grid: no animation offset */
    .history-list .clip-item { animation:none; }
    /* 3-col file grid */
    .file-list { grid-template-columns:1fr 1fr 1fr; }
  }

  /* ── Wide (≥ 1280px) ──────────────────────────────────────── */
  @media (min-width:1280px) {
    .app { max-width:1100px; }
    .history-list { grid-template-columns:1fr 1fr 1fr; }
    .file-list { grid-template-columns:1fr 1fr 1fr 1fr; }
  }
</style>
</head>
<body>
<div class="app">
  <header>
    <div class="logo">
      <span class="logo-icon">📋</span>
      <span class="logo-text">ClipSync</span>
    </div>
    <div class="header-actions">
      <button class="icon-btn" id="themeBtn" title="Toggle theme" onclick="toggleTheme()">🌓</button>
      <button class="icon-btn" id="refreshBtn" title="Refresh" onclick="refresh()">↻</button>
    </div>
  </header>

  <!-- iOS install hint -->
  <div class="install-banner" id="installBanner" style="display:none">
    <span>📱 Add to Home Screen: tap <b>Share</b> → <b>Add to Home Screen</b> to use like an app</span>
    <button onclick="document.getElementById('installBanner').style.display='none'" style="background:none;border:none;font-size:18px;cursor:pointer;color:var(--sub);padding:0 4px;">✕</button>
  </div>

  <!-- Connected devices -->
  <div class="devices-card" id="devicesCard">
    <div class="devices-title" id="devicesTitle">Connected Devices</div>
    <div id="devicesList"></div>
  </div>

  <!-- Tabs -->
  <div class="tabs">
    <button class="tab active" id="tabHistory" onclick="switchTab('history')">History</button>
    <button class="tab" id="tabFiles" onclick="switchTab('files')">Files</button>
  </div>

  <!-- History tab -->
  <div id="panelHistory">
    <div class="push-card">
      <textarea class="push-input" id="pushInput" rows="2" placeholder="Paste text here to send to desktop..."></textarea>
      <div class="push-row">
        <span class="push-target" id="pushTarget">To: this device</span>
        <button class="push-btn" id="pushBtn" onclick="pushText()">Push</button>
      </div>
    </div>
    <div class="section-title" id="historyTitle">Clipboard History</div>
    <div class="history-list" id="historyList"></div>
    <div class="status-msg" id="statusMsg">Loading...</div>
  </div>

  <!-- Files tab -->
  <div id="panelFiles" style="display:none">
    <div class="upload-card">
      <div class="upload-area" id="uploadArea" onclick="document.getElementById('fileInput').click()">
        <div class="upload-icon">📤</div>
        <div class="upload-text">Tap to select a file to upload</div>
        <div class="upload-file-name" id="uploadFileName" style="display:none"></div>
      </div>
      <input type="file" class="file-input-hidden" id="fileInput" onchange="onFileSelected(this)">
      <div class="push-row">
        <span class="push-target">Upload to desktop</span>
        <button class="upload-btn" id="uploadBtn" onclick="uploadFile()" disabled>Upload</button>
      </div>
    </div>
    <div class="section-title">Uploaded Files</div>
    <div class="file-list" id="fileList"></div>
    <div class="status-msg" id="fileStatusMsg">Loading...</div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
var I18N = __I18N__;
var DEVICE_ID = "__DEVICE_ID__";
var DEVICE_NAME = "__DEVICE_NAME__";

function t(key) { return I18N[key] || key; }

// ── Theme ─────────────────────────────────────────────────────
function applyTheme(dark) {
  document.documentElement.classList.toggle("dark", dark);
  document.getElementById("themeBtn").textContent = dark ? "☀" : "🌓";
  try { localStorage.setItem("clipsync_theme", dark ? "dark" : "light"); } catch(e) {}
}
function toggleTheme() {
  applyTheme(!document.documentElement.classList.contains("dark"));
}
(function initTheme() {
  var saved = null;
  try { saved = localStorage.getItem("clipsync_theme"); } catch(e) {}
  applyTheme(saved === "dark" || (!saved && window.matchMedia("(prefers-color-scheme:dark)").matches));
})();

// ── Toast ─────────────────────────────────────────────────────
var _toastTimer = 0;
function showToast(msg) {
  var el = document.getElementById("toast");
  el.textContent = msg; el.classList.add("show");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(function(){ el.classList.remove("show"); }, 1800);
}

// ── API helpers ───────────────────────────────────────────────
var _cache = { history: [], devices: [], files: [] };
var _currentTab = "history";

function api(path) {
  var sep = path.indexOf("?") >= 0 ? "&" : "?";
  return fetch(path + sep + "token=" + encodeURIComponent("__TOKEN__"))
    .then(function(r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
}

function loadData() {
  return Promise.all([
    api("/api/history"),
    api("/api/devices"),
  ]).then(function(results) {
    _cache.history = results[0].items || [];
    _cache.devices = results[1].devices || [];
    renderDevices();
    renderHistory();
    document.getElementById("statusMsg").style.display = "none";
  }).catch(function(e) {
    console.error("API error:", e);
    document.getElementById("statusMsg").textContent = I18N["web.error"] || "Connection lost.";
    document.getElementById("statusMsg").style.display = "";
  });
}

function loadFiles() {
  return api("/api/files").then(function(result) {
    _cache.files = result.files || [];
    renderFiles();
    document.getElementById("fileStatusMsg").style.display = "none";
  }).catch(function() {
    document.getElementById("fileStatusMsg").textContent = "Failed to load files.";
    document.getElementById("fileStatusMsg").style.display = "";
  });
}

// ── Tabs ──────────────────────────────────────────────────────
function switchTab(tab) {
  _currentTab = tab;
  document.getElementById("tabHistory").classList.toggle("active", tab === "history");
  document.getElementById("tabFiles").classList.toggle("active", tab === "files");
  document.getElementById("panelHistory").style.display = tab === "history" ? "" : "none";
  document.getElementById("panelFiles").style.display = tab === "files" ? "" : "none";
  if (tab === "files") loadFiles();
}

// ── Devices ───────────────────────────────────────────────────
function renderDevices() {
  var list = document.getElementById("devicesList");
  var title = document.getElementById("devicesTitle");
  var devs = _cache.devices;
  title.textContent = (I18N["web.connected_devices"] || "Connected Devices") + " (" + devs.length + ")";

  if (!devs.length) {
    list.innerHTML = '<div class="device-row"><span style="color:var(--sub);font-size:13px">No devices connected</span></div>';
    return;
  }
  list.innerHTML = devs.map(function(d) {
    var isLocal = d.device_id === DEVICE_ID;
    var statusText = isLocal ? "This device" : ((d.connected ? "Online" : "Offline") + (d.paired ? " · Paired" : ""));
    return '<div class="device-row">' +
      '<span class="status-dot ' + (d.connected ? 'online' : 'offline') + '"></span>' +
      '<span class="device-name">' + escapeHtml(d.device_name) + '</span>' +
      '<span class="device-status">' + statusText + '</span>' +
      '</div>';
  }).join("");
}

// ── History ───────────────────────────────────────────────────
function escapeHtml(s) {
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

var TYPE_ICONS = {"TEXT":"📝","HTML":"🌐","IMAGE":"🖼️","IMAGE_EMF":"🎨","RTF":"📋"};
var TYPE_LABELS = {"TEXT":"Text","HTML":"HTML","IMAGE":"Image","IMAGE_EMF":"Vector","RTF":"RTF"};

function formatTime(ts) {
  var d = new Date(ts * 1000);
  var now = new Date();
  var diff = Math.floor((now - d) / 1000);
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.floor(diff / 60) + "m ago";
  if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
  return d.toLocaleDateString();
}

function renderHistory() {
  var items = _cache.history;
  var list = document.getElementById("historyList");
  var title = document.getElementById("historyTitle");
  title.textContent = t("web.history_title") + (items.length ? " (" + items.length + ")" : "");

  if (!items.length) {
    list.innerHTML = '<div class="empty"><div class="empty-icon">📋</div>' +
      '<div class="empty-text">' + t("web.no_history") + '</div></div>';
    return;
  }

  list.innerHTML = items.map(function(item, i) {
    var icon = TYPE_ICONS[item.content_type] || "📄";
    var preview = escapeHtml(item.text_preview || "");
    var time = formatTime(item.timestamp);
    var isLocal = item.source_device === DEVICE_ID;
    var sourceLabel = isLocal ? I18N["web.this_device"] || "This Device" : (item.source_name || item.source_device || "Remote");
    var dotClass = isLocal ? "local" : "remote";
    var pinnedBadge = item.pinned ? ' 📌' : '';
    var pinBtnClass = item.pinned ? 'clip-action-btn btn-pin pinned' : 'clip-action-btn btn-pin';
    var pinLabel = item.pinned ? (I18N["web.unpin"] || "Unpin") : (I18N["web.pin"] || "Pin");
    var isUrl = item.text_preview && /^https?:\/\//.test(item.text_preview);
    var navBtn = isUrl
      ? '<button class="clip-action-btn btn-nav" onclick="event.stopPropagation();openOnDesktop(\'' +
        item.text_preview.replace(/'/g, "\\'") + '\')">🔗 ' + (I18N["web.open_on_desktop"] || "Open") + '</button>'
      : '';
    // Build action buttons
    var actions = '';
    if (isUrl) {
      actions += '<button class="clip-action-btn btn-nav" onclick="event.stopPropagation();openOnDesktop(\'' +
        item.text_preview.replace(/'/g, "\\'") + '\')">🔗 ' + (I18N["web.open_on_desktop"] || "Open") + '</button>';
    }
    actions += '<button class="' + pinBtnClass + '" onclick="event.stopPropagation();togglePin(' + i + ')">📌 ' + pinLabel + '</button>' +
      '<button class="clip-action-btn btn-delete" onclick="event.stopPropagation();deleteItem(' + i + ')">🗑 ' + (I18N["web.delete"] || "Delete") + '</button>';

    return '<div class="clip-item" onclick="copyItem(' + i + ', this)">' +
      '<div class="clip-icon">' + icon + '</div>' +
      '<div class="clip-body">' +
        '<div class="clip-preview">' + pinnedBadge + (preview || TYPE_LABELS[item.content_type] || "Item") + '</div>' +
        '<div class="clip-meta">' +
          '<span class="clip-source"><span class="device-indicator ' + dotClass + '"></span> ' +
          escapeHtml(sourceLabel) + '</span>' +
          '<span>' + time + '</span>' +
        '</div>' +
      '</div>' +
      '<div class="clip-actions">' + actions + '</div>' +
    '</div>';
  }).join("");
}

function copyItem(index, el) {
  var item = _cache.history[index];
  if (!item || !item.types) return;
  var keys = Object.keys(item.types);
  if (!keys.length) return;
  var ct = item.content_type || "";
  // Image types: download instead of copying
  if (ct === "IMAGE" || ct === "IMAGE_EMF") {
    var b64 = item.types[keys[0]];
    var mime = ct === "IMAGE_EMF" ? "image/emf" : "image/png";
    var ext = ct === "IMAGE_EMF" ? ".emf" : ".png";
    var a = document.createElement("a");
    a.href = "data:" + mime + ";base64," + b64;
    a.download = "clipboard_image" + ext;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    showToast("Image saved");
    return;
  }
  var key = keys[0];
  try {
    var decoded = atob(item.types[key]);
    var bytes = new Uint8Array(decoded.length);
    for (var i = 0; i < decoded.length; i++) bytes[i] = decoded.charCodeAt(i);
    var text = new TextDecoder("utf-8").decode(bytes);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function() {
        showToast(I18N["web.copied"] || "Copied!");
        el.style.borderColor = "var(--success)";
        setTimeout(function(){ el.style.borderColor = "transparent"; }, 600);
      }).catch(function() { showToast(I18N["web.copied"] || "Copied!"); });
    } else {
      var ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      document.execCommand("copy"); document.body.removeChild(ta);
      showToast(I18N["web.copied"] || "Copied!");
    }
  } catch(e) {
    showToast("Copy failed");
  }
}

function pushText() {
  var input = document.getElementById("pushInput");
  var text = input.value.trim();
  if (!text) return;
  var btn = document.getElementById("pushBtn");
  btn.disabled = true; btn.textContent = "...";
  var body = JSON.stringify({ text: text });
  fetch("/api/push?token=" + encodeURIComponent("__TOKEN__"), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: body,
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    if (data.ok) {
      input.value = "";
      showToast(I18N["web.push_sent"] || "Sent!");
    } else {
      showToast(data.error || "Failed");
    }
  })
  .catch(function(e) {
    showToast(I18N["web.server_error"] || "Server error");
  })
  .finally(function() {
    btn.disabled = false; btn.textContent = I18N["web.push_button"] || "Push";
  });
}

function _pickDevice(callback) {
  var devs = (_cache.devices || []).filter(function(d) { return d.connected; });
  if (devs.length <= 1) {
    callback(devs.length ? devs[0].device_id : "");
    return;
  }
  // Show device picker overlay
  var overlay = document.createElement("div");
  overlay.className = "picker-overlay";
  overlay.onclick = function() { overlay.remove(); };
  var panel = document.createElement("div");
  panel.className = "picker-panel";
  panel.onclick = function(e) { e.stopPropagation(); };
  panel.innerHTML = '<div class="picker-title">' + (I18N["web.select_device"] || "Select Device") + '</div>';
  devs.forEach(function(d) {
    var btn = document.createElement("button");
    btn.className = "picker-btn";
    btn.textContent = d.device_name;
    btn.onclick = function() { overlay.remove(); callback(d.device_id); };
    panel.appendChild(btn);
  });
  var cancel = document.createElement("button");
  cancel.className = "picker-btn picker-cancel";
  cancel.textContent = I18N["web.cancel"] || "Cancel";
  cancel.onclick = function() { overlay.remove(); };
  panel.appendChild(cancel);
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
}

function openOnDesktop(url) {
  _pickDevice(function(deviceId) {
    fetch("/api/nav?token=" + encodeURIComponent("__TOKEN__"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url, device_id: deviceId }),
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.ok) {
        showToast(I18N["web.push_sent"] || "Sent!");
      } else {
        showToast(I18N["web.server_error"] || "Server error");
      }
    })
    .catch(function() {
      showToast(I18N["web.server_error"] || "Server error");
    });
  });
}

// ── File upload / download ────────────────────────────────────
var _selectedFile = null;

function onFileSelected(input) {
  if (input.files.length) {
    _selectedFile = input.files[0];
    document.getElementById("uploadFileName").style.display = "";
    document.getElementById("uploadFileName").textContent = _selectedFile.name;
    document.getElementById("uploadBtn").disabled = false;
  }
}

function uploadFile() {
  if (!_selectedFile) return;
  _pickDevice(function(deviceId) {
    var btn = document.getElementById("uploadBtn");
    btn.disabled = true; btn.textContent = "...";
    var form = new FormData();
    form.append("file", _selectedFile);
    if (deviceId) form.append("device_id", deviceId);
    fetch("/api/upload?token=" + encodeURIComponent("__TOKEN__"), {
      method: "POST", body: form,
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.ok) {
        showToast("Uploaded: " + data.name);
        _selectedFile = null;
        document.getElementById("fileInput").value = "";
        document.getElementById("uploadFileName").style.display = "none";
        document.getElementById("uploadBtn").disabled = true;
        loadFiles();
      } else {
        showToast(data.error || "Upload failed");
      }
    })
    .catch(function() {
      showToast(I18N["web.server_error"] || "Server error");
    })
    .finally(function() {
      btn.disabled = false; btn.textContent = "Upload";
    });
  });
}

function downloadFile(filename) {
  var a = document.createElement("a");
  a.href = "/api/download?file=" + encodeURIComponent(filename) + "&token=" + encodeURIComponent("__TOKEN__");
  a.download = filename;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
}

function renderFiles() {
  var list = document.getElementById("fileList");
  var files = _cache.files;
  if (!files.length) {
    list.innerHTML = '<div class="empty"><div class="empty-icon">📂</div>' +
      '<div class="empty-text">No uploaded files yet</div></div>';
    return;
  }
  list.innerHTML = files.map(function(f) {
    return '<div class="file-item">' +
      '<span class="file-icon">📄</span>' +
      '<div class="file-info">' +
        '<div class="file-name">' + escapeHtml(f.name) + '</div>' +
        '<div class="file-meta">' + f.size + ' · ' + f.time + '</div>' +
      '</div>' +
      '<button class="file-dl" onclick="downloadFile(\'' + escapeHtml(f.name) + '\')">Download</button>' +
      '</div>';
  }).join("");
}

// ── Delete / Pin ──────────────────────────────────────────────
function deleteItem(index) {
  if (!confirm(I18N["web.delete_confirm"] || "Delete this item?")) return;
  fetch("/api/delete?token=" + encodeURIComponent("__TOKEN__"), {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({index: index}),
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    if (data.ok) {
      showToast(I18N["web.deleted"] || "Deleted");
      refresh();
    } else {
      showToast(data.error || "Failed");
    }
  })
  .catch(function() { showToast(I18N["web.server_error"] || "Server error"); });
}

function togglePin(index) {
  var item = _cache.history[index];
  if (!item) return;
  fetch("/api/pin?token=" + encodeURIComponent("__TOKEN__"), {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({index: index}),
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    if (data.ok) {
      showToast(data.pinned ? (I18N["web.pinned"] || "Pinned") : (I18N["web.unpinned"] || "Unpinned"));
      refresh();
    } else {
      showToast(data.error || "Failed");
    }
  })
  .catch(function() { showToast(I18N["web.server_error"] || "Server error"); });
}

// ── iOS detection ─────────────────────────────────────────────
function isIOS() {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

// ── Auto-refresh ──────────────────────────────────────────────
function refresh() {
  var btn = document.getElementById("refreshBtn");
  btn.classList.add("spin");
  setTimeout(function(){ btn.classList.remove("spin"); }, 600);
  loadData();
  if (_currentTab === "files") loadFiles();
}

// Pulse push button when user types
document.addEventListener("DOMContentLoaded", function() {
  var input = document.getElementById("pushInput");
  var btn = document.getElementById("pushBtn");
  input.addEventListener("input", function() {
    if (input.value.trim()) {
      btn.style.transform = "scale(1.05)";
      setTimeout(function(){ btn.style.transform = ""; }, 150);
    }
  });
});

(function init() {
  // iOS install banner
  if (isIOS() && !window.navigator.standalone) {
    document.getElementById("installBanner").style.display = "";
  }
  document.getElementById("historyTitle").textContent = I18N["web.history_title"] || "Clipboard History";
  document.getElementById("pushInput").placeholder = I18N["web.push_placeholder"] || "Paste text here...";
  document.getElementById("pushBtn").textContent = I18N["web.push_button"] || "Push";
  document.getElementById("statusMsg").textContent = I18N["web.loading"] || "Loading...";

  // Glass header scroll effect
  var header = document.querySelector("header");
  window.addEventListener("scroll", function() {
    header.classList.toggle("scrolled", window.scrollY > 4);
  }, { passive: true });

  refresh();
  setInterval(refresh, 3000);
})();
</script>
</body>
</html>"""

# ── PWA icons (generated at startup) ────────────────────────────

def _make_icon(size: int, dark: bool = False) -> bytes:
    """Generate a simple clipboard icon PNG with PIL."""
    from io import BytesIO
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = size // 8
    r = size // 6
    # Background rounded rect
    bg_color = (26, 39, 50, 255) if dark else (26, 82, 118, 255)
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=r, fill=bg_color,
    )
    # Clipboard shape: white rectangle with top clip
    cx = size // 2
    cy = size // 2
    bw, bh = size * 3 // 8, size * 5 // 12
    left, top = cx - bw // 2, cy - bh // 2
    # Board body
    draw.rounded_rectangle(
        [left, top + r // 2, left + bw, top + bh],
        radius=r // 2, fill=(255, 255, 255, 240),
    )
    # Clip on top
    clip_w = bw // 2
    draw.rounded_rectangle(
        [cx - clip_w // 2, top - r // 2, cx + clip_w // 2, top + r],
        radius=r // 3, fill=(255, 255, 255, 240),
    )
    # Lines on clipboard
    lx = left + bw // 5
    lw = bw * 3 // 5
    line_color = bg_color
    for li in range(3):
        ly = top + bh // 3 + li * (bh // 5)
        draw.rectangle([lx, ly, lx + lw, ly + size // 30], fill=line_color)

    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()

class WebServer:
    """Lightweight HTTP server for the ClipSync web companion."""

    def __init__(self, cfg, clipboard_history, sync_mgr, get_connected_ids=None,
                 on_nav_url=None, on_forward_file=None):
        self._cfg = cfg
        self._history = clipboard_history
        self._sync_mgr = sync_mgr
        self._get_connected_ids = get_connected_ids
        self._on_nav_url = on_nav_url
        self._on_forward_file = on_forward_file
        self._httpd: HTTPServer | None = None
        self._thread: Thread | None = None
        self._firewall_ok: bool = False
        # Pre-generate PWA icons
        self._icon_192 = _make_icon(192)
        self._icon_512 = _make_icon(512)
        self._upload_dir = _get_upload_dir()

    @property
    def firewall_ok(self) -> bool:
        return self._firewall_ok

    @property
    def is_running(self) -> bool:
        if self._thread is None or not self._thread.is_alive() or self._httpd is None:
            return False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            sock.connect(("127.0.0.1", self._cfg.web_port))
            sock.close()
            return True
        except Exception:
            return False

    FW_RULE_NAME = "ClipSync Web Companion"

    @staticmethod
    def check_firewall_rule(port: int | None = None) -> tuple[bool, str]:
        if sys.platform != "win32":
            return (True, "")
        import re
        try:
            import subprocess
            check = subprocess.run(
                ["netsh", "advfirewall", "firewall", "show", "rule",
                 f"name={WebServer.FW_RULE_NAME}", "verbose"],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=10,
            )
            if check.returncode != 0 or WebServer.FW_RULE_NAME not in check.stdout:
                return (False, "Blocked")
            if port is not None:
                m = re.search(r"LocalPort:\s+(\S+)", check.stdout)
                if not m or str(port) not in m.group(1).split(","):
                    actual = m.group(1) if m else "none"
                    return (False, f"Wrong port (got {actual}, needs {port})")
            return (True, "OK")
        except Exception:
            return (False, "Unknown")

    def _open_firewall(self, port: int) -> bool:
        if sys.platform != "win32":
            return True
        import subprocess
        ok, detail = WebServer.check_firewall_rule(port)
        if ok:
            return True
        if detail.startswith("Wrong port"):
            logger.info("Deleting stale firewall rule with wrong port")
            try:
                subprocess.run(
                    ["netsh", "advfirewall", "firewall", "delete", "rule",
                     f"name={WebServer.FW_RULE_NAME}"],
                    capture_output=True, text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=10,
                )
            except Exception:
                pass
        try:
            result = subprocess.run(
                ["netsh", "advfirewall", "firewall", "add", "rule",
                 f"name={WebServer.FW_RULE_NAME}",
                 "dir=in", "action=allow",
                 f"localport={port}", "protocol=TCP",
                 "profile=any"],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=10,
            )
            if result.returncode == 0:
                logger.info("Firewall rule created for port %d", port)
                return True
            else:
                logger.warning("Failed to create firewall rule: %s", result.stderr.strip()
                               or result.stdout.strip())
                return False
        except Exception as e:
            logger.warning("Firewall setup error: %s", e)
            return False

    def start(self) -> None:
        if self._thread is not None:
            return
        host = "0.0.0.0"
        port = self._cfg.web_port
        logger.info("Starting web companion on %s:%d", host, port)
        self._firewall_ok = self._open_firewall(port)

        cfg = self._cfg
        history = self._history
        sync_mgr = self._sync_mgr
        get_connected_ids = self._get_connected_ids
        on_nav_url = self._on_nav_url
        on_forward_file = self._on_forward_file
        upload_dir = self._upload_dir
        icon_192 = self._icon_192
        icon_512 = self._icon_512

        class _Handler(BaseHTTPRequestHandler):
            def log_message(inner_self, fmt, *args):
                pass

            def _token_ok(inner_self) -> bool:
                qs = urllib.parse.urlparse(inner_self.path).query
                params = urllib.parse.parse_qs(qs)
                tokens = params.get("token", [])
                return len(tokens) == 1 and tokens[0] == cfg.web_token

            def _send_json(inner_self, data, status=200):
                inner_self.send_response(status)
                inner_self.send_header("Content-Type", "application/json; charset=utf-8")
                inner_self.send_header("Cache-Control", "no-cache")
                inner_self.end_headers()
                try:
                    inner_self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
                except OSError:
                    pass

            def _send_html(inner_self, html: str, status=200):
                inner_self.send_response(status)
                inner_self.send_header("Content-Type", "text/html; charset=utf-8")
                inner_self.send_header("Cache-Control", "no-cache")
                inner_self.end_headers()
                try:
                    inner_self.wfile.write(html.encode("utf-8"))
                except OSError:
                    pass

            def _send_file(inner_self, filepath: str, mime: str = "application/octet-stream"):
                if not os.path.isfile(filepath):
                    inner_self._send_json({"error": "not found"}, 404)
                    return
                fsize = os.path.getsize(filepath)
                fname = os.path.basename(filepath)
                # RFC 5987: support non-ASCII filenames
                try:
                    fname.encode("latin-1")
                    disp = f'attachment; filename="{fname}"'
                except UnicodeEncodeError:
                    encoded = urllib.parse.quote(fname, safe="")
                    disp = f'attachment; filename="download"; filename*=UTF-8\'\'{encoded}'
                inner_self.send_response(200)
                inner_self.send_header("Content-Type", mime)
                inner_self.send_header("Content-Length", str(fsize))
                inner_self.send_header("Content-Disposition", disp)
                inner_self.send_header("Cache-Control", "no-cache")
                inner_self.end_headers()
                try:
                    with open(filepath, "rb") as f:
                        inner_self.wfile.write(f.read())
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    pass

            def do_OPTIONS(inner_self):
                inner_self.send_response(204)
                inner_self.send_header("Access-Control-Allow-Origin", "*")
                inner_self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                inner_self.send_header("Access-Control-Allow-Headers", "Content-Type")
                inner_self.send_header("Access-Control-Max-Age", "86400")
                inner_self.end_headers()

            def do_GET(inner_self):
                if not inner_self._token_ok():
                    inner_self._send_json({"error": "invalid token"}, 403)
                    return

                path = urllib.parse.urlparse(inner_self.path).path

                if path == "/" or path == "/index.html":
                    from internal.i18n import LOCALES
                    locale = cfg.language if cfg.language in LOCALES else "en"
                    i18n_json = json.dumps(LOCALES.get(locale, LOCALES["en"]), ensure_ascii=False)
                    html = _HTML.replace("__TOKEN__", cfg.web_token)
                    html = html.replace("__I18N__", i18n_json)
                    html = html.replace("__DEVICE_ID__", cfg.device_id)
                    html = html.replace("__DEVICE_NAME__", cfg.device_name)
                    inner_self._send_html(html)

                elif path == "/api/history":
                    items = history.get_all()
                    limit = cfg.web_history_limit
                    if limit > 0 and len(items) > limit:
                        items = items[:limit]
                    device_names = {cfg.device_id: cfg.device_name}
                    for peer in cfg.peers.values():
                        device_names[peer.device_id] = peer.device_name
                    device_names["__web__"] = "📱 Web"
                    result = []
                    for entry in items:
                        sid = entry.get("source_device", "")
                        result.append({
                            "timestamp": entry.get("timestamp"),
                            "content_type": entry.get("content_type", "TEXT"),
                            "text_preview": entry.get("text_preview", ""),
                            "types": entry.get("types", {}),
                            "source_device": sid,
                            "source_name": device_names.get(sid, sid),
                            "entry_id": entry.get("entry_id"),
                            "pinned": entry.get("pinned", False),
                        })
                    inner_self._send_json({"items": result})

                elif path == "/api/devices":
                    # Only return connected devices
                    connected_ids = set(get_connected_ids()) if get_connected_ids else set()
                    devices = [{
                        "device_id": cfg.device_id,
                        "device_name": cfg.device_name,
                        "connected": True,
                        "paired": True,
                    }]
                    for peer in cfg.peers.values():
                        is_conn = peer.device_id in connected_ids
                        if is_conn:
                            devices.append({
                                "device_id": peer.device_id,
                                "device_name": peer.device_name,
                                "connected": True,
                                "paired": peer.paired,
                            })
                    inner_self._send_json({"devices": devices})

                elif path == "/api/files":
                    files = []
                    try:
                        for fname in sorted(os.listdir(upload_dir)):
                            fpath = os.path.join(upload_dir, fname)
                            if os.path.isfile(fpath):
                                st = os.stat(fpath)
                                size_kb = max(1, st.st_size // 1024)
                                mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
                                files.append({
                                    "name": fname,
                                    "size": f"{size_kb} KB" if size_kb < 1024 else f"{size_kb // 1024:.1f} MB",
                                    "time": mtime,
                                })
                    except Exception:
                        pass
                    inner_self._send_json({"files": files})

                elif path == "/api/download":
                    qs = urllib.parse.urlparse(inner_self.path).query
                    params = urllib.parse.parse_qs(qs)
                    fname = (params.get("file", [""])[0] or "").strip()
                    if not fname or ".." in fname or "/" in fname or "\\" in fname:
                        inner_self._send_json({"error": "invalid filename"}, 400)
                        return
                    fpath = os.path.join(upload_dir, fname)
                    inner_self._send_file(fpath)

                elif path == "/api/status":
                    inner_self._send_json({"ok": True, "device": cfg.device_name})

                elif path == "/manifest.json":
                    manifest = {
                        "name": "ClipSync Web",
                        "short_name": "ClipSync",
                        "start_url": f"/?token={cfg.web_token}",
                        "display": "standalone",
                        "background_color": "#F5F7FA",
                        "theme_color": "#1A5276",
                        "icons": [
                            {"src": f"/icon-192.png?token={cfg.web_token}", "sizes": "192x192", "type": "image/png"},
                            {"src": f"/icon-512.png?token={cfg.web_token}", "sizes": "512x512", "type": "image/png"},
                        ],
                    }
                    inner_self._send_json(manifest)

                elif path == "/icon-192.png":
                    inner_self.send_response(200)
                    inner_self.send_header("Content-Type", "image/png")
                    inner_self.send_header("Content-Length", str(len(icon_192)))
                    inner_self.send_header("Cache-Control", "public, max-age=86400")
                    inner_self.end_headers()
                    try:
                        inner_self.wfile.write(icon_192)
                    except OSError:
                        pass

                elif path == "/icon-512.png":
                    inner_self.send_response(200)
                    inner_self.send_header("Content-Type", "image/png")
                    inner_self.send_header("Content-Length", str(len(icon_512)))
                    inner_self.send_header("Cache-Control", "public, max-age=86400")
                    inner_self.end_headers()
                    try:
                        inner_self.wfile.write(icon_512)
                    except OSError:
                        pass

                else:
                    inner_self._send_json({"error": "not found"}, 404)

            def do_POST(inner_self):
                if not inner_self._token_ok():
                    inner_self._send_json({"error": "invalid token"}, 403)
                    return

                path = urllib.parse.urlparse(inner_self.path).path
                length = int(inner_self.headers.get("Content-Length", 0))
                body = inner_self.rfile.read(length) if length else b""

                if path == "/api/push":
                    try:
                        data = json.loads(body.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        inner_self._send_json({"ok": False, "error": "invalid json"}, 400)
                        return
                    text = data.get("text", "").strip()
                    if not text:
                        inner_self._send_json({"ok": False, "error": "empty text"}, 400)
                        return

                    from internal.clipboard.format import ClipboardContent, ContentType, SyncMessage
                    import uuid
                    tee_bytes = text.encode("utf-8")
                    WEB_SOURCE = "__web__"
                    content = ClipboardContent(
                        types={ContentType.TEXT: tee_bytes},
                        source_device=WEB_SOURCE,
                    )
                    from internal.clipboard.platform import create_writer
                    writer = create_writer()
                    writer.write(content)
                    sync_mgr._suppress_monitor_until = time.time() + 2.0
                    if sync_mgr._history is not None:
                        try:
                            sync_mgr._history.add(content)
                        except Exception:
                            logger.debug("Failed to add web push to history", exc_info=True)
                    msg = SyncMessage(
                        content=content,
                        msg_id=uuid.uuid4().hex,
                        source_device=cfg.device_id,
                    )
                    if sync_mgr.on_send:
                        try:
                            sync_mgr.on_send(msg)
                        except Exception:
                            logger.debug("Web push broadcast failed", exc_info=True)
                    logger.info("Web push: %d chars", len(text))
                    inner_self._send_json({"ok": True, "len": len(text)})

                elif path == "/api/nav":
                    try:
                        data = json.loads(body.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        inner_self._send_json({"ok": False, "error": "invalid json"}, 400)
                        return
                    url = data.get("url", "").strip()
                    if not url:
                        inner_self._send_json({"ok": False, "error": "empty url"}, 400)
                        return
                    target_device = data.get("device_id", "")
                    if target_device and target_device != cfg.device_id and on_nav_url:
                        on_nav_url(url, target_device)
                    else:
                        import webbrowser
                        webbrowser.open(url)
                    logger.info("Web nav: %s -> %s", url[:80], target_device[:12] or "local")
                    inner_self._send_json({"ok": True})

                elif path == "/api/upload":
                    content_type = inner_self.headers.get("Content-Type", "")
                    if "multipart" not in content_type:
                        inner_self._send_json({"ok": False, "error": "expect multipart/form-data"}, 400)
                        return
                    fields = _parse_multipart(body, content_type)
                    file_field = fields.get("file")
                    if not file_field:
                        inner_self._send_json({"ok": False, "error": "no file field"}, 400)
                        return
                    fname, fdata = file_field
                    if not fname:
                        fname = "uploaded_file"
                    target_device = ""
                    target_field = fields.get("device_id")
                    if target_field:
                        target_device = target_field[1].decode("utf-8", errors="replace")
                    # Sanitize filename
                    safe_name = os.path.basename(fname).replace("\\", "_").replace("/", "_")
                    if not safe_name:
                        safe_name = "uploaded_file"
                    dest = os.path.join(upload_dir, safe_name)
                    # Avoid overwriting: append number if exists
                    base, ext = os.path.splitext(safe_name)
                    counter = 1
                    while os.path.exists(dest):
                        dest = os.path.join(upload_dir, f"{base} ({counter}){ext}")
                        counter += 1
                    with open(dest, "wb") as f:
                        f.write(fdata)
                    logger.info("Web upload: %s (%d bytes) → %s", safe_name, len(fdata), dest)
                    if target_device and target_device != cfg.device_id and on_forward_file:
                        on_forward_file(dest, target_device)
                    inner_self._send_json({"ok": True, "name": os.path.basename(dest), "size": len(fdata)})

                elif path == "/api/delete":
                    try:
                        data = json.loads(body.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        inner_self._send_json({"ok": False, "error": "invalid json"}, 400)
                        return
                    idx = data.get("index", -1)
                    if not isinstance(idx, int) or idx < 0:
                        inner_self._send_json({"ok": False, "error": "invalid index"}, 400)
                        return
                    ok = history.delete(idx)
                    inner_self._send_json({"ok": ok})

                elif path == "/api/pin":
                    try:
                        data = json.loads(body.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        inner_self._send_json({"ok": False, "error": "invalid json"}, 400)
                        return
                    idx = data.get("index", -1)
                    if not isinstance(idx, int) or idx < 0:
                        inner_self._send_json({"ok": False, "error": "invalid index"}, 400)
                        return
                    entry = history.get(idx)
                    if entry is None:
                        inner_self._send_json({"ok": False, "error": "not found"}, 404)
                        return
                    if entry.get("pinned"):
                        history.unpin(idx)
                        inner_self._send_json({"ok": True, "pinned": False})
                    else:
                        history.pin(idx)
                        inner_self._send_json({"ok": True, "pinned": True})

                else:
                    inner_self._send_json({"error": "not found"}, 404)

        try:
            self._httpd = HTTPServer((host, port), _Handler)
        except OSError as e:
            logger.warning("Web server failed to bind %s:%d: %s", host, port, e)
            return

        self._thread = Thread(target=self._httpd.serve_forever, daemon=True, name="web-server")
        self._thread.start()
        logger.info("Web companion listening on http://%s:%d", self._get_lan_ip(), port)

    def stop(self) -> None:
        if self._httpd is not None:
            logger.info("Stopping web companion")
            self._httpd.shutdown()
            self._httpd = None
        self._thread = None

    @staticmethod
    def _get_lan_ip() -> str:
        """Return the best LAN IP reachable from other devices on the local network.

        Prefers 192.168.x.x over 10.x.x.x over 172.16-31.x.x (VPN range).
        Falls back to the OS-chosen default route if no private IP is found.
        """
        all_ips = WebServer.get_all_ips()
        if not all_ips:
            return "127.0.0.1"
        # Sort by preference: real LAN > VPN/tunnel ranges
        def _priority(ip):
            if ip.startswith("192.168."):
                return 0
            if ip.startswith("10."):
                return 1
            # 172.16.0.0 – 172.31.255.255 (often VPN)
            if ip.startswith("172."):
                try:
                    second = int(ip.split(".")[1])
                    if 16 <= second <= 31:
                        return 2
                except ValueError:
                    pass
            return 3  # non-private, use as last resort
        all_ips.sort(key=_priority)
        return all_ips[0]

    @staticmethod
    def get_all_ips() -> list[str]:
        ips = []
        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET,
                                           socket.SOCK_STREAM, 0, socket.AI_PASSIVE):
                ip = info[4][0]
                if ip and not ip.startswith("127.") and ip not in ips:
                    ips.append(ip)
        except Exception:
            pass
        if not ips:
            # Fallback: use OS default route
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0)
                s.connect(("10.254.254.254", 1))
                default_ip = s.getsockname()[0]
                s.close()
                if default_ip and default_ip not in ips:
                    ips.append(default_ip)
            except Exception:
                if "127.0.0.1" not in ips:
                    ips.append("127.0.0.1")
        return ips
