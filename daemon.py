from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import secrets
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from presets6 import PRESET_INFO6, Runner6
from osrdev import AXES6, CENTER, DeviceNotFound, add_mode_args, open_device
from osr_emu import EmuDevice
from osr import (BT_HINTS, DEFAULT_BAUD, SPP_HINTS,
                 USB_HINTS, list_serial_ports)
import osr as osrmod

HOST = "127.0.0.1"
PORT = 5199
LOG_FILE = os.path.join(HERE, "daemon.log")

ESTOP_PORT = 5198
ESTOP_TOKEN_FILE = os.path.join(HERE, ".estop_token")
ESTOP_TOKEN = ""

TICK = 0.02
PRESET_TICK = 0.10
IDLE_TIMEOUT = 300.0
LOG_MAX = 1_000_000
OWNER_TTL = 60.0

CONNECT_TIMEOUT = 8.0

PRESETS = list(PRESET_INFO6)

CTX = {
    "mode": "idle",
    "auto": False,
    "axes": None,
    "fade": None,
    "seq": None,
    "runner": None,
    "preset": None,
    "intensity": 1.0,
    "speed": 1.0,
    "device": None,
    "real": False,
    "estop": False,
    "estop_since": 0.0,
    "out": {},
    "want_real": None,
    "reconnect": False,
    "switching": False,
    "last_error": "",
    "owner": "",
    "owner_until": 0.0,
    "want_port": None,
    "t0": 0.0,
    "next_switch": 0.0,
}

DEV = {"dev": None}

LINK = {
    "transport": None,
    "endpoint": None,
    "baud": None,
    "connected": False,
    "since": 0.0,
    "error": "",
    "ports": [],
    "ports_at": 0.0,
}

TRANSPORT_NAME = {
    "serial": "USB 串口",
    "udp": "网络 UDP",
    "ble": "蓝牙 BLE",
    "emu": "本地模拟",
}

PORTS_TTL = 3.0


def probe_ports(ttl=PORTS_TTL) -> list:
    now = time.monotonic()
    if now - LINK["ports_at"] < ttl:
        return LINK["ports"]

    out = []
    try:
        for p in list_serial_ports():
            blob = " ".join(str(x) for x in (
                p.device, p.name, p.description, p.hwid,
                getattr(p, "manufacturer", None), getattr(p, "product", None),
            ) if x).upper()
            likely = any(h.upper() in blob
                         for h in SPP_HINTS + BT_HINTS + USB_HINTS)
            out.append({
                "device": p.device,
                "desc": (p.description or p.name or "")[:60],
                "likely": likely,
            })
    except Exception as e:
        log(f"枚举串口失败：{type(e).__name__}: {e}")
        out = []

    out.sort(key=lambda x: (not x["likely"], x["device"]))
    LINK["ports"] = out[:8]
    LINK["ports_at"] = now
    return LINK["ports"]


def link_on(dev):
    if isinstance(dev, EmuDevice):
        LINK.update(transport="emu", endpoint="local", baud=None)
    else:
        LINK.update(
            transport=getattr(dev, "transport", None) or "serial",
            endpoint=getattr(dev, "endpoint", None),
            baud=DEFAULT_BAUD if getattr(dev, "transport", None) == "serial" else None,
        )
    LINK["connected"] = True
    LINK["since"] = time.monotonic()
    LINK["error"] = ""


def link_off(error="", transport=None):
    LINK["connected"] = False
    LINK["since"] = 0.0
    if transport:
        LINK["transport"] = transport
        LINK["baud"] = DEFAULT_BAUD if transport == "serial" else None
        LINK["endpoint"] = None
    if error:
        lines = [x.strip() for x in error.split("\n") if x.strip()]
        key = next((x for x in lines if x.startswith("0)")),
                   lines[0] if lines else "")
        LINK["error"] = key[:200]


def link_pending(real: bool):
    if real:
        LINK.update(transport="serial", endpoint=None, baud=DEFAULT_BAUD)
    else:
        LINK.update(transport="emu", endpoint="local", baud=None)
    LINK["connected"] = False
    LINK["since"] = 0.0
    LINK["error"] = ""


