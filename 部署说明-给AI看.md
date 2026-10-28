# 部署说明（给 AI / agent 看）

本文件是给「要驱动 OSR6 的本地 agent」看的操作手册。人看的版本在
`使用说明-给主人看.txt`。

---

## 0. 一句话架构

```
agent ──TCP 5199(JSON)──> daemon.py ──TCode──> OSR6 真机 / osr-emu 模拟器
                              ^
用户 ──TCP 5198(令牌)──> estop.py  （急停按钮，agent 碰不到）

浏览器 ──HTTP 5197──> webui.py ──转发──> 5199 / 5198(带令牌)
```

agent 只和 5199 打交道。**5198 是用户专属通道，agent 不要连、也解不开。**
5197 是给人看的界面，agent 不需要、也不应该绕它。

---

## 1. 环境

```powershell
python --version                 # 3.10+
python -m pip install -r requirements.txt   # pyserial + bleak
python selftest.py               # 286 项离线自检，全绿再往下
python livetest.py               # 可选：webui 在跑时做端到端冒烟
```

依赖：`pyserial`（USB 串口）、`bleak`（蓝牙 BLE）。
走 USB 时 `bleak` 用不上，但装上无害。

一键装：`powershell -ExecutionPolicy Bypass -File install.ps1`
（依赖 + tkinter 检查 + 3D 库下载 + 自测；开关 `-Clean` / `-Force` / `-SkipVerify`）

---

## 2. 真机 / 模拟器开关

**默认是模拟器，而且不记忆。** 上次退出时是真机，下次启动还是模拟器。

这不是缺陷，是刻意的：这个开关决定指令会不会真的驱动硬件，能记住就会出现
"昨天调试切了真机，今天一开机设备自己动了"。

```powershell
python config.py --show     # 看当前模式（唯一的只读命令）
```

`config.py --real` / `--emu` **已废弃**，调用只打印提示、不改任何东西。
磁盘上的 `config.json` 一律被无视（文件可以删）。

**三种切法，都只对当次有效：**

| 方式 | 生效范围 |
|---|---|
| `python daemon.py --real --port COM5` | 这一条命令 |
| 环境变量 `OSR_REAL=1`（或 `0`） | 这个进程 |
| WebUI 点「切到真机」 | 这次运行（`webui.py` 内存里的 `WANT`，不落盘） |

优先级：`环境变量` > `命令行` > 默认（模拟器）。

**判断当前在哪种模式**：`ctl.py --status` 的 `"real"` 字段。
`false` = 模拟器，`true` = 真机。

---

## 3. 启动顺序

```powershell
# ① 服务（常驻，一个就够）
python daemon.py                 # 模拟器
python daemon.py --real --port COM5   # 真机，明确串口

# ② 急停按钮（建议每次都开，用户自己按）
python estop.py                  # 置顶窗口 + 全局热键 F12

# ③ 确认
python ctl.py --status
# {"ok":true,"device":"osr6","real":true,"estop":false,"mode":"idle",...}
```

三个关键字段：`real`（真机/模拟）、`estop`（是否锁定）、`mode`
（`idle`/`axes`/`fade`/`seq`/`preset`）。

串口被占用（`Access denied`）时，先关掉 DKFunPlayer 和
`play_script.py`——它们会自己抓串口。

---

## 4. 图形界面（WebUI，用户侧）

用户双击 `启动.bat` 就有界面了。**agent 不需要碰这一层**，但要知道它的存在，
否则会困惑于「为什么 5199 上已经有人在跑」。

```
浏览器 ──HTTP 5197──> webui.py ──TCP 5199──> daemon.py
                            └──TCP 5198(令牌)──> 急停 / 切模式
```

| 端口 | 谁在用 | agent 要不要连 |
|---|---|---|
| 5197 | WebUI（浏览器） | **不要** |
| 5199 | daemon 控制通道 | 要，只连这个 |
| 5198 | 急停 / 切模式（令牌） | **绝对不要** |

**关键点：**

