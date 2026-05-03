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
  :root {
    --bg: #F5F7FA; --card: #FFFFFF; --text: #1A1A2E;
    --sub: #7F8C8D; --accent: #2471A3; --accent-hover: #1A5276;
    --border: #E5E7EB; --tag-bg: #EBF5FB; --tag-text: #2471A3;
    --success: #27AE60; --warn: #E67E22; --danger: #E74C3C; --shadow: 0 2px 12px rgba(0,0,0,.06);
    --radius: 14px;
  }
  .dark {
    --bg: #0F1923; --card: #1A2732; --text: #E8EDF2;
    --sub: #8899AA; --accent: #5DADE2; --accent-hover: #85C1E9;
    --border: #2C3E50; --tag-bg: #1B3346; --tag-text: #5DADE2;
    --shadow: 0 2px 12px rgba(0,0,0,.25);
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  html { height:100%; }
  body {
    font-family:-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background:var(--bg); color:var(--text); min-height:100%;
    -webkit-tap-highlight-color:transparent;
  }
  .app { max-width:600px; margin:0 auto; padding:0 16px 24px; }

  header { padding:20px 0 16px; display:flex; align-items:center; justify-content:space-between; }
  .logo { display:flex; align-items:center; gap:10px; }
  .logo-icon { font-size:28px; }
  .logo-text { font-size:20px; font-weight:700; letter-spacing:-0.3px; }
  .header-actions { display:flex; gap:8px; }
  .icon-btn {
    background:var(--card); border:1px solid var(--border); border-radius:10px;
    width:38px; height:38px; display:flex; align-items:center; justify-content:center;
    cursor:pointer; font-size:16px; color:var(--text); transition:all .15s;
  }
  .icon-btn:active { transform:scale(.93); background:var(--border); }
  .icon-btn.spin { animation: spin360 .6s ease-in-out; }
  @keyframes spin360 { to { transform:rotate(360deg); } }

  /* History item animations */
  .clip-item {
    background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow);
    padding:14px; display:flex; gap:12px; cursor:pointer; transition:all .15s;
    align-items:flex-start; border:1.5px solid transparent;
    animation: fadeUp .3s ease-out;
  }
  @keyframes fadeUp { from { opacity:0; transform:translateY(12px); } to { opacity:1; transform:translateY(0); } }
  .clip-item:active { transform:scale(.98); border-color:var(--accent); }

  /* Connected devices */
  .devices-card {
    background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow);
    padding:12px 14px; margin-bottom:16px;
  }
  .devices-title {
    font-size:12px; font-weight:600; color:var(--sub); text-transform:uppercase;
    letter-spacing:.8px; margin-bottom:8px;
  }
  .device-row {
    display:flex; align-items:center; gap:10px; padding:6px 0;
    font-size:14px;
  }
  .device-row + .device-row { border-top:1px solid var(--border); }
  .status-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
  .status-dot.online { background:var(--success); }
  .status-dot.offline { background:var(--sub); }
  .device-name { flex:1; font-weight:500; }
  .device-status { font-size:11px; color:var(--sub); }

  /* Tabs */
  .tabs {
    display:flex; gap:4px; margin-bottom:16px;
    background:var(--card); border-radius:var(--radius); padding:4px;
    box-shadow:var(--shadow);
  }
  .tab {
    flex:1; text-align:center; padding:8px 0; border-radius:10px;
    font-size:13px; font-weight:600; cursor:pointer; color:var(--sub);
    transition:all .15s; border:none; background:transparent; font-family:inherit;
  }
  .tab.active { background:var(--accent); color:#fff; transition:all .2s ease; }
  .tab-content { animation: fadeUp .25s ease-out; }

  /* Push area */
  .push-card {
    background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow);
    padding:14px; margin-bottom:16px;
  }
  .push-input {
    width:100%; min-height:80px; background:var(--bg); border:1px solid var(--border);
    border-radius:10px; padding:12px; font-size:15px; font-family:inherit;
    color:var(--text); resize:vertical; line-height:1.5;
  }
  .push-input::placeholder { color:var(--sub); }
  .push-input:focus { outline:none; border-color:var(--accent); }
  .push-row { display:flex; align-items:center; justify-content:space-between; margin-top:10px; gap:10px; }
  .push-target { font-size:13px; color:var(--sub); flex:1; }
  .push-btn, .upload-btn {
    background:var(--accent); color:#fff; border:none; border-radius:10px;
    padding:10px 22px; font-size:14px; font-weight:600; font-family:inherit;
    cursor:pointer; transition:all .15s; white-space:nowrap;
  }
  .push-btn:active, .upload-btn:active { transform:scale(.96); opacity:.85; }
  .push-btn:disabled, .upload-btn:disabled { opacity:.5; pointer-events:none; }

  /* File upload */
  .upload-card {
    background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow);
    padding:14px; margin-bottom:16px;
  }
  .upload-area {
    border:2px dashed var(--border); border-radius:10px; padding:24px;
    text-align:center; cursor:pointer; transition:border-color .15s;
  }
  .upload-area:active, .upload-area.drag-over { border-color:var(--accent); }
  .upload-icon { font-size:32px; margin-bottom:8px; }
  .upload-text { font-size:13px; color:var(--sub); }
  .upload-file-name { font-size:13px; margin-top:8px; color:var(--accent); }
  .file-input-hidden { display:none; }

  /* File list */
  .file-list { display:flex; flex-direction:column; gap:8px; }
  .file-item {
    background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow);
    padding:12px 14px; display:flex; align-items:center; gap:10px;
  }
  .file-icon { font-size:24px; flex-shrink:0; }
  .file-info { flex:1; min-width:0; }
  .file-name { font-size:14px; font-weight:500; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .file-meta { font-size:11px; color:var(--sub); margin-top:2px; }
  .file-dl {
    background:var(--accent); color:#fff; border:none; border-radius:8px;
    padding:6px 12px; font-size:12px; font-weight:600; cursor:pointer;
    font-family:inherit; white-space:nowrap;
  }
  .file-dl:active { transform:scale(.95); }

  /* History section */
  .section-title {
    font-size:13px; font-weight:600; color:var(--sub); text-transform:uppercase;
    letter-spacing:.8px; margin-bottom:10px; padding-left:2px;
  }
  .history-list { display:flex; flex-direction:column; gap:10px; }
  .clip-icon {
    font-size:22px; width:36px; height:36px; display:flex; align-items:center;
    justify-content:center; border-radius:10px; flex-shrink:0;
    background:var(--tag-bg);
  }
  .clip-body { flex:1; min-width:0; }
  .clip-preview {
    font-size:14px; line-height:1.45; display:-webkit-box;
    -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden;
    word-break:break-all;
  }
  .clip-meta { display:flex; gap:10px; margin-top:6px; font-size:11px; color:var(--sub); }
  .clip-source {
    display:inline-flex; align-items:center; gap:4px;
    background:var(--tag-bg); color:var(--tag-text); border-radius:6px;
    padding:2px 8px; font-size:11px; font-weight:500;
  }
  .device-indicator { width:6px; height:6px; border-radius:50%; display:inline-block; }
  .device-indicator.local { background:var(--accent); }
  .device-indicator.remote { background:var(--warn); }

  .empty { text-align:center; padding:48px 24px; color:var(--sub); }
  .empty-icon { font-size:48px; margin-bottom:12px; opacity:.6; }
  .empty-text { font-size:14px; line-height:1.6; }

  .toast {
    position:fixed; bottom:40px; left:50%; transform:translateX(-50%);
    background:#1A1A2E; color:#fff; padding:10px 24px; border-radius:20px;
    font-size:14px; font-weight:500; opacity:0; pointer-events:none;
    transition:opacity .25s; z-index:100; box-shadow:0 4px 20px rgba(0,0,0,.3);
  }
  .toast.show { opacity:1; }
  .status-msg { text-align:center; padding:24px; color:var(--sub); font-size:14px; }

  /* Install banner */
  .install-banner {
    background:var(--card); border:1px solid var(--accent); border-radius:10px;
    padding:10px 14px; margin-bottom:12px; display:flex; align-items:center;
    gap:8px; font-size:12px; color:var(--sub); line-height:1.4;
  }
  .install-banner b { color:var(--accent); }

  /* Item action buttons */
  .clip-actions { display:flex; gap:6px; margin-top:8px; }
  .clip-action-btn {
    background:transparent; border:1px solid var(--border); border-radius:8px;
    padding:5px 12px; font-size:12px; font-weight:500; cursor:pointer;
    color:var(--sub); font-family:inherit; transition:all .15s;
  }
  .clip-action-btn:active { transform:scale(.94); }
  .clip-action-btn.btn-delete { color:var(--danger); border-color:var(--danger); }
  .clip-action-btn.btn-pin { color:var(--accent); border-color:var(--accent); }
  .clip-action-btn.pinned { background:var(--accent); color:#fff; border-color:var(--accent); }

  @media (min-width:600px) {
    .app { padding:0 0 32px; }
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
    return '<div class="clip-item" onclick="copyItem(' + i + ', this)">' +
      '<div class="clip-icon">' + icon + '</div>' +
      '<div class="clip-body">' +
        '<div class="clip-preview">' + pinnedBadge + (preview || TYPE_LABELS[item.content_type] || "Item") + '</div>' +
        '<div class="clip-meta">' +
          '<span class="clip-source"><span class="device-indicator ' + dotClass + '"></span> ' +
          escapeHtml(sourceLabel) + '</span>' +
          '<span>' + time + '</span>' +
        '</div>' +
        '<div class="clip-actions">' +
          '<button class="' + pinBtnClass + '" onclick="event.stopPropagation();togglePin(' + i + ')">📌 ' + pinLabel + '</button>' +
          '<button class="clip-action-btn btn-delete" onclick="event.stopPropagation();deleteItem(' + i + ')">🗑 ' + (I18N["web.delete"] || "Delete") + '</button>' +
        '</div>' +
      '</div>' +
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
  var btn = document.getElementById("uploadBtn");
  btn.disabled = true; btn.textContent = "...";
  var form = new FormData();
  form.append("file", _selectedFile);
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

    def __init__(self, cfg, clipboard_history, sync_mgr, get_connected_ids=None):
        self._cfg = cfg
        self._history = clipboard_history
        self._sync_mgr = sync_mgr
        self._get_connected_ids = get_connected_ids
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
                inner_self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

            def _send_html(inner_self, html: str, status=200):
                inner_self.send_response(status)
                inner_self.send_header("Content-Type", "text/html; charset=utf-8")
                inner_self.send_header("Cache-Control", "no-cache")
                inner_self.end_headers()
                inner_self.wfile.write(html.encode("utf-8"))

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
                with open(filepath, "rb") as f:
                    inner_self.wfile.write(f.read())

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
                    inner_self.wfile.write(icon_192)

                elif path == "/icon-512.png":
                    inner_self.send_response(200)
                    inner_self.send_header("Content-Type", "image/png")
                    inner_self.send_header("Content-Length", str(len(icon_512)))
                    inner_self.send_header("Cache-Control", "public, max-age=86400")
                    inner_self.end_headers()
                    inner_self.wfile.write(icon_512)

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
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0)
            s.connect(("10.254.254.254", 1))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

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
            ips.append(WebServer._get_lan_ip())
        return ips
