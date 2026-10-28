from __future__ import annotations

import asyncio
import json
import math
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import daemon
from osr import build_line, encode, magnitude
from presets6 import AXES, CENTER, PRESET_INFO6, Runner6

FAIL = []
PASSED = 0


def check(cond, msg, detail=""):
    global PASSED
    if cond:
        PASSED += 1
    else:
        FAIL.append(msg)
        print(f"  [FAIL] {msg}" + (f"  <- {detail}" if detail else ""))


def near(a, b, tol=1e-6):
    return abs(a - b) <= tol



def test_encode():
    print("[1] TCode 编码")
    check(magnitude(0.5) == "5000", "0.5 应编成 5000", magnitude(0.5))
    check(magnitude(0.05) == "0500", "0.05 应编成 0500（前导零不能丢）",
          magnitude(0.05))
    check(magnitude(1.0) == "9999", "1.0 应截到 9999", magnitude(1.0))
    check(magnitude(0) == "0000", "0 应编成 0000", magnitude(0))
    check(magnitude(-3) == "0000", "负值应夹到 0", magnitude(-3))
    check(magnitude(float("nan")) == "5000", "NaN 应回落到中位",
          magnitude(float("nan")))
    check(encode("l0", 0.5) == "L05000", "轴名应大写", encode("l0", 0.5))
    check(build_line({"L0": 0.5, "R1": 0.25}) == "L05000 R12500",
          "多轴拼行", build_line({"L0": 0.5, "R1": 0.25}))
    try:
        encode("XX", 0.5)
        check(False, "非法轴名应抛 ValueError")
    except ValueError:
        check(True, "非法轴名抛 ValueError")



MIN_ACTIVE = {"pulse": 55}


def test_presets():
    print(f"[2] 预设（{len(PRESET_INFO6)} 个）")
    for name in PRESET_INFO6:
        r = Runner6(name, seed=7)
        lo = {a: 1.0 for a in AXES}
        hi = {a: 0.0 for a in AXES}
        active = 0
        frames = 0
        bad = 0
        for i in range(700):
            t = i * 0.06
            v = r.step(t)
            frames += 1
            if set(v) - set(AXES) - {"V0"}:
                bad += 1
            dev = 0.0
            for a in AXES:
                x = v[a]
                if not (0.0 <= x <= 1.0):
                    bad += 1
                lo[a] = min(lo[a], x)
                hi[a] = max(hi[a], x)
                dev = max(dev, abs(x - CENTER))
            if dev > 0.03:
                active += 1
        span_l0 = hi["L0"] - lo["L0"]
        act = active * 100 // frames
        check(bad == 0, f"{name}: 数值越界或轴名异常", f"{bad} 帧异常")
        check(act >= MIN_ACTIVE.get(name, 85), f"{name}: 活跃帧过低", f"{act}%")
        if name in MIN_ACTIVE:
            check(act <= 90, f"{name}: 应当有停顿，活跃帧不该这么高",
                  f"{act}%")
        check(span_l0 >= 0.15, f"{name}: L0 行程太小", f"{span_l0:.2f}")
        check("V0" in v, f"{name}: 缺 V0 通道")
        print(f"     {name:9s} 活跃 {act:3d}%  L0 行程 {span_l0:.2f}  "
              f"L0 [{lo['L0']:.2f},{hi['L0']:.2f}]")



def reset_ctx():
    daemon.CTX.update({
        "mode": "idle", "auto": False, "axes": None, "fade": None,
        "seq": None, "runner": None, "preset": None,
        "intensity": 1.0, "speed": 1.0,
        "estop": False, "estop_since": 0.0,
        "want_real": None, "real": False, "reconnect": False,
        "switching": False, "last_error": "", "device": None,
        "owner": "", "owner_until": 0.0, "want_port": None,
    })
    daemon.SUPPORTED = set(AXES) | {"V0"}


def test_commands():
    print("[3] 指令解析")
    reset_ctx()

    r = daemon.apply_command({"status": True})
    check(r["ok"] and r["mode"] == "idle", "status 应回 ok + 当前模式", r)

    r = daemon.apply_command({"axes": {"L0": 70, "L2": 35}})
    check(r["ok"] and r["mode"] == "axes", "axes 应进入 axes 模式", r)
    check(r["axes"] == {"L0": 70, "L2": 35}, "axes 应回显目标值", r["axes"])

    r = daemon.apply_command({"axes": {"L0": 130, "XX": 5}})
    check(r["ok"] and r["axes"]["L0"] == 100, "超范围应夹到 100", r.get("axes"))
    check(r.get("ignored") == ["XX"], "不支持的轴应进 ignored", r.get("ignored"))

    r = daemon.apply_command({"axes": {"XX": 5}})
    check(not r["ok"], "全是非法轴应报错", r)

    r = daemon.apply_command({"axes": {"L0": 40}, "ms": 500})
    check(r["ok"] and r["mode"] == "fade", "带 ms 应进入 fade 模式", r)
    check(daemon.CTX["fade"]["ms"] == 500, "fade 时长应记录", daemon.CTX["fade"])

    r = daemon.apply_command({"seq": [[0, {"L0": 20}], [500, {"L0": 80}]]})
    check(r["ok"] and r["mode"] == "seq", "seq 应进入 seq 模式", r)

    r = daemon.apply_command({"seq": [[0, {"L0": 20}]]})
    check(not r["ok"], "seq 只有 1 帧应报错", r)

    r = daemon.apply_command({"seq": "nope"})
    check(not r["ok"], "seq 非数组应报错", r)

    r = daemon.apply_command({"preset": "orbit"})
    check(r["ok"] and r["preset"] == "orbit", "preset 应切换", r)
    check(daemon.CTX["mode"] == "preset", "preset 应进入 preset 模式")

    r = daemon.apply_command({"preset": "nope"})
    check(not r["ok"] and "available" in r, "未知预设应报错并列出可选", r)

    r = daemon.apply_command({"preset": "tide", "intensity": 1.4})
    check(near(daemon.CTX["intensity"], 1.4), "切预设应同时设强度",
          daemon.CTX["intensity"])
    check(near(daemon.CTX["runner"].intensity, 1.4), "强度应传给 runner")

    r = daemon.apply_command({"intensity": 0.6})
    check(r["ok"] and near(daemon.CTX["intensity"], 0.6), "单独改强度", r)
    check(near(daemon.CTX["runner"].intensity, 0.6), "单独改强度应同步 runner")

    r = daemon.apply_command({"speed": 9})
    check(r["ok"] and near(daemon.CTX["speed"], 3.0), "speed 应夹到 3.0",
          daemon.CTX["speed"])

    r = daemon.apply_command({"home": True})
    check(r["ok"] and r["axes"]["L0"] == 50, "home 应回中位", r.get("axes"))
    check(r["mode"] == "fade", "home 默认走过渡", r["mode"])

    r = daemon.apply_command({"vib": 50})
    check(not r["ok"] and "V0" in r["error"], "三通道指令应被明确拒绝", r)

    r = daemon.apply_command({"nonsense": 1})
    check(not r["ok"], "看不懂的指令应报错", r)

    r = daemon.apply_command({"auto": True})
    check(r["ok"] and daemon.CTX["auto"] and r["preset"] in PRESET_INFO6,
          "auto 应选一个预设并置位", r)



