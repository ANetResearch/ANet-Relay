# 04 控制面：设备级接口、应用级接口、再飞、多套 MCP 与浮动 Copilot（v0.2.1）

本文是 v0.2.1 的约束性契约，补充 01–03。目标：智能体 / 大语言模型能够以尽可能细的粒度操作飞机与设备（直到单个电机、单个执行器、单条 MAVLink 命令），并且能够操作 ANet Relay 应用本身的全部功能（语言、工作区、选中、视角、手柄、扫描、模拟机、设置、记录、再飞……）。所有接口在 `/api/v1` 下，错误体与 03 §10 相同。

## 1. 原则

1. **同一套权限模型**：飞机级写操作要求"控制者"身份（持有认领的会话，或附身中的操作员），与现有 `sticks`、`skills` 相同（`actor(request, "control")`）。附身始终优先。
2. **危险操作靠确认词**，不靠隐藏接口。每个危险等级有固定确认词；真机必须携带，模拟机不需要（`TERMINATE` 例外，两者都要）。

| 等级 | 确认词 | 额外条件 |
|---|---|---|
| `motor`：电机测试、电机通道上的执行器输出 | `MOTOR` | 必须上锁状态 |
| `arm`：解锁 | `ARM` | 通过起飞前检查（现有规则） |
| `force_arm`：强制解锁（跳过飞控自检） | `FORCE` | — |
| `reboot`：重启飞控 | `REBOOT` | 必须上锁 |
| `calibrate`：传感器校准 | `CALIBRATE` | 必须上锁 |
| `terminate`：飞行终止（立即停桨） | `TERMINATE` | 真机与模拟机都要求 |
| `raw`：目录中标记为危险的原始命令 | 目录项给出的确认词 | 目录项给出的条件 |

缺确认词：400 `confirm_required`（`detail.confirm` 给出所需词）；需上锁而已解锁：409 `must_be_disarmed`。
3. **一切可见**：每个写操作产生事件；应用级操作产生 `app_command` 事件并在界面右下角提示"<会话名> 将语言切换为中文"之类。
4. **模拟机与真机同构**：每个设备级接口在模拟机上也要有可观察的效果（伺服输出、命令应答、姿态目标），测试据此编写。

## 2. 设备级接口（后端 `relay/api/routes_device.py`，模型放 `relay/api/schemas_device.py`，逻辑放 `relay/device.py` 与 `fleet.py`）

