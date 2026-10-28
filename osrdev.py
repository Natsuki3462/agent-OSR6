from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "pylib"))

from osr import AXES6, CENTER, DeviceNotFound, OSR6

__all__ = ["Device", "open_osr", "open_device", "add_mode_args",
           "AXES6", "CENTER", "DeviceNotFound"]


class Device:
    name = "osr6"

    def __init__(self, dev: OSR6):
        self._osr = dev

    @property
    def supported(self) -> set:
        return self._osr.supported

    @property
    def alive(self) -> bool:
        return self._osr.transport is not None

    @property
    def transport(self):
        return self._osr.transport

    @property
    def endpoint(self):
        return self._osr.endpoint

    def describe(self) -> str:
        return (f"OSR6 (TCode v0.3) "
                f"[{self._osr.transport}:{self._osr.endpoint}]")

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
            if k in self._osr.supported:
                vals[k] = max(0.0, min(1.0, v))
        if vals:
            await self._osr.send(vals)

    async def stop(self):
        await self._osr.stop(home=True)

    async def close(self):
        await self._osr.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()
        return False


def _hard_close(dev: OSR6):
    try:
        if dev._ser:
            dev._ser.close()
    except Exception:
        pass
    try:
        if dev._sock:
            dev._sock.close()
    except Exception:
        pass
    dev._ser = dev._sock = None
    dev.transport = None


async def open_osr(port=None, udp=None, ble=None, quiet=False) -> Device:
    dev = OSR6(port=port, udp=udp, ble=ble)
    await dev.connect()
    info = await dev.identify()

    d0 = (info["raw"].get("D0") or "").strip()
    d1 = (info["raw"].get("D1") or "").strip()
    if "TCode" not in d1:
        _hard_close(dev)
        raise DeviceNotFound(
            f"{dev.transport}:{dev.endpoint} 的应答不是 TCode"
            f"（D1 = {d1!r}）。可能连错了设备，或者控制器还没启动完。\n"
            "  设备侧：确认已开机、手机 App 已断开、DKFunPlayer 没占用串口\n"
            "  电脑侧：用 --port COM5 明确指定串口"
        )

    if not quiet:
        print(f"已连接 {dev.transport}:{dev.endpoint}")
        print(f"  设备信息 : {d0 or '（无应答）'}")
        print(f"  TCode    : {d1}")
        axes = " ".join(sorted(dev.supported))
        print(f"  可用轴   : {axes or '（默认六轴）'}")
        if "L0" not in dev.supported:
            print("  [注意] 轴列表里没有 L0，六轴动作可能发不出去")

    return Device(dev)



def add_mode_args(p):
    g = p.add_mutually_exclusive_group()
    g.add_argument("--real", dest="real", action="store_const", const=True,
                   default=None, help="连接真实硬件（覆盖 config.json）")
    g.add_argument("--emu", dest="real", action="store_const", const=False,
                   help="使用 osr-emu 模拟器演示（覆盖 config.json）")
    return p


async def open_device(real=None, port=None, udp=None, ble=None, quiet=False):
    from config import resolve_real

    if resolve_real(real):
        return await open_osr(port=port, udp=udp, ble=ble, quiet=quiet)

    from osr_emu import EmuDevice
    if not quiet:
        print("[EMU] 未连接真实设备，使用 osr-emu 模拟器演示")
        print("      切到真机：python config.py --real   或加 --real 参数")
    return EmuDevice(verbose=not quiet)