def test_interp():
    print("[4] 过渡 / 关键帧插值")
    reset_ctx()

    now = time.monotonic()
    daemon.CTX["mode"] = "fade"
    daemon.CTX["fade"] = {"t0": now, "ms": 1000,
                          "from": {"L0": 0.0}, "to": {"L0": 1.0}}
    v = daemon._current_axes(now + 0.0)
    check(near(v["L0"], 0.0, 0.02), "过渡起点应为 0", v)
    v = daemon._current_axes(now + 0.5)
    check(near(v["L0"], 0.5, 0.02), "过渡中点应约 0.5", v)
    v = daemon._current_axes(now + 1.2)
    check(near(v["L0"], 1.0), "过渡结束应为 1", v)
    check(daemon.CTX["mode"] == "axes", "过渡结束应转成 axes 保持",
          daemon.CTX["mode"])

    reset_ctx()
    daemon.apply_command({"seq": [[0, {"L0": 20}], [500, {"L0": 80}],
                                  [1000, {"L0": 20}]]})
    q = daemon.CTX["seq"]
    t0 = q["t0"]
    v = daemon._current_axes(t0 + 0.25)
    check(near(v["L0"], 0.5, 0.03), "seq 250ms 应在 0.5 附近", v)
    v = daemon._current_axes(t0 + 0.50)
    check(near(v["L0"], 0.8, 0.03), "seq 500ms 应到 0.8", v)
    v = daemon._current_axes(t0 + 1.10)
    check(near(v["L0"], 0.2, 0.03), "seq 播完应停在最后一帧", v)
    check(daemon.CTX["mode"] == "axes", "seq 播完应转成静态保持",
          daemon.CTX["mode"])

    reset_ctx()
    daemon.apply_command({"seq": [[0, {"L0": 0}], [1000, {"L0": 100}]],
                          "loop": True})
    q = daemon.CTX["seq"]
    t0 = q["t0"]
    v = daemon._current_axes(t0 + 1.5)
    check(0.0 <= v["L0"] <= 1.0, "循环时数值应在范围内", v)
    check(daemon.CTX["mode"] == "seq", "循环不应退出 seq 模式",
          daemon.CTX["mode"])

    reset_ctx()
    daemon.apply_command({"seq": [[0, {"L0": 20}], [400, {"L0": 60, "L2": 80}]]})
    v = daemon._current_axes(daemon.CTX["seq"]["t0"] + 0.2)
    check("L2" in v and near(v["L2"], 0.65, 0.05),
          "后一帧新增的轴应从上一帧补位插值", v)


