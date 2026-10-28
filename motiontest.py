from __future__ import annotations

import argparse
import json
import socket
import sys
import time

AXES = ["L0", "L1", "L2", "R0", "R1", "R2"]
FAIL = []
PASSED = 0


def check(cond, msg, detail=""):
    global PASSED
    if cond:
        PASSED += 1
    else:
        FAIL.append(msg)
        print(f"  [FAIL] {msg}" + (f"  <- {detail}" if detail else ""))


class Dev:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.f = self.sock.makefile("rwb")
        self.last_rtt = 0.0

    def ask(self, obj):
        t0 = time.perf_counter()
        self.f.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
        self.f.flush()
        line = self.f.readline()
        self.last_rtt = (time.perf_counter() - t0) * 1000
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


def out_of(st):
    return {k: st["out"][k] for k in AXES if st.get("out") and k in st["out"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5199)
    ap.add_argument("--preset", default="orbit")
    ap.add_argument("--seconds", type=float, default=3.0)
    a = ap.parse_args()

    print("=" * 62)
    print("OSR 设备运动测试")
    print("=" * 62)

    d = Dev(a.port)
    st = d.status()
    print(f"设备    : {st.get('device')}   模式: "
          f"{'REAL 真机' if st.get('real') else 'EMU 模拟器'}")
    print(f"急停    : {st.get('estop')}    运行态: {st.get('mode')}")
    if st.get("estop"):
        print("急停锁着，先解除再测。")
        return 1
    if not st.get("device"):
        print(f"服务没有设备（last_error: {st.get('last_error')}）")
        return 1

    print("\n[1] 往返延迟（同一连接，连发 30 条）")
    rtts = []
    for _ in range(30):
        d.ask({"status": True})
        rtts.append(d.last_rtt)
    rtts.sort()
    avg = sum(rtts) / len(rtts)
    print(f"    平均 {avg:.2f}ms   中位 {rtts[len(rtts)//2]:.2f}ms   "
          f"最差 {rtts[-1]:.2f}ms")
    check(avg < 20, "本地往返延迟应 < 20ms", f"{avg:.2f}ms")

    print("\n[2] 过渡：L0 50 -> 85，ms=600")
    d.ask({"home": True, "ms": 300})
    time.sleep(0.5)
    base = out_of(d.status())["L0"]
    check(base == 50, "home 后应在中位", base)

    t0 = time.perf_counter()
    d.ask({"axes": {"L0": 85}, "ms": 600})
    samples = []
    for _ in range(9):
        time.sleep(0.1)
        s = out_of(d.status())["L0"]
        samples.append((round((time.perf_counter() - t0) * 1000), s))
    print("    " + "  ".join(f"{ms}ms:{v}" for ms, v in samples))
    vals = [v for _, v in samples]
    check(vals[-1] == 85, "600ms 后应到位", vals[-1])
    check(all(b >= a - 1 for a, b in zip(vals, vals[1:])),
          "过渡应单调向上，不能来回抖", vals)
    mid = [v for ms, v in samples if 200 <= ms <= 500]
    check(mid and all(50 < v < 85 for v in mid),
          "中途应处在 50~85 之间（真的是渐变，不是瞬移）", mid)

    print("\n[3] 关键帧：L0 30 -> 90 -> 40，每段 400ms")
    t0 = time.perf_counter()
    d.ask({"seq": [[0, {"L0": 30}], [400, {"L0": 90}],
                   [800, {"L0": 40}]], "ms": 0})
    seen = []
    for _ in range(12):
        time.sleep(0.1)
        seen.append((round((time.perf_counter() - t0) * 1000),
                     out_of(d.status())["L0"]))
    print("    " + "  ".join(f"{ms}:{v}" for ms, v in seen))
    peak_ms = max(seen, key=lambda x: x[1])
    check(abs(peak_ms[0] - 400) < 180,
          "峰值应出现在 400ms 附近", peak_ms)
    check(peak_ms[1] >= 85, "峰值应接近 90", peak_ms[1])

    print(f"\n[4] 预设 {a.preset}：跑 {a.seconds:.0f} 秒，看六轴是否真的在动")
    d.ask({"preset": a.preset, "intensity": 1.0})
    frames = []
    n = int(a.seconds * 10)
    for _ in range(n):
        time.sleep(0.1)
        frames.append(out_of(d.status()))
    spans = {}
    for k in AXES:
        vals = [f[k] for f in frames if k in f]
        spans[k] = (max(vals) - min(vals)) if vals else 0
    print("    每轴行程: " + "  ".join(f"{k}={v}" for k, v in spans.items()))
    moving = [k for k, v in spans.items() if v >= 5]
    check(len(moving) >= 2, "至少两轴在动", spans)
    check(all(0 <= v <= 100 for f in frames for v in f.values()),
          "所有值应在 0-100 内")

    print("\n[5] 强度：0.5 与 1.5 的行程应不同")
    d.ask({"preset": a.preset, "intensity": 0.5})
    lo = []
    for _ in range(int(a.seconds * 10)):
        time.sleep(0.1)
        lo.append(out_of(d.status()))
    d.ask({"intensity": 1.5})
    hi = []
    for _ in range(int(a.seconds * 10)):
        time.sleep(0.1)
        hi.append(out_of(d.status()))
    lo_span = max(max(f[k] for f in lo) - min(f[k] for f in lo) for k in AXES)
    hi_span = max(max(f[k] for f in hi) - min(f[k] for f in hi) for k in AXES)
    print(f"    0.5 最大行程 {lo_span}   1.5 最大行程 {hi_span}")
    check(hi_span >= lo_span, "强度越大行程应越大", (lo_span, hi_span))

    print("\n[6] 停止：应回中位，服务仍可用")
    d.ask({"stop": True})
    time.sleep(0.6)
    st = d.status()
    o = out_of(st)
    print("    " + "  ".join(f"{k}={v}" for k, v in o.items()))
    check(all(v == 50 for v in o.values()), "stop 后六轴应回中位", o)
    check(st.get("ok") and st.get("device"), "stop 后服务应仍活着", st.get("mode"))

    print("\n[7] 一致性：发什么，服务就报什么")
    d.ask({"axes": {"L0": 42, "R1": 66}, "ms": 0})
    time.sleep(0.3)
    o = out_of(d.status())
    check(o["L0"] == 42 and o["R1"] == 66, "out 应等于刚下发的值", o)

    d.close()

    print("\n" + "-" * 62)
    total = PASSED + len(FAIL)
    if FAIL:
        print(f"结果：{PASSED}/{total} 通过，{len(FAIL)} 项失败")
        for m in FAIL:
            print(f"  - {m}")
        return 1
    print(f"结果：全部通过（{PASSED} 项）—— 设备按指令运动，时序与行程都对得上")
    return 0


if __name__ == "__main__":
    sys.exit(main())
