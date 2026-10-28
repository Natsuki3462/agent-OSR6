from __future__ import annotations

import argparse
import json
import os
import queue
import socket
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TOKEN_FILE = os.path.join(HERE, ".estop_token")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5198

VK = {
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74, "F6": 0x75,
    "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "ESC": 0x1B, "PAUSE": 0x13, "SCROLLLOCK": 0x91, "END": 0x23,
    "HOME": 0x24, "INSERT": 0x2D, "DELETE": 0x2E,
}



def read_token():
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            t = f.read().strip()
        return t or None
    except OSError:
        return None


def send(obj, host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=1.5) -> dict:
    token = read_token()
    if not token:
        return {"ok": False,
                "error": "找不到 .estop_token —— 先启动 daemon.py"}
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            payload = dict(obj)
            payload["token"] = token
            s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
    except (ConnectionRefusedError, socket.timeout, OSError) as e:
        return {"ok": False, "error": f"连不上急停通道 {host}:{port} —— {e}"}

    text = buf.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
    if not text:
        return {"ok": False, "error": "空应答"}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"应答不是 JSON: {text!r}"}



def start_hotkey(name: str, on_fire, on_error):
    if os.name != "nt":
        on_error("全局热键只在 Windows 上支持")
        return
    vk = VK.get(name.upper())
    if vk is None:
        on_error(f"不认识的热键 {name}（支持 {'/'.join(VK)}）")
        return

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    MOD_NOREPEAT = 0x4000
    WM_HOTKEY = 0x0312

    def loop():
        if not user32.RegisterHotKey(None, 1, MOD_NOREPEAT, vk):
            on_error(f"热键 {name} 注册失败（可能被别的程序占用）")
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                on_fire()

    threading.Thread(target=loop, daemon=True).start()



class Bridge:
    def __init__(self, host, port, interval=0.5):
        self.host, self.port, self.interval = host, port, interval
        self.q = queue.Queue()
        self.stop_flag = threading.Event()

    def start(self):
        threading.Thread(target=self._poll, daemon=True).start()

    def _poll(self):
        while not self.stop_flag.is_set():
            self.q.put(("status", send({"status": True}, self.host, self.port)))
            self.stop_flag.wait(self.interval)

    def request(self, obj):
        threading.Thread(
            target=lambda: self.q.put(("reply", send(obj, self.host, self.port))),
            daemon=True).start()


