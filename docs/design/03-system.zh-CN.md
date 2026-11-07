# ANet Relay 系统设计（设计 v1.0，2026-11-06）

> 本文规定 ANet Relay 怎么实现：进程与模块、控制权状态机、安全设计、MAVLink 细节、模拟机、穿环判定、对外接口（REST / WebSocket / MCP / SDK）、数据、桌面外壳、打包与发布、测试与工作分解。需求见 01，界面见 02。安全条款与 02 冲突时以本文为准。

## 1. 总体架构

```
┌──────────────────────── Electron（ANet Relay.app）────────────────────────┐
│ main 进程：后端进程管理 · 操作员令牌 · 端口选择 · 菜单 · 权限 · 单实例       │
│   │ spawn                                         │ BrowserWindow          │
│   ▼                                               ▼                        │
│ Python 后端 relay（uvicorn 127.0.0.1:8765）◀─HTTP/WS(令牌)─ 渲染进程 web/   │
└───┼──────────────────────────────────────────────────────────────────────┘
    │ UDP 0.0.0.0:14550 ⇄ 各数传 <ip>:14555        ▲ HTTP/WS（会话令牌）
    │ HTTP <ip>:80（扫描、配网）                      │
    ▼                                               外部智能体 / harness / MCP 客户端
  真机（ESP8266 数传 + Pixracer）        进程内：模拟机 · 航线 · SQLite 记录
```

原则：

- **后端是唯一真相**：控制权、杆量下发、模拟、航线判定、记录全部在后端；渲染进程只是一个带操作员令牌的客户端。这保证人与智能体看到的是同一个世界，也保证关掉窗口不会留下"在渲染进程里飞着"的东西。
- **同一条路**：模拟机与真机实现同一个 `Driver` 协议；模拟机生成真实的 pymavlink 消息对象，经同一个解码器更新状态。
- **人工动作必须来自窗口**：操作员令牌由 Electron 生成、只交给后端与本窗口。

## 2. 仓库布局

```
ANet-Relay/
├── package.json · electron-builder.yml
├── electron/          main.mjs · backend.mjs · ports.mjs · menu.mjs · preload.cjs · paths.mjs · strings.mjs
├── backend/
│   ├── pyproject.toml
│   ├── relay/         §3
│   ├── tests/
│   └── relay-backend.spec      PyInstaller onedir
├── web/               Vite + React 19 + TS + Tailwind 4 + shadcn（02 全部界面）
├── sdk/python/        anet_relay_client.py · examples/
├── mcp/               anet_relay_mcp.py（stdio MCP 服务器，只依赖 SDK 与标准库）
├── tools/
│   ├── fakes/fake_vehicle.py   假数传 + 假飞控
│   └── icons/build_icons.py
├── brand/             icon-1024.png（源）· icon.icns · icon.ico · icon.png · anet-logo.svg
├── e2e/               Playwright（_electron）
├── docs/
│   ├── design/        01 · 02 · 03（本文）
│   ├── agent-api.md · agent-api.zh-CN.md
│   ├── hardware.zh-CN.md        平台、配网、首飞、排障
│   └── media/                    README 横幅与截图
├── .github/workflows/ ci.yml · release.yml
├── README.md · README.zh-CN.md · LICENSE · CHANGELOG.md
└── refs/              旧 demo、平台资料、原型（.gitignore）
```

## 3. 后端（Python 3.12，包 `relay`）

依赖钉版：fastapi 0.162.0、uvicorn 0.54.0、pymavlink 2.4.50、pydantic（随 fastapi）、websockets；开发：pytest、httpx、ruff。打包：pyinstaller 6.22.3。

```
relay/
├── __main__.py       入口：读环境变量 → create_app → uvicorn.run(app 对象)（PyInstaller 友好）
├── config.py         常量、门限、Settings（settings.json 覆盖）
├── errors.py         RelayError(code, status, message_en, message_zh, detail)
├── model.py          dataclass：Claim、Possession、StickInput、Capability、VehicleView
├── frame.py          杆量坐标系 → ArduPilot PWM / PX4 MANUAL_CONTROL（§5.2）
├── mav/
│   ├── link.py       UDP 套接字；按来源 IP 分流；收发计数；本机地址过滤（RELAY_ALLOW_LOOPBACK）
│   ├── decode.py     MAVLink → 状态 dict（单一入口 apply_message）
│   ├── modes.py      ArduPilot Copter / PX4 模式表、模式类别（manual / althold / position / auto）
│   ├── params.py     参数协议（PARAM_REQUEST_LIST、PARAM_VALUE 汇总、PARAM_SET 类型与回读）
│   ├── discovery.py  网段探测、广播唤醒
│   └── bridge.py     mavesp8266 读写（§9）与通用表单退路
├── drivers/
│   ├── base.py       Driver 协议
│   ├── mavlink.py    MavlinkDriver（真机）
│   └── sim.py        SimDriver（模拟机，包装 sim.autopilot）
├── sim/
│   ├── physics.py    多旋翼动力学（§7）
│   └── autopilot.py  模拟飞控（ArduPilot Copter 行为子集、参数表、消息生成）
├── authority.py      控制权状态机（纯函数，§4）
├── sessions.py       智能体会话与租约
├── skills.py         技能执行器（takeoff / land / hold / goto，§5.6）
├── course.py         航线与过环判定（§8）
├── preflight.py      起飞前检查（§6.3，纯函数）
├── topology.py       全局拓扑（纯函数）
├── alerts.py         告警计算与确认
├── events.py         事件总线（单调 seq，环形 5000）
├── history.py        SQLite（§11）
├── netinfo.py        本机网卡、网段、是否在数传热点上
├── fleet.py          FleetService：车辆表、主循环、对外方法（线程安全）
└── api/              app.py（工厂）· deps.py（鉴权）· schemas.py · routes_*.py · stream.py
```

### 3.1 线程与时序

- `FleetService` 一个后台线程，主循环 200 Hz 节拍（`time.monotonic`）：
  - 每拍：收 UDP（非阻塞批量）、处理命令队列；
  - 50 Hz：下发杆量、技能设定点；
  - 1 Hz：每架 GCS 心跳；
  - 模拟机：每拍步进物理（内步 250 Hz 累积）；
  - 10 Hz：快照组装（缓存，API 与 WS 读缓存，不在主循环外计算）；
  - 5 Hz：链路状态、告警、记录采样、航线判定、租约过期。
- API 线程只通过 `service.call(fn)`（放入命令队列并等待结果，超时 2 s）修改状态，读取用快照缓存；不持锁调用外部 I/O。
- 扫描、配网的 HTTP 请求在独立线程池里，结果经命令队列回到主循环。