1. `webui.py` 会把 daemon 作为**子进程**拉起来（`CREATE_NO_WINDOW`）。
   如果你先起了 `webui.py`，5199 就已经被占了 —— 不要再起一个 `daemon.py`，
   第二个会绑端口失败。反过来也一样：5199 已有人听时，`webui.py` 会直接接管，
   不会起第二个（`spawn_daemon()` 先 `port_open()` 探测）。
2. 页面里**没有硬编码** 5199/5198，全部走 `/api/*`。浏览器永远拿不到
   急停令牌（`.estop_token`），这是有意的：网页跑在浏览器里，同机任何脚本
   都能读它，不能让它持有解除急停的能力。
3. **切换真机/模拟是双向的，且不落盘**：`/api/mode` 是「改内存意图 + 通知
   运行中的服务立刻重连」，不用重启。`config.py --real` 已废弃，不再改任何配置。
   进程退出后模式回到默认（模拟器）—— 下次要真机，再显式说一遍。
4. 切换瞬间服务会**先断开设备再重连**，这期间 5199 仍在监听（`run()` 里
   server 先起、设备后连），`{"status":true}` 照常应答，只是 `last_error`
   可能短暂出现「没找到 OSR 设备」。agent 遇到这种状态**不要判定服务死了**，
   看 `mode` 还是不是正常值、过 2 秒再查一次。

`webui.py` 只用标准库 `http.server`，没有额外依赖。
它只额外开放了一个静态目录 `/vendor/*`（给 3D 模型库用），做了路径规范化 +
前缀校验，`/vendor/../.estop_token` 这类请求会被 400 挡掉（自测里有这条）。
项目根目录**不**对外，因为那里有令牌文件。

---

## 5. 协议（TCP 127.0.0.1:5199）

一行一个 JSON，一行一个应答。**同一连接可一直发，不要每条重连。**

**不需要带任何来源标记**：不带 `src` 就自动算 `agent`。只有 `webui.py`
转发时会注入 `src:"ui"`，用来区分"人点的"和"你发的"（见第 7 节控制权）。
别自己伪造 `src:"ui"` 去绕过接管检查 —— 那是给人用的。

### 发动作

| 指令 | 作用 |
|---|---|
| `{"axes":{"L0":70,"L2":35}}` | 立刻到位（0-100，50=中位） |
| `{"axes":{"L0":70},"ms":800}` | 800ms 线性过渡（**优先用这个**） |
| `{"seq":[[0,{"L0":30}],[600,{"L0":80}],[1200,{"L0":50}]]}` | 播一段关键帧，本地插值 |
| `{"seq":[...],"loop":true}` | 循环播 |
| `{"preset":"orbit"}` | 跑预设（持续） |
| `{"preset":"tide","intensity":1.3}` | 切预设 + 强度（0.1-2.0） |
| `{"intensity":0.8}` | 只改当前预设强度 |
| `{"speed":1.5}` | 整体速度（0.2-3.0，影响预设与 seq） |
| `{"auto":true}` | 每 25-45 秒随机换预设 |
| `{"home":true}` | 回中位（默认 600ms 过渡） |

### 控制

| 指令 | 作用 |
|---|---|
| `{"stop":true}` | 归零停止，**服务继续运行**，之后还能控制 |
| `{"status":true}` | 状态快照 |
| `{"quit":true}` | 归零 + 断开 + 退出服务 |

### 应答

```json
{"ok":true,"device":"osr6","real":true,"estop":false,
 "mode":"axes","preset":null,"auto":false,
 "intensity":1.0,"speed":1.0,"axes":{"L0":70}}
```

失败：

```json
{"ok":false,"error":"unknown preset: xxx","available":["sway",...]}
```

轴名设备不支持时进 `"ignored":["XX"]`，不会静默丢弃。

---

## 6. 急停：agent 必须遵守的规则

**看到 `"estop": true` 就停手，不要重试。**

锁定期间所有控制指令都会被拒：

```json
{"ok":false,"estop":true,
 "error":"E-STOP 已按下并锁定：所有控制被拒绝。请在急停窗口按一下按钮解除（控制通道上没有解除命令）。"}
```

正确做法：

1. 停止发送动作指令（重试没有意义，也不会生效）
2. 告诉用户「急停已按下，请在急停窗口按一下按钮解除」
3. 定期 `{"status":true}` 轮询，看到 `estop:false` 再继续
4. **不要**尝试 `{"estop":false}` —— 控制通道没有这个命令，会被拒

