from __future__ import annotations

import argparse
import json
import math
import socket
import sys
import time

AXES = ["L0", "L1", "L2", "R0", "R1", "R2"]


class Dev:
    def __init__(self, port=5199):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.f = self.sock.makefile("rwb")

    def ask(self, obj):
        self.f.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
        self.f.flush()
        line = self.f.readline()
        if not line:
            raise RuntimeError("服务断了")
        return json.loads(line.decode("utf-8"))

    def status(self):
        return self.ask({"status": True})

    def close(self):
        try:
            self.f.close()
            self.sock.close()
        except OSError:
            pass


def show(d, tag):
    o = d.status().get("out") or {}
    line = "  ".join(f"{k}={o[k]:>3}" for k in AXES if k in o)
    print(f"      {tag:<14} {line}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--port", type=int, default=5199)
    a = ap.parse_args()
    k = 0.45 if a.quick else 1.0

    d = Dev(a.port)
    st = d.status()
    if st.get("estop"):
        print("急停锁着，先解除。")
        return 1
    if not st.get("device"):
        print(f"没有设备：{st.get('last_error')}")
        return 1
    print(f"接管 {st.get('device')}（{'真机' if st.get('real') else '模拟器'}）\n")

    print("[0] 回中位")
    d.ask({"home": True, "ms": int(600 * k)})
    time.sleep(0.9 * k)
    show(d, "中位")

    print("\n[1] L0 行程：50 → 85 → 15 → 50（看立柱起落）")
    d.ask({"axes": {"L0": 85}, "ms": int(700 * k)})
    time.sleep(0.9 * k); show(d, "推到顶")
    d.ask({"axes": {"L0": 15}, "ms": int(1100 * k)})
    time.sleep(1.3 * k); show(d, "压到底")
    d.ask({"axes": {"L0": 50}, "ms": int(700 * k)})
    time.sleep(0.9 * k)

    print("\n[2] L2 横扫：15 → 85（看头部左右平移）")
    d.ask({"axes": {"L2": 15}, "ms": int(500 * k)})
    time.sleep(0.7 * k); show(d, "左")
    d.ask({"axes": {"L2": 85}, "ms": int(1200 * k)})
    time.sleep(1.4 * k); show(d, "右")
    d.ask({"axes": {"L2": 50}, "ms": int(500 * k)})
    time.sleep(0.7 * k)

    print("\n[3] L1 前后 + R2 俯仰：一起推拉")
    d.ask({"axes": {"L1": 20, "R2": 25}, "ms": int(700 * k)})
    time.sleep(0.9 * k); show(d, "后仰")
    d.ask({"axes": {"L1": 80, "R2": 75}, "ms": int(1100 * k)})
    time.sleep(1.3 * k); show(d, "前倾")
    d.ask({"axes": {"L1": 50, "R2": 50}, "ms": int(700 * k)})
    time.sleep(0.9 * k)

    print("\n[4] R0 旋转 + R1 滚转：看头部自转和侧倾")
    d.ask({"axes": {"R0": 20, "R1": 25}, "ms": int(800 * k)})
    time.sleep(1.0 * k); show(d, "逆时针")
    d.ask({"axes": {"R0": 80, "R1": 75}, "ms": int(1400 * k)})
    time.sleep(1.6 * k); show(d, "顺时针")
    d.ask({"axes": {"R0": 50, "R1": 50}, "ms": int(600 * k)})
    time.sleep(0.8 * k)

    print("\n[5] 六轴联动：画圆两圈（L1/L2 走圆，L0 起伏，R0 反向转）")
    seq = []
    N = 24
    for i in range(N + 1):
        th = 2 * math.pi * i / N
        seq.append([i * int(100 * k), {
            "L1": round(50 + 26 * math.cos(th)),
            "L2": round(50 + 26 * math.sin(th)),
            "L0": round(50 + 18 * math.sin(th * 2)),
            "R0": round(50 - 25 * math.sin(th)),
        }])
    d.ask({"seq": seq, "loop": True, "ms": 0})
    time.sleep(5.0 * k); show(d, "画圈中")
    d.ask({"stop": True})
    time.sleep(0.5 * k)

    print("\n[6] 预设 orbit：让它自己跑（看整体协调）")
    d.ask({"preset": "orbit", "intensity": 1.0, "speed": 1.0})
    time.sleep(4.0 * k); show(d, "orbit")
    d.ask({"intensity": 1.5})
    print("      强度拉到 1.5")
    time.sleep(3.0 * k); show(d, "orbit×1.5")

    print("\n[7] 预设 tide：最慢的那种（看它慢慢漂）")
    d.ask({"preset": "tide", "intensity": 1.2})
    time.sleep(4.0 * k); show(d, "tide")

    print("\n[8] 收工：回中位")
    d.ask({"stop": True})
    time.sleep(0.8 * k)
    show(d, "已停止")

    d.close()
    print("\n演示结束，设备已回中位并静止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