### 3.2 Driver 协议

```python
class Driver(Protocol):
    kind: Literal["real", "sim"]
    def tick(self, now: float) -> list[MAVLinkMessage]      # 收到的消息（真机由 link 推入；模拟机自产）
    def send_sticks(self, s: StickInput) -> None             # 已乘符号与映射前的单位量
    def stop_sticks(self) -> None                            # §5.3
    def arm(self, on: bool) -> None
    def set_mode(self, name: str) -> None
    def command_long(self, cmd: int, params: tuple[float, ...]) -> None
    def position_target_local(self, x, y, z, yaw | None) -> None  # 技能用
    def request_params(self) -> None
    def set_param(self, name: str, value: float, ptype: int) -> None
    def heartbeat(self) -> None
```

## 4. 控制权（接力）

### 4.1 状态

每架飞机：`claim: Claim | None`（智能体会话）、`possession: Possession | None`（操作员输入源）。

```
controller(v) = "human" if v.possession
               else "agent" if v.claim and session_alive(v.claim)
               else "none"
claim.suspended = bool(v.possession)
```

### 4.2 转移（`authority.py` 纯函数，返回新状态 + 事件 + 副作用指令）

| 动作 | 前置 | 结果 | 事件 | 副作用 |
|---|---|---|---|---|
| possess(v, input) | 操作员令牌；该 input 未附身其他飞机（若有则先对那架执行 release） | possession = input | possession_started（含被挂起的 claim） | 停止该机正在执行的技能；必要时切附身模式（§5.4）；清空杆量 |
| release(v) | v 被附身 | possession = None | 有存活 claim → relay_handback；否则 possession_released | stop_sticks；空中且无控制者 → 告警 airborne_no_controller |
| claim(v, s) | 会话存活；v.claim 为空或同会话 | claim = s | claim_started | — |
| unclaim(v, s) | v.claim 属于 s | claim = None | claim_released | 若 controller 原为 agent：stop_sticks、停止技能 |
| session_expired(s) | 租约到期 | 清空 s 的全部 claim | agent_session_expired、claim_released × n | 同 unclaim |
| control(v, actor) | actor 必须等于 controller：操作员 → 必须附身 v 且输入源匹配；智能体 → 必须持有 v 的 claim 且未被挂起 | — | — | — |
| stop(v, actor) | 操作员任意；智能体须为 controller | 不变 | stop | 油门 0 + 申请上锁 + 停止技能 |
| stop_all | 操作员 | 不变 | stop_all | 对全部飞机 stop |

拒绝时的错误码：`not_possessed`、`possessed_by_human`（附带 possession）、`claimed_by_other`、`not_claimed`、`claim_suspended`、`session_expired`、`operator_required`。

### 4.3 不变量（单元测试逐条锁定）

1. 任一时刻每架飞机至多一个杆量来源在下发。
2. 人工附身总能成功（只需操作员令牌）。
3. 后端从不在交还时代替智能体发送任何杆量、模式或命令。
4. stop / stop_all 不改变 claim 与 possession。
5. 会话过期 ≤ 租约 + 200 ms 内其飞机杆量停止。
6. 杆量最后一次刷新 0.8 s 后停发。
7. 模拟机与真机遵守完全相同的规则。

## 5. MAVLink

### 5.1 身份与链路

- 本端 sysid 255、compid 190（MAV_COMP_ID_MISSIONPLANNER）——ArduPilot 只接受 sysid 等于 `MAV_GCS_SYSID`（旧名 `SYSID_MYGCS`，缺省 255）的 RC 覆盖，因此**不得改 255**；起飞前检查读取该参数核对（§6.3）。
- 以首个 `autopilot != MAV_AUTOPILOT_INVALID` 的心跳锁定该 IP 的 sysid / compid / autopilot；之后只认同一身份更新心跳计时（数传自身、相机等其他组件的心跳不算）。
- 链路状态：ok → delayed（心跳 > 3 s）→ lost（> 5 s：停止杆量与技能、继续心跳探测）→ offline（> 30 s：卡片变灰但保留）。恢复后不自动恢复杆量。
- 丢包率：按 MAVLink 序号（seq）缺口统计，窗口 5 s。往返时延：每 2 s 发 `TIMESYNC`，有应答时计算，无应答显示 `—`。

### 5.2 杆量映射（坐标系：roll 右正、pitch 前正、yaw 顺时针正，−1..1；throttle 0..1）

- ArduPilot `RC_CHANNELS_OVERRIDE`（通道 1–4 = roll、pitch、throttle、yaw；5–8 填 65535 = 不改；不发送 9–18 扩展字段）：
  - ch1 = 1500 + 500·roll·s_roll
  - ch2 = 1500 − 500·pitch·s_pitch（ArduPilot 约定：俯仰 PWM 低 = 前推）
  - ch3 = 1000 + 1000·throttle
  - ch4 = 1500 + 500·yaw·s_yaw
- PX4 `MANUAL_CONTROL`：x = 1000·pitch·s_pitch；y = 1000·roll·s_roll；z = 1000·throttle（0..1000）；r = 1000·yaw·s_yaw；buttons 0。
- 取整、限幅；`s_*` 为每架标定的符号（缺省 +1）。旧 demo 的 PX4 x 取反错误不得复现。
- 油门曲线（人工输入在前端完成，智能体输入原样）：手柄左杆 y∈[−1,1] → throttle = 分段线性（−1→0、0→hover、+1→1），再叠加 Expo。

### 5.3 停止杆量

- ArduPilot：连续 4 帧（80 ms）`RC_CHANNELS_OVERRIDE` 通道 1–8 全 0（释放给 RC）。无遥控器时这会在 `RC_OVERRIDE_TIME` 后触发飞控 RC 失控保护（FS_THR_*），这是期望行为：控制交回飞控自身的失控策略。
- PX4：**直接停止发送** `MANUAL_CONTROL`；PX4 在手动控制丢失超时后按 `COM_RCL_EXCEPT` / 失控保护处理。**禁止**发送 z=0 的帧（那是主动给最低油门）。
- 急停 stop：先 50 ms 发油门 0（ArduPilot ch3=1000 / PX4 z=0，roll/pitch/yaw 中位），再 `MAV_CMD_COMPONENT_ARM_DISARM(0)`，然后 stop_sticks。空中上锁可能被飞控拒绝（ACK 结果展示给用户）；v0.1 不提供强制上锁（param2 = 21196）——这是有意的，避免空中断电坠落伤人；设置里也不提供。