| 方法与路径 | 权限 | 说明 |
|---|---|---|
| `GET /vehicles/{id}/device` | – | 设备概览：`{motors: {count, layout, outputs_us[], test_supported}, servos: [{index, us}], rc: [us], frame: {mav_type, name}, sensors: [...], estimator: {...} \| null, vibration: {...} \| null, home: {...} \| null, params: {loaded, total}, message_rates: {NAME: hz}}` |
| `GET /vehicles/{id}/messages/{name}` | – | 该消息最近一帧 `{name, fields, t, hz}`；未收到过返回 404 `message_not_seen` |
| `GET /vehicles/{id}/params/{name}` | – | 单个参数 `{name, value, type}`；未载入时按名读取并最多等待 2 s，仍无返回 404 `param_not_found` |
| `POST /vehicles/{id}/params/read` | 控制者或操作员 | `{names: [..≤32]}` 按名读取，最多等待 3 s，返回 `{values: {NAME: value}, missing: [..]}` |
| `GET /commands` | – | 原始命令目录（见 §2.1） |
| `POST /vehicles/{id}/command` | 控制者 | `{command: "MAV_CMD_..." 或编号, params: [p1..p7], frame?: int, use_int?: bool, confirm?, timeout_s?: ≤10}` → `{command, ack: {result, result_name, progress} \| null}`。只接受目录中的命令（400 `command_not_allowed`）；等待 `COMMAND_ACK`，超时 `ack: null` |
| `POST /vehicles/{id}/motors/test` | 控制者 | `{motor: 1..8 或 "all", throttle_pct: 0..30（模拟机 0..100）, seconds: 0.2..5, confirm}`；`all` 按序逐个；替代旧 `/motor-test`（保留旧路径作别名） |
| `POST /vehicles/{id}/motors/stop` | 控制者 | 所有电机测试立即停止 |
| `POST /vehicles/{id}/actuators/set` | 控制者 | `{index: 1..16, value?: -1..1, pwm?: 800..2200, seconds?: 0..10（0 = 保持）, confirm?}`。PX4：`MAV_CMD_ACTUATOR_TEST`；ArduPilot：`MAV_CMD_DO_SET_SERVO`。`index` 落在电机通道时按 `motor` 等级处理 |
| `POST /vehicles/{id}/rc` | 控制者 | 单帧通道覆盖，与杆量相同的 0.8 s 失效规则：`{channels?: {"1": 1000..2000, …"18"}, axes?: {roll, pitch, yaw, throttle, aux1..aux6}}`。ArduPilot 发 `RC_CHANNELS_OVERRIDE`；PX4 发 `MANUAL_CONTROL`（aux 用扩展字段）。与 `sticks` 互斥：最后一次写入者生效 |
| `POST /vehicles/{id}/message-interval` | 控制者或操作员 | `{message: "ATTITUDE", hz: 0..50}`（0 = 停止） |
| `POST /vehicles/{id}/request-message` | 控制者或操作员 | `{message}`，`MAV_CMD_REQUEST_MESSAGE` |
| `POST /vehicles/{id}/reboot` | 控制者 | `{confirm: "REBOOT"}`，上锁 |
| `POST /vehicles/{id}/calibrate` | 控制者 | `{sensor: "gyro" \| "level" \| "baro" \| "accel_simple", confirm}`，上锁；`MAV_CMD_PREFLIGHT_CALIBRATION` |
| `POST /vehicles/{id}/home` | 控制者 | `{here: true}` 或 `{lat, lon, alt}`，`MAV_CMD_DO_SET_HOME` |
| `POST /vehicles/{id}/terminate` | 控制者 | `{confirm: "TERMINATE"}`，`MAV_CMD_DO_FLIGHTTERMINATION` 后强制上锁 |
| `POST /vehicles/{id}/arm` | 控制者 | 新增 `force: true`（确认词 `FORCE`，`param2 = 21196`） |

### 2.1 原始命令目录

`relay/device.py` 中的常量表，每项 `{id, name, label_en, label_zh, params: [{index, name, unit, min?, max?}], danger: null | "motor" | "reboot" | "calibrate" | "terminate" | "arm", needs_disarmed: bool, autopilots: ["PX4","ArduPilot"]}`。至少包含：`COMPONENT_ARM_DISARM`、`DO_SET_MODE`、`NAV_TAKEOFF`、`NAV_LAND`、`NAV_RETURN_TO_LAUNCH`、`NAV_LOITER_UNLIM`、`DO_CHANGE_SPEED`、`CONDITION_YAW`、`DO_REPOSITION`、`DO_SET_HOME`、`DO_SET_SERVO`、`DO_REPEAT_SERVO`、`DO_SET_RELAY`、`DO_MOTOR_TEST`、`ACTUATOR_TEST`、`DO_FLIGHTTERMINATION`、`PREFLIGHT_CALIBRATION`、`PREFLIGHT_REBOOT_SHUTDOWN`、`SET_MESSAGE_INTERVAL`、`REQUEST_MESSAGE`、`DO_SET_ROI_LOCATION`、`DO_SET_ROI_NONE`、`MISSION_START`、`DO_PAUSE_CONTINUE`、`DO_GRIPPER`、`DO_DIGICAM_CONTROL`、`IMAGE_START_CAPTURE`、`VIDEO_START_CAPTURE`、`VIDEO_STOP_CAPTURE`。

### 2.2 新技能（`skills.py`，复用技能执行器、接管、事件与动作序列）