def run_gui(host, port, hotkey, interval):
    try:
        import tkinter as tk
        from tkinter import font as tkfont
    except ImportError:
        print("没有 tkinter，用命令行模式：python estop.py --press")
        return 1

    root = tk.Tk()
    root.title("OSR 急停")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    root.configure(bg="#101418")

    state = {"latched": None, "device": "", "real": None, "err": "",
             "estop_s": 0}

    big = tkfont.Font(family="Microsoft YaHei", size=17, weight="bold")
    small = tkfont.Font(family="Microsoft YaHei", size=9)

    btn = tk.Button(root, width=18, height=4, bd=0, relief="flat",
                    font=big, fg="white", bg="#2e7d32",
                    activebackground="#388e3c", activeforeground="white",
                    cursor="hand2", text="运 行 中\n\n点击 = 急停")
    btn.pack(padx=14, pady=(14, 6))

    info = tk.Label(root, text="正在连接控制服务 ...", font=small,
                    fg="#9aa4af", bg="#101418", justify="center")
    info.pack(padx=12, pady=(0, 4))

    hint = tk.Label(root, text=f"热键 {hotkey}（窗口不在前台也有效）"
                     if hotkey else "热键已关闭",
                    font=small, fg="#5f6a75", bg="#101418")
    hint.pack(padx=12, pady=(0, 12))

    def render():
        latched = state["latched"]
        if latched:
            btn.configure(text="已 急 停\n\n点击 = 解除锁定", bg="#c62828",
                          activebackground="#d32f2f")
        else:
            btn.configure(text="运 行 中\n\n点击 = 急停", bg="#2e7d32",
                          activebackground="#388e3c")

        if state["err"]:
            info.configure(text=state["err"], fg="#ef9a9a")
            return
        if latched is None:
            info.configure(text="控制服务未连接", fg="#9aa4af")
            return
        dev = state["device"] or "?"
        mode = "真机" if state["real"] else "模拟器"
        txt = f"{dev} · {mode}\n"
        if latched:
            txt += f"已锁定 {state['estop_s']}s，agent 无法控制"
        else:
            txt += "可接受控制"
        info.configure(text=txt, fg="#ef9a9a" if latched else "#9aa4af")

    def apply(reply, is_toggle=False):
        if not reply.get("ok"):
            state["err"] = reply.get("error", "发送失败")[:40]
            state["latched"] = None if is_toggle else state["latched"]
            render()
            return
        state["err"] = ""
        state["latched"] = bool(reply.get("estop"))
        if reply.get("device"):
            state["device"] = reply["device"]
        render()

    def toggle():
        target = not bool(state["latched"])
        state["err"] = ""
        bridge.request({"estop": target})

    def pump():
        try:
            while True:
                kind, payload = bridge.q.get_nowait()
                if kind == "status":
                    if payload.get("ok"):
                        state["err"] = ""
                        state["latched"] = bool(payload.get("estop"))
                        state["device"] = payload.get("device") or ""
                        state["estop_s"] = payload.get("estop_s", 0) or 0
                        if payload.get("real") is not None:
                            state["real"] = bool(payload["real"])
                        render()
                    else:
                        state["latched"] = None
                        state["err"] = payload.get("error", "连接失败")[:40]
                        render()
                elif kind == "reply":
                    apply(payload, is_toggle=True)
                elif kind == "hotkey":
                    toggle()
        except queue.Empty:
            pass
        root.after(100, pump)

    bridge = Bridge(host, port, interval)
    bridge.start()

    btn.configure(command=toggle)
    root.bind("<Escape>", lambda e: toggle())
    root.bind("<space>", lambda e: toggle())

    if hotkey:
        def fire():
            bridge.q.put(("hotkey", True))

        def oops(msg):
            hint.configure(text=msg, fg="#ef9a9a")

        start_hotkey(hotkey, fire, oops)

    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    sw = root.winfo_screenwidth()
    root.geometry(f"+{max(0, sw - w - 40)}+70")

    render()
    root.after(100, pump)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop_flag.set()
    return 0



def main():
    p = argparse.ArgumentParser(description="OSR 急停按钮（可按下保持）")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help=f"急停通道端口（默认 {DEFAULT_PORT}）")
    p.add_argument("--hotkey", default="F12",
                   help=f"全局热键，默认 F12；支持 {'/'.join(VK)}")
    p.add_argument("--no-hotkey", action="store_true", help="关闭全局热键")
    p.add_argument("--interval", type=float, default=0.5,
                   help="状态刷新间隔秒")
    p.add_argument("--press", action="store_true", help="命令行：按下急停")
    p.add_argument("--release", action="store_true", help="命令行：解除急停")
    p.add_argument("--toggle", action="store_true", help="命令行：切换")
    p.add_argument("--status", action="store_true", help="命令行：查状态")
    a = p.parse_args()

    if a.press or a.release or a.toggle or a.status:
        if a.press:
            obj = {"estop": True}
        elif a.release:
            obj = {"estop": False}
        elif a.status:
            obj = {"status": True}
        else:
            cur = send({"status": True}, a.host, a.port)
            obj = {"estop": not bool(cur.get("estop"))}
        r = send(obj, a.host, a.port)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if r.get("ok") else 1

    return run_gui(a.host, a.port, None if a.no_hotkey else a.hotkey,
                   a.interval)


if __name__ == "__main__":
    sys.exit(main())