### 5.4 附身模式

人工杆量只在"接受杆量"的模式下有意义：ArduPilot STABILIZE / ALT_HOLD / LOITER / POSHOLD / ACRO / SPORT / DRIFT / FLOWHOLD；PX4 MANUAL / STABILIZED / ALTCTL / POSCTL / ACRO。附身时若当前模式不属于此类（GUIDED、AUTO、RTL、LAND、OFFBOARD、MISSION…），后端发送模式切换到设置的附身模式（缺省：飞控报告有气压计 → ALT_HOLD / ALTCTL；否则 STABILIZE / STABILIZED），等待心跳确认（≤ 1.5 s），失败则保持附身并告警 `mode_switch_failed`。交还时不改模式——智能体自己决定。

补充（v0.2.0）：附身设置为 `auto` 且有气压计时，若飞机**在地面**处于油门直通的杆量模式（STABILIZE / STABILIZED / MANUAL / ACRO 等，例如平台随附的 `keyboard_rc_controller.py` 飞过之后），同样切到 ALT_HOLD / ALTCTL；空中附身不改模式，避免在操作员手上突然变化。

### 5.5 数据流

首个有效飞控心跳后立即：ArduPilot `REQUEST_DATA_STREAM`（EXTRA1 20 Hz、EXTRA2 4、EXTRA3 2、EXTENDED_STATUS 2、POSITION 10、RC_CHANNELS 4、RAW_CONTROLLER 0）；PX4 `MAV_CMD_SET_MESSAGE_INTERVAL`（ATTITUDE 20 Hz、LOCAL_POSITION_NED 10、GLOBAL_POSITION_INT 4、SYS_STATUS 2、BATTERY_STATUS 2、VFR_HUD 4、EXTENDED_SYS_STATE 2、RC_CHANNELS 4、SERVO_OUTPUT_RAW 4、VIBRATION 2）；ArduPilot 的 VIBRATION 随 EXTRA3 下发；两者都 `MAV_CMD_REQUEST_MESSAGE(AUTOPILOT_VERSION)`。每 10 s 检查缺失的关键消息并重请求一次。参数表在首次心跳后 2 s 自动拉取（可在设置关闭）。

### 5.6 技能（智能体与界面共用）

| 技能 | 能力要求 | ArduPilot | PX4 | 模拟机 |
|---|---|---|---|---|
| takeoff(alt) | althold | GUIDED + arm + `NAV_TAKEOFF`(alt) | `NAV_TAKEOFF`(alt, 相对) | 同 ArduPilot |
| land | 任意 | 模式 LAND | `NAV_LAND` | LAND |
| hold | althold | LOITER（有位置）否则 ALT_HOLD + 杆量中位由技能执行器发送 | `DO_SET_MODE` POSCTL / ALTCTL（中位杆量同） | LOITER |
| rtl | position | RTL | `NAV_RETURN_TO_LAUNCH` | RTL |
| goto(x, y, z, yaw?) | position | GUIDED + `SET_POSITION_TARGET_LOCAL_NED`（技能执行器 10 Hz 重发） | OFFBOARD：先以 10 Hz 推设定点 1 s 再切模式；之后持续 10 Hz | GUIDED |
| course_fly | position | 由外部智能体逐环 goto（Relay 不内置自动穿环；示例里实现） | 同 | 同 |

能力（每架在快照中报告）：

- `manual`：有飞控心跳。
- `althold`：模式表中有定高类模式且传感器有气压计。
- `position`：有本地位置估计（`LOCAL_POSITION_NED` 新鲜 ≤ 0.5 s、更新 ≥ 5 Hz，且 EKF/estimator 状态正常）。

goto 的目标点必须在安全区内（设置项；缺省 3 × 3 × 2 m，原点为该机首次解锁点或模拟机网格原点），否则 400 `outside_safety_volume`。

### 5.7 无定位源的杆量技能（v0.2.0）

本平台的典型配置只有 IMU 与气压计。为了让"起飞—悬停—前进 1 m—后退 1 m—转 45°—降落"在这种配置下可用，技能执行器在没有 `position` 时改用定高模式下的杆量实现；技能是唯一的杆量来源，操作员拨杆超过接管阈值即取消技能。

| 技能 | 有位置估计 | 无位置估计（ALT_HOLD / ALTCTL + 杆量） |
|---|---|---|
| takeoff(alt) | 同 5.6 | 油门杆 0.72 爬升，接近目标改 0.62，按气压高度（VFR_HUD alt − 解锁前地面值）在 ±0.06 m 内改平 |
| hold(seconds?) | 同 5.6 | 中位杆量；带 `seconds` 时到时报告完成并继续保持 |
| move(forward, right, up) | 按当前航向换算为目标点，走 goto 闭环 | 开环：倾斜杆量 = atan(0.8 / g) / 最大倾角（PX4 `MPC_MAN_TILT_MAX`，ArduPilot `ANGLE_MAX`），按 v' = g·tan(倾角) − 0.35·v 推算位移，估计位移加制动距离到达目标即反向制动，稳定 1.2 s 后完成；单步水平距离 ≤ 2 m；`up` 用气压高度闭环 |
| yaw(deg) | 按姿态航向闭环，误差 ≤ 3° | 同左 |
| land | 同 5.6 | 定高模式下降杆 0.3，近地 0.36 缓降，落地后上锁 |

开环距离是估算（模拟机上误差约 ±30%），界面、接口文档与 Copilot 都明确告知；精确平移需要光流 + 测距等定位源。`move`、`yaw` 要求飞机在空中（`not_airborne`）。

### 5.8 动作序列（`plans.py`，v0.2.0）

动作序列是最多 24 步的技能列表，由舰队线程在每个杆量周期检查当前技能状态并启动下一步。步骤在开始前全部校验（动作名、字段、范围、能力）；`hover` 是 `hold(seconds)` 的别名。任一步失败或被取代、控制权变化、操作员拨杆、急停都会使序列失败；`plan/cancel` 主动取消并原地悬停。真机从地面开始的序列须带 `confirm: "ARM"`。事件：`plan_started`、`plan_step`、`plan_finished`、`plan_failed`、`plan_cancelled`；快照中每架飞机的 `plan` 字段报告进度。

### 5.9 飞行健康（`flighthealth.py`，v0.2.0）

舰队线程以 10 Hz 采样，保留最近 5 s，每次解锁清零（保留解锁前静置电压）。飞行类项目只在 `landed == "air"` 且样本 ≥ 1.5 s 时评估，起降过程不参与；倾斜与航向漂移只取摇杆回中（|杆量| < 0.06）的样本。