| 技能 | 参数 | 要求 | 实现 |
|---|---|---|---|
| `sticks` | `roll, pitch, yaw ∈ [-1,1], throttle ∈ [0,1], seconds ∈ (0,10]` | manual，已解锁 | 按 50 Hz 输出给定杆量，到时回中（定高模式下油门回 0.5，否则保持最后油门）后完成。给只能按回合思考的智能体提供"推杆 N 秒" |
| `attitude` | `roll_deg, pitch_deg ∈ [-30,30], yaw_rate_dps ∈ [-90,90], thrust ∈ [0,1], seconds ∈ (0,10]` | 已解锁、在空中 | ArduPilot：`GUIDED_NOGPS` + `SET_ATTITUDE_TARGET` 20 Hz；PX4：`OFFBOARD` + `SET_ATTITUDE_TARGET`（先推 1 s 设定点再切模式）；模拟机：同 ArduPilot。结束后切回定高模式悬停 |
| `velocity` | `vx, vy, vz`（ENU，m/s，各 ≤ 2）、`yaw_rate_dps?`、`seconds ∈ (0,10]` | position | `SET_POSITION_TARGET_LOCAL_NED` 速度掩码 10 Hz；受安全区约束（越界前停止并失败 `outside_safety_volume`） |
| `replay` | 内部使用（§4） | — | 按原时间轴回放记录的杆量 |

动作序列（plans）的动作集合增加 `sticks`、`attitude`、`velocity`。

## 3. 应用级接口（后端 `relay/app_bridge.py`、`relay/api/routes_app.py`、`relay/api/schemas_app.py`；渲染进程 `web/src/appctl/`）

### 3.1 通道

渲染进程（操作员 WebSocket）与后端之间新增三种消息：

- 客户端 → 服务端 `{"type": "ui_state", "data": UiState}`：界面状态变化时发送（≤ 5 Hz，另每 5 s 一次心跳）。
- 服务端 → 客户端 `{"type": "ui_command", "id": "<uuid>", "action": "<name>", "args": {...}, "by": "<会话名或 operator>"}`：只发给最近一个发送过 `ui_state` 的操作员连接。
- 客户端 → 服务端 `{"type": "ui_result", "id": "<uuid>", "ok": bool, "error"?: "<code>", "data"?: {...}}`。

`UiState`：`{lang: "en"|"zh", workspace: "fly"|"agents"|"records", selected: id|null, view: "overview"|"chase"|"top", inspector: {open: bool, tab}, dialog: null|"settings"|"connect"|"palette"|..., settings_section, copilot_open: bool, prefs: {units, startView, chaseOnCourse, padDeadzone, padExpo}, gamepads: [{index, id, name, mapping, connected, vehicle: id|null, input: "gamepad"}], possession: [{vehicle, input, pad}], window: {focused, width, height, fullscreen}, record: {session_id, playing, t}|null, version}`。

### 3.2 REST

| 方法与路径 | 权限 | 说明 |
|---|---|---|
| `GET /app` | – | `{version, uptime_s, platform: {os, arch, python}, http_port, listen, scan, counts: {vehicles, real, sims, sessions, gamepads, alerts}, ui: {connected: bool, age_s, state: UiState \| null}, agents_app_control: bool}` |
| `GET /app/ui` | – | `UiState` 与 `age_s`；无窗口时 `state: null` |
| `GET /app/gamepads` | – | `UiState.gamepads` |
| `GET /app/actions` | – | 界面动作目录（名称、参数、说明） |
| `POST /app/ui` | 操作员；或会话且设置 `agents_app_control = true`（缺省 true，否则 403 `app_control_disabled`） | `{action, args}` → 经通道转发，最多等待 5 s：`{ok: true, data}`；无窗口 503 `ui_unavailable`；超时 504 `ui_timeout`；动作失败 409 `ui_action_failed`（`detail.error`） |

界面动作（渲染进程实现，`web/src/appctl/actions.ts`）：

