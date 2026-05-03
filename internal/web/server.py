"""Built-in HTTP server for ClipSync Web Companion.

Serves a mobile-optimised web page that lets phones on the same LAN
view clipboard history and push text back to the desktop.  Requires
a valid token in the query string for all requests.
"""

import base64
import json
import logging
import secrets
import socket
import sys
import time
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

logger = logging.getLogger(__name__)

# ── Embedded HTML template (single-page app, mobile-first) ────────────
# Served at / with embedded CSS/JS.  All i18n strings are provided
# by the server via a JS bootstrap variable to keep the HTML cacheable.

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="theme-color" content="#1A5276">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="ClipSync">
<title>ClipSync Web</title>
<style>
  :root {
    --bg: #F5F7FA; --card: #FFFFFF; --text: #1A1A2E;
    --sub: #7F8C8D; --accent: #2471A3; --accent-hover: #1A5276;
    --border: #E5E7EB; --tag-bg: #EBF5FB; --tag-text: #2471A3;
    --success: #27AE60; --warn: #E67E22; --shadow: 0 2px 12px rgba(0,0,0,.06);
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

  /* Header */
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

  /* Device selector */
  .device-bar {
    display:flex; align-items:center; gap:10px; padding:10px 14px;
    background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow);
    margin-bottom:16px;
  }
  .device-dot { width:10px; height:10px; border-radius:50%; background:var(--success); flex-shrink:0; }
  .device-select {
    flex:1; background:transparent; border:none; color:var(--text); font-size:14px;
    font-weight:600; font-family:inherit; appearance:none;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M6 8L1 3h10z' fill='%237F8C8D'/%3E%3C/svg%3E");
    background-repeat:no-repeat; background-position:right center; padding-right:20px;
  }

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
  .push-btn {
    background:var(--accent); color:#fff; border:none; border-radius:10px;
    padding:10px 22px; font-size:14px; font-weight:600; font-family:inherit;
    cursor:pointer; transition:all .15s; white-space:nowrap;
  }
  .push-btn:active { transform:scale(.96); opacity:.85; }

  /* History section */
  .section-title {
    font-size:13px; font-weight:600; color:var(--sub); text-transform:uppercase;
    letter-spacing:.8px; margin-bottom:10px; padding-left:2px;
  }
  .history-list { display:flex; flex-direction:column; gap:10px; }
  .clip-item {
    background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow);
    padding:14px; display:flex; gap:12px; cursor:pointer; transition:all .15s;
    align-items:flex-start; border:1.5px solid transparent;
  }
  .clip-item:active { transform:scale(.98); border-color:var(--accent); }
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

  /* Empty state */
  .empty {
    text-align:center; padding:48px 24px; color:var(--sub);
  }
  .empty-icon { font-size:48px; margin-bottom:12px; opacity:.6; }
  .empty-text { font-size:14px; line-height:1.6; }

  /* Toast */
  .toast {
    position:fixed; bottom:40px; left:50%; transform:translateX(-50%);
    background:#1A1A2E; color:#fff; padding:10px 24px; border-radius:20px;
    font-size:14px; font-weight:500; opacity:0; pointer-events:none;
    transition:opacity .25s; z-index:100; box-shadow:0 4px 20px rgba(0,0,0,.3);
  }
  .toast.show { opacity:1; }

  /* Loading / error */
  .status-msg { text-align:center; padding:24px; color:var(--sub); font-size:14px; }
  .error-msg { text-align:center; padding:24px; color:#E74C3C; font-size:14px; }

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
    </div>
  </header>

  <div class="device-bar">
    <span class="device-dot"></span>
    <select class="device-select" id="deviceFilter" onchange="renderHistory()">
      <option value="all">All Devices</option>
      <option value="local">This Device</option>
    </select>
  </div>

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

<div class="toast" id="toast"></div>

<script>
// Bootstrap data from server
var I18N = __I18N__;
var DEVICE_ID = "__DEVICE_ID__";
var DEVICE_NAME = "__DEVICE_NAME__";

function t(key) {
  return I18N[key] || key;
}

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
var _cache = { history: [], devices: [] };
var _cacheTime = 0;

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
    _cacheTime = Date.now();
    var sel = document.getElementById("deviceFilter");
    var cur = sel.value;
    sel.innerHTML = '<option value="all">' + t("web.all_devices") + '</option>' +
      '<option value="local">' + t("web.this_device") + '</option>';
    _cache.devices.forEach(function(d) {
      if (d.device_id !== DEVICE_ID) {
        sel.innerHTML += '<option value="' + d.device_id + '">' +
          escapeHtml(d.device_name) + '</option>';
      }
    });
    sel.value = cur;
    updatePushTarget();
  }).catch(function(e) {
    console.error("API error:", e);
    throw e;
  });
}

function updatePushTarget() {
  var v = document.getElementById("deviceFilter").value;
  if (v === "all") {
    document.getElementById("pushTarget").textContent = "To: all devices";
  } else if (v === "local") {
    document.getElementById("pushTarget").textContent = "To: " + (DEVICE_NAME || "this device");
  } else {
    var d = findDevice(v);
    document.getElementById("pushTarget").textContent = "To: " + (d ? d.device_name : v);
  }
}

function findDevice(id) {
  for (var i = 0; i < _cache.devices.length; i++) {
    if (_cache.devices[i].device_id === id) return _cache.devices[i];
  }
  return null;
}