def test_switch():
    print("[5] 真机 / 模拟器 开关（不持久化）")
    import shutil
    import tempfile

    import config

    check(config.load().get("real_device") is False,
          "默认必须是模拟器（安全）", config.load().get("real_device"))
    check(config.resolve_real(False) is False, "--emu 应返回模拟器")
    check(config.resolve_real(True) is True, "--real 应返回真机")
    check(config.resolve_real() is False, "不带参数时默认模拟器（不读磁盘）")

    d = tempfile.mkdtemp()
    old_file = config.CONFIG_FILE
    config.CONFIG_FILE = os.path.join(d, "config.json")
    try:
        with open(config.CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write('{"real_device": true}')
        check(config.load().get("real_device") is False,
              "config.json 里的 true 必须被忽略（模式不持久化）",
              config.load())
        check(config.resolve_real() is False,
              "磁盘上的 true 不能影响启动模式", config.resolve_real())
    finally:
        config.CONFIG_FILE = old_file
        shutil.rmtree(d, ignore_errors=True)

    old = os.environ.get("OSR_REAL")
    os.environ["OSR_REAL"] = "0"
    check(config.resolve_real(True) is False,
          "环境变量 OSR_REAL=0 应覆盖 --real")
    os.environ["OSR_REAL"] = "1"
    check(config.resolve_real(False) is True,
          "环境变量 OSR_REAL=1 应覆盖 --emu")
    if old is None:
        os.environ.pop("OSR_REAL", None)
    else:
        os.environ["OSR_REAL"] = old


async def test_emu():
    print("[6] osr-emu 模拟器")
    from osr_emu import EmuDevice
    from osrdev import open_device

    d = EmuDevice(verbose=False)
    check(d.alive and "EMU" in d.describe(), "模拟器应能描述自己")
    check(set(AXES) <= d.supported, "模拟器应支持六轴", sorted(d.supported))

    await d.send6({"L0": 0.6, "R1": 0.25})
    check(d.lines[-1] == "L06000 R12500", "应发出正确 TCode", d.lines[-1])

    n_before = d.tx_count
    await d.send6({"L0": 0.6, "R1": 0.25})
    check(d.tx_count == n_before, "同值重复下发应被去重", d.tx_count)

    await d.send6({"L0": 2.0, "ZZ": 0.3})
    check(near(d.state["L0"], 1.0), "超范围应夹到 1.0", d.state["L0"])
    check("ZZ" not in d.state, "不支持的轴应被丢弃")

    await d.send6({"L0": 0.1})
    check(d.tx_count == 3, "应记录 3 次下发", d.tx_count)

    await d.stop()
    check(near(d.state["L0"], 0.5) and near(d.state["V0"], 0.0),
          "stop 应回中位、振动归零", d.state)
    check(d.lines[-1] == "DSTOP", "stop 应发 DSTOP", d.lines[-1])

    await d.close()
    check(not d.alive, "close 后 alive 应为 False")

    dev = await open_device(real=False, quiet=True)
    check(isinstance(dev, EmuDevice), "open_device(real=False) 应返回模拟器",
          type(dev).__name__)
    await dev.close()



class FakeOSR:
    name = "osr6"

    def __init__(self):
        self.calls = []
        self.transport = "fake"
        self.endpoint = "FAKE"
        self.supported = set(AXES) | {"V0"}
        self.closed = False

    def describe(self):
        return "OSR6 (TCode v0.3) [fake:FAKE]"

    @property
    def alive(self):
        return not self.closed

    async def send6(self, axes, t=None):
        self.calls.append((time.monotonic(), dict(axes)))

    async def stop(self):
        self.calls.append((time.monotonic(), "stop"))

    async def close(self):
        self.closed = True
        self.calls.append((time.monotonic(), "close"))


async def _connect(port, tries=60):
    last = None
    for _ in range(tries):
        try:
            return await asyncio.open_connection("127.0.0.1", port)
        except OSError as e:
            last = e
            await asyncio.sleep(0.05)
    raise RuntimeError(f"连不上测试服务：{last}")


async def _ask(reader, writer, obj, expect_reply=True):
    writer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
    await writer.drain()
    if not expect_reply:
        return None
    raw = await asyncio.wait_for(reader.readline(), 3.0)
    return json.loads(raw.decode("utf-8"))


async def _estop(port, on=None, real=None):
    r, w = await _connect(port)
    payload = {"token": daemon.ESTOP_TOKEN}
    if on is not None:
        payload["estop"] = on
    if real is not None:
        payload["real"] = real
    w.write((json.dumps(payload) + "\n").encode())
    await w.drain()
    resp = json.loads((await asyncio.wait_for(r.readline(), 3.0)).decode())
    w.close()
    return resp


async def test_daemon():
    print("[7] 守护进程协议")
    fake = FakeOSR()

    async def fake_open(**kw):
        fake.closed = False
        fake.transport = "fake"
        return fake

    daemon.open_device = fake_open
    daemon.PORT = 5211
    daemon.ESTOP_PORT = 5212
    daemon.QUIT.clear()
    reset_ctx()

    task = asyncio.create_task(
        daemon.main({"port": None, "udp": None, "ble": None, "preset": None}))

    reader, writer = await _connect(5211)
    check(True, "服务应能接受连接")

    r = await _ask(reader, writer, {"status": True})
    check(r["ok"] and r["device"] == "osr6", "status 应报告设备", r)
    check("real" in r, "status 应报告当前是真机还是模拟器", r)

    replies = []
    for i in range(6):
        replies.append(await _ask(reader, writer,
                                  {"axes": {"L0": 30 + i * 10}}))
    check(all(x["ok"] for x in replies), "同一连接连发 6 条应全部成功",
          replies)
    check(len(replies) == 6, "应收到 6 条应答", len(replies))

    fake.calls.clear()
    await asyncio.sleep(1.0)
    n = len(fake.calls)
    check(n >= 30, "六轴模式下发频率应接近 50Hz", f"1.0s 内 {n} 次")
    print(f"      实测下发 {n} 次/秒（目标 50，Windows 定时精度已提到 1ms）")

    await _ask(reader, writer, {"axes": {"L0": 90}, "ms": 400})
    r = await _ask(reader, writer, {"status": True})
    check(r["mode"] == "fade" and r.get("fade_ms", 0) > 0,
          "过渡中 status 应显示剩余毫秒", r)
    await asyncio.sleep(0.5)
    r = await _ask(reader, writer, {"status": True})
    check(r["mode"] == "axes" and r["axes"]["L0"] == 90,
          "过渡结束应停在目标值", r)

    r = await _ask(reader, writer,
                   {"seq": [[0, {"L0": 20}], [300, {"L0": 80}]]})
    check(r["ok"] and r["mode"] == "seq", "seq 应被接受", r)
    await asyncio.sleep(0.45)
    r = await _ask(reader, writer, {"status": True})
    check(r["mode"] == "axes" and r["axes"]["L0"] == 80,
          "seq 播完应停在末帧", r)

    r = await _ask(reader, writer, {"preset": "sway"})
    check(r["ok"] and r["preset"] == "sway", "预设应切换", r)
    fake.calls.clear()
    await asyncio.sleep(0.5)
    check(len(fake.calls) >= 3, "预设模式应有下发（10Hz）", len(fake.calls))

    fake.calls.clear()
    r = await _ask(reader, writer, {"axes": {"L0": 60}})
    check(r["ok"], "急停前应能正常控制", r)

    er, ew = await _connect(5212)
    ew.write((json.dumps({"token": "wrong", "estop": True}) + "\n").encode())
    await ew.drain()
    r = json.loads((await asyncio.wait_for(er.readline(), 3.0)).decode())
    check(not r["ok"], "错误令牌应被拒绝", r)
    ew.close()

    er, ew = await _connect(5212)
    ew.write((json.dumps({"token": daemon.ESTOP_TOKEN,
                          "estop": True}) + "\n").encode())
    await ew.drain()
    r = json.loads((await asyncio.wait_for(er.readline(), 3.0)).decode())
    check(r["ok"] and r["estop"] is True, "按下按钮应进入锁定", r)
    ew.close()
    check(any(c[1] == "stop" for c in fake.calls if isinstance(c[1], str)),
          "按下瞬间应立刻归零")

    fake.calls.clear()
    for cmd in ({"axes": {"L0": 70}}, {"preset": "orbit"}, {"home": True},
                {"seq": [[0, {"L0": 10}], [300, {"L0": 90}]]},
                {"auto": True}, {"intensity": 1.5}):
        r = await _ask(reader, writer, cmd)
        check(not r["ok"] and r.get("estop") is True,
              f"锁定中应拒绝 {cmd}", r)
    await asyncio.sleep(0.3)
    check(len(fake.calls) == 0, "锁定中不应有任何下发", len(fake.calls))

    r = await _ask(reader, writer, {"status": True})
    check(r["ok"] and r["estop"] is True, "锁定中应能查状态", r)

    r = await _ask(reader, writer, {"estop": False})
    check(not r["ok"], "控制通道不能解除急停", r)
    r = await _ask(reader, writer, {"status": True})
    check(r["estop"] is True, "尝试解除后应仍然锁定", r)

    er, ew = await _connect(5212)
    ew.write((json.dumps({"token": daemon.ESTOP_TOKEN,
                          "estop": False}) + "\n").encode())
    await ew.drain()
    r = json.loads((await asyncio.wait_for(er.readline(), 3.0)).decode())
    check(r["ok"] and r["estop"] is False, "按钮解除应生效", r)
    ew.close()

    r = await _ask(reader, writer, {"axes": {"L0": 65}})
    check(r["ok"], "解除后应能继续控制", r)

    await _ask(reader, writer, {"preset": "sway"})
    await asyncio.sleep(0.4)
    r = await _ask(reader, writer, {"status": True})
    check(isinstance(r.get("out"), dict) and "L0" in r["out"],
          "status 应带当前输出帧 out", r.get("out"))
    check(0 <= r["out"]["L0"] <= 100, "out 应是 0-100 的整数", r["out"])

    await _estop(5212, True)
    r = await _estop(5212, real=True)
    check(r.get("ok") and r.get("real") is True, "应能请求切到真机", r)
    await asyncio.sleep(0.8)
    r = await _ask(reader, writer, {"status": True})
    check(r["ok"], "切换期间控制连接应保持（服务不重启）", r)
    check(r["real"] is True, "切换后 real 应为 true", r)
    check(r["estop"] is True, "切换模式不应清掉急停锁定", r)
    await _estop(5212, False)

    r = await _ask(reader, writer, {"preset": "nope"})
    check(not r["ok"] and "available" in r, "未知预设应报错", r)
    r = await _ask(reader, writer, {"vib": 50})
    check(not r["ok"], "三通道指令应被拒绝", r)

    writer.write(b"{bad json\n")
    await writer.drain()
    raw = await asyncio.wait_for(reader.readline(), 3.0)
    r = json.loads(raw.decode())
    check(not r["ok"] and "bad json" in r["error"], "坏 JSON 应回错误", r)

    r = await _ask(reader, writer, {"stop": True})
    check(r["ok"] and r["stopped"], "stop 应回 stopped", r)
    check(any(c[1] == "stop" for c in fake.calls if isinstance(c[1], str)),
          "stop 应调用设备的 stop()")
    r = await _ask(reader, writer, {"status": True})
    check(r["ok"] and r["mode"] == "idle", "stop 之后服务应仍然响应", r)
    r = await _ask(reader, writer, {"axes": {"L0": 60}})
    check(r["ok"], "stop 之后应还能继续控制", r)

    writer.write((json.dumps({"quit": True}) + "\n").encode())
    await writer.drain()
    raw = await asyncio.wait_for(reader.readline(), 3.0)
    r = json.loads(raw.decode())
    check(r.get("bye") is True, "quit 应回 bye", r)
    await asyncio.wait_for(task, 5.0)
    check(task.done(), "quit 后服务应退出")
    check(fake.closed, "quit 后应关闭设备")

    writer.close()



def test_client():
    print("[8] 客户端（ctl.Client）")
    import ctl
    check(ctl.parse_axes_text("L0=60,L2=35") == {"L0": 60.0, "L2": 35.0},
          "parse_axes_text 解析", ctl.parse_axes_text("L0=60,L2=35"))
    try:
        ctl.parse_axes_text("L0")
        check(False, "缺 = 应报错")
    except SystemExit:
        check(True, "缺 = 会报错退出")

    check(hasattr(ctl.Client, "send"), "Client 应有 send()")
    check(ctl.Client.__init__.__defaults__[1] == 5199,
          "Client 默认端口应为 5199")




async def test_mode_recover():
    print("[10] 切模式：真机连不上时切回模拟器应自动恢复")
    fake = FakeOSR()

    async def open_device(real=None, **kw):
        if real:
            raise daemon.DeviceNotFound("模拟：没找到 OSR 设备")
        fake.closed = False
        return fake

    orig = daemon.open_device
    daemon.open_device = open_device
    daemon.PORT = 5221
    daemon.ESTOP_PORT = 5222
    daemon.QUIT.clear()
    reset_ctx()
    try:
        task = asyncio.create_task(daemon.main(
            {"port": None, "udp": None, "ble": None, "preset": None,
             "real": True}))
        r, w = await _connect(daemon.PORT)

        err = ""
        st = {}
        for _ in range(50):
            await asyncio.sleep(0.1)
            st = await _ask(r, w, {"status": True})
            err = st.get("last_error") or ""
            if err:
                break
        check("没找到" in err, "真机连不上应报 last_error", err)
        check(st.get("device") is None, "连不上时不应有设备", st.get("device"))

        resp = await _estop(daemon.ESTOP_PORT, real=False)
        check(resp.get("ok") is True, "切换请求应被接受", resp)

        ok = False
        for _ in range(50):
            await asyncio.sleep(0.1)
            st = await _ask(r, w, {"status": True})
            if st.get("device") == "osr6" and not st.get("last_error"):
                ok = True
                break
        check(ok, "切回模拟器后应自动连上（不能卡在「没找到设备」）", st)

        await _ask(r, w, {"axes": {"L0": 80}})
        await asyncio.sleep(0.3)
        st = await _ask(r, w, {"status": True})
        check(st.get("out", {}).get("L0") == 80,
              "恢复后指令应真的生效", st.get("out"))

        await _ask(r, w, {"quit": True})
        await asyncio.wait_for(task, 5.0)
        w.close()
    finally:
        daemon.open_device = orig
        daemon.QUIT.clear()



def test_token():
    print("[9] 急停令牌（必须稳定，不能被重启换掉）")
    import shutil
    import tempfile

    old = daemon.ESTOP_TOKEN_FILE
    d = tempfile.mkdtemp()
    path = os.path.join(d, ".estop_token")
    daemon.ESTOP_TOKEN_FILE = path
    try:
        t1 = daemon._load_or_make_token()
        check(len(t1) >= 16, "首次应生成令牌", t1)

        t2 = daemon._load_or_make_token()
        check(t1 == t2, "再次调用必须复用同一把（否则急停会失效）", (t1, t2))

        with open(path, encoding="utf-8") as f:
            on_disk = f.read().strip()
        check(on_disk == t1,
              "第二个实例启动不能改写已有令牌（改了急停就按不动）",
              (on_disk, t1))

        os.unlink(path)
        t3 = daemon._load_or_make_token()
        check(t3 != t1 and len(t3) >= 16, "文件被删后才应换新令牌", t3)

        with open(path, "w", encoding="utf-8") as f:
            f.write("abc")
        t4 = daemon._load_or_make_token()
        check(t4 != "abc" and len(t4) >= 16, "过短的令牌应重建", t4)
    finally:
        daemon.ESTOP_TOKEN_FILE = old
        shutil.rmtree(d, ignore_errors=True)



def _fake_daemon():
    import socket
    import threading

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    srv.settimeout(0.2)
    port = srv.getsockname()[1]
    seen = []
    halt = threading.Event()

    def reply(obj):
        if obj.get("status"):
            return {"ok": True, "device": "osr6", "real": False, "estop": False,
                    "mode": "idle", "preset": None, "axes": {a: 50 for a in AXES},
                    "out": {a: 50 for a in AXES}, "intensity": 1.0, "speed": 1.0,
                    "last_error": "",
                    "link": {"transport": "emu", "transport_name": "本地模拟",
                             "endpoint": "local", "baud": None,
                             "connected": True, "uptime_s": 3,
                             "hint": "本地模拟器", "error": "",
                             "ports": [], "serial_ready": True}}
        if "estop" in obj:
            return {"ok": True, "estop": bool(obj["estop"]), "by": "fake"}
        if "real" in obj:
            return {"ok": True, "real": bool(obj["real"]), "by": "fake"}
        return {"ok": True, "echo": obj}

    def loop():
        while not halt.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                conn.settimeout(1.0)
                buf = b""
                try:
                    while b"\n" not in buf:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                except OSError:
                    continue
                try:
                    obj = json.loads(buf.split(b"\n")[0].decode("utf-8"))
                except Exception:
                    obj = {}
                if not isinstance(obj, dict):
                    obj = {}
                seen.append(obj)
                body = json.dumps(reply(obj), ensure_ascii=False) + "\n"
                try:
                    conn.sendall(body.encode("utf-8"))
                except OSError:
                    pass

    th = threading.Thread(target=loop, daemon=True)
    th.start()

    def close():
        halt.set()
        try:
            srv.close()
        except OSError:
            pass

    return port, seen, close


def _closed_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_webui():
    print("[11] WebUI 后端（webui.py）")
    import socket
    import tempfile
    import threading
    import urllib.error
    import urllib.request

    import webui

    old = (webui.DAEMON_PORT, webui.ESTOP_PORT, webui.TOKEN_FILE)
    tokpath = None
    fport, seen, close_fake = _fake_daemon()
    dead = _closed_port()
    try:
        r = webui.talk(fport, {"status": True})
        check(r.get("ok") is True and r.get("device") == "osr6",
              "talk() 能拿到应答", r)
        check("token" not in seen[-1], "控制通道不应带令牌", seen[-1])

        webui.talk(fport, {"estop": True}, token="s3cret")
        check(seen[-1].get("token") == "s3cret", "talk(token=) 应注入令牌", seen[-1])
        check(seen[-1].get("estop") is True, "令牌不影响业务字段", seen[-1])

        r = webui.talk(dead, {"status": True})
        check(r.get("ok") is False and "error" in r, "连不上应回 ok=False", r)

        check(webui.port_open(fport) is True, "port_open 对活端口应为 True")
        check(webui.port_open(dead) is False, "port_open 对死端口应为 False")

        tmp = tempfile.NamedTemporaryFile("w", suffix=".tok", delete=False,
                                          encoding="utf-8")
        tmp.write("  abc123\n")
        tmp.close()
        tokpath = tmp.name
        webui.TOKEN_FILE = tokpath
        check(webui.read_token() == "abc123", "read_token 应去掉空白",
              repr(webui.read_token()))

        webui.TOKEN_FILE = os.path.join(HERE, "不存在的令牌文件.tok")
        check(webui.read_token() == "", "令牌文件不存在应回空串")
        r = webui.estop_talk({"estop": True})
        check(r.get("ok") is False and "令牌" in r.get("error", ""),
              "没令牌时 estop_talk 应明确报错", r)

        webui.TOKEN_FILE = tmp.name
        webui.ESTOP_PORT = fport
        r = webui.estop_talk({"estop": True})
        check(r.get("ok") is True and r.get("estop") is True,
              "estop_talk 应带令牌走 5198", r)
        check(seen[-1].get("token") == "abc123", "estop 请求应带令牌", seen[-1])

        webui.DAEMON_PORT = fport
        st = webui.collect_state()
        check(st["daemon_up"] is True, "collect_state 应认出服务在跑", st)
        check(st["mode"] == "idle", "collect_state 应透传 mode", st)
        check(st["out"] == {a: 50 for a in AXES}, "collect_state 应透传 out", st)
        check((st.get("link") or {}).get("transport") == "emu",
              "collect_state 应透传 link（切真机时靠它显示连接方式）",
              st.get("link"))
        check((st.get("link") or {}).get("connected") is True,
              "link.connected 也要透传", st.get("link"))

        webui.DAEMON_PORT = dead
        st = webui.collect_state()
        check(st["daemon_up"] is False, "服务没起时 daemon_up 应为 False", st)
        check(st["mode"] == "offline", "服务没起时 mode 应为 offline", st)
        check(st.get("link") is None,
              "服务没起时 link 应为 None（不能编一个假的连接状态）", st)
        check(st["last_error"] == "控制服务未启动",
              "服务没起时应给出原因", st.get("last_error"))
        check(st["real"] == st["config_real"],
              "服务没起时 real 应退回配置意图", st)

        webui.DAEMON_PORT = fport
        r = webui.spawn_daemon()
        check(r.get("ok") is True and r.get("note") == "已在运行",
              "端口已占用时 spawn_daemon 不应再起一个", r)

        real_set = []
        orig_set_real, orig_load = webui.config.set_real, webui.config.load
        webui.config.set_real = lambda v: real_set.append(bool(v)) or {
            "real_device": bool(v)}
        webui.config.load = lambda: {"real_device": False}

        httpd = webui.serve(0)
        hport = httpd.server_address[1]
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()

        def get(path):
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{hport}{path}", timeout=3) as resp:
                return resp.status, resp.read()

        def post(path, obj):
            req = urllib.request.Request(
                f"http://127.0.0.1:{hport}{path}",
                data=json.dumps(obj).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))

        code, body = get("/")
        html = body.decode("utf-8", "replace")
        check(code == 200, "GET / 应回 200", code)
        check("<html" in html.lower(), "GET / 应回页面", html[:40])
        check('id="console"' in html, "页面应有左控制台")
        check('id="sim"' in html, "页面应有右模拟视图")
        check("collapsed" in html, "页面应有抽屉收起状态")
        for word in ("emu", "real", "estop", "offline"):
            check(f'body[data-state="{word}"]' in html,
                  f"页面应有 {word} 主题（绿/红/黄/灰）")

        code, body = get("/api/state")
        st2 = json.loads(body.decode("utf-8"))
        check(code == 200 and st2.get("daemon_up") is True,
              "GET /api/state 应反映服务在线", st2)

        code, r = post("/api/cmd", {"axes": {"L0": 60}})
        check(code == 200 and r.get("ok") is True, "POST /api/cmd 应转发", r)
        check(seen[-1].get("axes") == {"L0": 60},
              "/api/cmd 应把指令原样转发", seen[-1])
        check(seen[-1].get("src") == "ui",
              "/api/cmd 必须标记 src=ui（daemon 靠它区分界面与 agent）",
              seen[-1])
        check("token" not in seen[-1], "/api/cmd 不能带令牌", seen[-1])

        code, r = post("/api/estop", {"on": True})
        check(r.get("ok") is True and r.get("estop") is True,
              "POST /api/estop 应走令牌通道", r)
        check(seen[-1].get("token") == "abc123" or seen[-1].get("token"),
              "/api/estop 必须带令牌", seen[-1])

        code, r = post("/api/mode", {"real": True})
        check(r.get("ok") is True, "POST /api/mode 应成功", r)
        check(real_set == [],
              "/api/mode 不该再写配置（模式不持久化，每次启动回模拟器）",
              real_set)
        check(seen[-1].get("real") is True, "/api/mode 应通知服务切模式", seen[-1])

        code, r = post("/api/port", {"port": "com5"})
        check(seen[-1].get("port") == "COM5",
              "/api/port 应把端口名规范成大写", seen[-1])
        check(seen[-1].get("token"), "/api/port 必须带令牌（属于设备配置）",
              seen[-1])

        code, r = post("/api/release", {})
        check(seen[-1].get("release") is True,
              "/api/release 应走令牌通道（agent 拿不回控制权）", seen[-1])

        code, r = post("/api/log", {"action": "tail", "name": "daemon.log"})
        check("lines" in r, "日志接口应始终带 lines 字段", r)
        code, r = post("/api/log", {"action": "open", "name": "../../etc/passwd"})
        check(r.get("ok") is False, "日志 open 只认白名单，不接受任意路径", r)
        code, r = post("/api/log", {"action": "bogus"})
        check(r.get("ok") is False, "未知日志 action 应报错", r)

        try:
            get("/api/nope")
            check(False, "未知 GET 应 404")
        except urllib.error.HTTPError as e:
            check(e.code == 404, "未知 GET 应 404", e.code)

        import shutil as _sh
        import tempfile as _tf

        vdir = _tf.mkdtemp()
        with open(os.path.join(vdir, "fake.js"), "w", encoding="utf-8") as f:
            f.write("window.OSREmulator = function () {};")
        old_vendor = webui.VENDOR_DIR
        webui.VENDOR_DIR = vdir
        try:
            code, body = get("/vendor/fake.js")
            check(code == 200 and b"OSREmulator" in body,
                  "/vendor/* 应能取到文件", (code, len(body)))

            def raw_status(path):
                with socket.create_connection(("127.0.0.1", hport),
                                              timeout=3) as sk:
                    sk.sendall(("GET %s HTTP/1.1\r\nHost: x\r\n"
                                "Connection: close\r\n\r\n" % path)
                               .encode("ascii"))
                    data = b""
                    while True:
                        chunk = sk.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                return int(data.split(b" ")[1])

            st_code = raw_status("/vendor/../.estop_token")
            check(st_code in (400, 404),
                  "目录穿越必须被拒（别把令牌漏出去）", st_code)
            st_code = raw_status("/vendor/nope.js")
            check(st_code == 404, "vendor 里没有的文件应 404", st_code)
        finally:
            webui.VENDOR_DIR = old_vendor
            _sh.rmtree(vdir, ignore_errors=True)

        httpd.shutdown()
        httpd.server_close()
        webui.config.set_real, webui.config.load = orig_set_real, orig_load
    finally:
        close_fake()
        webui.DAEMON_PORT, webui.ESTOP_PORT, webui.TOKEN_FILE = old
        if tokpath:
            try:
                os.unlink(tokpath)
            except OSError:
                pass

    idx = os.path.join(HERE, "webui.html")
    check(os.path.exists(idx), "webui.html 应存在")
    with open(idx, "r", encoding="utf-8") as f:
        html = f.read()
    check(len(html) > 20000, "webui.html 不应被截断", len(html))
    for k in ("ALERTS", "requestAnimationFrame", "iso", "render("):
        check(k.lower() in html.lower(), f"页面应含 {k}")
    check("5199" not in html, "页面不应直连 5199（要走 /api）")
    check("vendor/osr-emu.dist.min.js" in html, "页面应引用内置 3D 库")
    check("initEmu3D" in html and "EMU3D.ok" in html,
          "页面应有 3D 初始化与降级判断")
    check("feedEmu3D" in html, "页面应把输出帧喂给 3D 模型")
    vend = os.path.join(HERE, "vendor", "osr-emu.dist.min.js")
    if os.path.exists(vend):
        check(os.path.getsize(vend) > 1_000_000, "3D 库体积应正常",
              os.path.getsize(vend))

    def hue_of(state, var="--accent"):
        m = re.search(r'body\[data-state="%s"\]\s*\{(.*?)\}' % state,
                      html, re.S)
        if not m:
            return None
        m2 = re.search(r"%s:\s*#([0-9a-fA-F]{6})" % var, m.group(1))
        if not m2:
            return None
        r, g, b = (int(m2.group(1)[i:i + 2], 16) / 255 for i in (0, 2, 4))
        mx, mn = max(r, g, b), min(r, g, b)
        d = mx - mn
        if d == 0:
            h = 0.0
        elif mx == r:
            h = (60 * ((g - b) / d)) % 360
        elif mx == g:
            h = 60 * ((b - r) / d) + 120
        else:
            h = 60 * ((r - g) / d) + 240
        sat = 0.0 if mx == 0 else d / mx
        return h, sat, mx

    emu = hue_of("emu")
    check(emu and 100 <= emu[0] <= 175 and emu[1] > 0.4,
          "模拟主题应偏绿", emu)
    real = hue_of("real")
    check(real and (real[0] <= 20 or real[0] >= 340) and real[1] > 0.5,
          "真机主题应偏红", real)
    est = hue_of("estop")
    check(est and 35 <= est[0] <= 68 and est[1] > 0.6,
          "急停主题应偏黄", est)
    est_bg = hue_of("estop", "--alert-bg")
    check(est_bg and 35 <= est_bg[0] <= 68 and est_bg[2] > 0.8,
          "急停 Alert 背景应是亮黄", est_bg)
    off = hue_of("offline")
    check(off and off[1] < 0.35, "离线主题应是低饱和灰", off)

    m = re.search(r"function stateName\(\)\s*\{(.*?)\n\}", html, re.S)
    check(bool(m), "页面应有 stateName()")
    if m:
        body = m.group(1)
        order = [body.find(k) for k in ('"estop"', '"offline"', '"real"')]
        check(all(i >= 0 for i in order), "stateName 应含四种状态", order)
        check(order[0] < order[1] < order[2],
              "急停 > 离线 > 真机 的判定顺序", order)

    check('id="stLink"' in html, "状态卡应有「连接」一项")
    check('"chipLink"' in html, "模拟视图 HUD 应有链路芯片（JS 动态建）")
    check("function linkView()" in html, "应有 linkView() 负责链路文案")
    check("serial_ready" in html,
          "缺 pyserial 时要能提示，而不是只显示「未连接」")
    check("watchLink" in html, "切换后应盯一会儿链路并把结果写进日志")



