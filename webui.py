from __future__ import annotations

import argparse
import atexit
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import config

WEB_PORT = 5197
DAEMON_PORT = 5199
ESTOP_PORT = 5198
INDEX = os.path.join(HERE, "webui.html")
TOKEN_FILE = os.path.join(HERE, ".estop_token")
DAEMON_LOG = os.path.join(HERE, "webui-daemon.log")
RUN_LOG = os.path.join(HERE, "daemon.log")

WANT = {"real": False}

VENDOR_DIR = os.path.join(HERE, "vendor")
MIME = {
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}

CHILD = {"p": None}



def port_open(port, host="127.0.0.1", timeout=0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def talk(port, obj, timeout=1.2, token=None) -> dict:
    payload = dict(obj)
    if token:
        payload["token"] = token
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(8192)
                if not chunk:
                    break
                buf += chunk
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    line = buf.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
    if not line:
        return {"ok": False, "error": "空应答"}
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"ok": False, "error": line}


def read_token() -> str:
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def estop_talk(obj, timeout=2.5) -> dict:
    tok = read_token()
    if not tok:
        return {"ok": False,
                "error": "还没有令牌文件，控制服务可能没起来"}
    return talk(ESTOP_PORT, obj, timeout, token=tok)



LOG_MAX = 1_000_000


def _rotate_webui_log():
    try:
        if os.path.getsize(DAEMON_LOG) < LOG_MAX:
            return
    except OSError:
        return
    try:
        bak = DAEMON_LOG + ".1"
        if os.path.exists(bak):
            os.remove(bak)
        os.replace(DAEMON_LOG, bak)
    except OSError:
        pass


def _log_files():
    out = []
    for p in (RUN_LOG, DAEMON_LOG):
        try:
            st = os.stat(p)
            out.append({"name": os.path.basename(p), "size": st.st_size,
                        "mtime": time.strftime("%m-%d %H:%M",
                                               time.localtime(st.st_mtime))})
        except OSError:
            out.append({"name": os.path.basename(p), "size": 0, "mtime": ""})
    return out


def _log_path(name):
    name = os.path.basename(str(name or "daemon.log"))
    return {"daemon.log": RUN_LOG, "webui-daemon.log": DAEMON_LOG}.get(name)


def log_tail(name="daemon.log", n=150):
    path = _log_path(name)
    if not path:
        return {"ok": False, "error": "不认识的日志文件"}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "lines": [],
                "files": _log_files()}
    return {"ok": True, "name": os.path.basename(path),
            "lines": [x.rstrip("\n") for x in lines[-max(1, min(2000, n)):]],
            "files": _log_files()}


def log_clear():
    done, skipped = [], []
    for p in (RUN_LOG, RUN_LOG + ".1", DAEMON_LOG, DAEMON_LOG + ".1"):
        if not os.path.exists(p):
            continue
        try:
            if os.path.basename(p).startswith("daemon.log"):
                os.remove(p)
            else:
                with open(p, "w", encoding="utf-8"):
                    pass
            done.append(os.path.basename(p))
        except OSError:
            skipped.append(os.path.basename(p))
    return {"ok": True, "cleared": done, "skipped": skipped,
            "files": _log_files()}


def log_open(name="daemon.log"):
    path = _log_path(name)
    if not path:
        return {"ok": False, "error": "不认识的日志文件"}
    if not os.path.exists(path):
        return {"ok": False, "error": "日志文件还不存在（服务还没跑过？）"}
    try:
        if os.name == "nt":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "path": path}