| 项目 | 指标 | 注意 / 异常阈值 |
|---|---|---|
| oscillation | 横滚、俯仰角速度去均值后的均方根 | 18 / 40 °/s |
| lean | 回中时平均倾角 | 3 / 6° |
| yaw_drift | 回中时平均偏航角速度 | 6 / 15 °/s |
| alt_hold | 定高且油门回中时垂直速度标准差 | 0.18 / 0.4 m/s |
| hover_throttle | 平均油门 | < 20% 或 ≥ 70% / ≥ 82% |
| motors | 各电机平均输出最大差；输出 ≥ 1950 µs 的比例 | 120 / 220 µs；3% / 12% |
| vibration | VIBRATION 峰值；本次飞行加速度计削波次数 | 15 / 30 m/s²；削波即异常 |
| battery_sag | （静置电压 − 最低电压）/ 节数 | 0.45 / 0.7 V |
| link | 丢包率；往返延迟 | 3 / 15%；120 / 300 ms |
| stick_stream | 人工杆量到达频率；最大间隔 | < 20 / < 10 Hz；0.15 / 0.35 s |

阈值只用于指向原因，不作为调参依据；界面为每项给出原因与处理建议，详见 `docs/flight-tuning.zh-CN.md`。

## 6. 安全设计

### 6.1 危险源与控制

| # | 危险 | 控制措施 | 验证 |
|---|---|---|---|
| H1 | 飞错了飞机（选中≠附身） | 杆量只发往附身的那架；附身与选中在界面上不同的视觉；手柄绑定在卡片与舞台都显示；切换选中不改变附身 | e2e + 单元 |
| H2 | 陈旧杆量（窗口卡死、网络抖动） | 0.8 s 刷新超时；窗口失焦停发；WS 断开即后端视为该输入源停止 | 单元 |
| H3 | 链路丢失时继续"控制" | lost 停发；恢复不自动续发 | e2e（假飞机断流） |
| H4 | 意外解锁 | 真机必须输入 ARM；起飞前检查阻断；手柄不能解锁真机 | 单元 + e2e |
| H5 | 人机抢夺 | 状态机唯一来源；人总能抢占；智能体被挂起时一切控制被拒 | 单元 |
| H6 | 交还瞬间失控 | 交还时后端不发任何东西；空中无控制者常驻告警 | 单元 |
| H7 | 模拟与真实混淆 | 模拟机永不经网络；视觉三处区分；手柄自动绑定只对模拟机 | 单元（sim 驱动无套接字）+ 走查 |
| H8 | 同机恶意 / 错误进程冒充人 | 操作员令牌；API 只绑 127.0.0.1；CORS 只允许 `app://relay`、`file://` 与开发端口 | 单元 |
| H9 | 智能体失控（洪泛请求、越界） | 每会话限速（控制类 60 次 / s、其他 20 次 / s）；goto 受安全区约束；租约过期自动释放 | 单元 |
| H10 | 低电压继续飞 | 电压门限告警；critical 时界面常驻，记录事件（不自动降落——飞控有自身电池失控保护） | 单元 |
| H11 | 空中强制上锁坠落 | 不提供强制上锁 | 代码检查 |
| H12 | 写错参数 | 输入参数名确认；类型校验；回读比对 | e2e |
| H13 | 配网写错导致失联 | 写入前展示全部将写入的值；保留 AP 回落；失败排查清单 | 走查 |

### 6.2 鉴权

- 操作员：头 `X-Relay-Operator`（或 WS 查询参数 `operator`）；令牌比较用 `hmac.compare_digest`。
- 智能体：`X-Relay-Session`。
- 只读接口匿名。
- 开发模式 `--dev` 且未提供令牌时，后端生成令牌并只打印到 stderr。

### 6.3 起飞前检查（`preflight.py`）

| id | 级别 | 通过条件 | 说明文案（EN） |
|---|---|---|---|
| link | block | 链路 ok | Link is live |
| autopilot | block | 已识别 PX4 / ArduPilot 且心跳身份稳定 | Autopilot identified |
| battery | block / warn | ≥ critical × 节数为通过，< warn × 节数为 warn | Battery above threshold |
| prearm | block | SYS_STATUS 的 PREARM_CHECK 健康（未报告则 info） | Autopilot pre-arm checks pass |
| gcs_input | block | ArduPilot：`MAV_GCS_SYSID`/`SYSID_MYGCS` = 255 且 `RC_OVERRIDE_TIME` ≠ 0；PX4：`COM_RC_IN_MODE` 允许 MAVLink 手动输入（≠ 0 RC only） | Autopilot accepts Relay's stick input |
| signs | block（真机） | 本次运行中已标定确认 | Stick directions verified without propellers |
| props | block（真机） | 操作员确认桨向与防护网 | Propellers and safety net checked |
| mode | warn | 当前模式接受杆量 | Mode accepts manual input |
| failsafe | warn | ArduPilot `FS_THR_ENABLE` ≠ 0 或 `FS_GCS_ENABLE` ≠ 0；PX4 `NAV_RCL_ACT` ≠ 0 | A link-loss failsafe is configured |
| position | info | 有位置能力 | Position estimate available |

参数类检查在参数尚未拉取完成时显示 `Waiting for parameters`（不通过但不报错）。

### 6.4 安全区

设置项（宽、长、高，缺省 3 × 3 × 2 m）。用途：舞台显示、航线缩放、goto 约束。不向飞控写围栏（v0.1 不改飞控参数，避免越权）。

## 7. 模拟机

> v0.3.0 起：悬停辅助开关、物理效应（地效、湍流、非对称电机响应）与命中冲量见 `05-v030.zh-CN.md` §5、§7.4。

### 7.1 动力学（`sim/physics.py`）

状态：位置 p、速度 v（ENU，m、m/s）、姿态四元数 q、机体角速度 ω。内步 250 Hz（半隐式欧拉）。

- 质量 0.12 kg；推重比 2.4；悬停油门 0.50；电机一阶 τ 0.03 s。
- 姿态内环（理想化）：目标倾角 → 角速度 → 一阶 τ 0.08 s；最大倾角 30°（STABILIZE）/ 25°（其他）；最大偏航速率 120°/s。
- 阻力：线性 0.35·v + 二次 0.08·|v|·v（水平极速约 7 m/s）。
- 地面：z ≤ 0 时接触；落地垂直速度 > 2.5 m/s 或水平 > 3 m/s 或倾角 > 60° → crash（上锁、记事件、需复位）。
- 风：可选（设置里关，缺省 0）。
- 电池：3S 850 mAh，OCV(SOC) 曲线（4.20…3.30 V/节分段线性），内阻 0.075 Ω；电流 = 0.4 A + 11 A·油门^1.5；SOC 积分；电压 < 3.3 V/节时推力按比例衰减。