def test_link():
    print("[12] 链路信息（连接方式 + 有没有连上）")
    import daemon as D
    from osr_emu import EmuDevice

    D.link_pending(True)
    s = D.link_snapshot()
    check(s["transport"] == "serial", "切真机后 transport 应为 serial", s)
    check(s["connected"] is False, "还没连上时 connected 必须是 False", s)
    check(s["hint"] == "USB 串口 · 未连接", "未连接要说清是哪种方式", s["hint"])
    check(s["baud"] == 115200, "串口应带波特率", s["baud"])
    check(isinstance(s.get("serial_ready"), bool), "serial_ready 必须是布尔", s)

    class FakeDev:
        transport = "serial"
        endpoint = "COM5"

    D.link_on(FakeDev())
    s = D.link_snapshot()
    check(s["connected"] is True and s["endpoint"] == "COM5", "连上后应记端口", s)
    check(s["hint"] == "USB 串口 · COM5 @115200", "提示应含端口和波特率",
          s["hint"])
    check("COM5" in s["hint"], "端口名必须在提示里", s["hint"])

    D.link_on(EmuDevice(verbose=False))
    s = D.link_snapshot()
    check(s["transport"] == "emu" and s["connected"], "模拟器链路应连上", s)
    check(s["hint"] == "本地模拟器", "模拟器提示", s["hint"])
    check(s["ports"] == [], "模拟模式不必枚举串口", s["ports"])

    D.link_pending(True)
    D.link_on(FakeDev())
    D.link_off("设备已断开")
    s = D.link_snapshot()
    check(s["connected"] is False, "断开后 connected 应为 False", s)
    check(s["transport"] == "serial", "断开后仍要记得上次是串口", s)
    check("设备已断开" in s["error"], "断开原因要带上", s["error"])

    D.link_pending(True)
    D.link_off("没找到 OSR 设备。依次检查：\n"
               "  0) [环境] 没装 pyserial，串口连不了\n"
               "  1) 设备已开机、电量充足")
    s = D.link_snapshot()
    check("[环境]" in s["error"],
          "多行错误应挑出「0) [环境] …」那行", s["error"])
    check("依次检查" not in s["error"], "别把空话当原因", s["error"])

    ports = D.probe_ports(0)
    check(isinstance(ports, list), "probe_ports 应返回列表", type(ports))
    for p in ports[:3]:
        check(set(p) == {"device", "desc", "likely"}, "每个端口字段要齐", p)
        break

    s = D.link_snapshot()
    check("ports" in s, "未连接时应带 ports 字段", s)

    D.link_on(EmuDevice(verbose=False))



