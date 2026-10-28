from __future__ import annotations

import argparse
import asyncio
import os
import re
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "pylib"))

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


AXES6 = ["L0", "L1", "L2", "R0", "R1", "R2"]
VIB_AXES = ["V0", "V1", "V2"]
AUX_AXES = ["A0", "A1", "A2"]
CENTER = 0.5
PRECISION = 4
DEFAULT_BAUD = 115200

TCODE_BLE_SERVICE = "ff1b451d-3070-4276-9c81-5dc5ea1043bc"
TCODE_BLE_CHAR = "c5f1543e-338d-47a0-8525-01e3c621359d"

SPP_HINTS = ("DKSJ", "OSR", "SR6", "SXNG")
BT_HINTS = ("BTHENUM", "Bluetooth", "SPP", "Serial over Bluetooth")
USB_HINTS = ("1A86", "CH340", "CH341", "CH910", "CH9102", "USB-SERIAL", "CP210")

UDP_CANDIDATES = ("tcode.local:8000", "127.0.0.1:8000")


class DeviceNotFound(RuntimeError):
    pass



def magnitude(value, precision=PRECISION) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = CENTER
    if v != v:
        v = CENTER
    v = max(0.0, min(1.0, v))
    n = int(round(v * (10 ** precision)))
    n = min(n, 10 ** precision - 1)
    return f"{n:0{precision}d}"


def encode(axis: str, value, precision=PRECISION) -> str:
    axis = axis.strip().upper()
    if len(axis) < 2 or not axis[0].isalpha() or not axis[1].isdigit():
        raise ValueError(f"非法轴名：{axis!r}（应形如 L0 / R2 / V0）")
    return f"{axis[0]}{axis[1]}{magnitude(value, precision)}"


def build_line(values: dict, precision=PRECISION) -> str:
    return " ".join(encode(a, v, precision) for a, v in values.items())



def list_serial_ports():
    if list_ports is None:
        return []
    return list(list_ports.comports())


def find_serial_port(preferred=None) -> str | None:
    ports = list_serial_ports()
    if not ports:
        return None
    if preferred:
        want = preferred.strip().upper()
        for p in ports:
            if p.device.upper() == want:
                return p.device
        return None
    scored = []
    for p in ports:
        blob = " ".join(
            str(x) for x in (
                p.device, p.name, p.description, p.hwid,
                getattr(p, "manufacturer", None), getattr(p, "product", None),
            ) if x
        ).upper()
        score = 0
        score += 10 * sum(1 for h in SPP_HINTS if h.upper() in blob)
        score += 5 * sum(1 for h in BT_HINTS if h.upper() in blob)
        score += 4 * sum(1 for h in USB_HINTS if h.upper() in blob)
        if score:
            scored.append((score, p.device, p.description or p.name))
    if scored:
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored[0][1]
    if len(ports) == 1:
        return ports[0].device
    return None


def probe_udp(endpoint: str, timeout: float = 1.5) -> str:
    host, _, port = endpoint.partition(":")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(b"D1\n", (host, int(port or 8000)))
        data, _ = s.recvfrom(256)
    return data.decode("ascii", "replace").strip()


async def probe(timeout: float = 1.2):
    port = await asyncio.to_thread(find_serial_port)
    if port:
        return ("serial", port)
    for ep in UDP_CANDIDATES:
        try:
            resp = await asyncio.to_thread(probe_udp, ep, timeout)
            if "TCode" in resp:
                return ("udp", ep)
        except Exception:
            continue
    return None


async def probe_ble(timeout: float = 5.0):
    try:
        from bleak import BleakScanner
    except ImportError:
        return None
    try:
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    except Exception as e:
        if "powered" in str(e).lower() or "radio" in str(e).lower():
            print("提示：电脑蓝牙没开，跳过 BLE 扫描。")
            return None
        return None
    for addr, (dev, adv) in found.items():
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if TCODE_BLE_SERVICE.lower() in uuids:
            return addr
    return None