def spawn_daemon(real=None) -> dict:
    if port_open(DAEMON_PORT):
        return {"ok": True, "note": "已在运行"}
    if real is None:
        real = WANT["real"] if WANT["real"] is not None else False
    _rotate_webui_log()
    args = [sys.executable, os.path.join(HERE, "daemon.py"),
            "--real" if real else "--emu",
            "--listen-port", str(DAEMON_PORT),
            "--estop-port", str(ESTOP_PORT)]
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        log = open(DAEMON_LOG, "ab")
        p = subprocess.Popen(args, cwd=HERE, stdout=log,
                             stderr=subprocess.STDOUT, creationflags=flags)
    except Exception as e:
        return {"ok": False, "error": f"启动失败：{e}"}
    CHILD["p"] = p

    for _ in range(80):
        if port_open(DAEMON_PORT):
            return {"ok": True, "note": "已启动", "real": bool(real)}
        if p.poll() is not None:
            return {"ok": False, "error": f"进程退出了（码 {p.returncode}），"
                                          f"看 {os.path.basename(DAEMON_LOG)}"}
        time.sleep(0.1)
    return {"ok": False, "error": "启动超时，看 webui-daemon.log"}


def stop_daemon() -> dict:
    r = talk(DAEMON_PORT, {"quit": True}, timeout=3.0)
    time.sleep(0.3)
    return r


def kill_child():
    p = CHILD.get("p")
    if p and p.poll() is None:
        try:
            p.terminate()
        except Exception:
            pass


atexit.register(kill_child)



def collect_state() -> dict:
    cfg_real = bool(WANT["real"])
    st = {
        "daemon_up": False,
        "real": cfg_real,
        "estop": False,
        "mode": "offline",
        "preset": None,
        "axes": None,
        "out": None,
        "intensity": 1.0,
        "speed": 1.0,
        "last_error": "",
        "device": None,
        "link": None,
        "owner": "",
        "owner_left_s": 0,
        "config_real": cfg_real,
    }
    if not port_open(DAEMON_PORT):
        st["last_error"] = "控制服务未启动"
        return st

    r = talk(DAEMON_PORT, {"status": True}, timeout=1.0)
    if not r.get("ok"):
        st["last_error"] = r.get("error", "查询失败")
        return st

    st["daemon_up"] = True
    for k in ("real", "estop", "mode", "preset", "axes", "out",
              "intensity", "speed", "last_error", "device", "link",
              "owner", "owner_left_s"):
        if k in r:
            st[k] = r[k]
    return st