def test_owner():
    print("[13] 控制权：agent 优先，急停最高")
    import daemon as D

    reset_ctx()

    r = D.apply_command({"axes": {"L0": 70}, "src": "ui"})
    check(r.get("ok") is True, "没接管时界面应能控制", r)

    D.claim_owner("agent")
    check(D.CTX["owner"] == "agent", "agent 指令应拿到控制权")
    check(D.owner_state()["owner_left_s"] > 0, "接管应有倒计时",
          D.owner_state())

    r = D.apply_command({"axes": {"L0": 20}, "src": "ui"})
    check(r.get("ok") is False, "接管期间界面指令必须被拒", r)
    check("agent" in (r.get("error") or ""),
          "拒绝原因要说清是 agent 接管", r)

    r = D.apply_command({"axes": {"L0": 25}})
    check(r.get("ok") is True, "agent 指令不该被自己的锁挡住", r)

    r = D.apply_command({"home": True, "src": "ui"})
    check(r.get("ok") is True, "接管期间「回中位」应允许", r)

    D.CTX["owner_until"] = 0.0
    r = D.apply_command({"axes": {"L0": 30}, "src": "ui"})
    check(r.get("ok") is True, "接管超时后界面应恢复控制", r)
    check(D.CTX["owner"] == "", "超时后 owner 应清空", D.CTX["owner"])

    D.claim_owner("agent")
    asyncio.run(D.engage_estop(None, why="测试"))
    check(D.CTX["owner"] == "", "急停必须收回控制权", D.CTX["owner"])
    check(D.CTX["estop"] is True, "急停应锁定", D.CTX["estop"])

    r = D.apply_command({"axes": {"L0": 40}})
    check(r.get("ok") is False, "急停锁定期间 agent 也应被拒", r)

    D.release_estop("测试")
    r = D.apply_command({"axes": {"L0": 40}, "src": "ui"})
    check(r.get("ok") is True, "解除急停后界面应能控制", r)

    D.claim_owner("ui")
    check(D.CTX["owner"] == "", "界面指令不该拿到控制权", D.CTX["owner"])