def link_snapshot() -> dict:
    t = LINK["transport"]
    name = TRANSPORT_NAME.get(t, "未连接")
    ep = LINK["endpoint"]
    on = bool(LINK["connected"])

    if t == "emu":
        hint = "本地模拟器"
    elif on and ep:
        hint = f"{name} · {ep}"
        if LINK["baud"]:
            hint += f" @{LINK['baud']}"
    elif t:
        hint = f"{name} · 未连接"
    else:
        hint = "未连接"

    s = {
        "transport": t,
        "transport_name": name,
        "endpoint": ep,
        "baud": LINK["baud"],
        "connected": on,
        "uptime_s": (int(time.monotonic() - LINK["since"])
                     if on and LINK["since"] else 0),
        "hint": hint,
        "error": LINK["error"],
        "ports": [],
        "serial_ready": osrmod.serial is not None,
    }
    if t != "emu" and not on:
        s["ports"] = list(LINK["ports"])
    return s


async def ports_watcher():
    while not QUIT.is_set():
        if LINK["transport"] not in (None, "emu") and not LINK["connected"]:
            try:
                await asyncio.to_thread(probe_ports, 0)
            except Exception as e:
                log(f"枚举串口失败：{type(e).__name__}: {e}")
            await asyncio.sleep(2.0)
        else:
            await asyncio.sleep(1.0)


SUPPORTED = set(AXES6)
QUIT = asyncio.Event()



def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    _rotate_log()
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _rotate_log():
    try:
        if os.path.getsize(LOG_FILE) < LOG_MAX:
            return
    except OSError:
        return
    try:
        bak = LOG_FILE + ".1"
        if os.path.exists(bak):
            os.remove(bak)
        os.replace(LOG_FILE, bak)
    except OSError:
        pass


