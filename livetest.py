import json
import time
import urllib.request

BASE = "http://127.0.0.1:5197"

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path):
    with _OPENER.open(BASE + path, timeout=5) as r:
        return json.loads(r.read().decode())


def post(path, obj):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(obj).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with _OPENER.open(req, timeout=8) as r:
        return json.loads(r.read().decode())


def state_name(s):
    if s.get("estop"):
        return "estop"
    if not s.get("daemon_up"):
        return "offline"
    return "real" if s.get("real") else "emu"


def show(tag):
    s = get("/api/state")
    print(f"  {tag:8} -> {state_name(s):8} "
          f"daemon_up={s['daemon_up']} real={s['real']} estop={s['estop']} "
          f"mode={s['mode']}")
    return s


print("=== 1. 初始（应为 emu）===")
s = show("start")
assert state_name(s) == "emu", s

print("=== 2. 按下急停（应为 estop，且 Alert 转黄）===")
r = post("/api/estop", {"on": True})
print("  press:", r)
s = show("pressed")
assert state_name(s) == "estop", s

print("=== 3. 急停期间发动作（必须被拒）===")
r = post("/api/cmd", {"axes": {"L0": 80}})
print("  cmd during estop:", r.get("ok"), r.get("error", "")[:40])
assert r.get("ok") is False and r.get("estop") is True, r

print("=== 4. 解除急停（应回 emu）===")
r = post("/api/estop", {"on": False})
print("  release:", r)
s = show("released")
assert state_name(s) == "emu", s

print("=== 5. 切真机（设备没插，应保持 real + 报未就绪）===")
r = post("/api/mode", {"real": True})
print("  switch:", r)
for i in range(9):
    time.sleep(0.5)
    s = get("/api/state")
    if i in (0, 4, 8):
        print(f"    +{(i + 1) * 0.5:.1f}s state={state_name(s):8} "
              f"up={s['daemon_up']} err={s['last_error'][:30]}")
assert s["real"] is True, s
assert state_name(s) == "real", s

print("=== 6. 切回模拟（应回 emu）===")
r = post("/api/mode", {"real": False})
print("  switch back:", r)
for _ in range(10):
    time.sleep(0.4)
    s = get("/api/state")
    if not s["real"] and s["daemon_up"]:
        break
for _ in range(30):
    time.sleep(0.2)
    s = get("/api/state")
    if s["daemon_up"] and not s["real"] and s.get("device"):
        break
s = show("final")
assert state_name(s) == "emu", s
assert s.get("device"), f"切回模拟器后应有设备，实际 device={s.get('device')}"
assert not s.get("last_error"), f"切回后不应残留错误：{s.get('last_error')}"

print("=== 7. 页面本身 ===")
with _OPENER.open(BASE + "/", timeout=5) as r:
    html = r.read().decode()
print(f"  HTTP {r.status}, {len(html)} 字节, "
      f"含 stateName={('stateName' in html)}, 含抽屉={('collapsed' in html)}")

print("=== 8. 停服务（应变 offline 灰）===")
r = post("/api/daemon", {"action": "stop"})
print("  stop:", r)
for _ in range(12):
    time.sleep(0.4)
    s = get("/api/state")
    if not s["daemon_up"]:
        break
s = show("stopped")
assert state_name(s) == "offline", s
assert s["last_error"] == "控制服务未启动", s

print("=== 9. 界面上的「启动控制服务」（应变回 emu）===")
r = post("/api/daemon", {"action": "start"})
print("  start:", r)
for _ in range(20):
    time.sleep(0.4)
    s = get("/api/state")
    if s["daemon_up"]:
        break
s = show("restarted")
assert state_name(s) == "emu", s

print("\n全部通过：四态判定、急停拦截、真机切换、离线兜底、重新拉起都符合预期。")