def test_log():
    print("[14] 日志：轮转与降噪")
    import shutil
    import tempfile

    import daemon as D

    d = tempfile.mkdtemp()
    old = D.LOG_FILE
    D.LOG_FILE = os.path.join(d, "daemon.log")
    try:
        D.log("第一行")
        with open(D.LOG_FILE, encoding="utf-8") as f:
            check("第一行" in f.read(), "log 应写进文件")

        with open(D.LOG_FILE, "a", encoding="utf-8") as f:
            f.write("x" * (D.LOG_MAX + 10))
        D.log("轮转之后")
        check(os.path.exists(D.LOG_FILE + ".1"), "超过 1MB 应轮转出 .1")
        check(os.path.getsize(D.LOG_FILE) < 100_000,
              "轮转后新文件应是小的", os.path.getsize(D.LOG_FILE))
        with open(D.LOG_FILE, encoding="utf-8") as f:
            check("轮转之后" in f.read(), "新内容应写进新文件")

        with open(D.LOG_FILE, "a", encoding="utf-8") as f:
            f.write("y" * (D.LOG_MAX + 10))
        D.log("第二次轮转")
        check(not os.path.exists(D.LOG_FILE + ".2"),
              "只保留 .1 一份，不无限堆积")
    finally:
        D.LOG_FILE = old
        shutil.rmtree(d, ignore_errors=True)

    src = open(os.path.join(HERE, "daemon.py"), encoding="utf-8").read()
    check('log(f"client connected' not in src,
          "daemon 不该再记 client connected")
    check('log(f"client closed' not in src, "daemon 不该再记 client closed")



