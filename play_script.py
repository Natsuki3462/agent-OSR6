from __future__ import annotations

import argparse
import asyncio
import bisect
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TICK = 0.06

NAME_AXIS = {
    "twist": "R0", "yaw": "R0",
    "roll": "R1",
    "surge": "L1", "forward": "L1",
    "sway": "L2", "left": "L2",
    "pitch": "R2",
    "vib": "V0", "vibration": "V0", "vibrate": "V0",
    "stroke": "L0", "up": "L0", "l0": "L0",
}
SUFFIX_AXIS = {
    "twist": "R0", "roll": "R1", "surge": "L1", "sway": "L2",
    "pitch": "R2", "vib": "V0", "stroke": "L0",
}
COMPANIONS = ["twist", "roll", "surge", "sway", "pitch", "vib", "stroke"]



def load_script(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    actions = data.get("actions") or data.get("frames") or []
    if not actions:
        raise ValueError(f"{path} 里没有 actions")
    at = [float(a.get("at", a.get("time", 0))) for a in actions]
    rng = float(data.get("range", 100) or 100)
    pos = [max(0.0, min(100.0, float(a.get("pos", 0)))) / 100.0
           for a in actions]
    pairs = sorted(zip(at, pos))
    at = [p[0] for p in pairs]
    pos = [p[1] for p in pairs]
    meta = data.get("metadata") or {}
    dur = float(meta.get("duration") or 0)
    if not dur:
        dur = (at[-1] / 1000.0) if at else 0.0
    return {"path": path, "at": at, "pos": pos, "dur": dur, "range": rng}


def interp(script: dict, t_ms: float) -> float:
    at = script["at"]
    if not at:
        return 0.5
    if t_ms <= at[0]:
        return script["pos"][0]
    if t_ms >= at[-1]:
        return script["pos"][-1]
    i = bisect.bisect_right(at, t_ms)
    t0, t1 = at[i - 1], at[i]
    p0, p1 = script["pos"][i - 1], script["pos"][i]
    if t1 <= t0:
        return p1
    k = (t_ms - t0) / (t1 - t0)
    return p0 + (p1 - p0) * k


def axis_of(path: str) -> str:
    stem = os.path.basename(path)
    stem = re.sub(r"\.funscript$", "", stem, flags=re.I)
    tokens = [t.lower() for t in re.split(r"[^A-Za-z0-9]+", stem) if t]
    for t in tokens:
        if t in NAME_AXIS:
            return NAME_AXIS[t]
    for t in reversed(tokens):
        if t in SUFFIX_AXIS:
            return SUFFIX_AXIS[t]
    for t in tokens:
        if re.fullmatch(r"[lrv]\d", t):
            return t.upper()
    return "L0"


def discover(main_path: str) -> dict:
    d = os.path.dirname(os.path.abspath(main_path))
    stem = re.sub(r"\.funscript$", "",
                  os.path.basename(main_path), flags=re.I)
    found = {axis_of(main_path): main_path}
    for suf in COMPANIONS:
        for cand in (f"{stem}.{suf}.funscript", f"{stem}_{suf}.funscript",
                     f"{stem} {suf}.funscript"):
            p = os.path.join(d, cand)
            if os.path.exists(p):
                ax = SUFFIX_AXIS[suf]
                if ax not in found:
                    found[ax] = p
    return found



def show(mapping: dict):
    print("识别到的轴：")
    for ax, p in sorted(mapping.items()):
        try:
            s = load_script(p)
            print(f"  {ax}  {os.path.basename(p)}  "
                  f"({len(s['at'])} 点, {s['dur']:.0f}s)")
        except Exception as e:
            print(f"  {ax}  {os.path.basename(p)}  读取失败: {e}")


async def dry_run(mapping, seconds, speed, offset):
    scripts = {ax: load_script(p) for ax, p in mapping.items()}
    t0 = time.monotonic()
    while True:
        elapsed = time.monotonic() - t0
        if elapsed > seconds:
            break
        t = elapsed * speed + offset
        vals = {ax: interp(s, t * 1000.0) for ax, s in scripts.items()}
        line = "  ".join(f"{ax}={v:.2f}" for ax, v in sorted(vals.items()))
        print(f"{t:6.2f}s  {line}", flush=True)
        await asyncio.sleep(TICK)


async def play(mapping, minutes, speed, offset, loop, dev_args):
    from osrdev import open_device

    scripts = {ax: load_script(p) for ax, p in mapping.items()}
    dur = max((s["dur"] for s in scripts.values()), default=0.0)

    dev = await open_device(**dev_args)
    print("脚本：")
    for ax, s in sorted(scripts.items()):
        print(f"  {ax}  {os.path.basename(s['path'])}  {s['dur']:.0f}s")
    if minutes:
        print(f"播放 {minutes} 分钟（Ctrl+C 提前停）")
    elif loop:
        print("无限循环（Ctrl+C 停）")
    else:
        print(f"播完 {dur:.0f}s 自动停")

    start = time.monotonic()
    t0 = start
    first = True
    try:
        while True:
            now = time.monotonic()
            if minutes > 0 and (now - start) >= minutes * 60:
                break
            raw = (now - t0) * speed + (offset if first else 0.0)
            if dur > 0 and raw >= dur:
                if loop or minutes > 0:
                    first, t0 = False, now
                    continue
                break
            vals = {ax: interp(s, raw * 1000.0) for ax, s in scripts.items()}
            await dev.send6(vals, t=raw)
            await asyncio.sleep(TICK)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await dev.close()
        print("归零，断开")


def build_mapping(args) -> dict:
    mapping = discover(args.script)
    for item in args.map or []:
        ax, _, p = item.partition("=")
        if not p:
            raise SystemExit(f"--map 格式：轴=文件（收到 {item!r}）")
        mapping[ax.strip().upper()] = p.strip()
    return mapping


def main():
    p = argparse.ArgumentParser(description="funscript 播放（OSR6 六轴）")
    p.add_argument("--script", required=True, help=".funscript 主文件")
    p.add_argument("--map", action="append",
                   help="手动指定轴脚本，如 R0=xx.twist.funscript（可多个）")
    p.add_argument("--show", action="store_true", help="只看认到了哪些轴")
    p.add_argument("--minutes", type=float, default=0,
                   help="播多久，0=按脚本长度（--loop 时无限）")
    p.add_argument("--speed", type=float, default=1.0, help="倍速")
    p.add_argument("--offset", type=float, default=0.0, help="起播偏移秒数")
    p.add_argument("--loop", action="store_true", help="循环播放")
    p.add_argument("--dry-run", action="store_true", help="只打印不连设备")
    p.add_argument("--seconds", type=float, default=10, help="dry-run 时长")
    p.add_argument("--port", help="串口，如 COM5")
    p.add_argument("--udp", help="UDP，如 tcode.local:8000")
    p.add_argument("--ble", nargs="?", const=True, default=None)
    from osrdev import add_mode_args
    add_mode_args(p)
    args = p.parse_args()

    if not os.path.exists(args.script):
        raise SystemExit(f"找不到脚本：{args.script}")

    mapping = build_mapping(args)
    if args.show:
        show(mapping)
        return

    dev_args = dict(real=args.real, port=args.port, udp=args.udp,
                    ble=args.ble)
    if args.dry_run:
        asyncio.run(dry_run(mapping, args.seconds, args.speed, args.offset))
    else:
        from osrdev import DeviceNotFound
        try:
            asyncio.run(play(mapping, args.minutes, args.speed, args.offset,
                             args.loop, dev_args))
        except DeviceNotFound as e:
            print(f"\n[设备] {e}")
            raise SystemExit(1)


if __name__ == "__main__":
    main()