### 7.2 模拟飞控（`sim/autopilot.py`，扮作 ArduPilot Copter 4.5，`autopilot=3`、`type=QUADROTOR`）

- 模式：STABILIZE、ALT_HOLD（油门杆 → 爬升率 ±1.5 m/s，中位死区 ±0.1）、LOITER（杆 → 速度 ±3 m/s、刹停）、GUIDED（`SET_POSITION_TARGET_LOCAL_NED` 位置环，最大 2 m/s）、LAND（0.6 m/s 下降、触地自动上锁）、RTL（升至 1.5 m → 回出生点 → LAND）。
- 解锁：油门 < 0.05、未 crash、在地面；GUIDED 下 `NAV_TAKEOFF`。
- 杆量丢失（0.8 s 未刷新且已解锁在空中）：模拟 RC 失控保护 → LAND（与真机常见配置一致）；手柄断开时前端先停发 → 同样触发。
- 参数表（约 40 项、可读写、类型正确）：`SYSID_MYGCS`(255)、`RC_OVERRIDE_TIME`(3)、`FS_THR_ENABLE`(1)、`BATT_LOW_VOLT`(10.5)、`BATT_CRT_VOLT`(9.9)、`PILOT_SPEED_UP`(150)、`WPNAV_SPEED`(200)、`ANGLE_MAX`(3000)、`ATC_RAT_*` 等；写 `ANGLE_MAX`、`PILOT_SPEED_UP`、`WPNAV_SPEED` 会改变动力学。
- 消息（按 §5.5 速率）：HEARTBEAT、SYS_STATUS、BATTERY_STATUS、ATTITUDE、LOCAL_POSITION_NED、VFR_HUD、EXTENDED_SYS_STATE、RC_CHANNELS、SERVO_OUTPUT_RAW、STATUSTEXT（解锁、模式、坠毁、低电）、COMMAND_ACK、PARAM_VALUE、AUTOPILOT_VERSION。全部是 pymavlink 消息对象，经 `decode.apply_message`。
- 模拟机的 `position` 能力恒为真；`gcs_input`、`signs`、`props` 预检项恒通过并标注 `Simulated`。

### 7.3 假飞机（`tools/fakes/fake_vehicle.py`）

一个进程 = 一块 mavesp8266 数传 + 一个飞控，用于 e2e 与无硬件联调：复用 `relay.sim`，`--autopilot px4|ardupilot`、`--bind 127.0.0.2:14555`、`--gcs 127.0.0.1:14550`、`--http 127.0.0.2:8080`（mavesp8266 的 `/`、`/setup`、`/getparameters`、`/setparameters`、`/reboot`、`/status.json` 原样复刻，参数持久化到临时文件，`reboot` 后按新模式"消失 / 重新出现"）、`--no-position`（PX4 风格无光流）、`--drop-after 20`（20 s 后断流，测链路丢失）。PX4 风格接受 MANUAL_CONTROL 与 PX4 模式名、`COM_RC_IN_MODE` 参数。

## 8. 穿环航线（`course.py`）

- 航线：`{id, name_en, name_zh, rings: [{c: [x, y, z], yaw_deg, r}]}`；坐标东 x、北 y、上 z（m），相对航线原点。预设（按 3 × 3 × 2 m 安全区设计，安全区不同则等比缩放，环半径不缩到 0.3 m 以下）：
  - `cage-loop`：4 环，中心 (0, 1.0, 1.0) → (1.0, 0, 1.0) → (0, −1.0, 1.0) → (−1.0, 0, 1.0)，法向沿顺时针切向，r 0.45。
  - `slalom`：6 环，y 从 −1.25 到 1.25 等距，x 交替 ±0.6，z 1.0，法向 +y，r 0.4。
  - `climb`：5 环，绕原点半径 1.0 的螺旋，z 0.6 → 1.6，r 0.45。
- 原点：模拟机 = 出生点；真机 = 开始时的本地位置（落地点）与偏航（航线绕原点旋转到该偏航）。
- 状态机：idle → waiting_takeoff（Start 后）→ running（首次 z > 0.3 m 或 landed→air）→ finished / aborted。
- 判定：每次位置更新（模拟机 50 Hz，真机按 LOCAL_POSITION_NED），取线段 p0→p1；若跨越下一环所在平面（有向距离变号，从负到正 = 沿法向正向穿越），求交点；交点到环心距离 ≤ r → 通过（split = t − 上一环时间）。只认下一环。
- 中止条件：上锁、坠毁、链路 lost、位置能力丢失 > 1 s、Stop、控制者变为无（空中无人控制）。
- 计时用后端单调时钟；成绩写 `course_runs`；事件 course_started、ring_passed、course_finished、course_aborted。
- 真机无位置能力时 start 返回 409 `no_position_estimate`。

## 9. 数传配网（`mav/bridge.py`）

- 识别：`GET http://<ip>/` 标题含 "MavLink Bridge" 或页面含 `/setup` 链接 → mavesp8266。
- 读取：`GET /getparameters`（HTML 表：Name / Value；含 version、mode AP/STA、IP、AP SSID/密码、STA SSID/密码与其他整数参数），解析为字典；`GET /status.json`（若有）补充收发计数。
- 写入：`GET /setparameters?mode=1&ssidsta=…&pwdsta=…&ipsta=0.0.0.0&gatewaysta=0.0.0.0&subnetsta=0.0.0.0&hport=14550&cport=14555&reboot=1`（URL 编码；只发生变化的与必需的字段；`mode` 0 = AP、1 = Station）。数传保存后约 100 ms 重启。
- 退路：识别不出 mavesp8266 → 解析页面第一个 form 原样提交（字段由用户在界面中编辑）；或"在 Relay 内打开设置页"。
- 本机是否在数传热点上：网卡地址在 192.168.4.0/24。
- 后端拿不到 Wi-Fi SSID（跨平台无稳定接口），由用户输入；Relay 记住最近一次输入（不含密码）。密码不落盘。

## 10. 对外接口

### 10.1 REST（`/api/v1`，127.0.0.1）