def test_download():
    print("[15] 3D 库下载：进度条与断点续传")
    import http.server
    import shutil
    import socketserver
    import tempfile
    import threading

    import fetch_vendor as F

    data = bytes(range(256)) * 8192

    class H(http.server.BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("ETag", '"v1"')
            self.end_headers()

        def do_GET(self):
            rng = self.headers.get("Range")
            if rng:
                start = int(rng.split("=")[1].split("-")[0])
                body = data[start:]
                self.send_response(206)
                self.send_header("Content-Range",
                                 f"bytes {start}-{len(data) - 1}/{len(data)}")
            else:
                body = data
                self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("ETag", '"v1"')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    d = tempfile.mkdtemp()
    old = (F.URL, F.DEST, F.PART, F.PART_ETAG, F.LICENSE_DEST, F.fetch_license)
    F.URL = f"http://127.0.0.1:{port}/osr-emu.dist.min.js"
    F.DEST = os.path.join(d, "osr-emu.dist.min.js")
    F.PART = F.DEST + ".part"
    F.PART_ETAG = F.DEST + ".part.etag"
    F.LICENSE_DEST = os.path.join(d, "LICENSE.txt")
    F.fetch_license = lambda **k: True
    try:
        bar = F._bar(4 * 1048576, 8 * 1048576, 0)
        check("50.0%" in bar, "进度条应显示百分比", bar)
        check(F._bar(1024, 0, 0).count("?") > 0,
              "总大小未知时应降级显示", F._bar(1024, 0, 0))

        half = len(data) // 4
        with open(F.PART, "wb") as f:
            f.write(data[:half])
        with open(F.PART_ETAG, "w", encoding="utf-8") as f:
            f.write("v1")
        rc = F.fetch(False)
        check(rc == 0, "续传应成功", rc)
        got = open(F.DEST, "rb").read()
        check(len(got) == len(data), "续传后大小应完整",
              f"{len(got)} vs {len(data)}")
        check(got == data, "续传拼出来的内容必须和原文件一致")
        check(not os.path.exists(F.PART), "下完应清掉 .part")

        os.remove(F.DEST)
        with open(F.PART, "wb") as f:
            f.write(b"\x00" * 100000)
        with open(F.PART_ETAG, "w", encoding="utf-8") as f:
            f.write("v-OLD")
        rc = F.fetch(False)
        check(rc == 0, "版本变化后应重下成功", rc)
        check(open(F.DEST, "rb").read() == data,
              "版本变了必须整份重下，不能拼接")

        os.remove(F.DEST)
        with open(F.PART, "wb") as f:
            f.write(b"\x00" * 1000)
        rc = F.fetch(True)
        check(rc == 0 and open(F.DEST, "rb").read() == data,
              "--force 应无视半成品重下", rc)
    finally:
        srv.shutdown()
        srv.server_close()
        (F.URL, F.DEST, F.PART, F.PART_ETAG,
         F.LICENSE_DEST, F.fetch_license) = old
        shutil.rmtree(d, ignore_errors=True)



def main():
    print("=" * 60)
    print("OSR-Control 离线自测（不需要设备）")
    print("=" * 60)

    test_encode()
    test_presets()
    test_commands()
    test_interp()
    test_switch()
    asyncio.run(test_emu())
    asyncio.run(test_daemon())
    test_client()
    test_token()
    asyncio.run(test_mode_recover())
    test_webui()
    test_link()
    test_owner()
    test_log()
    test_download()

    print("-" * 60)
    total = PASSED + len(FAIL)
    if FAIL:
        print(f"结果：{PASSED}/{total} 通过，{len(FAIL)} 项失败")
        for m in FAIL:
            print(f"  - {m}")
        return 1
    print(f"结果：全部通过（{PASSED} 项检查）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
