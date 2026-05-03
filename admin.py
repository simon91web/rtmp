import atexit
import collections
import datetime
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser

from flask import Flask, Response, jsonify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_JS = os.path.join(BASE_DIR, "server.js")

SERVER_HTTP_PORT = 8080
SERVER_HTTPS_PORT = 8443
ADMIN_PORT = 8888
LOG_BUFFER_SIZE = 200
HOTSPOT_IP  = "192.168.137.1"
LOOPBACK_RE = re.compile(r"^127\.")
APIPA_RE    = re.compile(r"^169\.254\.")


class LogBuffer:
    def __init__(self, maxlen=200):
        self._buf = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, line: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self._buf.append(f"[{ts}] {line.rstrip()}")

    def get_lines(self) -> list:
        with self._lock:
            return list(self._buf)

    def clear(self):
        with self._lock:
            self._buf.clear()


class ServerManager:
    def __init__(self, log: LogBuffer):
        self._proc = None
        self._start_time = None
        self._lock = threading.Lock()
        self._log = log

    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def status(self) -> dict:
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> dict:
        if self._proc is None or self._proc.poll() is not None:
            return {"running": False, "pid": None, "uptime_seconds": 0, "uptime_str": "—"}
        elapsed = int(time.time() - self._start_time)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        return {
            "running": True,
            "pid": self._proc.pid,
            "uptime_seconds": elapsed,
            "uptime_str": f"{h:02d}:{m:02d}:{s:02d}",
        }

    def start(self) -> dict:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return {**self._status_locked(), "error": "already running"}
            kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {}
            try:
                self._proc = subprocess.Popen(
                    ["node", SERVER_JS],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=BASE_DIR,
                    text=True,
                    bufsize=1,
                    **kwargs,
                )
            except FileNotFoundError:
                self._log.append("[admin] ERROR: 'node' not found in PATH")
                return {"running": False, "pid": None, "uptime_seconds": 0, "uptime_str": "—",
                        "error": "node not found in PATH"}
            self._start_time = time.time()
            proc_ref = self._proc
        t = threading.Thread(target=self._reader, args=(proc_ref,), daemon=True)
        t.start()
        return self.status()

    def _reader(self, proc):
        try:
            for line in proc.stdout:
                self._log.append(line)
        except Exception as e:
            self._log.append(f"[admin] reader error: {e}")
        self._log.append(f"[admin] node process exited (code={proc.returncode})")

    def stop(self) -> dict:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                return {**self._status_locked(), "error": "not running"}
            pid = self._proc.pid
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=10)
        except Exception as e:
            self._log.append(f"[admin] taskkill error: {e}")
        with self._lock:
            try:
                if self._proc:
                    self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            self._proc = None
            self._start_time = None
        return self.status()

    def restart(self) -> dict:
        self.stop()
        time.sleep(0.5)
        return self.start()