def _num(v, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    if v != v:
        return default
    return v


def _clamp01(v):
    return max(0.0, min(1.0, v))


def _parse_axes(obj, supported=None):
    out, ignored = {}, []
    if not isinstance(obj, dict):
        return out, ignored
    for k, v in obj.items():
        k = str(k).upper().strip()
        v = _num(v, None)
        if v is None:
            ignored.append(k)
            continue
        if supported and k not in supported:
            ignored.append(k)
            continue
        out[k] = max(0.0, min(100.0, v))
    return out, ignored



def _owner_alive() -> bool:
    return CTX["owner"] == "agent" and time.monotonic() <= CTX["owner_until"]


def owner_state() -> dict:
    if CTX["owner"] == "agent" and time.monotonic() > CTX["owner_until"]:
        CTX["owner"] = ""
        CTX["owner_until"] = 0.0
        log("agent 接管超时，控制权已自动交还")
    return {
        "owner": CTX["owner"],
        "owner_left_s": (max(0, int(CTX["owner_until"] - time.monotonic()))
                         if _owner_alive() else 0),
    }


def claim_owner(src: str):
    if src != "agent":
        return
    if not _owner_alive():
        log(f"agent 接管控制（{int(OWNER_TTL)} 秒无指令自动交还）")
    CTX["owner"] = "agent"
    CTX["owner_until"] = time.monotonic() + OWNER_TTL


def release_owner(why="按钮"):
    if CTX["owner"]:
        log(f"控制权交还（{why}）")
    CTX["owner"] = ""
    CTX["owner_until"] = 0.0


def snapshot() -> dict:
    now = time.monotonic()
    s = {
        "device": CTX["device"],
        "real": CTX["real"],
        "estop": CTX["estop"],
        "mode": CTX["mode"],
        "preset": CTX["preset"],
        "auto": CTX["auto"],
        "intensity": round(CTX["intensity"], 2),
        "speed": round(CTX["speed"], 2),
        "axes": None,
        "out": ({k: int(round(v * 100)) for k, v in CTX["out"].items()}
                if CTX["out"] else None),
        "last_error": CTX["last_error"],
        "link": link_snapshot(),
        **owner_state(),
    }
    if CTX["estop"]:
        s["estop_s"] = int(now - CTX["estop_since"]) if CTX["estop_since"] else 0
    if CTX["mode"] in ("axes", "fade") and CTX["axes"]:
        s["axes"] = {k: int(round(v)) for k, v in CTX["axes"].items()}
    if CTX["mode"] == "fade" and CTX["fade"]:
        left = CTX["fade"]["ms"] - (now - CTX["fade"]["t0"]) * 1000.0
        s["fade_ms"] = max(0, int(left))
    if CTX["mode"] == "seq" and CTX["seq"]:
        q = CTX["seq"]
        elapsed = (now - q["t0"]) * 1000.0 * CTX["speed"]
        s["seq"] = {"dur_ms": int(q["dur"]), "loop": q["loop"],
                    "pos_ms": int(min(elapsed, q["dur"]))}
    return s


def ok(**extra) -> dict:
    return {"ok": True, **snapshot(), **extra}


def err(msg, **extra) -> dict:
    return {"ok": False, "error": msg, **extra}



def _axes01() -> dict:
    if not CTX["axes"]:
        return {}
    return {k: _clamp01(v / 100.0) for k, v in CTX["axes"].items()}


def _seq_axes(now) -> dict:
    q = CTX["seq"]
    keys = q["keys"]
    dur = q["dur"]
    elapsed = (now - q["t0"]) * 1000.0 * CTX["speed"]

    if elapsed >= dur:
        if q["loop"] and dur > 0:
            q["t0"] += (dur / 1000.0) / max(1e-6, CTX["speed"])
            elapsed = (now - q["t0"]) * 1000.0 * CTX["speed"]
        else:
            last = dict(keys[-1][1])
            CTX["mode"] = "axes"
            CTX["axes"] = {k: v * 100.0 for k, v in last.items()}
            CTX["seq"] = None
            return last

    if elapsed <= keys[0][0]:
        return dict(keys[0][1])
    for i in range(1, len(keys)):
        t1, a1 = keys[i]
        if elapsed <= t1:
            t0k, a0 = keys[i - 1]
            span = t1 - t0k
            k = 1.0 if span <= 0 else (elapsed - t0k) / span
            names = set(a0) | set(a1)
            return {ax: a0.get(ax, CENTER)
                    + (a1.get(ax, CENTER) - a0.get(ax, CENTER)) * k
                    for ax in names}
    return dict(keys[-1][1])


def _current_axes(now) -> dict:
    mode = CTX["mode"]

    if mode == "fade":
        f = CTX["fade"]
        if not f:
            CTX["mode"] = "axes"
            return _axes01()
        k = 1.0 if f["ms"] <= 0 else (now - f["t0"]) / (f["ms"] / 1000.0)
        if k >= 1.0:
            CTX["mode"] = "axes"
            CTX["fade"] = None
            return dict(f["to"])
        return {ax: f["from"].get(ax, CENTER)
                + (v - f["from"].get(ax, CENTER)) * k
                for ax, v in f["to"].items()}

    if mode == "seq" and CTX["seq"]:
        return _seq_axes(now)

    if mode == "axes":
        return _axes01()

    return {}



def start_preset(name, intensity=None) -> bool:
    if name not in PRESET_INFO6:
        return False
    if intensity is not None:
        CTX["intensity"] = max(0.1, min(2.0, float(intensity)))
    CTX["runner"] = Runner6(name, intensity=CTX["intensity"])
    CTX["preset"] = name
    CTX["mode"] = "preset"
    CTX["auto"] = False
    CTX["axes"] = None
    CTX["fade"] = None
    CTX["seq"] = None
    CTX["t0"] = time.monotonic()
    CTX["next_switch"] = time.monotonic() + random.uniform(25, 45)
    log(f"preset -> {name} (intensity {CTX['intensity']:.2f})")
    return True


def _cmd_axes(cmd) -> dict:
    axes, ignored = _parse_axes(cmd.get("axes"), SUPPORTED)
    if not axes:
        return err("axes 里没有设备支持的合法轴", ignored=ignored)

    ms = _num(cmd.get("ms"), 0.0)
    now = time.monotonic()
    to = {k: _clamp01(v / 100.0) for k, v in axes.items()}

    if ms > 0:
        cur = _current_axes(now) or {}
        CTX["fade"] = {"t0": now, "ms": ms,
                       "from": {a: cur.get(a, CENTER) for a in to},
                       "to": to}
        CTX["mode"] = "fade"
    else:
        CTX["mode"] = "axes"
        CTX["fade"] = None

    CTX["axes"] = dict(axes)
    CTX["seq"] = None
    CTX["runner"] = None
    CTX["preset"] = None
    CTX["auto"] = False

    reply = ok()
    if ignored:
        reply["ignored"] = ignored
    return reply


def _cmd_seq(cmd) -> dict:
    raw = cmd.get("seq")
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return err('seq 至少 2 个关键帧，如 '
                   '[[0,{"L0":30}],[600,{"L0":80}],[1200,{"L0":50}]]')

    keys = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return err(f"关键帧格式错误：{item!r}（应为 [毫秒, {{轴:值}}]）")
        t, ax = item
        t = _num(t, None)
        if t is None or t < 0:
            return err(f"关键帧时间不合法：{t!r}")
        axes, ignored = _parse_axes(ax, SUPPORTED)
        if not axes:
            return err(f"关键帧 {t:.0f}ms 没有合法轴值", ignored=ignored)
        keys.append((float(t), {k: _clamp01(v / 100.0)
                                for k, v in axes.items()}))

    keys.sort(key=lambda x: x[0])
    if keys[0][0] > 0:
        keys.insert(0, (0.0, dict(keys[0][1])))
    dur = keys[-1][0]
    if dur <= 0:
        return err("seq 总时长为 0，没有动作可播")

    CTX["mode"] = "seq"
    CTX["seq"] = {"keys": keys, "loop": bool(cmd.get("loop")),
                  "t0": time.monotonic(), "dur": dur}
    CTX["fade"] = None
    CTX["axes"] = None
    CTX["runner"] = None
    CTX["preset"] = None
    CTX["auto"] = False
    return ok()


def _cmd_preset(cmd) -> dict:
    name = str(cmd["preset"]).strip()
    if not start_preset(name, cmd.get("intensity")):
        return err(f"unknown preset: {name}",
                   available=PRESETS)
    return ok()


def _cmd_auto() -> dict:
    name = random.choice(PRESETS)
    CTX["auto"] = True
    start_preset(name)
    CTX["auto"] = True
    log(f"auto -> {name}")
    return ok()


def _cmd_home(cmd) -> dict:
    axes = {a: 50.0 for a in AXES6 if a in SUPPORTED}
    if "V0" in SUPPORTED:
        axes["V0"] = 0.0
    if not axes:
        return err("设备没有可用的位置轴")
    return _cmd_axes({"axes": axes, "ms": _num(cmd.get("ms"), 600.0)})


def apply_command(cmd: dict) -> dict:
    if not isinstance(cmd, dict):
        return err("顶层必须是 JSON 对象")

    if cmd.get("status"):
        return ok()

    if CTX["estop"]:
        return err(
            "E-STOP 已按下并锁定：所有控制被拒绝。"
            "请在急停窗口按一下按钮解除（控制通道上没有解除命令）。",
            estop=True,
        )

    src = str(cmd.get("src") or "agent")
    if src == "ui" and _owner_alive() and not cmd.get("home"):
        return err("agent 正在接管控制，界面操作已暂停（急停仍然有效）",
                   owner="agent",
                   owner_left_s=owner_state()["owner_left_s"])

    if "axes" in cmd:
        return _cmd_axes(cmd)

    if "seq" in cmd:
        return _cmd_seq(cmd)

    if "preset" in cmd:
        return _cmd_preset(cmd)

    if cmd.get("auto"):
        return _cmd_auto()

    if cmd.get("home"):
        return _cmd_home(cmd)

    if "intensity" in cmd:
        v = _num(cmd.get("intensity"), None)
        if v is None:
            return err("intensity 要是数字（0.1-2.0）")
        CTX["intensity"] = max(0.1, min(2.0, v))
        if CTX["runner"]:
            CTX["runner"].intensity = CTX["intensity"]
        return ok()

    if "speed" in cmd:
        v = _num(cmd.get("speed"), None)
        if v is None:
            return err("speed 要是数字（0.2-3.0）")
        CTX["speed"] = max(0.2, min(3.0, v))
        return ok()

    if any(k in cmd for k in ("vib", "thrust", "rot_raw")):
        return err('本分支只有六轴，没有三通道。用 axes，例如 '
                   '{"axes":{"L0":60,"V0":30}}（振动是 V0）')

    return err("看不懂的指令。支持：axes / seq / preset / auto / "
               "intensity / speed / home / stop / status / quit")



async def control_loop(dev):
    next_preset = 0.0
    deadline = time.perf_counter()
    while True:
        now = time.monotonic()
        try:
            if CTX["estop"]:
                pass
            elif CTX["mode"] == "preset" and CTX["runner"]:
                if CTX["auto"] and now >= CTX["next_switch"]:
                    start_preset(random.choice(PRESETS))
                    CTX["auto"] = True
                if now >= next_preset:
                    next_preset = now + PRESET_TICK
                    tt = (now - CTX["t0"]) * CTX["speed"]
                    vals = CTX["runner"].step(tt)
                    CTX["out"] = vals
                    if not CTX["estop"]:
                        await dev.send6(vals, t=now)
            else:
                vals = _current_axes(now)
                if vals:
                    CTX["out"] = vals
                    if not CTX["estop"]:
                        await dev.send6(vals, t=now)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log(f"control_loop 出错：{type(e).__name__}: {e}")

        deadline += TICK
        delay = deadline - time.perf_counter()
        if delay < 0:
            deadline = time.perf_counter()
            delay = 0.0
        await asyncio.sleep(delay)


async def do_stop(dev):
    _clear_motion()
    if dev is not None:
        try:
            await dev.stop()
        except Exception as e:
            log(f"stop 失败：{type(e).__name__}: {e}")
    log("STOP -> 已归零（服务继续运行）")



def _home_out() -> dict:
    return {a: (CENTER if a[0] in "LR" else 0.0) for a in AXES6}


def _clear_motion():
    CTX["mode"] = "idle"
    CTX["auto"] = False
    CTX["runner"] = None
    CTX["preset"] = None
    CTX["axes"] = None
    CTX["fade"] = None
    CTX["seq"] = None
    CTX["out"] = _home_out()


async def engage_estop(dev, why="button"):
    if CTX["estop"]:
        return
    CTX["estop"] = True
    CTX["estop_since"] = time.monotonic()
    _clear_motion()
    release_owner("急停")
    if dev is not None:
        try:
            await dev.stop()
        except Exception as e:
            log(f"急停归零失败：{type(e).__name__}: {e}")
    log(f"E-STOP 按下（{why}）-> 已归零并锁定，所有控制被拒绝")


def release_estop(why="button"):
    if not CTX["estop"]:
        return
    CTX["estop"] = False
    CTX["estop_since"] = 0.0
    _clear_motion()
    log(f"E-STOP 解除（{why}）-> 保持静止，等待新的指令")


def estop_snapshot() -> dict:
    return {
        "ok": True,
        "estop": CTX["estop"],
        "estop_s": (int(time.monotonic() - CTX["estop_since"])
                    if CTX["estop"] and CTX["estop_since"] else 0),
        "mode": CTX["mode"],
        "device": CTX["device"],
        "real": CTX["real"],
        "link": link_snapshot(),
        **owner_state(),
    }



def _reply(writer, obj):
    writer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))