解除后设备保持静止，不会自动恢复上一个动作，需要重新下发。

`{"stop":true}` 在锁定期间仍然可用（它只是停）。

---

## 7. 控制权：agent 优先，急停最高

**这是 5199 上最容易踩的坑，先读完再发指令。**

用户在网页上拖滑杆时，可能正好和你抢同一台设备。规则是：

```
急停（最高）  >  agent 接管中  >  用户界面操作  >  空闲
```

- 你**每发一条非 `status` / 非 `quit` 的指令**，就自动「接管」控制权，
  并把租约续到 60 秒后。
- 接管期间，来自界面的控制指令（`src:"ui"`）会被拒绝，但 **`home`（回中）
  和 `stop` 永远放行** —— 用户必须能随时叫停。
- 你**超过 60 秒没发指令**，租约过期，控制权自动交还，界面恢复可操作。
  日志会写一行「agent 接管超时，控制权已自动交还」。
- 用户点界面上的「收回控制」，或**按下急停**，都会立刻清空你的租约。
  之后你再发指令会重新接管（除非急停还锁着）。

被拒绝时界面收到的应答长这样（你收到的是 `ok:true`，不会看到这个）：

```json
{"ok":false,"owner":"agent","owner_left_s":52,
 "error":"agent 正在接管控制，界面操作已暂停（急停仍然有效）"}
```

`{"status":true}` 里带 `owner`（`""` / `"agent"`）和 `owner_left_s`：

```json
{"ok":true,"owner":"agent","owner_left_s":59,...}
```

**给你的实践建议：**

1. 长动作别只发一条就撒手。`{"preset":"tide"}` 之后 60 秒内再发点什么
   （哪怕 `{"status":true}` 也算，但只有控制指令续期 —— 想续租约就发
   `{"intensity":1.0}` 这类空转指令），否则用户会在中途把控制权收回去。
2. 交接前先 `{"home":true}`，别把设备停在半路。
3. 看到 `owner` 变成 `""` 而你没发过 `home`，说明用户收回了 —— 别硬抢，
   等用户明确再要。
4. **不要试图连 5198** 去释放控制权，那是用户专属通道。

---

## 8. 实时控制的正确姿势

**不要试图每秒推 50 条 JSON。** LLM 做不到，也没必要。

| 想要的 | 该发的 |
|---|---|
| 平滑移动到一个位置 | `{"axes":{...},"ms":600}` |
| 做一个小动作（往返、抽动） | `{"seq":[[0,{...}],[400,{...}],[800,{...}]]}` |
| 连续跑一段时间 | `{"preset":"tide"}` |
| 逐帧流式（脚本/外部数据源） | `ctl.py --stdin`，一行一条 |

本地服务保证 50Hz 下发（预设 10Hz），时序误差实测 < 2ms。
Windows 上 daemon 会自动 `timeBeginPeriod(1)` 把定时精度从 15.6ms 提到 1ms，
不这么做实际只有 33Hz（`selftest.py` 会打印实测值，正常应显示 50）。

轴的含义：

| 轴 | 含义 | 0 | 0.5 | 1 |
|---|---|---|---|---|
| `L0` | 行程上下 | 底 | 中位 | 顶 |
| `L1` | 前后 | 后 | 中位 | 前 |
| `L2` | 左右 | 左 | 中位 | 右 |
| `R0` | 旋转 | 逆 | 中位 | 顺 |
| `R1` | 滚转 | — | 中位 | — |
| `R2` | 俯仰 | — | 中位 | — |
| `V0` | 振动 | 关 | — | 最强 |

方向以实机为准。第一次接真机先小幅度试：`{"axes":{"L0":56},"ms":300}`。

---

## 9. 故障排查

