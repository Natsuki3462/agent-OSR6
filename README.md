# OSR-Control

**English** · [中文说明](#中文说明)

A local control stack for **OSR6 / SR6** six-axis haptic hardware, built around
one idea: an agent (or a script) should send *intent*, and the machine should
handle *timing*.

The daemon holds the device, accepts one-line JSON over TCP, and interpolates
locally at 50 Hz. Your program never has to keep up with the motor loop.

```
agent ──TCP 5199 (JSON)──▶ daemon ──TCode v0.3──▶ OSR6 hardware
                             ▲                     or built-in emulator
user  ──TCP 5198 (token)──▶ estop
browser ──HTTP 5197──▶ webui ──▶ 5199 / 5198
```

No hardware? Everything still runs. The default mode is the built-in emulator,
and it never persists — every start is a safe start.

---

## Features

| | |
|---|---|
| **Six-axis + vibration** | `L0 L1 L2 R0 R1 R2 V0`, all `0–100`, `50` = center |
| **TCode v0.3** | native protocol, with `D0/D1/D2` device identification |
| **Three transports** | USB serial, UDP (`tcode.local:8000`), Bluetooth LE |
| **Local interpolation** | daemon drives 50 Hz, presets 10 Hz, timing error < 2 ms |
| **Built-in emulator** | fake device that answers `D0/D1/D2`, so the whole stack runs offline |
| **10 presets** | `sway orbit roll pitch figure8 storm tide coil pulse weave` |
| **Keyframe sequences** | `{"seq":[[0,{...}],[600,{...}]]}`, interpolated locally |
| **Browser UI** | sliders, presets, live 3D model, four-color status bar |
| **E-stop** | separate token-protected channel, always wins |
| **Ownership arbitration** | agent takes over while driving; the UI is paused, not the E-stop |
| **Test suite** | 285 offline assertions (286 with the 3D library) + 118 for the 3D layer |

---

## Requirements

- **Python 3.10+**
- `pyserial` — USB serial (required for real hardware)
- `bleak` — Bluetooth LE (optional, only for the BLE transport)
- **Node.js** — only for `camtest.js`
- `install.ps1` and `启动.bat` are Windows-only; the Python core is portable

---

## Install

```powershell
git clone <this-repo> OSR-Control
cd OSR-Control
powershell -ExecutionPolicy Bypass -File install.ps1
```

`install.ps1` installs dependencies, checks `tkinter` (the E-stop window needs
it), downloads the 3D model library, and runs the test suite.

| Flag | Effect |
|---|---|
| `-Clean` | clean logs and caches only, do not install |
| `-Force` | re-download the 3D library even if present |
| `-SkipVerify` | skip the test run at the end |

Manual equivalent:

```powershell
python -m pip install -r requirements.txt
python fetch_vendor.py
python selftest.py
```

**The 3D library is not in this repo.** It is ~8 MB and fetched by
`fetch_vendor.py` (progress bar, resume support, ETag validation). Without it the
UI falls back to a hand-drawn isometric view — nothing breaks.

---

## Run

```powershell
启动.bat                # Windows: starts webui.py and opens the browser
python webui.py         # any platform
```

Then open <http://127.0.0.1:5197/>.

Command line:

```powershell
python daemon.py                       # control service, emulator mode
python daemon.py --real --port COM5    # real hardware, explicit port
python estop.py                        # always-on-top E-stop button, hotkey F12
python ctl.py --preset orbit           # start a preset
python ctl.py --set "L0=75" --ms 800   # smooth move
python ctl.py --status                 # snapshot
python ctl.py --stop                   # stop, keep the service alive
python ctl.py --quit                   # shut down
```

Do not run `启动.bat` and `python daemon.py` at the same time — they compete for
port 5199. If a daemon is already listening, `webui.py` attaches to it instead of
starting a second one.

---

## Ports

| Port | Who | Notes |
|---|---|---|
| **5197** | Web UI | the browser only ever talks to this |
| **5199** | control channel | one-line JSON, what agents use |
| 5198 | E-stop / mode switch | **token required**, generated on first run |

The browser never receives the E-stop token. Anything running on the same
machine can read a file, so the page is not allowed to hold the key that
releases the E-stop.

---

## Protocol (TCP 127.0.0.1:5199)

One JSON object per line, one reply per line. Keep the connection open.

### Motion

| Command | Effect |
|---|---|
| `{"axes":{"L0":70,"L2":35}}` | jump to position |
| `{"axes":{"L0":70},"ms":800}` | linear move over 800 ms — **prefer this** |
| `{"seq":[[0,{"L0":30}],[600,{"L0":80}]]}` | keyframe sequence, local interpolation |
| `{"seq":[...],"loop":true}` | loop it |
| `{"preset":"orbit"}` | run a preset |
| `{"preset":"tide","intensity":1.3}` | preset + intensity (`0.1–2.0`) |
| `{"intensity":0.8}` | change intensity of the current preset |
| `{"speed":1.5}` | global speed (`0.2–3.0`) |
| `{"auto":true}` | rotate presets every 25–45 s |
| `{"home":true}` | return to center |

### Control

| Command | Effect |
|---|---|
| `{"stop":true}` | zero and stop, service keeps running |
| `{"status":true}` | snapshot |
| `{"quit":true}` | zero, disconnect, exit |

### Reply

```json
{"ok":true,"device":"osr6","real":true,"estop":false,
 "mode":"axes","preset":null,"auto":false,
 "intensity":1.0,"speed":1.0,"axes":{"L0":70},
 "owner":"agent","owner_left_s":59}
```

Failure carries a reason:

```json
{"ok":false,"error":"unknown preset: xxx","available":["sway","orbit"]}
```

Unsupported axis names land in `"ignored":["XX"]` instead of being dropped
silently.

---

## Ownership: agent first, E-stop above everything

```
E-STOP  >  agent holding control  >  UI input  >  idle
```

- Every control command from the agent takes the lease and renews it to **60 s**.
- While held, UI input (`src:"ui"`) is rejected — except `stop` and `home`,
  which are always allowed because they move toward safety.
- After 60 s of silence the lease expires and the UI is usable again.
- The **E-stop releases the lease unconditionally**, and so does the user's
  "release control" button (token channel only).

Rejection looks like this to the UI:

```json
{"ok":false,"owner":"agent","owner_left_s":52,
 "error":"agent is in control, UI input paused (E-stop still works)"}
```

Practical advice: do not fire one long preset and walk away. Renew with a cheap
control command before the lease runs out, and `{"home":true}` before handing
back.

---

## E-stop

`estop.py` is a press-and-hold button: press once to lock, press again to
release.

```powershell
python estop.py            # always-on-top window, global hotkey F12
python estop.py --press    # lock from the command line
python estop.py --release  # unlock
python estop.py --status   # read state
```

While locked, **every** control command is refused, from any source, and the
device returns to center. The control channel has no unlock command by design —
only the token channel does.

Agents: when you see `"estop": true`, stop sending motion. Poll `status` and wait
for `false`. Retrying is pointless and will not work.

---

## Web UI

Left: sliders, presets, quick actions, intensity/speed, E-stop, mode switch.
Right: live 3D model of the device that follows the axes.

| Bar color | Meaning |
|---|---|
| Green | emulator — values move, hardware untouched |
| Red | real hardware — motion is physical |
| Yellow | E-stop locked — everything refused |
| Grey | service offline, with the reason |

Priority is **E-stop > offline > real/emulator**. A locked E-stop stays yellow
even when the service is down; "the yellow went away" is not the same as "it is
safe to move".

The 3D view freezes and dims in real mode. That is deliberate: a picture that
moves while the device does not is worse than a picture that stops.

The log panel has **open** and **clear** buttons. Logs rotate at 1 MB, one
backup kept. Connection and disconnection events are not logged — they used to
be 92% of the file and buried everything useful.

---

## Project layout

| File | Role |
|---|---|
| `osr.py` | TCode v0.3 driver: encoding, serial/UDP/BLE, `D0/D1/D2` |
| `osrdev.py` | device facade: identity check, transport dispatch |
| `osr_emu.py` | emulator: in-process device + standalone UDP TCode server |
| `daemon.py` | resident service: 5199 control, 5198 E-stop, 50 Hz loop |
| `ctl.py` | client: single command, `--stdin` streaming, or library |
| `estop.py` | E-stop button (user side) |
| `webui.py` / `webui.html` | UI backend / page |
| `presets6.py` | the 10 six-axis presets |
| `presets.py` | preset runner, one-shot |
| `play_script.py` | funscript player (opens the port itself) |
| `config.py` | mode resolution, read-only, never persists |
| `fetch_vendor.py` | downloads the 3D library |
| `install.ps1` | one-shot environment setup |
| `启动.bat` | Windows launcher |
| `selftest.py` | offline test suite |
| `livetest.py` | end-to-end smoke test against a running UI |
| `motiontest.py` | motion tests: latency, transitions, keyframes, presets |
| `camtest.js` | 3D layer tests (camera math, render, TCode conversion) |
| `gesture.json` | sample keyframe sequence |

---

## Tests

```powershell
python selftest.py     # 285 assertions, no device, does not touch a running service
node camtest.js        # 118 assertions for the 3D layer (115 without the library)
python livetest.py     # needs webui.py running: four states, E-stop, mode switch
python motiontest.py   # drives the device like an agent would, measures latency
```

`selftest.py` covers TCode encoding, the 10 presets, command parsing,
interpolation, mode precedence, the emulator, the TCP protocol, the 50 Hz loop,
the E-stop chain, token stability, ownership arbitration, log rotation, and the
downloader (it spins up a local HTTP server and really resumes a transfer).

---

## Third-party

The 3D view uses [osr-emu](https://github.com/ayvasoftware/osr-emu) (MIT,
three.js + an SR6 model). It is downloaded at install time, not vendored.
`osr_emu.py` in this repo is unrelated — that one is the *device* emulator,
written in Python.

---

## Known limitations

1. **Single machine, no authentication.** The service binds `127.0.0.1` only.
   Any local process can control the device. Fine for personal use, not for a
   shared host.
2. **Ports are fixed.** 5197 / 5199 / 5198 are not configurable, and there is no
   fallback if something else holds them.
3. **`play_script.py` bypasses the daemon** and opens the serial port itself, so
   the two cannot run at the same time.
4. **ESP-based controllers may reset on port open** (DTR). Identification retries,
   but the first commands after power-up can be lost.
5. **`estop.py` needs `tkinter`.** Bundled with the official Python installer,
   absent from some embedded builds. `install.ps1` checks for it.
6. **Link state is what the process sees, not an electrical guarantee.**
   `connected: true` means the port opened and `D1` answered `TCode` — not that
   the motors are powered or the travel is unobstructed.
7. **The serial port name can be hardcoded.** Default is `COM5`; override with
   `--port` or the UI dialog. The override lives in memory only.

---

# 中文说明

一个本地控制栈，专门服务 **OSR6 / SR6** 六轴设备。核心设计只有一句话：
**agent 只表达意图，机器负责节奏。**

常驻服务握着设备，通过 TCP 收单行 JSON，然后在本地按 50 Hz 插值下发。
调用方不需要跟得上电机循环。

## 它解决什么问题

让大模型直接控制硬件，最大的障碍不是"发不出指令"，而是"发不够快"——
六轴要顺滑，下发频率得 30–50 Hz，但 LLM 每秒吐不出 50 条 JSON。

所以节奏放在本地：agent 说"800 毫秒内到 L0=70"，`daemon.py` 负责把它拆成
50 段平滑下发。时序误差实测 < 2 ms（Windows 上会调 `timeBeginPeriod(1)`
把定时精度从 15.6 ms 提到 1 ms，否则实际只有 33 Hz）。

## 特性

| | |
|---|---|
| 六轴 + 振动 | `L0 L1 L2 R0 R1 R2 V0`，取值 `0–100`，`50` 为中位 |
| TCode v0.3 | 原生协议，支持 `D0/D1/D2` 设备识别 |
| 三种连接 | USB 串口、UDP（`tcode.local:8000`）、蓝牙 BLE |
| 本地插值 | 服务 50 Hz、预设 10 Hz |
| 内置模拟器 | 假装成一台 OSR6，不插设备也能跑通全流程 |
| 10 个预设 | `sway orbit roll pitch figure8 storm tide coil pulse weave` |
| 关键帧序列 | `{"seq":[[0,{...}],[600,{...}]]}`，本地插值 |
| 网页界面 | 滑杆、预设、实时 3D 模型、四色状态条 |
| 急停 | 独立令牌通道，优先级最高 |
| 控制权仲裁 | agent 接管期间界面暂停，但急停不受影响 |

## 环境要求

- **Python 3.10+**
- `pyserial`（USB 串口，接真机必需）
- `bleak`（蓝牙，可选）
- **Node.js**（只有 `camtest.js` 需要）
- `install.ps1` 和 `启动.bat` 仅 Windows；Python 部分是跨平台的

## 安装

```powershell
git clone <本仓库> OSR-Control
cd OSR-Control
powershell -ExecutionPolicy Bypass -File install.ps1
```

它会装依赖、检查 `tkinter`（急停窗口要用）、下载 3D 模型库、最后跑一遍自测。

| 开关 | 作用 |
|---|---|
| `-Clean` | 只清日志和缓存，不安装 |
| `-Force` | 强制重新下载 3D 库 |
| `-SkipVerify` | 跳过最后的自测 |

手动等价：

```powershell
python -m pip install -r requirements.txt
python fetch_vendor.py
python selftest.py
```

**3D 库不在仓库里**，8 MB，由 `fetch_vendor.py` 下载（带进度条、支持断点
续传、校验 ETag）。没有它界面会退回手绘等轴视图，功能不受影响。

## 运行

```powershell
启动.bat                # Windows：拉起 webui.py 并打开浏览器
python webui.py         # 任意平台
```

然后打开 <http://127.0.0.1:5197/>。

命令行：

```powershell
python daemon.py                       # 常驻服务，模拟器模式
python daemon.py --real --port COM5    # 真机，明确指定串口
python estop.py                        # 置顶急停窗口，热键 F12
python ctl.py --preset orbit           # 跑预设
python ctl.py --set "L0=75" --ms 800   # 平滑移动
python ctl.py --status                 # 看状态
python ctl.py --stop                   # 停住，服务继续运行
python ctl.py --quit                   # 退出服务
```

`启动.bat` 和 `python daemon.py` 不要同时用，它们抢 5199。5199 上已经有服务时，
`webui.py` 会直接接管，不会起第二个。

## 端口

| 端口 | 用途 | 说明 |
|---|---|---|
| **5197** | 网页界面 | 浏览器只连这个 |
| **5199** | 控制通道 | 单行 JSON，agent 用 |
| 5198 | 急停 / 切模式 | **需要令牌**，首次运行自动生成 |

浏览器永远拿不到急停令牌。同机任何脚本都能读文件，所以不能让网页持有
"解除急停"的钥匙。

## 控制协议（TCP 127.0.0.1:5199）

一行一个 JSON，一行一个应答，连接可以一直复用。

### 发动作

| 指令 | 作用 |
|---|---|
| `{"axes":{"L0":70,"L2":35}}` | 立刻到位 |
| `{"axes":{"L0":70},"ms":800}` | 800 ms 线性过渡 —— **优先用这个** |
| `{"seq":[[0,{"L0":30}],[600,{"L0":80}]]}` | 关键帧序列 |
| `{"seq":[...],"loop":true}` | 循环播放 |
| `{"preset":"orbit"}` | 跑预设 |
| `{"preset":"tide","intensity":1.3}` | 预设 + 强度（`0.1–2.0`） |
| `{"intensity":0.8}` | 只改当前预设强度 |
| `{"speed":1.5}` | 整体速度（`0.2–3.0`） |
| `{"auto":true}` | 每 25–45 秒随机换预设 |
| `{"home":true}` | 回中位 |

### 控制

| 指令 | 作用 |
|---|---|
| `{"stop":true}` | 归零停止，服务继续运行 |
| `{"status":true}` | 状态快照 |
| `{"quit":true}` | 归零、断开、退出 |

### 应答

```json
{"ok":true,"device":"osr6","real":true,"estop":false,
 "mode":"axes","preset":null,"auto":false,
 "intensity":1.0,"speed":1.0,"axes":{"L0":70},
 "owner":"agent","owner_left_s":59}
```

失败会带原因：

```json
{"ok":false,"error":"unknown preset: xxx","available":["sway","orbit"]}
```

设备不支持的轴名会进 `"ignored":["XX"]`，不会静默丢弃。

## 控制权：agent 优先，急停最高

```
急停  >  agent 接管中  >  界面操作  >  空闲
```

- agent 每发一条控制指令就接管，租约续到 **60 秒**后。
- 接管期间界面指令（`src:"ui"`）被拒，但 `stop` 和 `home` 永远放行 ——
  那是往安全方向走。
- 60 秒没指令，租约过期，界面恢复可用。
- **急停无条件收回控制权**；用户点"收回控制"（令牌通道）也一样。

界面收到的拒绝长这样：

```json
{"ok":false,"owner":"agent","owner_left_s":52,
 "error":"agent 正在接管控制，界面操作已暂停（急停仍然有效）"}
```

给 agent 的建议：别发一条长预设就撒手。租约到期前用一条轻量控制指令续期，
交接前先 `{"home":true}`。

## 急停

`estop.py` 是可按下保持的按钮：按一下锁定，再按一下解除。

```powershell
python estop.py            # 置顶窗口 + 全局热键 F12
python estop.py --press    # 命令行按下
python estop.py --release  # 命令行解除
python estop.py --status   # 只看状态
```

锁定期间**所有**控制指令被拒，设备回到中位。控制通道上没有解除命令 ——
只有令牌通道有，这是故意的。

看到 `"estop": true` 就停手，轮询 `status` 等它变 `false`，重试没有意义。

## 网页界面

左边控制台：滑杆、预设、快捷动作、强度/速度、急停、切模式。
右边：跟随轴值实时变形的 3D 模型。

| 状态条颜色 | 含义 |
|---|---|
| 绿 | 模拟器 —— 数值在动，不碰硬件 |
| 红 | 真机 —— 动作是物理的 |
| 黄 | 急停锁定 —— 全部拒绝 |
| 灰 | 服务离线，附带原因 |

优先级：**急停 > 离线 > 真机/模拟**。急停锁着时哪怕服务断了也是黄色，
"黄灯灭了"不等于"能动了"。

真机模式下 3D 视图会冻结变暗。这是故意的：画面在动而设备不动，比画面不动
更容易误判。

日志卡片有**打开**和**清理**两个按钮。日志超过 1 MB 自动轮转，只留一份备份。
连接/断开事件不再记录 —— 它们曾经占了 92%，把有用的信息全埋了。

## 目录结构

| 文件 | 作用 |
|---|---|
| `osr.py` | TCode v0.3 驱动：编码、串口/UDP/BLE、`D0/D1/D2` |
| `osrdev.py` | 设备门面：身份校验、连接方式分发 |
| `osr_emu.py` | 模拟器：进程内设备 + 独立 UDP TCode 服务 |
| `daemon.py` | 常驻服务：5199 控制、5198 急停、50 Hz 循环 |
| `ctl.py` | 客户端：单条指令、`--stdin` 流式、或当库用 |
| `estop.py` | 急停按钮 |
| `webui.py` / `webui.html` | 界面后端 / 页面 |
| `presets6.py` | 10 个六轴预设 |
| `presets.py` | 预设命令行 |
| `play_script.py` | funscript 播放器（自己占串口） |
| `config.py` | 模式判定，只读，不落盘 |
| `fetch_vendor.py` | 下载 3D 库 |
| `install.ps1` | 一键装环境 |
| `启动.bat` | Windows 启动入口 |
| `selftest.py` | 离线自测 |
| `livetest.py` | 端到端冒烟（需要界面在跑） |
| `motiontest.py` | 运动测试：延迟、过渡、关键帧、预设 |
| `camtest.js` | 3D 层自测（相机数学、渲染、TCode 换算） |
| `gesture.json` | 关键帧示例 |

## 测试

```powershell
python selftest.py     # 285 项，不需要设备，不碰正在跑的服务
node camtest.js        # 118 项，3D 层（没有 3D 库时跑 115 项）
python livetest.py     # 需要 webui.py 在跑：四态、急停、切模式
python motiontest.py   # 像 agent 一样真去驱动，量延迟
```

`selftest.py` 覆盖 TCode 编码、10 个预设、指令解析、插值、模式优先级、
模拟器、TCP 协议、50 Hz 循环、急停链路、令牌稳定性、控制权仲裁、日志轮转，
以及下载器（起一个本地 HTTP 服务真跑断点续传）。

## 第三方

3D 视图用 [osr-emu](https://github.com/ayvasoftware/osr-emu)（MIT，three.js +
SR6 模型），安装时下载，不放进仓库。仓库里的 `osr_emu.py` 和它无关 ——
那个是**设备端**模拟器，Python 写的。

## 已知限制

1. **单机、无鉴权。** 服务只绑 `127.0.0.1`，同机任何进程都能控制设备。
   个人使用没问题，别放在共享主机上。
2. **端口写死。** 5197 / 5199 / 5198 不可配置，被占用了不会自动换。
3. **`play_script.py` 绕过服务**，自己开串口，和 daemon 二选一。
4. **ESP 类控制器开串口可能复位**（DTR）。识别会重试，但刚上电的前几条指令
   仍可能丢。
5. **`estop.py` 依赖 `tkinter`。** 官方安装包自带，精简版可能没有，
   `install.ps1` 会检查。
6. **链路状态是进程视角，不是电气保证。** `connected: true` 只说明串口打开
   且 `D1` 回了 `TCode`，不代表电机带电、行程没卡住。
7. **串口名可以写死。** 默认 `COM5`，用 `--port` 或界面弹窗临时指定，
   指定值只在内存里。