class OSR6:
    def __init__(self, port=None, udp=None, ble=None, precision=PRECISION,
                 baud=DEFAULT_BAUD, couple=True):
        self.port = port
        self.udp = udp
        self.ble = ble
        self.precision = precision
        self.baud = baud
        self.transport: str | None = None
        self.endpoint: str | None = None
        self.supported: set[str] = set(AXES6)
        self._ser = None
        self._sock = None
        self._addr = None
        self._client = None
        self._last: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def connect(self) -> "OSR6":
        if self.port:
            await self._open_serial(self.port)
            return self
        if self.udp:
            await self._open_udp(self.udp)
            return self
        if self.ble:
            addr = self.ble if isinstance(self.ble, str) else await probe_ble()
            if not addr:
                raise DeviceNotFound("BLE 没扫到带 TCode 服务的设备（服务 "
                                     f"{TCODE_BLE_SERVICE}）")
            await self._open_ble(addr)
            return self

        port = await asyncio.to_thread(find_serial_port)
        if port:
            await self._open_serial(port)
            return self
        for ep in UDP_CANDIDATES:
            try:
                await self._open_udp(ep)
                return self
            except Exception:
                continue
        addr = await probe_ble()
        if addr:
            await self._open_ble(addr)
            return self
        tips = [
            "没找到 OSR 设备。依次检查：",
        ]
        if serial is None:
            tips.append("  0) [环境] 没装 pyserial，串口连不了："
                        "pip install pyserial（或双击 install.ps1）")
        tips += [
            "  1) 设备已开机、电量充足",
            "  2) 已与 Windows 配对（蓝牙名 DKSJ-OSR6-SPP）",
            "  3) 手机 App 已断开（设备同时只连一个主）",
            "  4) DKFunPlayer 没有占用串口（先关掉它）",
            "  5) 仍不行就手动指定：--port COM3 / --udp tcode.local:8000",
        ]
        raise DeviceNotFound("\n".join(tips))

    async def _open_serial(self, port: str):
        if serial is None:
            raise DeviceNotFound("没装 pyserial：pip install pyserial")
        try:
            self._ser = await asyncio.to_thread(
                serial.Serial, port, self.baud, timeout=0.5, write_timeout=3.0
            )
        except Exception as e:
            raise DeviceNotFound(
                f"打不开串口 {port}：{e}\n"
                "多半是被 DKFunPlayer 占着，先关掉它再试；"
                "或用 --port 指定别的口。"
            ) from e
        await asyncio.sleep(0.4)
        try:
            self._ser.reset_input_buffer()
        except Exception:
            pass
        self.transport, self.endpoint = "serial", port

    async def _open_udp(self, endpoint: str):
        host, _, port = endpoint.partition(":")
        port = int(port or 8000)

        def _probe():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2.0)
            try:
                sock.sendto(b"D1\n", (host, port))
                data, _ = sock.recvfrom(256)
            except Exception:
                sock.close()
                raise
            return sock, data

        try:
            sock, data = await asyncio.to_thread(_probe)
        except Exception as e:
            raise DeviceNotFound(f"UDP {endpoint} 无应答：{e}") from e

        resp = data.decode("ascii", "replace")
        if "TCode" not in resp:
            sock.close()
            raise DeviceNotFound(f"UDP {endpoint} 应答不是 TCode：{resp!r}")
        self._sock, self._addr = sock, (host, port)
        self.transport, self.endpoint = "udp", endpoint

    async def _open_ble(self, addr: str):
        try:
            from bleak import BleakClient
        except ImportError as e:
            raise DeviceNotFound("没装 bleak：pip install bleak") from e
        client = BleakClient(addr, timeout=20)
        ok = await client.connect()
        if not ok:
            raise DeviceNotFound(f"BLE 连接失败：{addr}")
        self._client = client
        self.transport, self.endpoint = "ble", addr

    async def _write(self, text: str):
        data = (text if text.endswith("\n") else text + "\n").encode("ascii")
        async with self._lock:
            t = self.transport
            if t == "serial":
                def _w():
                    self._ser.write(data)
                    self._ser.flush()
                await asyncio.to_thread(_w)
            elif t == "udp":
                self._sock.sendto(data, self._addr)
            elif t == "ble":
                await self._client.write_gatt_char(
                    TCODE_BLE_CHAR, data, response=False)
            else:
                raise DeviceNotFound("还没连接")

    async def _read(self, timeout: float = 1.5) -> str:
        try:
            if self.transport == "serial":
                self._ser.timeout = timeout
                raw = await asyncio.to_thread(self._ser.readline)
                return raw.decode("ascii", "replace").strip()
            if self.transport == "udp":
                self._sock.settimeout(timeout)
                data, _ = self._sock.recvfrom(512)
                return data.decode("ascii", "replace").strip()
            if self.transport == "ble":
                raw = await self._client.read_gatt_char(TCODE_BLE_CHAR)
                return raw.decode("ascii", "replace").strip()
        except Exception:
            return ""
        return ""

    async def _read_all(self, timeout: float = 2.0, idle: float = 0.35) -> str:
        lines = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = await self._read(min(idle, max(0.05, deadline - time.monotonic())))
            if not line:
                break
            lines.append(line)
        return "\n".join(lines)

    async def send(self, values: dict, force: bool = False):
        if not values:
            return
        out = {}
        for axis, v in values.items():
            axis = axis.upper()
            if axis not in self.supported:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            v = max(0.0, min(1.0, v))
            if force or axis not in self._last or abs(self._last[axis] - v) > 1e-9:
                out[axis] = v
        if not out:
            return
        await self._write(build_line(out, self.precision))
        self._last.update(out)

    async def send_axis(self, axis: str, value, force: bool = False):
        await self.send({axis: value}, force=force)

    async def stop(self, home: bool = True):
        if home:
            vals = {}
            for a in self.supported:
                if a[0] in "LR":
                    vals[a] = CENTER
                else:
                    vals[a] = 0.0
            if vals:
                try:
                    await self.send(vals, force=True)
                    await asyncio.sleep(0.15)
                except Exception:
                    pass
        try:
            await self._write("DSTOP")
        except Exception:
            pass
        self._last.clear()

    async def identify(self, retries: int = 3) -> dict:
        out = {"device": "", "tcode": "", "axes": [], "raw": {}}

        tcode = ""
        for _ in range(max(1, retries)):
            try:
                await self._write("D1")
                tcode = await self._read_all(1.2)
            except Exception:
                tcode = ""
            if "TCode" in tcode:
                break
            await asyncio.sleep(1.0)
        out["raw"]["D1"] = tcode

        try:
            await self._write("D0")
            out["raw"]["D0"] = await self._read_all(1.2)
        except Exception:
            out["raw"]["D0"] = ""

        try:
            await self._write("D2")
            d2 = await self._read_all(2.0)
        except Exception:
            d2 = ""
        out["raw"]["D2"] = d2
        if d2:
            out["axes"] = list(dict.fromkeys(re.findall(r"[LRVA]\d", d2.upper())))

        if out["axes"] and "L0" in out["axes"]:
            self.supported = set(out["axes"])
        else:
            self.supported = set(AXES6)
        return out

    async def close(self):
        try:
            await self.stop(home=True)
        except Exception:
            pass
        try:
            if self._ser:
                self._ser.close()
        except Exception:
            pass
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        try:
            if self._client:
                await self._client.disconnect()
        except Exception:
            pass
        self._ser = self._sock = self._client = None
        self.transport = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.close()
        return False