def get_network_info() -> dict:
    interfaces = []
    try:
        out = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=5).stdout
        current_adapter = "Unknown"
        for line in out.splitlines():
            if line and not line[0].isspace() and line.rstrip().endswith(":"):
                current_adapter = line.rstrip().rstrip(":").strip()
            m = re.search(r":\s*(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s*$", line)
            if m:
                ip = m.group(1).strip()
                if not ip.startswith("255.") and not LOOPBACK_RE.match(ip) and not APIPA_RE.match(ip):
                    itype = "hotspot" if ip == HOTSPOT_IP else "wifi"
                    interfaces.append({"name": current_adapter, "ip": ip, "type": itype})
    except Exception as e:
        interfaces.append({"name": "error", "ip": str(e), "type": "error"})
    return {"interfaces": interfaces}


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RTMP Admin</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
  <style>
    :root { color-scheme: dark; }
    body  { background: #0f0f0f; color: #e0e0e0; }

    .card { background: #1a1a1a; border: 1px solid #2a2a2a; }
    .card-header { background: #222; border-bottom: 1px solid #2a2a2a;
                   font-size: 0.78rem; font-weight: 600; letter-spacing: 0.06em;
                   text-transform: uppercase; color: #888; padding: 0.6rem 1rem; }

    /* Status dot */
    .sdot { width: 80px; height: 80px; border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            font-size: 2rem; transition: background 0.4s, box-shadow 0.4s; }
    .sdot.running { background: #155724; box-shadow: 0 0 28px #19875460; }
    .sdot.stopped { background: #1f1f1f; box-shadow: none; }

    /* URL rows */
    .url-card { background: #111; border: 1px solid #2a2a2a; border-radius: 8px;
                padding: 0.75rem 1rem; margin-bottom: 0.6rem; }
    .url-label { font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.07em;
                 color: #666; margin-bottom: 3px; }
    .url-text  { font-family: 'Courier New', monospace; font-size: 0.88rem; color: #e8c97a; }

    /* QR */
    .qr-wrap canvas, .qr-wrap img { border-radius: 6px; display: block; }

    /* Log box */
    #log-box { height: 240px; overflow-y: auto; background: #080808; color: #00e676;
               font-family: 'Courier New', monospace; font-size: 0.72rem;
               padding: 0.7rem; border-radius: 6px; white-space: pre-wrap; word-break: break-all; }

    .badge-hotspot { background: #7c5a00; color: #ffd166; border: 1px solid #7c5a00; }
    .badge-wifi    { background: #1a3a4a; color: #7dd3fc; border: 1px solid #1a3a4a; }

    .btn-primary-custom { background: #198754; border: none; font-size: 1.05rem;
                          padding: 0.65rem 1.5rem; border-radius: 8px; }
    .btn-primary-custom:hover { background: #157347; }
  </style>
</head>
<body>

<!-- Header -->
<div class="d-flex align-items-center px-4 py-3"
     style="background:#141414;border-bottom:1px solid #2a2a2a">
  <span class="fw-bold me-auto" style="letter-spacing:0.04em">&#x1F4E1; RTMP Admin</span>
  <span id="nav-pill" class="badge rounded-pill bg-secondary px-3 py-2">—</span>
</div>

<div class="container py-4" style="max-width:840px">
  <div class="row g-4">

    <!-- LEFT: Control -->
    <div class="col-md-4">
      <div class="card h-100">
        <div class="card-header">Server</div>
        <div class="card-body d-flex flex-column align-items-center text-center py-4 gap-2">
          <div id="sdot" class="sdot stopped mb-1">&#x23F9;</div>
          <div id="status-text" class="fw-semibold fs-5">Stopped</div>
          <div id="uptime-text" class="text-secondary mb-3" style="font-size:0.82rem">—</div>

          <div class="d-grid w-100 gap-2">
            <button id="btn-start"   class="btn btn-success btn-primary-custom text-white"
                    onclick="doAction('/api/start')">&#x25B6; Start</button>
            <button id="btn-restart" class="btn btn-outline-warning"
                    onclick="doAction('/api/restart')" style="display:none">&#x21BA; Restart</button>
            <button id="btn-stop"    class="btn btn-outline-danger"
                    onclick="doAction('/api/stop')" style="display:none">&#x25A0; Stop</button>
          </div>

          <div class="mt-3 text-secondary" style="font-size:0.75rem">
            PID&nbsp;<span id="pid-text" class="text-light">—</span>
          </div>
        </div>
      </div>
    </div>

    <!-- RIGHT: Stream URLs -->
    <div class="col-md-8">
      <div class="card">
        <div class="card-header d-flex justify-content-between align-items-center">
          <span>Stream URLs</span>
          <button class="btn btn-link btn-sm text-secondary p-0" style="font-size:0.72rem"
                  onclick="fetchNetwork()">&#x21BB; refresh</button>
        </div>
        <div class="card-body p-3" id="urls-body">
          <div class="text-secondary text-center py-4" style="opacity:0.5">
            Detecting network&hellip;
          </div>
        </div>
      </div>
    </div>

  </div><!-- /row -->

  <!-- Logs -->
  <div class="mt-4">
    <button class="btn w-100 d-flex justify-content-between align-items-center px-3 py-2"
            style="background:#1a1a1a;border:1px solid #2a2a2a;color:#888;font-size:0.78rem"
            data-bs-toggle="collapse" data-bs-target="#log-wrap">
      <span>LOGS &nbsp;<span id="log-count" class="badge bg-dark border border-secondary">0</span></span>
      <span style="font-size:0.65rem">&#x25BC;</span>
    </button>
    <div class="collapse" id="log-wrap">
      <div class="d-flex justify-content-end py-1">
        <button class="btn btn-sm btn-outline-secondary" onclick="clearLogs()">Clear</button>
      </div>
      <div id="log-box">(no logs yet)</div>
    </div>
  </div>

</div><!-- /container -->

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
// ── helpers ──────────────────────────────────────────────────────────────────
async function api(url, method) {
  const r = await fetch(url, { method: method || 'GET' });
  return r.json();
}

// ── status ────────────────────────────────────────────────────────────────────
function applyStatus(d) {
  const dot  = document.getElementById('sdot');
  const txt  = document.getElementById('status-text');
  const nav  = document.getElementById('nav-pill');
  const up   = document.getElementById('uptime-text');
  const pid  = document.getElementById('pid-text');

  if (d.running) {
    dot.className    = 'sdot running mb-1';
    dot.textContent  = '▶';
    txt.textContent  = 'Running';
    nav.className    = 'badge rounded-pill bg-success px-3 py-2';
    nav.textContent  = 'Running';
    up.textContent   = 'Uptime: ' + (d.uptime_str || '—');
    pid.textContent  = d.pid || '—';
    document.getElementById('btn-start').style.display   = 'none';
    document.getElementById('btn-restart').style.display = '';
    document.getElementById('btn-stop').style.display    = '';
  } else {
    dot.className    = 'sdot stopped mb-1';
    dot.textContent  = '⏹';
    txt.textContent  = 'Stopped';
    nav.className    = 'badge rounded-pill bg-secondary px-3 py-2';
    nav.textContent  = 'Stopped';
    up.textContent   = '—';
    pid.textContent  = '—';
    document.getElementById('btn-start').style.display   = '';
    document.getElementById('btn-restart').style.display = 'none';
    document.getElementById('btn-stop').style.display    = 'none';
  }
}

async function doAction(url) {
  const d = await api(url, 'POST');
  applyStatus(d);
}

async function fetchStatus() {
  const d = await api('/api/status');
  applyStatus(d);
}

// ── network / URLs ────────────────────────────────────────────────────────────
const qrDone = {};

async function fetchNetwork() {
  const d = await api('/api/network');
  renderURLs(d.interfaces);
}

function renderURLs(ifaces) {
  const body = document.getElementById('urls-body');
  if (!ifaces || ifaces.length === 0) {
    body.innerHTML = '<p class="text-secondary text-center py-3 mb-0">No local network addresses found.</p>';
    return;
  }

  body.innerHTML = ifaces.map(function(iface) {
    var http  = 'http://'  + iface.ip + ':8080';
    var https = 'https://' + iface.ip + ':8443';
    var qrId  = 'qr-' + iface.ip.replace(/\./g, '-');
    var badgeCls = iface.type === 'hotspot' ? 'badge-hotspot' : 'badge-wifi';

    return '<div class="mb-4">'
      + '<div class="d-flex align-items-center gap-2 mb-2">'
      + '  <span class="badge rounded-pill ' + badgeCls + '">' + iface.type.toUpperCase() + '</span>'
      + '  <code style="color:#e8c97a;font-size:0.9rem">' + iface.ip + '</code>'
      + '  <span class="text-secondary ms-auto" style="font-size:0.7rem">' + iface.name + '</span>'
      + '</div>'

      + '<div class="url-card">'
      + '  <div class="url-label">Phone / Flat view (HTTP)</div>'
      + '  <div class="d-flex align-items-center gap-2">'
      + '    <span class="url-text flex-grow-1">' + http + '</span>'
      + '    <button class="btn btn-sm btn-outline-secondary" onclick="copyURL(this,\'' + http + '\')">Copy</button>'
      + '    <a class="btn btn-sm btn-outline-secondary" href="' + http + '" target="_blank">Open</a>'
      + '  </div>'
      + '</div>'

      + '<div class="url-card">'
      + '  <div class="url-label">Quest VR (HTTPS)</div>'
      + '  <div class="d-flex align-items-center gap-2">'
      + '    <span class="url-text flex-grow-1">' + https + '</span>'
      + '    <button class="btn btn-sm btn-outline-secondary" onclick="copyURL(this,\'' + https + '\')">Copy</button>'
      + '  </div>'
      + '</div>'

      + '<div class="mt-2">'
      + '  <div class="url-label mb-1">Scan to open on phone</div>'
      + '  <div id="' + qrId + '" class="qr-wrap"></div>'
      + '</div>'
      + '</div>';
  }).join('<hr style="border-color:#2a2a2a;margin:0.25rem 0 1rem">');

  // generate QR codes after DOM update
  ifaces.forEach(function(iface) {
    var qrId = 'qr-' + iface.ip.replace(/\./g, '-');
    if (!qrDone[qrId]) {
      qrDone[qrId] = true;
      var el = document.getElementById(qrId);
      if (el) {
        new QRCode(el, {
          text: 'http://' + iface.ip + ':8080',
          width: 96, height: 96,
          colorDark: '#e8c97a', colorLight: '#080808',
          correctLevel: QRCode.CorrectLevel.M,
        });
      }
    }
  });
}

// ── copy button ───────────────────────────────────────────────────────────────
function copyURL(btn, url) {
  navigator.clipboard.writeText(url).then(function() {
    var orig = btn.textContent;
    btn.textContent = 'Copied!';
    btn.classList.add('btn-success');
    btn.classList.remove('btn-outline-secondary');
    setTimeout(function() {
      btn.textContent = orig;
      btn.classList.remove('btn-success');
      btn.classList.add('btn-outline-secondary');
    }, 1500);
  });
}

// ── logs ──────────────────────────────────────────────────────────────────────
var prevLogCount = -1;
async function fetchLogs() {
  const d = await api('/api/logs');
  document.getElementById('log-count').textContent = d.count;
  if (d.count !== prevLogCount) {
    prevLogCount = d.count;
    var box = document.getElementById('log-box');
    var atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    box.textContent = d.lines.length ? d.lines.join('\n') : '(no logs yet)';
    if (atBottom) box.scrollTop = box.scrollHeight;
  }
}

async function clearLogs() {
  await api('/api/logs/clear', 'POST');
  prevLogCount = -1;
  fetchLogs();
}

// ── init ──────────────────────────────────────────────────────────────────────
fetchStatus();
fetchNetwork();
fetchLogs();
setInterval(fetchStatus, 2000);
setInterval(fetchLogs,   1500);
setInterval(fetchNetwork, 30000);
</script>
</body>
</html>"""


app = Flask(__name__)
log_buffer = LogBuffer(LOG_BUFFER_SIZE)
server = ServerManager(log_buffer)


@app.route("/")
def index():
    return Response(HTML, mimetype="text/html")


@app.route("/api/status")
def api_status():
    return jsonify(server.status())


@app.route("/api/start", methods=["POST"])
def api_start():
    return jsonify(server.start())


@app.route("/api/stop", methods=["POST"])
def api_stop():
    return jsonify(server.stop())


@app.route("/api/restart", methods=["POST"])
def api_restart():
    return jsonify(server.restart())


@app.route("/api/logs")
def api_logs():
    lines = log_buffer.get_lines()
    return jsonify({"lines": lines, "count": len(lines)})


@app.route("/api/logs/clear", methods=["POST"])
def api_logs_clear():
    log_buffer.clear()
    return jsonify({"ok": True})


@app.route("/api/network")
def api_network():
    return jsonify(get_network_info())


def _shutdown():
    if server.is_running():
        log_buffer.append("[admin] Admin shutting down — stopping server.js")
        server.stop()


atexit.register(_shutdown)


if __name__ == "__main__":
    def _open_browser():
        time.sleep(1.4)
        webbrowser.open(f"http://localhost:{ADMIN_PORT}")

    threading.Thread(target=_open_browser, daemon=True).start()
    print(f"[admin] http://localhost:{ADMIN_PORT}  — opening browser...")
    app.run(host="0.0.0.0", port=ADMIN_PORT, debug=False, use_reloader=False)