async def handle(reader, writer, holder):
    peer = writer.get_extra_info("peername")
    try:
        while True:
            try:
                raw = await asyncio.wait_for(reader.readline(), IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                log(f"client idle timeout {peer}")
                break
            if not raw:
                break

            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue

            try:
                cmd = json.loads(line)
                if not isinstance(cmd, dict):
                    raise ValueError("顶层必须是 JSON 对象")
            except Exception as e:
                _reply(writer, err(f"bad json: {e}"))
                await writer.drain()
                continue

            dev = holder.get("dev")

            src = str(cmd.get("src") or "agent")
            if not (cmd.get("status") or cmd.get("quit")):
                claim_owner(src)

            if cmd.get("quit"):
                _reply(writer, {"ok": True, "bye": True})
                await writer.drain()
                await do_stop(dev)
                if dev is not None:
                    await dev.close()
                log("QUIT -> 归零、断开、退出")
                QUIT.set()
                return

            if cmd.get("stop"):
                await do_stop(dev)
                reply = {"ok": True, "stopped": True, **snapshot()}
            else:
                try:
                    reply = apply_command(cmd)
                except Exception as e:
                    reply = err(f"{type(e).__name__}: {e}")

            _reply(writer, reply)
            await writer.drain()
    except (ConnectionResetError, asyncio.IncompleteReadError):
        pass
    except Exception as e:
        log(f"handle error {type(e).__name__}: {e}")
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def estop_handle(reader, writer, holder):
    dev = holder.get("dev")
    try:
        raw = await asyncio.wait_for(reader.readline(), 5.0)
        obj = json.loads(raw.decode("utf-8", "replace") or "{}")
        if not isinstance(obj, dict):
            raise ValueError("顶层必须是对象")
    except Exception as e:
        _reply(writer, {"ok": False, "error": f"bad request: {e}"})
        try:
            await writer.drain()
            writer.close()
        except Exception:
            pass
        return

    if not ESTOP_TOKEN or obj.get("token") != ESTOP_TOKEN:
        _reply(writer, {"ok": False,
                        "error": "token 不对；令牌在 daemon 目录的 .estop_token"})
        try:
            await writer.drain()
            writer.close()
        except Exception:
            pass
        log("急停通道收到无效令牌，已拒绝")
        return

    try:
        if "estop" in obj:
            if bool(obj["estop"]):
                await engage_estop(dev, why="按钮")
            else:
                release_estop("按钮")
            reply = estop_snapshot()
        elif obj.get("release"):
            release_owner("按钮")
            reply = estop_snapshot()
        elif "port" in obj:
            port = str(obj.get("port") or "").strip().upper() or None
            CTX["want_port"] = port
            if port:
                CTX["want_real"] = True
                CTX["real"] = True
            CTX["last_error"] = ""
            _clear_motion()
            link_pending(True)
            CTX["reconnect"] = True
            CTX["switching"] = True
            log(f"手动指定串口 -> {port or '（回到自动探测）'}")
            reply = {"ok": True, "port": port, "switching": True,
                     "link": link_snapshot()}
        elif "real" in obj:
            want = bool(obj["real"])
            CTX["want_real"] = want
            CTX["real"] = want
            CTX["last_error"] = ""
            _clear_motion()
            link_pending(want)
            CTX["reconnect"] = True
            CTX["switching"] = True
            log(f"切换设备模式 -> {'REAL 真机' if want else 'EMU 模拟器'}")
            reply = {"ok": True, "real": want, "switching": True,
                     "estop": CTX["estop"], "link": link_snapshot()}
        elif obj.get("status"):
            reply = estop_snapshot()
        else:
            reply = {"ok": False,
                     "error": "只接受 {estop:true/false} / {real:true/false} / "
                              "{status:true}"}
    except Exception as e:
        reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    _reply(writer, reply)
    try:
        await writer.drain()
        writer.close()
    except Exception:
        pass


async def run(dev_args):
    global SUPPORTED

    args = {k: v for k, v in dev_args.items() if k != "preset"}
    if CTX["want_real"] is not None:
        args["real"] = CTX["want_real"]

    server = await asyncio.start_server(
        lambda r, w: handle(r, w, DEV), HOST, PORT)
    estop_srv = await asyncio.start_server(
        lambda r, w: estop_handle(r, w, DEV), HOST, ESTOP_PORT)
    log(f"listening {HOST}:{PORT}")
    log(f"急停/模式通道 {HOST}:{ESTOP_PORT}"
        f"（令牌 {os.path.basename(ESTOP_TOKEN_FILE)}）")

    loop_task = None
    ports_task = asyncio.create_task(ports_watcher())
    try:
        while not QUIT.is_set():
            if CTX["want_real"] is not None:
                args["real"] = CTX["want_real"]
            if CTX["want_port"]:
                args["port"] = CTX["want_port"]
            try:
                dev = await asyncio.wait_for(open_device(**args),
                                             timeout=CONNECT_TIMEOUT)
            except asyncio.TimeoutError:
                CTX["last_error"] = (
                    f"连接超时（{CONNECT_TIMEOUT:.0f} 秒）：没找到串口，"
                    "UDP / BLE 探测也没有应答")
                link_off(CTX["last_error"],
                         transport="serial" if args.get("real") else None)
                log(f"连接超时（{CONNECT_TIMEOUT:.0f}s），继续重试")
                await asyncio.sleep(1.0)
                continue
            except DeviceNotFound as e:
                CTX["last_error"] = str(e)
                link_off(str(e), transport="serial" if args.get("real") else None)
                log(f"没找到设备：{e}")
                await asyncio.sleep(1.5)
                continue
            except Exception as e:
                CTX["last_error"] = f"{type(e).__name__}: {e}"
                link_off(CTX["last_error"],
                         transport="serial" if args.get("real") else None)
                log(f"连接失败：{CTX['last_error']}")
                await asyncio.sleep(1.5)
                continue

            DEV["dev"] = dev
            CTX["device"] = dev.name
            CTX["real"] = not isinstance(dev, EmuDevice)
            CTX["last_error"] = ""
            link_on(dev)
            SUPPORTED = set(getattr(dev, "supported", AXES6) or AXES6)
            log(f"connected {dev.describe()}")
            log(f"mode: {'REAL 真机' if CTX['real'] else 'EMU 模拟器（未接硬件）'}")
            log(f"supported axes: {' '.join(sorted(SUPPORTED))}")

            if dev_args.get("preset") and CTX["mode"] == "idle":
                start_preset(dev_args["preset"])

            loop_task = asyncio.create_task(control_loop(dev))
            why = "lost"
            try:
                while not QUIT.is_set():
                    await asyncio.sleep(0.3)
                    if CTX["reconnect"]:
                        CTX["reconnect"] = False
                        why = "switch"
                        log("按请求重连设备（切换模式）")
                        break
                    if not dev.alive:
                        log("设备断开了，准备重连")
                        break
            finally:
                if loop_task:
                    loop_task.cancel()
                    try:
                        await loop_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    loop_task = None
                DEV["dev"] = None
                CTX["device"] = None
                if why == "switch":
                    link_pending(bool(CTX["want_real"]))
                else:
                    link_off("设备已断开")
                try:
                    await dev.close()
                except Exception:
                    pass

            await asyncio.sleep(0.2)
    finally:
        ports_task.cancel()
        for srv in (server, estop_srv):
            try:
                srv.close()
            except Exception:
                pass
        DEV["dev"] = None
        CTX["device"] = None
        log("服务已关闭")


def _load_or_make_token() -> str:
    try:
        with open(ESTOP_TOKEN_FILE, "r", encoding="utf-8") as f:
            tok = f.read().strip()
        if len(tok) >= 16:
            return tok
    except OSError:
        pass

    tok = secrets.token_hex(16)
    try:
        with open(ESTOP_TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(tok)
        if os.name != "nt":
            os.chmod(ESTOP_TOKEN_FILE, 0o600)
    except Exception as e:
        log(f"写令牌文件失败：{type(e).__name__}: {e}")
    return tok


def _raise_timer_resolution():
    if os.name != "nt":
        return None
    try:
        import ctypes
        winmm = ctypes.WinDLL("winmm")
        if winmm.timeBeginPeriod(1) == 0:
            return winmm
    except Exception as e:
        log(f"提高定时精度失败（功能不受影响，只是帧率偏低）：{e}")
    return None


async def main(dev_args):
    global ESTOP_TOKEN
    ESTOP_TOKEN = _load_or_make_token()

    timer = _raise_timer_resolution()
    try:
        await run(dev_args)
    except Exception as e:
        CTX["last_error"] = f"{type(e).__name__}: {e}"
        log(f"服务异常退出：{CTX['last_error']}")
    finally:
        if timer is not None:
            try:
                timer.timeEndPeriod(1)
            except Exception:
                pass


def parse_args():
    global PORT, ESTOP_PORT
    p = argparse.ArgumentParser(description="OSR 常驻控制服务（TCP 5199）")
    p.add_argument("--port", help="串口，如 COM5")
    p.add_argument("--udp", help="UDP，如 tcode.local:8000")
    p.add_argument("--ble", nargs="?", const=True, default=None,
                   help="走 BLE（可跟地址）")
    p.add_argument("--preset", help="启动即开跑某个预设")
    p.add_argument("--listen-port", type=int, default=PORT,
                   help=f"TCP 监听端口（默认 {PORT}）")
    p.add_argument("--estop-port", type=int, default=ESTOP_PORT,
                   help=f"急停按钮端口（默认 {ESTOP_PORT}）")
    add_mode_args(p)
    a = p.parse_args()

    PORT = a.listen_port
    ESTOP_PORT = a.estop_port
    return dict(real=a.real, port=a.port, udp=a.udp, ble=a.ble,
                preset=a.preset)


if __name__ == "__main__":
    try:
        asyncio.run(main(parse_args()))
    except KeyboardInterrupt:
        print("\n已停止")