async def do_scan(ble_timeout: float = 5.0):
    print("== 串口 ==")
    ports = list_serial_ports()
    if not ports:
        print("  （无 —— 设备没开机时 SPP 虚拟串口不会出现）")
    best = find_serial_port()
    for p in ports:
        mark = "  ← 候选" if p.device == best else ""
        print(f"  {p.device:8s} {p.description or p.name}{mark}")
        if p.hwid:
            print(f"           {p.hwid}")

    print("== UDP ==")
    for ep in UDP_CANDIDATES:
        try:
            resp = await asyncio.to_thread(probe_udp, ep, 1.2)
            print(f"  {ep:24s} OK  {resp}")
        except Exception as e:
            print(f"  {ep:24s} ✗ {e}")

    print("== BLE ==")
    try:
        from bleak import BleakScanner
    except ImportError:
        print("  没装 bleak")
        return
    try:
        found = await BleakScanner.discover(timeout=ble_timeout,
                                            return_adv=True)
    except Exception as e:
        if "powered" in str(e).lower() or "radio" in str(e).lower():
            print("  电脑蓝牙没开 —— 系统设置里打开蓝牙再扫。")
        else:
            print(f"  扫描失败：{type(e).__name__}: {e}")
        return
    for addr, (dev, adv) in sorted(found.items(),
                                   key=lambda kv: -(kv[1][1].rssi or -999)):
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        tag = "  ← TCode 服务" if TCODE_BLE_SERVICE.lower() in uuids else ""
        print(f"  {addr}  RSSI={adv.rssi:>4}  {dev.name}{tag}")


