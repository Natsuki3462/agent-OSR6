from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from presets6 import PRESET_INFO6, Runner6, format_line
from osrdev import add_mode_args, open_device

TICK = 0.06


def cmd_list():
    print(f"共 {len(PRESET_INFO6)} 个六轴预设：\n")
    for name, (label, desc) in PRESET_INFO6.items():
        print(f"  {name:9s} {label:6s} {desc}")
    print("\n用法：python presets.py --preset <名字> --minutes 10")


async def run_live(args):
    dev = await open_device(real=args.real, port=args.port,
                            udp=args.udp, ble=args.ble)
    runner = Runner6(args.preset, seed=args.seed, intensity=args.intensity)
    label = PRESET_INFO6[args.preset][0]
    total = args.minutes * 60.0
    print(f"预设 {args.preset}（{label}），强度 {args.intensity}，"
          f"跑 {args.minutes} 分钟（Ctrl+C 提前停）")

    t0 = time.monotonic()
    try:
        while True:
            t = time.monotonic() - t0
            if total > 0 and t >= total:
                break
            await dev.send6(runner.step(t), t=t)
            await asyncio.sleep(TICK)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n中断")
    finally:
        await dev.close()
        print("已归零断开")


async def run_dry(args):
    runner = Runner6(args.preset, seed=args.seed, intensity=args.intensity)
    t0 = time.monotonic()
    while True:
        t = time.monotonic() - t0
        if t > args.seconds:
            break
        print(format_line(t, runner.step(t)), flush=True)
        await asyncio.sleep(TICK)


def main():
    p = argparse.ArgumentParser(description="OSR6 六轴预设")
    p.add_argument("--list", action="store_true", help="列出全部预设")
    p.add_argument("--preset", help="预设名，如 orbit / tide")
    p.add_argument("--minutes", type=float, default=10.0, help="跑多少分钟")
    p.add_argument("--intensity", type=float, default=1.0,
                   help="强度倍数，1.0=原幅度，0.6=更收敛，1.4=更狠")
    p.add_argument("--seed", type=int, default=None, help="随机种子（复现用）")
    p.add_argument("--dry-run", action="store_true", help="只打印不连设备")
    p.add_argument("--seconds", type=float, default=10.0, help="dry-run 时长")
    p.add_argument("--port", help="串口，如 COM5")
    p.add_argument("--udp", help="UDP，如 tcode.local:8000")
    p.add_argument("--ble", nargs="?", const=True, default=None,
                   help="走 BLE（可跟地址）")
    add_mode_args(p)
    args = p.parse_args()

    if args.list or not args.preset:
        cmd_list()
        return
    if args.preset not in PRESET_INFO6:
        raise SystemExit(f"没有这个预设：{args.preset}\n"
                         f"可选：{', '.join(PRESET_INFO6)}")

    if args.dry_run:
        asyncio.run(run_dry(args))
    else:
        from osrdev import DeviceNotFound
        try:
            asyncio.run(run_live(args))
        except DeviceNotFound as e:
            print(f"\n[设备] {e}")
            raise SystemExit(1)


if __name__ == "__main__":
    main()
