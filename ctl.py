from __future__ import annotations

import argparse
import json
import socket
import sys
import time

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5199


class Client:
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock = None
        self._buf = b""

    def connect(self):
        self._sock = socket.create_connection((self.host, self.port),
                                              timeout=self.timeout)
        self._sock.settimeout(self.timeout)
        return self

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()
        return False

    def send(self, obj: dict) -> dict:
        if self._sock is None:
            self.connect()
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        self._sock.sendall(line.encode("utf-8"))
        return self._read_reply()

    def _read_reply(self) -> dict:
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("守护进程关闭了连接")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        text = line.decode("utf-8", "replace").strip()
        if not text:
            return {"ok": False, "error": "空应答"}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"ok": False, "error": f"应答不是 JSON: {text!r}"}


def _print(reply: dict):
    print(json.dumps(reply, ensure_ascii=False), flush=True)


def parse_axes_text(text: str) -> dict:
    out = {}
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        k, sep, v = part.partition("=")
        if not sep:
            raise SystemExit(f"--set 格式：L0=60,L2=35（收到 {part!r}）")
        try:
            out[k.strip().upper()] = float(v)
        except ValueError:
            raise SystemExit(f"轴值不是数字：{part!r}")
    if not out:
        raise SystemExit("--set 里没有轴值")
    return out


def stream(host, port):
    with Client(host, port) as c:
        while True:
            raw = sys.stdin.readline()
            if not raw:
                return
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                _print({"ok": False, "error": f"bad json: {e}"})
                continue
            _print(c.send(obj))
            if obj.get("quit"):
                return


def watch(host, port, interval):
    with Client(host, port) as c:
        try:
            while True:
                s = c.send({"status": True})
                bits = [f"mode={s.get('mode')}"]
                if s.get("preset"):
                    bits.append(f"preset={s['preset']}")
                if s.get("axes"):
                    bits.append(" ".join(f"{k}={v}" for k, v in
                                         sorted(s["axes"].items())))
                if s.get("seq"):
                    q = s["seq"]
                    bits.append(f"seq {q['pos_ms']}/{q['dur_ms']}ms")
                if s.get("fade_ms"):
                    bits.append(f"fade {s['fade_ms']}ms")
                print("  ".join(bits), flush=True)
                time.sleep(interval)
        except KeyboardInterrupt:
            return


def main():
    p = argparse.ArgumentParser(description="OSR 控制服务客户端（TCP 5199）")
    p.add_argument("json", nargs="?",
                   help='一条 JSON 指令，如 \'{"preset":"orbit"}\'')
    p.add_argument("--set", dest="axes_text",
                   help='设置轴值，如 "L0=60,L2=35"（0-100）')
    p.add_argument("--ms", type=float, default=0.0,
                   help="配合 --set：过渡毫秒数（0=立刻到位）")
    p.add_argument("--preset", help="跑某个预设，如 orbit / tide")
    p.add_argument("--intensity", type=float, default=None,
                   help="预设强度 0.1-2.0")
    p.add_argument("--seq", help='关键帧动作，如 [[0,{"L0":30}],[800,{"L0":70}]]')
    p.add_argument("--seq-file", help="从 JSON 文件读关键帧动作")
    p.add_argument("--loop", action="store_true", help="配合 --seq：循环播")
    p.add_argument("--home", action="store_true", help="回中位")
    p.add_argument("--stop", action="store_true", help="归零停止（服务不退出）")
    p.add_argument("--status", action="store_true", help="查状态")
    p.add_argument("--quit", action="store_true", help="归零、断开、退出服务")
    p.add_argument("--stdin", action="store_true", help="流式：从标准输入读 JSON 行")
    p.add_argument("--watch", action="store_true", help="持续打印状态")
    p.add_argument("--interval", type=float, default=0.5, help="--watch 间隔秒")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--timeout", type=float, default=5.0)
    a = p.parse_args()

    if a.stdin:
        stream(a.host, a.port)
        return
    if a.watch:
        watch(a.host, a.port, a.interval)
        return

    if a.json:
        try:
            obj = json.loads(a.json)
        except json.JSONDecodeError as e:
            raise SystemExit(f"JSON 不合法：{e}")
    elif a.axes_text:
        obj = {"axes": parse_axes_text(a.axes_text)}
        if a.ms > 0:
            obj["ms"] = a.ms
    elif a.seq or a.seq_file:
        if a.seq_file:
            try:
                with open(a.seq_file, "r", encoding="utf-8-sig") as f:
                    seq = json.load(f)
            except OSError as e:
                raise SystemExit(f"读不了 {a.seq_file}：{e}")
            except json.JSONDecodeError as e:
                raise SystemExit(f"{a.seq_file} 不是合法 JSON：{e}")
        else:
            try:
                seq = json.loads(a.seq)
            except json.JSONDecodeError as e:
                raise SystemExit(f"--seq 不是合法 JSON：{e}\n"
                                 f"提示：Windows 引号很容易出错，建议用 --seq-file")
        obj = {"seq": seq, "loop": bool(a.loop)}
    elif a.preset:
        obj = {"preset": a.preset}
        if a.intensity is not None:
            obj["intensity"] = a.intensity
    elif a.home:
        obj = {"home": True}
    elif a.stop:
        obj = {"stop": True}
    elif a.status:
        obj = {"status": True}
    elif a.quit:
        obj = {"quit": True}
    elif a.intensity is not None:
        obj = {"intensity": a.intensity}
    else:
        p.print_help()
        return

    try:
        with Client(a.host, a.port, a.timeout) as c:
            _print(c.send(obj))
    except (ConnectionRefusedError, ConnectionError, socket.timeout) as e:
        raise SystemExit(f"连不上控制服务 {a.host}:{a.port} —— {e}\n"
                         f"先启动：python daemon.py --port COM5")


if __name__ == "__main__":
    main()
