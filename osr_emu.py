from __future__ import annotations

import argparse
import asyncio
import math
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from osr import AXES6, CENTER, build_line, encode

EMU_AXES = AXES6 + ["V0", "A0", "A1", "A2"]
D2_LABELS = {
    "L0": "Up", "L1": "Forward", "L2": "Left",
    "R0": "Twist", "R1": "Roll", "R2": "Pitch",
    "V0": "Vibe1", "A0": "Valve", "A1": "Suck", "A2": "Lube",
}


def _home_state() -> dict:
    st = {a: (CENTER if a[0] in "LR" else 0.0) for a in EMU_AXES}
    return st


def _bar(v: float, width: int = 20) -> str:
    n = int(max(0.0, min(1.0, v)) * width)
    return "#" * n + "." * (width - n)



class EmuDevice:
    name = "osr6"

    def __init__(self, verbose: bool = True, delay: float = 0.0,
                 print_hz: float = 5.0):
        self.supported = set(EMU_AXES)
        self.state = _home_state()
        self.lines = []
        self.tx_count = 0
        self._closed = False
        self._last_print = 0.0
        self._print_gap = 1.0 / max(0.5, print_hz)
        self.verbose = verbose
        self.delay = max(0.0, delay)

    @property
    def alive(self) -> bool:
        return not self._closed

    def describe(self) -> str:
        return "OSR6-EMU [模拟器，未连接真实设备]"

    def snapshot(self) -> dict:
        return {k: round(v, 3) for k, v in self.state.items()}

    async def send6(self, axes: dict, t=None):
        vals = {}
        for k, v in (axes or {}).items():
            k = str(k).upper().strip()
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if v != v:
                continue
            if k not in self.supported:
                continue
            vals[k] = max(0.0, min(1.0, v))
        if not vals:
            return

        changed = {k: v for k, v in vals.items()
                   if abs(self.state.get(k, -1.0) - v) > 1e-6}
        if not changed:
            return

        if self.delay:
            await asyncio.sleep(self.delay)
        self.lines.append(build_line(changed))
        self.tx_count += 1
        self.state.update(changed)
        self._maybe_print()

    async def stop(self):
        self.state = _home_state()
        self.lines.append("DSTOP")
        self.tx_count += 1
        if self.verbose:
            print("[EMU] DSTOP -> 六轴回中位、振动关闭", flush=True)
        self._last_print = 0.0

    async def close(self):
        if self._closed:
            return
        try:
            await self.stop()
        except Exception:
            pass
        self._closed = True
        if self.verbose:
            print(f"[EMU] 已断开（共发出 {self.tx_count} 条指令）", flush=True)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()
        return False

    def _maybe_print(self):
        if not self.verbose:
            return
        now = time.monotonic()
        if now - self._last_print < self._print_gap:
            return
        self._last_print = now
        parts = " ".join(f"{a}={self.state[a]:.2f}" for a in AXES6)
        print(f"[EMU] {parts}  V0={self.state['V0']:.2f}  "
              f"tx={self.tx_count}", flush=True)



class _Proto(asyncio.DatagramProtocol):
    def __init__(self, state, verbose=True):
        self.state = state
        self.verbose = verbose
        self.transport = None
        self._last = 0.0
        self.count = 0

    def connection_made(self, transport):
        self.transport = transport

    def _d2(self) -> str:
        out = []
        for a in EMU_AXES:
            lo, hi = (0, 9999)
            out.append(f"{a} {lo} {hi} {D2_LABELS.get(a, 'Axis')}")
        return "\n".join(out)

    def datagram_received(self, data, addr):
        try:
            text = data.decode("ascii", "replace").strip()
        except Exception:
            return
        if not text:
            return

        up = text.upper()
        if up == "D0":
            self._send("OSR6-EMU (osr-emu simulator)", addr)
            return
        if up == "D1":
            self._send("TCode v0.3", addr)
            return
        if up == "D2":
            self._send(self._d2(), addr)
            return
        if up in ("DSTOP", "DSTOP\n"):
            self.state.update(_home_state())
            self._show("DSTOP")
            return

        changed = False
        for m in re.finditer(r"([A-Za-z])(\d)(\d{1,4})", text):
            letter, digit, mag = m.group(1).upper(), m.group(2), m.group(3)
            axis = letter + digit
            if axis not in self.state:
                continue
            v = int(mag) / (10 ** len(mag))
            self.state[axis] = max(0.0, min(1.0, v))
            changed = True
        if changed:
            self.count += 1
            self._show(text)

    def _send(self, text, addr):
        if self.transport:
            self.transport.sendto(text.encode("ascii"), addr)
        if self.verbose:
            print(f"[EMU] <- {text.splitlines()[0][:60]}", flush=True)

    def _show(self, raw):
        if not self.verbose:
            return
        now = time.monotonic()
        if now - self._last < 0.2:
            return
        self._last = now
        parts = " ".join(f"{a}={self.state[a]:.2f}" for a in AXES6)
        print(f"[EMU] {parts}  V0={self.state['V0']:.2f}  <- {raw[:48]}",
              flush=True)


async def serve(host: str, port: int, verbose: bool = True):
    loop = asyncio.get_running_loop()
    state = _home_state()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _Proto(state, verbose), local_addr=(host, port))
    print(f"[EMU] TCode 服务已启动：{host}:{port}（UDP）")
    print("      用真实驱动去连它：")
    print(f"        python osr.py --udp {host}:{port} --identify")
    print(f"        python osr.py --udp {host}:{port} --axes \"L0=70,L2=30\" --sec 2")
    print("      Ctrl+C 退出")
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        transport.close()
        print("\n[EMU] 已停止")


def main():
    p = argparse.ArgumentParser(description="OSR6 模拟器（osr-emu）")
    p.add_argument("--serve", action="store_true",
                   help="作为独立 TCode 服务运行（UDP），供真实驱动连接")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--quiet", action="store_true", help="不打印轴值")
    p.add_argument("--demo", action="store_true",
                   help="进程内跑一段演示（画圆 6 秒）")
    a = p.parse_args()

    if a.serve:
        try:
            asyncio.run(serve(a.host, a.port, not a.quiet))
        except KeyboardInterrupt:
            pass
        return

    if a.demo:
        async def run_demo():
            dev = EmuDevice(verbose=True)
            print("[EMU] 演示：L0/L1/L2 画圆 6 秒")
            t0 = time.monotonic()
            while True:
                t = time.monotonic() - t0
                if t > 6:
                    break
                w = math.tau / 3.0
                await dev.send6({
                    "L0": 0.5 + 0.35 * math.sin(t * w),
                    "L1": 0.5 + 0.28 * math.sin(t * w + 2.09),
                    "L2": 0.5 + 0.28 * math.sin(t * w + 4.19),
                    "R0": 0.5 + 0.2 * math.sin(t * w * 0.5),
                    "V0": 0.3,
                })
                await asyncio.sleep(0.02)
            await dev.close()
            print(f"[EMU] 演示结束，共 {dev.tx_count} 条指令，"
                  f"最后一条：{dev.lines[-1] if dev.lines else '（无）'}")

        asyncio.run(run_demo())
        return

    print("osr-emu —— OSR6 模拟器")
    print("  python osr_emu.py --demo            进程内演示一段动作")
    print("  python osr_emu.py --serve           起 UDP TCode 服务（默认 127.0.0.1:8000）")
    print("  python osr_emu.py --serve --port 8000")
    print("\n平时不用手动跑：config.json 里 real_device=false 时，")
    print("daemon.py / presets.py / play_script.py 会自动使用进程内模拟器。")


if __name__ == "__main__":
    main()