| 方法 路径 | 鉴权 | 说明 |
|---|---|---|
| GET /health | – | `{ok, version, uptime_s}` |
| GET /capabilities | – | 版本、坐标系、门限、技能表、允许命令、限速、文档链接 |
| GET /fleet | – | 快照（§10.3） |
| GET /vehicles/{id} | – | 单机完整状态 |
| GET /vehicles/{id}/messages | – | 各类 MAVLink 最近一帧与频率 |
| GET /vehicles/{id}/params | – | 参数表 |
| GET /vehicles/{id}/preflight | – | 检查项 |
| GET /topology | – | 拓扑（§10.4） |
| GET /events?after=&limit= | – | 事件 |
| GET /network | – | 网卡、网段、是否在数传热点上、UDP 监听状态 |
| GET /courses | – | 预设与自定义航线（已按安全区缩放） |
| PUT /courses/{id} | operator | 写自定义航线 |
| POST /discover | operator | 开始扫描 |
| POST /vehicles | operator | 直连 `{host, port?}` |
| DELETE /vehicles/{id} | operator | 移除 / 删除 |
| POST /vehicles/{id}/label | operator | 改名 |
| POST /vehicles/{id}/calibration | operator | `{signs?, hover?}` |
| POST /vehicles/{id}/confirm | operator | `{item: "signs"|"props"}` |
| POST /vehicles/{id}/possess | operator | `{input: "keyboard"|"screen"|"gamepad", pad?: 1..4}` |
| POST /vehicles/{id}/release | operator | |
| POST /vehicles/{id}/claim · /unclaim | session | |
| POST /vehicles/{id}/sticks | 控制者 | `{roll, pitch, yaw, throttle}` |
| POST /vehicles/{id}/arm | 控制者 | `{arm: true, confirm: "ARM"}` / `{arm: false}` |
| POST /vehicles/{id}/mode | 控制者 | `{name}` |
| POST /vehicles/{id}/skills/{name} | 控制者 | takeoff `{alt}` · land · hold · rtl · goto `{x, y, z, yaw?}` |
| POST /vehicles/{id}/course/start · /stop | 控制者或操作员 | `{course_id}` |
| POST /vehicles/{id}/stop | 控制者或操作员 | 急停 |
| POST /stop-all | operator | |
| POST /vehicles/{id}/params/refresh | 控制者或操作员 | |
| POST /vehicles/{id}/params | operator | `{name, value, confirm: name}` |
| POST /vehicles/{id}/motor-test | operator | `{motor, throttle_pct, seconds, confirm: "MOTOR"}`，未解锁真机 |
| POST /sims · DELETE /sims · POST /sims/{id}/reset | operator | |
| POST /sessions | – | `{name}` → `{session_id, token, lease_s}` |
| POST /sessions/heartbeat · DELETE /sessions | session | |
| GET /sessions | – | 会话（不含令牌）与最近调用 |
| GET /bridges/{ip} · POST /bridges/{ip}/configure | operator | 配网 |
| GET /history/sessions · GET /history/sessions/{sid} · GET /history/sessions/{sid}/export?fmt=csv|json | – | 记录 |
| GET /alerts · POST /alerts/{id}/ack | –、operator | |
| GET /settings · PUT /settings | –、operator | |
| WS /stream | 可选 | §10.2 |

错误体统一：`{"error": {"code", "message_en", "message_zh", "detail"}}`；状态码 400 / 401 / 403 / 404 / 409 / 429 / 503。

### 10.2 WebSocket `/api/v1/stream`

- 服务端：`hello {version, operator: bool, session: str|null}` → `fleet` 10 Hz（完整快照，gzip 前约 2 KB/架）→ `event` 实时。
- 客户端：`sticks {vehicle, roll, pitch, yaw, throttle}`（需鉴权；后端以帧到达刷新 0.8 s 超时）；`ping`。
- 渲染进程的人工杆量只走 WS（50 Hz）。WS 断开 = 该连接的全部人工杆量立即停止。

### 10.3 快照（`web/src/api/types.ts` 由后端 pydantic 模型导出的 JSON Schema 生成，CI 校验一致）

```ts
interface Fleet {
  t: number; version: string
  listen: { port: number; ok: boolean; error: string | null }
  scan: { running: boolean; done: number; total: number; networks: string[]; found: number; error: string | null }
  sessions: { session_id: string; name: string; expires_in_s: number; vehicles: string[] }[]
  safety_volume: { x: number; y: number; z: number }
  vehicles: Vehicle[]
  alerts: Alert[]
}
interface Vehicle {
  id: string; kind: "real" | "sim"; label: string; host: string | null
  autopilot: "PX4" | "ArduPilot" | null; firmware: string | null; sysid: number | null
  bridge: { version: string | null; source: "udp" | "http" | "manual" } | null
  link: { state: "waiting" | "ok" | "delayed" | "lost" | "offline"; heartbeat_age_s: number | null; rx_hz: number; tx_hz: number; loss_pct: number | null; rtt_ms: number | null }
  armed: boolean; mode: string | null; mode_class: "manual" | "althold" | "position" | "auto" | null; modes: string[]
  landed: "unknown" | "ground" | "air" | "takeoff" | "landing"; crashed: boolean
  attitude: { roll: number; pitch: number; yaw: number } | null          // rad
  rates: { roll: number; pitch: number; yaw: number } | null
  position: { frame: "local" | "sim"; x: number; y: number; z: number; vx: number; vy: number; vz: number; age_s: number } | null  // ENU m
  global: { lat: number; lon: number; alt: number } | null
  altitude_m: number | null; climb_ms: number | null; groundspeed_ms: number | null; heading_deg: number | null; throttle_pct: number | null
  battery: { voltage: number | null; current: number | null; remaining_pct: number | null; cells: number | null; level: "ok" | "warn" | "critical" | "unknown" }
  gps: { fix: number; sats: number | null; hdop: number | null } | null
  sensors: { name: string; present: boolean; enabled: boolean; healthy: boolean }[]
  statustext: { t: number; severity: number; text: string }[]
  rc: number[] | null; servo: number[] | null
  flight_time_s: number; armed_time_s: number
  capabilities: { manual: boolean; althold: boolean; position: boolean }
  claim: { session_id: string; name: string; since: number; suspended: boolean } | null
  possession: { input: "keyboard" | "screen" | "gamepad"; pad: number | null; since: number } | null
  controller: "none" | "human" | "agent"
  sticks: { roll: number; pitch: number; yaw: number; throttle: number; age_s: number; source: "human" | "agent" } | null
  skill: { name: string; state: "running" | "done" | "failed"; detail: string | null } | null
  calibration: { signs: { roll: 1 | -1; pitch: 1 | -1; yaw: 1 | -1 }; hover: number }
  confirmed: { signs: boolean; props: boolean }
  preflight: { ok: boolean; blocking: string[] }
  course: { course_id: string; state: "waiting_takeoff" | "running" | "finished" | "aborted"; next: number; total: number; started_at: number | null; elapsed_s: number; splits: number[]; best_s: number | null; reason: string | null; origin: [number, number, number, number] } | null
  record_session: number | null
}
interface Alert { id: string; vehicle: string | null; level: "info" | "warn" | "critical"; code: string; text_en: string; text_zh: string; since: number; acked: boolean }
```