| 现象 | 原因 / 处理 |
|---|---|
| `连不上控制服务 127.0.0.1:5199` | `daemon.py` 没起，或已退出 |
| `D1 = 'ets Jul 29 2019...'` | ESP 控制器被 DTR 复位，还在吐启动横幅；`identify()` 已重试 3 次，仍失败就重新插拔 |
| `应答不是 TCode` | 串口连错了设备（Arduino 等）。用 `--port COM5` 明确指定 |
| 设备收到指令但不动 | 检查 `D2` 轴列表是否含 `L0`；`ctl.py --status` 看 `axes` 是否为目标值 |
| `打不开串口 Access denied` | DKFunPlayer / `play_script.py` 占着串口，关掉 |
| 动作一顿一顿 | 不要绕过 `daemon.py` 自己连设备；服务端已做去重与 50Hz 平滑 |
| 蓝牙扫不到 | 电脑蓝牙没开；手机 App 还连着设备（同时只认一个主） |
| 服务在跑、`device` 却是 `null`，发指令毫无反应 | 之前切到真机但没插设备，又切回模拟器，重连循环卡住了。**已修**（每轮重新读意图）；旧版本重启服务即可。判据：`{"status":true}` 里 `device` 为 `null` 且 `last_error` 是"没找到 OSR 设备" |
| 5199 `Address already in use` | 已经有一个 daemon 在跑（可能被 `webui.py` 拉起的）。先 `ctl.py --status` 探一下，别再起第二个 |
| 浏览器打开 5197 是空白/报错 | `webui.html` 没和 `webui.py` 放一起，或被杀软隔离；看 `webui-daemon.log` |
| 界面拖滑杆没反应，提示「agent 正在接管控制」 | 你的租约还没过期。要么等 `owner_left_s` 秒，要么在界面上点「收回控制」。想主动放手就 `{"home":true}` 然后停手，60 秒后自动交还 |
| `owner` 突然从 `agent` 变成 `""` | 租约过期（60 秒没发控制指令）、用户点了收回、或急停被按下。前两种情况下次发指令会自动重新接管 |
| 切真机后一直连不上，界面弹出「选择串口」 | 自动探测超时了。用户在弹窗里挑一个，或者 `POST /api/port {"port":"COM9"}`。列表里标「像目标设备」的最可能是对的 |
| `daemon.log` 越写越大 | 不会了。超过 1MB 自动滚成 `.1`，只留一份；连接/断开日志已取消（原来占 92%）。要手动清：界面「清理日志」或 `POST /api/log {"action":"clear"}` |

真机相关的三个前提：设备开机、手机 App 断开、DKFunPlayer 没占用串口。

---

## 10. 文件职责

| 文件 | 谁用 |
|---|---|
| `osr.py` | 协议层：TCode 编码、串口/UDP/BLE、D0/D1/D2 |
| `osrdev.py` | 门面：身份校验 + `open_device()` 开关分发 |
| `osr_emu.py` | 模拟器：进程内 `EmuDevice` + 独立 UDP TCode 服务 |
| `config.py` | 模式判定（`--show`；不落盘，`--real/--emu` 已废弃） |
| `presets6.py` | 10 个六轴预设 |
| `daemon.py` | 常驻服务（5199 控制 + 5198 急停） |
| `ctl.py` | 客户端（单条 / `--stdin` / 库） |
| `estop.py` | 急停按钮（用户专用） |
| `webui.py` / `webui.html` | 统一界面后端 / 页面（用户专用，agent 不碰） |
| `启动.bat` | 双击入口：拉起 `webui.py` 并开浏览器 |
| `play_script.py` | funscript 播放（自己占串口，和 daemon 二选一） |
| `selftest.py` | 离线自检（286 项，不需要设备） |
| `livetest.py` | 端到端冒烟（需要 `webui.py` 在跑） |
| `motiontest.py` | 设备运动测试：延迟 / 过渡 / 关键帧 / 预设 / 强度 / 停止 |
| `camtest.js` | 3D 层自测：相机数学 / 渲染 / TCode 换算（需要 node） |
| `vendor/osr-emu.dist.min.js` | 界面端 3D 模型库（上游 osr-emu，MIT）。和 `osr_emu.py`（设备端模拟器）是两回事 |
| `fetch_vendor.py` | 下载上面的库：进度条 + 断点续传 + ETag 校验（缺失时才需要） |
| `install.ps1` | 一键装环境（依赖 / tkinter / 3D 库 / 自测），`-Clean` `-Force` `-SkipVerify` |