class Handler(BaseHTTPRequestHandler):
    server_version = "OSR-WebUI/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            obj = json.loads(raw.decode("utf-8") or "{}")
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            try:
                with open(INDEX, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError as e:
                self._send(500, f"读不到 webui.html：{e}".encode("utf-8"),
                           "text/plain; charset=utf-8")
            return
        if path == "/api/state":
            self._json(collect_state())
            return
        if path == "/api/ports":
            st = collect_state()
            link = st.get("link") or {}
            self._json({"ok": True,
                        "ports": link.get("ports") or [],
                        "serial_ready": link.get("serial_ready", True),
                        "error": link.get("error") or st.get("last_error") or ""})
            return
        if path == "/api/log":
            q = self.path.split("?")[1] if "?" in self.path else ""
            kv = {}
            for part in q.split("&"):
                if "=" in part:
                    k, _, v = part.partition("=")
                    kv[k] = urllib.parse.unquote(v)
            n = int(kv.get("tail") or 150) if (kv.get("tail") or "150").isdigit() else 150
            self._json(log_tail(kv.get("name") or "daemon.log", n))
            return
        if path.startswith("/vendor/"):
            self._vendor(path[len("/vendor/"):])
            return
        if path == "/favicon.ico":
            self._send(204, b"")
            return
        self._json({"ok": False, "error": "not found"}, 404)

    def _vendor(self, rel: str):
        if not rel or ".." in rel or rel.startswith(("/", "\\")):
            self._json({"ok": False, "error": "bad path"}, 400)
            return
        full = os.path.normpath(os.path.join(VENDOR_DIR, rel))
        root = os.path.normpath(VENDOR_DIR) + os.sep
        if not full.startswith(root):
            self._json({"ok": False, "error": "bad path"}, 400)
            return
        try:
            with open(full, "rb") as f:
                body = f.read()
        except OSError:
            self._json({"ok": False, "error": "missing: " + rel}, 404)
            return
        ctype = MIME.get(os.path.splitext(full)[1].lower(),
                         "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()

        if path == "/api/cmd":
            body = dict(body)
            body["src"] = "ui"
            r = talk(DAEMON_PORT, body, timeout=2.0)
            self._json(r)
            return

        if path == "/api/estop":
            on = bool(body.get("on"))
            r = estop_talk({"estop": on})
            self._json(r)
            return

        if path == "/api/release":
            self._json(estop_talk({"release": True}))
            return

        if path == "/api/port":
            port = body.get("port")
            port = str(port).strip().upper() if port else None
            self._json(estop_talk({"port": port}))
            return

        if path == "/api/log":
            action = str(body.get("action") or "")
            name = body.get("name") or "daemon.log"
            if action == "clear":
                self._json(log_clear())
            elif action == "open":
                self._json(log_open(name))
            elif action == "tail":
                self._json(log_tail(name, int(body.get("n") or 150)))
            else:
                self._json({"ok": False, "error": "action 只能是 clear/open/tail"})
            return

        if path == "/api/mode":
            real = bool(body.get("real"))
            if port_open(DAEMON_PORT):
                r = estop_talk({"real": real})
                if r.get("ok"):
                    WANT["real"] = real
            else:
                r = {"ok": False,
                     "error": "控制服务没起来，先点「启动控制服务」"}
            self._json(r)
            return

        if path == "/api/daemon":
            action = str(body.get("action") or "")
            if action == "start":
                self._json(spawn_daemon())
            elif action == "stop":
                self._json(stop_daemon())
            elif action == "restart":
                stop_daemon()
                CHILD["p"] = None
                time.sleep(0.4)
                self._json(spawn_daemon())
            else:
                self._json({"ok": False, "error": "action 只能是 start/stop/restart"})
            return

        self._json({"ok": False, "error": "not found"}, 404)


def serve(port: int):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    return httpd


def serial_info() -> str:
    try:
        import serial
        return f"pyserial {getattr(serial, '__version__', '?')}"
    except ImportError:
        return "缺失 —— 真机连不上，先 pip install pyserial"


def main():
    p = argparse.ArgumentParser(description="OSR 统一 WebUI")
    p.add_argument("--port", type=int, default=WEB_PORT)
    p.add_argument("--no-browser", action="store_true")
    a = p.parse_args()

    url = f"http://127.0.0.1:{a.port}/"

    if port_open(a.port):
        print(f"已有一个 WebUI 在 {url} 运行，直接打开它。")
        if not a.no_browser:
            webbrowser.open(url)
        return 0

    real = config.resolve_real()
    print("=" * 52)
    print("  OSR-Control WebUI")
    print("=" * 52)
    print(f"  模式    : {'REAL 真机' if real else 'EMU 模拟器'}")
    print(f"  Python  : {sys.executable}")
    print(f"  串口库  : {serial_info()}")
    print(f"  页面    : {url}")
    print(f"  控制服务: 127.0.0.1:{DAEMON_PORT}  "
          f"{'（已运行）' if port_open(DAEMON_PORT) else '（未运行，正在拉起）'}")

    r = spawn_daemon(real)
    if r.get("ok"):
        print("  服务    : 已就绪")
    else:
        print(f"  服务    : 启动失败 —— {r.get('error')}")

    try:
        httpd = serve(a.port)
    except OSError as e:
        print(f"端口 {a.port} 打不开：{e}")
        return 1

    print("-" * 52)
    print("  关掉这个窗口即结束（会同时关掉控制服务）")
    print("=" * 52)

    if not a.no_browser:
        t = threading.Timer(0.6, lambda: webbrowser.open(url))
        t.daemon = True
        t.start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
        kill_child()
    return 0


if __name__ == "__main__":
    sys.exit(main())