async def do_identify(args):
    dev = OSR6(port=args.port, udp=args.udp, ble=args.ble)
    await dev.connect()
    print(f"已连接：{dev.transport} / {dev.endpoint}")

    def _fmt(text):
        if not text:
            return "（无应答）"
        return text.replace("\n", "\n                ")

    info = await dev.identify()
    print(f"  D0 设备信息 : {_fmt(info['raw'].get('D0'))}")
    print(f"  D1 TCode 版本: {_fmt(info['raw'].get('D1'))}")
    print(f"  D2 轴列表   : {_fmt(info['raw'].get('D2'))}")
    print(f"  解析出的轴  : {' '.join(sorted(info['axes'])) or '（默认）'}")
    print(f"  实际会发送  : {' '.join(sorted(dev.supported)) or '（无）'}")
    await dev.close()


async def do_test(args):
    dev = OSR6(port=args.port, udp=args.udp, ble=args.ble)
    await dev.connect()
    print(f"已连接：{dev.transport} / {dev.endpoint}")
    print("六轴小幅度依次试动（中位 ±0.06，每轴 0.8s）...")
    try:
        for axis in AXES6:
            print(f"  {axis} -> 0.56")
            await dev.send({axis: 0.56}, force=True)
            await asyncio.sleep(0.8)
            print(f"  {axis} -> 0.50")
            await dev.send({axis: CENTER}, force=True)
            await asyncio.sleep(0.5)
    finally:
        await dev.close()
    print("完成，已归零断开。")


async def do_axes(args, values):
    dev = OSR6(port=args.port, udp=args.udp, ble=args.ble)
    await dev.connect()
    print(f"已连接：{dev.transport} / {dev.endpoint}，"
          f"发送 {values}，保持 {args.sec}s")
    try:
        await dev.send(values, force=True)
        await asyncio.sleep(args.sec)
    finally:
        await dev.close()
    print("已归零断开。")


def parse_axes(text: str) -> dict:
    out = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        k, _, v = part.partition("=")
        out[k.strip().upper()] = float(v) / 100.0
    return out


def _run(coro):
    try:
        return asyncio.run(coro)
    except DeviceNotFound as e:
        print(f"\n[设备] {e}")
        raise SystemExit(1)


def main():
    p = argparse.ArgumentParser(description="OSR / OSR6 六轴控制（TCode v0.3）")
    p.add_argument("--scan", action="store_true", help="扫描串口 / UDP / BLE")
    p.add_argument("--identify", action="store_true", help="连接并读 D0/D1/D2")
    p.add_argument("--test", action="store_true",
                   help="六轴小幅度依次试动（±0.06）")
    p.add_argument("--center", action="store_true", help="回中并停止")
    p.add_argument("--axes", help='手动轴值，如 "L0=60,L2=40"（0-100）')
    p.add_argument("--sec", type=float, default=3.0, help="保持秒数")
    p.add_argument("--port", help="指定串口，如 COM3")
    p.add_argument("--udp", help="指定 UDP，如 tcode.local:8000")
    p.add_argument("--ble", nargs="?", const=True, default=None,
                   help="走 BLE（可跟地址）")
    args = p.parse_args()

    if args.scan:
        _run(do_scan())
    elif args.identify:
        _run(do_identify(args))
    elif args.test:
        _run(do_test(args))
    elif args.center:
        async def _c():
            d = OSR6(port=args.port, udp=args.udp, ble=args.ble)
            await d.connect()
            print(f"已连接：{d.transport} / {d.endpoint}，回中")
            await d.close()
        _run(_c())
    elif args.axes:
        _run(do_axes(args, parse_axes(args.axes)))
    else:
        p.print_help()


if __name__ == "__main__":
    main()