角度单位 rad（attitude、rates），heading_deg 为度；前端统一格式化。

### 10.4 拓扑

`{station: {hosts, listen}, nodes: [{id, type: "station"|"vehicle"|"session"|"bridge", ...}], edges: [{from, to, type: "link"|"claim"|"possession", ...}], relations: [{a, b, horizontal_m, vertical_m, bearing_deg}]}`；relations 只在两架处于同一坐标系时给出。

### 10.5 事件

`{seq, t, kind, vehicle?, session?, text_en, text_zh, data}`；kind：vehicle_discovered、vehicle_online、vehicle_offline、link_delayed、link_lost、link_restored、armed、disarmed、mode_changed、statustext、command_ack、possession_started、possession_released、relay_handback、claim_started、claim_released、agent_session_started、agent_session_closed、agent_session_expired、skill_started、skill_finished、skill_failed、stop、stop_all、battery_warn、battery_critical、crashed、airborne_no_controller、param_set、sim_added、sim_removed、bridge_configured、course_started、ring_passed、course_finished、course_aborted、listen_error。

### 10.6 Python SDK（`sdk/python/anet_relay_client.py`，只用标准库）

`RelayClient(base)`：`fleet() vehicle(id) topology() events(after) stream()（生成器）open_session(name)（后台续租线程）claim unclaim sticks arm disarm mode takeoff land hold rtl goto course_start course_stop stop close()`；异常 `RelayError(code, status, message)`。

示例：`hover_sim.py`（认领 → 解锁 → 起飞 1.2 m → 悬停 8 s → 降落 → 释放）、`watch_handback.py`（认领 sim-1 做绕圈 goto；人附身后暂停；收到 relay_handback 后从当前位置继续）、`course_sim.py`（逐环 goto 飞 cage-loop，打印成绩）、`swarm_square.py`（4 架模拟机方阵起飞、同步旋转、降落）。

### 10.7 MCP（`mcp/anet_relay_mcp.py`，stdio，JSON-RPC 2.0，自实现最小 MCP 协议，不引第三方依赖）

工具：`relay_fleet`、`relay_vehicle`、`relay_topology`、`relay_events`、`relay_claim`、`relay_release_claim`、`relay_arm`、`relay_disarm`、`relay_mode`、`relay_takeoff`、`relay_land`、`relay_hold`、`relay_goto`、`relay_stop`、`relay_course_start`。不暴露杆量（LLM 不适合 50 Hz 闭环）。会话在 MCP 进程启动时打开并自动续租，名字取 `--name`（缺省 `mcp`）。文档给出 Claude Desktop / Claude Code 的配置片段。

## 11. 数据

- 目录：`RELAY_DATA_DIR`（Electron 传 userData）：`history.db`、`settings.json`、`backend.log`（轮转 5 × 5 MB）、`http-port`。
- SQLite 表：`labels(host, label)`、`record_sessions(id, vehicle_id, kind, label, autopilot, started_at, ended_at)`、`samples(session_id, t, armed, mode, roll, pitch, yaw, x, y, z, alt, climb, voltage, current, remaining, throttle, controller)`（5 Hz）、`events(session_id, seq, t, kind, data_json)`、`control_segments(session_id, start, end, controller, who)`、`course_runs(id, session_id, vehicle_id, course_id, started_at, finished_at, state, total_s, splits_json, controller)`、`calibration(host, signs_json, hover)`。WAL 模式；写入批量（1 s）。
- 记录保留：缺省 90 天（设置可改）。

## 12. 桌面外壳（Electron 44.9.3）

- `contextIsolation`、`sandbox`、无 nodeIntegration；preload 只暴露 `window.relay = {apiBase, operatorToken, platform, version, openExternal(url), revealLogs(), openBridgePage(ip)}`。
- 后端：开发 = `.venv/bin/python -m relay`；打包 = `resources/backend/anet-relay-backend`。端口 8765 起找空闲（到 8775）；令牌 32 字节 hex 每次启动生成；`RELAY_DATA_DIR` = userData。健康检查 20 s；失败 / 退出 → 原生对话框（Restart backend / View log / Quit）并通知渲染进程显示 02 §15 的状态。退出时 SIGTERM → 1.5 s → SIGKILL。
- 窗口：1440 × 900、最小 1100 × 720、macOS hiddenInset、背景 #0A0B0D、ready-to-show 后显示；只允许 media（摄像头）权限；外链用系统浏览器；禁止导航离开应用；单实例。
- 渲染内容：生产从 asar 内 `web/dist` 以自定义协议 `app://relay/` 加载（避免 file:// 的 CORS 与字体问题）；开发用 Vite 地址。
- 菜单：ANet Relay（About、Settings… ⌘,、Quit）、Edit、View（Toggle fleet ⌥⌘B、Toggle inspector ⌥⌘I、Toggle timeline ⌥⌘T、Actual size、Full screen、开发模式下 Reload / DevTools）、Fly（Stop all ⌘.、Possess P…）、Window、Help（Agent API、Hardware guide、Open logs、GitHub）。

## 13. 打包与发布

- 后端：PyInstaller onedir（`backend/relay-backend.spec`；隐藏导入 pymavlink 方言 `ardupilotmega`、`common`；排除 tkinter）。
- 前端：`web/dist` 打进 asar。
- electron-builder：appId `org.agentnetwork.relay`，productName `ANet Relay`，`extraResources: backend`；mac：target zip（arm64、x64 分别构建），`identity: null` 后执行 ad-hoc 签名（`codesign --force --deep -s -`），否则 Apple Silicon 上下载的未签名应用会被报"已损坏"；linux：tar.gz + AppImage；win（S 级）：nsis。
- GitHub Actions：
  - `ci.yml`（push / PR 到 main，ubuntu）：后端 ruff + pytest；web tsc + oxlint + vitest + build；快照类型一致性检查。
  - `release.yml`（推送 `v*` 标签）：矩阵 macos-15（arm64）、macos-15-intel（x64）、ubuntu-22.04（x64）、windows-2022（S 级，失败不阻断）：构建后端 → 构建前端 → electron-builder → 产物 `ANet-Relay-<ver>-mac-arm64.zip`、`…-mac-x64.zip`、`…-linux-x64.tar.gz`、`…-linux-x64.AppImage`（、`…-win-x64.exe`）+ `SHA256SUMS.txt` → 上传到同名 Release（发布说明取 `CHANGELOG.md` 对应节）。
  - 若推送 `.github/workflows` 需要的令牌权限不足，退路：本机构建 Linux 包上传，macOS 包由负责人在 Mac 上 `npm run dist:mac` 后上传（README 写明）。