| 动作 | 参数 | 行为 |
|---|---|---|
| `get_state` | – | 返回当前 `UiState` |
| `set_language` | `{lang}` | 切换语言（原生菜单同步） |
| `set_workspace` | `{workspace}` | 切换工作区 |
| `select_vehicle` | `{vehicle \| null}` | 选中飞机 |
| `set_view` | `{view}` | 舞台视角 |
| `open_inspector` / `close_inspector` | `{tab?}` | 检查器与页签 |
| `open_settings` | `{section?}` | 打开设置 |
| `open_connect` | `{tab?: "scan"\|"address"\|"bridge"}` | 打开接入对话框 |
| `close_dialogs` | – | 关闭所有对话框与面板 |
| `set_pref` | `{key, value}` | 修改界面偏好（单位、起始视角、手柄死区、Expo、穿环时自动追尾） |
| `notify` | `{text, level?: "info"\|"warn"}` | 在界面显示提示 |
| `bind_gamepad` / `unbind_gamepad` | `{index, vehicle?}` | 手柄与飞机绑定 |
| `possess` / `release` | `{vehicle, input?: "keyboard"\|"screen"\|"gamepad", pad?}` | 以操作员身份附身 / 释放（走界面正常流程） |
| `open_record` | `{session_id}` | 打开记录回看 |
| `replay` | `{action: "play"\|"pause"\|"seek", t?}` | 控制回看 |
| `toggle_copilot` | `{open?}` | 显示 / 隐藏 Copilot 窗口 |
| `window` | `{action: "focus"\|"minimize"\|"maximize"\|"restore"\|"fullscreen"}` | 主窗口 |
| `screenshot` | `{max_width?: ≤1920}` | 主窗口截图，`data: {png_base64, width, height}`（主进程 `capturePage`） |

后端已有接口（扫描、连接、移除、模拟机、设置、告警、全部急停、网络、记录、航线、配网）由应用级 MCP 直接封装，无需新路径；`PUT /settings` 增加 `agents_app_control`。

这些写操作属于"应用操作"权限级别（依赖 `require_app_control`）：操作员，或 `agents_app_control` 开启时的任意会话；关闭时会话得到 403 `app_control_disabled`。`agents_app_control` 本身只能由操作员修改，会话提交与当前值不同的取值得到 403 `operator_required`（原样回写当前值不报错，便于整体读改写）。REST 附身与释放、杆量方向与桨叶确认仍为操作员专属；写参数（`POST /vehicles/{id}/params`）改为控制者级别：认领该飞机的会话或操作员。

事件：`app_command`（`data: {action, args, by}`），`refly_started`、`refly_finished`、`refly_failed`。

## 4. 再飞（`relay/refly.py`）

- 记录新增列 `s_roll, s_pitch, s_yaw, s_throttle, s_source`（当前发出的杆量与来源；旧库启动时 `ALTER TABLE` 补列），采样率不变（5 Hz）。
- `GET /history/sessions/{sid}/refly` → `{plan: {steps} | null, path: {waypoints, duration_s} | null, sticks: {duration_s, mode} | null, recommended: "plan"|"path"|"sticks"|null}`：
  - `plan`：该记录内的 `plan_started` 步骤，或按时间串起的 `skill_started`（名称与参数）；
  - `path`：有本地位置的样本，按 0.3 m 间隔抽成 `goto` 航点（相对首点，平移到目标飞机的当前原点）；
  - `sticks`：有杆量列的样本时间段（从解锁到上锁）。
- `POST /vehicles/{id}/refly` `{session_id, mode?: "auto"|"plan"|"path"|"sticks", speed?: 0.25..2（仅 sticks）, confirm?}`：`plan` 与 `path` 生成动作序列执行（返回 `{plan}`）；`sticks` 先切到记录中的模式，再以 `replay` 技能按原时间轴（乘以 speed）输出杆量（返回 `{skill}`）。真机从地面开始需 `confirm: "ARM"`；任何拨杆接管照常生效。

## 5. 三套 MCP 服务（`mcp/`，均仅用标准库，共享 `mcp/relay_mcp_common.py`）