// ── Render ────────────────────────────────────────────────────
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
  var filter = document.getElementById("deviceFilter").value;
  var items = _cache.history;
  var list = document.getElementById("historyList");
  var title = document.getElementById("historyTitle");
  var status = document.getElementById("statusMsg");
  status.style.display = "none";

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
    return '<div class="clip-item" onclick="copyItem(' + i + ', this)" data-idx="' + i + '">' +
      '<div class="clip-icon">' + icon + '</div>' +
      '<div class="clip-body">' +
        '<div class="clip-preview">' + (preview || TYPE_LABELS[item.content_type] || "Item") + '</div>' +
        '<div class="clip-meta">' +
          '<span class="clip-source"><span class="device-indicator ' + dotClass + '"></span> ' +
          escapeHtml(sourceLabel) + '</span>' +
          '<span>' + time + '</span>' +
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
  var target = document.getElementById("deviceFilter").value;
  var btn = document.getElementById("pushBtn");
  btn.disabled = true; btn.textContent = "...";
  var body = JSON.stringify({ text: text, target_device: target === "all" ? "" : target });
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

// ── Event listeners ───────────────────────────────────────────
document.getElementById("deviceFilter").addEventListener("change", function() {
  renderHistory();
  updatePushTarget();
});

// ── Auto-refresh every 3 seconds ──────────────────────────────
function refresh() {
  loadData().then(function() {
    renderHistory();
    document.getElementById("statusMsg").style.display = "none";
  }).catch(function() {
    document.getElementById("statusMsg").textContent = I18N["web.error"] || "Connection lost.";
    document.getElementById("statusMsg").style.display = "";
  });
}

// Initial load
(function init() {
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


class WebServer:
    """Lightweight HTTP server for the ClipSync web companion."""

    def __init__(self, cfg, clipboard_history, sync_mgr):
        self._cfg = cfg
        self._history = clipboard_history
        self._sync_mgr = sync_mgr
        self._httpd: HTTPServer | None = None
        self._thread: Thread | None = None
        self._firewall_ok: bool = False

    @property
    def firewall_ok(self) -> bool:
        return self._firewall_ok

    @property
    def is_running(self) -> bool:
        """Return True if the server is actually listening."""
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
        """Check whether a Windows Firewall inbound rule exists for the port.
        Returns (ok, detail) where detail is a human-readable status."""
        if sys.platform != "win32":
            return (True, "")
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
            if port is not None and f"LocalPort:  {port}" not in check.stdout:
                return (False, f"Wrong port (needs {port})")
            return (True, "OK")
        except Exception:
            return (False, "Unknown")

    def _open_firewall(self, port: int) -> bool:
        """Try to add a Windows Firewall inbound rule for the web companion.
        Returns True if the rule exists or was successfully created."""
        if sys.platform != "win32":
            return True  # non-Windows: assume no firewall issue

        ok, _ = WebServer.check_firewall_rule(port)
        if ok:
            return True

        try:
            import subprocess
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

        # Open Windows Firewall if needed
        self._firewall_ok = self._open_firewall(port)

        cfg = self._cfg
        history = self._history
        sync_mgr = self._sync_mgr

        class _Handler(BaseHTTPRequestHandler):
            def log_message(inner_self, fmt, *args):
                # Suppress default stderr logging
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
                    # Serve the main page with i18n and token baked in
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
                    # Resolve source device names from peer config
                    device_names = {cfg.device_id: cfg.device_name}
                    for peer in cfg.peers.values():
                        device_names[peer.device_id] = peer.device_name
                    # Web-pushed items use a special source marker
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
                        })
                    inner_self._send_json({"items": result})
                elif path == "/api/devices":
                    devices = [{"device_id": cfg.device_id, "device_name": cfg.device_name}]
                    # Add connected peers
                    for peer in cfg.peers.values():
                        devices.append({
                            "device_id": peer.device_id,
                            "device_name": peer.device_name,
                            "paired": peer.paired,
                        })
                    inner_self._send_json({"devices": devices})
                elif path == "/api/status":
                    inner_self._send_json({"ok": True, "device": cfg.device_name})
                else:
                    inner_self._send_json({"error": "not found"}, 404)

            def do_POST(inner_self):
                if not inner_self._token_ok():
                    inner_self._send_json({"error": "invalid token"}, 403)
                    return

                path = urllib.parse.urlparse(inner_self.path).path
                if path == "/api/push":
                    length = int(inner_self.headers.get("Content-Length", 0))
                    body = inner_self.rfile.read(length).decode("utf-8") if length else "{}"
                    try:
                        data = json.loads(body)
                    except json.JSONDecodeError:
                        inner_self._send_json({"ok": False, "error": "invalid json"}, 400)
                        return
                    text = data.get("text", "").strip()
                    if not text:
                        inner_self._send_json({"ok": False, "error": "empty text"}, 400)
                        return

                    # Write text to local clipboard and broadcast to peers.
                    # Use a special source marker so web-pushed items are
                    # distinguishable in history.
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

                    # Suppress the clipboard monitor so it doesn't
                    # double-read and re-broadcast the text we just wrote.
                    sync_mgr._suppress_monitor_until = time.time() + 2.0

                    # Add to history manually with the web source marker
                    if sync_mgr._history is not None:
                        try:
                            sync_mgr._history.add(content)
                        except Exception:
                            logger.debug("Failed to add web push to history", exc_info=True)

                    # Broadcast to connected peers
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

                    logger.info("Web push: %d chars → clipboard + broadcast", len(text))
                    inner_self._send_json({"ok": True, "len": len(text)})
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
        """Best-guess primary LAN IP via UDP connect trick."""
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
        """Return all non-loopback IPv4 addresses on this machine."""
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