- README（参照 ANet4D）：居中横幅图（应用截图合成，1600 × 600）、"ANet Relay" 标题、一句定位、徽章（Release、CI、License、ANet4D）、链接行（Download · Agent API · Hardware guide · ANet4D · ANet）、语言切换；正文：Capabilities 表、Download（各平台、首次打开说明）、Quick start（开发）、Connect your aircraft（配网摘要）、Fly（附身、接力、穿环）、For agents（REST、SDK、MCP 片段）、Feature tour（截图）、Safety、License、Acknowledgements。中文 README 同构。

## 14. 测试

| 层 | 内容 |
|---|---|
| 后端单元 | frame 公式；authority 全部转移与 §4.3 不变量；decode；modes；params 协议与类型；sim 物理（悬停、定高、定点刹停、GUIDED 到点、LAND 自动上锁、坠毁、电池）；course（穿越判定：正向、反向、擦边、跳环、斜穿；预设缩放）；preflight；topology；alerts；bridge（mavesp8266 HTML 解析、写入 URL）；API（鉴权、错误码、限速、OpenAPI）；WS（hello、fleet、sticks 鉴权、断开即停） |
| 后端集成 | 真 FleetService + 假飞机（PX4 与 ArduPilot 各一，127.0.0.2/3）：发现、心跳锁定、数据流请求、杆量线上编码（pymavlink 解码比对）、停止方式、链路丢失、模式切换、参数写读、配网全流程（重启后重新出现） |
| SDK / MCP | 示例对模拟机跑通；MCP 用脚本模拟客户端 initialize → tools/list → tools/call |
| 前端单元 | 输入映射（死区、Expo、油门分段）、按住计时、手柄绑定规则、红色唯一规则、i18n 键集一致、格式化 |
| e2e（Playwright `_electron`，Linux xvfb） | 启动动画 → 空状态 → 加 4 架模拟机 → 键盘附身 sim-1、解锁、爬升、释放 → SDK 会话认领 sim-2 悬停 → UI 附身 sim-2 → 释放 → SDK 收到 relay_handback → 模拟手柄接入（桩 navigator.getGamepads）看到提示 → 穿环 cage-loop（SDK 自动飞）完成并在记录中出现 → 扫描发现两架假飞机 → 配网向导对假数传 → 全部急停 → 中文 → 记录回看；每步截图 |
| 视觉走查 | 02 §20 清单，三种窗口尺寸 × 中英文的截图集 |

## 15. 工作分解（实现顺序）

| WP | 内容 | 依赖 | 交付 |
|---|---|---|---|
| WP1 后端核心 | config、errors、model、frame、mav/*、drivers、authority、sessions、events、alerts、preflight、topology、history、fleet、netinfo | — | 单元 + 集成测试 |
| WP2 模拟与航线 | sim/*、skills、course、fake_vehicle | WP1 接口 | 单元测试、假飞机 |
| WP3 接口 | api/*、SDK、MCP、类型导出、docs/agent-api | WP1、WP2 | API 测试、示例跑通 |
| WP4 外壳与打包 | electron/*、icons、PyInstaller spec、electron-builder、CI 与 release 工作流 | WP3 入口 | Linux 包可运行 |
| WP5 前端基础 | 脚手架、设计系统、外壳布局、启动、i18n、API 层、store、命令面板、告警、时间线 | WP3 类型 | 走查截图 |
| WP6 舞台与穿环前端 | 三维、视角、摆放、航线渲染、HUD、完成卡 | WP5 | 截图（1/4/8/16 架） |
| WP7 控制与手柄 | 附身、接力呈现、杆量、键盘、手柄、接入提示、解锁、预检、急停、标定、电机测试、图传 | WP5 | vitest、截图 |
| WP8 机群、接入、检查器、Agents、Records、设置 | 其余界面 | WP5 | 截图 |
| WP9 集成与 e2e | 全流程、e2e 套件、走查修正 | 全部 | e2e 全绿 |
| WP10 发布 | README（中英）、横幅与截图、CHANGELOG、硬件指南、推送、打标签、Release | WP9 | GitHub Release |

## 16. Copilot（内置智能体，v0.2.0）

- **位置**：Electron 主进程（`electron/copilot/`），基于 `@earendil-works/pi-agent-core` 与 `pi-ai` 1.4.0，打包时用 esbuild 合并为单文件 `agent.bundle.mjs`（附依赖许可清单）。渲染进程只负责对话界面，经 preload 暴露的 `window.relay.copilot` 收发消息与事件。
- **接入方式**：与外部智能体完全相同：打开名为 `copilot` 的会话（5 s 心跳续约），调用前自动认领目标飞机；附身时与其他智能体一样被挂起。工具为 `relay_fleet`、`relay_vehicle`、`relay_takeoff`、`relay_hover`、`relay_move`、`relay_yaw`、`relay_land`、`relay_plan`（轮询进度并回传）、`relay_cancel`、`relay_release`、`relay_stop`；不提供原始杆量。
- **审批**：目标为真机时，运动类工具（起飞、悬停、平移、转向、降落、序列）在执行前向界面发出审批请求，操作员批准后才发送；120 s 未处理视为拒绝，被拒绝的调用以错误返回给模型。
- **配置**：接口类型（`openai-completions` / `anthropic-messages` / `openai-responses`）、Base URL、模型 ID、API Key。Key 用 Electron `safeStorage`（系统钥匙串）加密存于用户数据目录 `copilot.json`；系统不提供加密时以 0600 权限明文保存并在设置中提示。Key 不回传渲染进程，界面只显示是否已保存及末 4 位。
- **测试**：`electron/copilot/test` 用 pi 的 faux provider 跑通工具调用；e2e `copilot.e2e.ts` 用本地伪造的 OpenAI 兼容 SSE 端点走完设置、模拟机序列与真机审批拒绝。