| 服务 | 文件 | 名称 | 内容 |
|---|---|---|---|
| 飞行 | `anet_relay_mcp.py`（保留） | `anet-relay` | 现有 18 个工具 + `relay_sticks_for`、`relay_refly` |
| 设备 | `anet_relay_device_mcp.py` | `anet-relay-device` | 设备概览、消息（列表 / 单条）、参数（单个 / 按名读取 / 全表 / 设置 / 刷新）、命令目录与原始命令、电机测试 / 停止、执行器、RC 通道、`sticks` / `attitude` / `velocity` 技能、消息频率、请求消息、重启、校准、设置 home、飞行终止、解锁（含强制）/ 上锁、模式（含可用模式列表） |
| 应用 | `anet_relay_app_mcp.py` | `anet-relay-app` | 应用状态、界面状态、手柄、全部界面动作（每个动作一个工具，如 `app_set_language`）、截图（以 MCP image content 返回）、扫描网络、按地址连接、移除飞机、添加 / 移除 / 重置模拟机、设置读写、告警与确认、全部急停、网络状态、记录列表 / 详情 / 导出、打开记录与回看控制、再飞选项、航线列表 |

工具命名：飞行 `relay_*`，设备 `device_*`，应用 `app_*`。每个服务启动时打开自己的会话（`--name`，缺省 `mcp`、`mcp-device`、`mcp-app`）。

## 6. Copilot（v0.2.1）

### 6.1 浮动窗口

- 独立 `BrowserWindow`（`electron/copilot/window.mjs`）：无边框、可拖动（顶部标题栏为拖动区）、可改变大小（最小 340 × 420）、`alwaysOnTop`（macOS 层级 `floating`）、`setVisibleOnAllWorkspaces(true, {visibleOnFullScreen: true})`、`skipTaskbar`。不是模态窗口：用户可以同时点击主窗口和系统中的任何其他应用。位置与大小保存在 `userData/copilot-window.json`，缺省停靠在主显示器右下；可以拖到屏幕任意位置（包括主窗口左侧）。
- 加载同一前端，`?window=copilot`，只渲染 Copilot 面板（`web/src/copilot/CopilotWindow.tsx`）。主窗口内原有的停靠面板移除；顶栏按钮与 ⌘J / Ctrl+J 显示或隐藏该窗口（IPC `relay:copilot-window`，参数 `show | hide | toggle`），关闭按钮只隐藏。
- 有待审批的指令时，窗口显示并闪烁标题栏一次；macOS 上 `app.dock.bounce('informational')`。
- 窗口不透明度可在 设置 → Copilot 中调整（60–100%）。

### 6.2 工具

Copilot 进程内实现三套工具，名称与 MCP 一致（`relay_*`、`device_*`、`app_*`），经 REST 调用。设置 → Copilot 中可选"工具范围"：飞行 / 飞行 + 设备 / 全部（缺省：全部）。每次提问前读取 `GET /app/ui` 获取当前选中的飞机与界面语言作为上下文。

审批：目标为真机时，所有运动类工具和危险等级非空的设备工具（电机、执行器的电机通道、原始危险命令、重启、校准、终止、强制解锁、RC 通道覆盖、`sticks` / `attitude` / `velocity`、再飞）在执行前需要操作员在聊天中批准。界面动作不需审批。

## 7. 测试

- 后端：每个设备级接口在 PX4 与 ArduPilot 模拟机上的效果与确认词规则；`attitude` 技能在无位置模拟机上改变姿态；`velocity` 在有位置模拟机上移动；`ui_command` 往返（用伪造的 WebSocket 客户端）；再飞三种模式（先飞一段记录，再对新模拟机再飞）。
- MCP：三套服务 `initialize → tools/list → tools/call` 冒烟测试。
- 前端：界面动作单元测试（store 变化）；e2e：外部会话通过 `POST /app/ui` 切换中文、选中飞机、截图；Copilot 窗口独立显示、拖动后位置保存、主窗口仍可点击。
