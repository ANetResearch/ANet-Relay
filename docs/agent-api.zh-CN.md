# ANet Relay 智能体接口

[English](agent-api.md) · 简体中文

ANet Relay 把它所管理的机群开放给外部程序：脚本、自主智能体与大语言模型。本文说明控制模型、REST 与 WebSocket 接口（飞行、设备级、应用控制三个层面）、Python SDK 与三套 MCP 服务。每个接口与数据模型的完整参考由运行中的后端生成，地址为 `http://127.0.0.1:8765/docs`（OpenAPI：`/openapi.json`）。

> **安全提示**：针对模拟机编写的代码可以不加修改地用于真机，区别只在飞机 ID。真机只能在四周有阻拦网、地面铺有缓冲垫的场地内飞行，且 Relay 窗口前必须有操作员，随时可以附身接管或急停。

## 1. 快速开始（无需硬件）

1. 启动 Relay，在桌面应用中点击机群栏底部的 **Add sim** 添加模拟机。从源码运行时也可以只启动后端：`.venv/bin/python -m relay --dev`（操作员令牌只在 stderr 打印一次）。
2. 用 Python SDK 驾驶模拟机（只用标准库，Python 3.10 及以上）：

```python
import sys
sys.path.insert(0, "sdk/python")            # 或把 anet_relay_client.py 复制到脚本旁边
from anet_relay_client import RelayClient

with RelayClient() as relay:                # 缺省 http://127.0.0.1:8765，或环境变量 $RELAY_API
    relay.open_session("my-agent")          # 租约在后台自动续期
    relay.claim("sim-1")
    relay.takeoff("sim-1", alt=1.2)         # 阻塞到技能完成
    relay.goto("sim-1", 0.5, 0.5, 1.2)
    relay.land("sim-1")
```

3. 或者通过 MCP 接入大语言模型（第 8 节）：飞行、设备级、应用控制各一套服务。

`sdk/python/examples/` 下的六个示例都在模拟机上运行：

| 示例 | 内容 |
|---|---|
| `hover_sim.py` | 认领 → 解锁 → 起飞到 1.2 m → 悬停 8 s → 降落 → 释放 |
| `watch_handback.py` | 让 sim-1 绕圈飞行；操作员附身后暂停；收到 `relay_handback` 后从当前位置继续 |
| `course_sim.py` | 用 `goto` 逐环飞完 `cage-loop` 穿环航线并打印成绩 |
| `swarm_square.py` | 四架模拟机同时起飞，同步做方阵旋转，然后一起降落 |
| `bench_routine.py` | 以一个动作序列完成起飞、悬停、前进 1 m、后退 1 m、转向 45°、降落 |
| `device_tour.py` | 设备总览、原始指令目录、单电机测试与 `attitude` 姿态保持 |

每个示例都接受 `--base` 与 `--name`。提供 `--operator-token`（或环境变量 `$RELAY_OPERATOR_TOKEN`）时，示例会自行补齐缺少的模拟机；否则请在应用中通过 **Add sim** 添加。

## 2. 控制模型

Relay 的核心是人与智能体之间的控制权接力。每架飞机同一时刻至多有一个控制者。

| 术语 | 含义 |
|---|---|
| **会话 Session** | 智能体的身份。`POST /sessions {name}` 返回令牌与租约（15 s）。任何已鉴权的调用都会续租，`POST /sessions/heartbeat` 可显式续租。会话到期后失去全部认领，其杆量流在 0.2 s 内停止。 |
| **认领 Claim** | 会话控制某架飞机的权利（`POST /vehicles/{id}/claim`）。每架飞机只有一个认领；认领另一活动会话持有的飞机返回 `claimed_by_other`。 |
| **附身 Possession** | 操作员在 Relay 窗口中用键盘、屏幕摇杆或手柄驾驶飞机。附身永远优先：它停止智能体的技能与杆量，并**挂起**认领。挂起期间所有控制调用返回 `possessed_by_human` 或 `claim_suspended`。 |
| **交还 Hand-back** | 操作员释放时，若挂起的认领仍然有效，则恢复生效，Relay 发出事件 `relay_handback`。此刻 Relay 不向飞机发送任何指令，下一步由智能体决定。 |
| **急停 Stop** | `POST /vehicles/{id}/stop`：油门归零 50 ms，然后请求上锁。急停不改变控制权归属。 |

每架飞机的 `controller` 字段取值为 `none`、`human` 或 `agent`。

因此，智能体应把 `possessed_by_human` 与 `claim_suspended` 视为"暂停"，在事件中等待 `relay_handback`，重新读取飞机状态后再继续。`watch_handback.py` 演示了这一模式。

### 控制层级与能力

| 层级 | 接口 | 所需能力 |
|---|---|---|
| 1 | `sticks`（roll、pitch、yaw、throttle） | `manual`（有飞控心跳） |
| 2 | `arm`、`mode`，技能 `takeoff`、`land`、`hold`、`move`、`yaw`；动作序列 | 起飞、悬停、平移、转向需要 `althold` |
| 3 | 技能 `goto`、`rtl`，穿环航线；闭环 `move` | `position`（新鲜的本地位置估计） |

每架飞机在快照中报告 `capabilities: {manual, althold, position}`。缺少能力时返回 `capability_missing` 或 `no_position_estimate`。模拟机三者齐备；真机只有接入光流、动作捕捉或 UWB 等本地定位源时才具备 `position`。

## 3. 坐标系与单位

- **位置**：米，东北天（ENU）：`x` 向东、`y` 向北、`z` 向上。所有模拟机共用坐标系 `sim`（各自出生在不同网格点）。每架真机报告自己的 `local` 坐标系，原点是飞控初始化估计器的位置。
- **姿态**：弧度；roll 右翼下沉为正，pitch 抬头为正，yaw 为 NED 航向（0 为正北，顺时针为正）。`heading_deg` 为角度。
- **杆量**：roll 向右为正、pitch 向前为正、yaw 顺时针为正，范围 −1..1；throttle 0..1。定高类模式下 0.5 保持高度。
- **goto 的 yaw 与环的朝向**：罗盘角度（0 为正北，顺时针）。
- **安全区**：一个长方体，缺省 3 × 3 × 2 m（见机群快照的 `safety_volume`），水平方向以飞机原点为中心，底面在原点高度。模拟机的原点是出生点，真机的原点是首次解锁点。超出安全区的 `goto` 目标返回 `outside_safety_volume`；穿环航线按安全区缩放。

## 4. 鉴权

| 调用方 | 请求头 | WebSocket 查询参数 |
|---|---|---|
| 智能体会话 | `X-Relay-Session: <token>` | `?session=<token>` |
| 操作员（Relay 窗口） | `X-Relay-Operator: <token>` | `?operator=<token>` |

只读接口无需令牌。API 只绑定 127.0.0.1，并且只响应发往 `127.0.0.1` 或 `localhost` 的请求。写操作分三个权限级别：

| 级别 | 调用方 | 包含 |
|---|---|---|
| 控制者 | 认领了该飞机的会话；操作员 | 杆量、解锁、模式、技能、动作序列、再飞、穿环航线、急停、设备级指令（电机、执行器、遥控覆盖、原始指令、重启、校准、终止）、写参数 |
| 应用操作 | 操作员；设置项 `agents_app_control` 开启时（缺省开启）的任意会话 | 扫描网络、按地址连接、移除飞机、模拟机增删与复位、全部急停、数传重试与配网、告警确认、航线编辑、修改设置、界面动作（`POST /app/ui`） |
| 操作员专属 | 仅 Relay 窗口 | REST 附身与释放、杆量方向与桨叶确认、授予或收回 `agents_app_control` |

`agents_app_control` 关闭时，会话发起的应用操作返回 403 `app_control_disabled`；会话试图修改该设置项返回 403 `operator_required`。会话发起的每个界面动作都会在窗口中显示通知并记录事件 `app_command`。

**限速**（每会话）：控制类调用（杆量、解锁、模式、技能）每秒 60 次，其他调用每秒 20 次。超出时返回 429 `rate_limited`，并带 `Retry-After: 1`。

## 5. 面向智能体的 REST 接口

路径均以 `/api/v1` 开头，请求体为 JSON。

| 方法与路径 | 鉴权 | 用途 |
|---|---|---|
| `GET /health` | – | `{ok, version, uptime_s}` |
| `GET /capabilities` | – | 坐标系、门限、技能及参数、模式名称、确认词 |
| `GET /fleet` | – | 机群快照：飞机、会话、告警、安全区 |
| `GET /vehicles/{id}` | – | 单机状态，另含 `detail`（home、安全区原点、单节电压、起飞前检查项）。`health` 为飞行健康诊断（见第 5 节） |
| `GET /vehicles/{id}/preflight` | – | 起飞前检查项 |
| `GET /topology` | – | 地面站、飞机、数传、会话、链路与相对距离 |
| `GET /events?after=&limit=&vehicle=&tail=` | – | `seq > after` 的事件，按时间正序；`tail=true` 返回最新的 `limit` 条 |
| `GET /courses` | – | 预设与自定义穿环航线（已按安全区缩放） |
| `POST /sessions` | – | `{name}` → `{session_id, name, token, lease_s}` |
| `POST /sessions/heartbeat` | 会话 | 续租 |
| `DELETE /sessions` | 会话 | 关闭会话并释放全部认领 |
| `GET /sessions` | – | 当前会话（不含令牌）与最近调用 |
| `POST /vehicles/{id}/claim` | 会话 | 认领 |
| `POST /vehicles/{id}/unclaim` | 会话 | 释放认领 |
| `POST /vehicles/{id}/sticks` | 控制者 | `{roll, pitch, yaw, throttle}`；以 10–50 Hz 重复发送 |
| `POST /vehicles/{id}/arm` | 控制者 | `{arm: true}`（真机：`{arm: true, confirm: "ARM"}`）或 `{arm: false}` |
| `POST /vehicles/{id}/mode` | 控制者 | `{name}`，例如 `LOITER`、`ALT_HOLD`、`POSCTL` |
| `POST /vehicles/{id}/skills/{name}` | 控制者 | `takeoff {alt}`、`land`、`hold {seconds?}`、`move {forward?, right?, up?}`、`yaw {deg}`、`rtl`、`goto {x, y, z, yaw?}` |
| `POST /vehicles/{id}/plan` | 控制者 | `{steps, name?, confirm?}`：执行动作序列（见下文） |
| `POST /vehicles/{id}/plan/cancel` | 控制者 | 取消正在执行的序列；空中的飞机原地悬停 |
| `GET /history/sessions/{sid}/refly` | – | 某次飞行记录可用于再飞的来源：`plan`、`path`、`sticks` 与 `recommended` |
| `POST /vehicles/{id}/refly` | 控制者 | `{session_id, mode?, speed?, confirm?}`：按记录再飞一次（见下文） |
| `POST /vehicles/{id}/course/start` | 控制者 | `{course_id}`（缺省 `cage-loop`） |
| `POST /vehicles/{id}/course/stop` | 控制者 | 中止航线 |
| `POST /vehicles/{id}/stop` | 控制者 | 急停 |

### 技能

技能调用立即返回 `{"skill": {"id", "name", "state": "running", "detail"}}`。之后飞机的 `skill` 字段以同一 `id` 报告 `state`：`running`、`done` 或 `failed`；事件 `skill_started`、`skill_finished`、`skill_failed` 的 `data.skill_id` 与之对应。启动新技能会替换正在运行的技能。SDK 会按 id 代为等待。

| 技能 | 所需能力 | 行为 |
|---|---|---|
| `takeoff {alt}` | althold | 模拟机按需自动解锁，爬升到起飞点上方 `alt` 米（0.2 m 至安全区高度）并保持。有位置估计时使用飞控自身的起飞（GUIDED / OFFBOARD）；没有位置估计时在 ALT_HOLD / ALTCTL 下以油门杆爬升，并按气压高度改平。地面上的真机须先解锁，或在调用中携带 `confirm: "ARM"` 并通过起飞前检查。已在空中时立即完成。 |
| `land` | 任意 | 原地降落；飞机落地并上锁后完成。 |
| `hold {seconds?}` | althold | 保持位置（LOITER / POSCTL）或高度（ALT_HOLD / ALTCTL）。带 `seconds` 时，到时报告 `done` 并继续保持。 |
| `move {forward?, right?, up?}` | althold，须在空中 | 机体坐标系下的米数（负值为后、左、下）。有位置估计时：闭环 `goto` 到目标点。没有位置估计时：**开环**，按定时倾斜并推算距离（误差约 ±30%），每步水平距离不超过 2 m；`up` 使用气压高度。 |
| `yaw {deg}` | althold，须在空中 | 按姿态航向转动 `deg` 度，顺时针为正；误差 3° 以内完成。 |
| `rtl` | position | 返回起飞点并降落。 |
| `goto {x, y, z, yaw?}` | position | 飞往飞机位置坐标系中的目标点；距离 0.15 m 以内即完成。 |
| `sticks {roll, pitch, yaw, throttle, seconds}` | manual，须已解锁 | 在手动类模式下保持一组杆量 `seconds` 秒（0.05–10 s），然后回中。适合大语言模型发出短促的手动输入。 |
| `attitude {roll_deg, pitch_deg, yaw_rate_dps, thrust, seconds}` | manual，须已解锁 | 以 `SET_ATTITUDE_TARGET` 保持姿态与总推力 `seconds` 秒（ArduPilot 自动切到 GUIDED_NOGPS，PX4 为 OFFBOARD）。 |
| `velocity {vx, vy, vz, yaw_rate_dps?, seconds}` | position | 以 ENU 速度（m/s）飞行 `seconds` 秒，然后悬停。 |

技能运行或保持期间，Relay 持续发送中位杆量（油门 0.5）作为遥控保活，使飞控的遥控丢失保护保持静默；这依赖的失控保护设置见 `hardware.zh-CN.md`。

### 动作序列（plan）

动作序列是最多 24 步的有序列表，每步一个技能，由 Relay 依次执行：

```json
POST /api/v1/vehicles/sim-1/plan
{"name": "bench", "steps": [
  {"action": "takeoff", "alt": 1.0},
  {"action": "hover", "seconds": 3},
  {"action": "move", "forward": 1},
  {"action": "move", "forward": -1},
  {"action": "yaw", "deg": 45},
  {"action": "land"}
]}
```

可用动作：`takeoff {alt}`（缺省 1 m）、`hover {seconds}` / `hold {seconds}`（缺省 3 s）、`move {forward, right, up}`、`yaw {deg}`、`land`、`goto {x, y, z, yaw?}`、`rtl`，以及 `sticks`、`attitude`、`velocity`（参数同上表）。序列开始前会校验每一步（`invalid_request` 指出出错的步骤与字段；缺少能力的步骤返回 `capability_missing`）。不以 `takeoff` 开头的序列要求飞机已在空中（`not_airborne`）。真机从地面开始的序列需要 `confirm: "ARM"`。

飞机的 `plan` 字段报告 `{id, name, state, index, count, steps, results, detail}`，`state` 为 `running`、`done`、`failed` 或 `cancelled`；事件 `plan_started`、`plan_step`、`plan_finished`、`plan_failed`、`plan_cancelled` 标记进度。任一步失败、新的技能、控制者发送杆量、操作员拨杆或接管、急停，都会结束序列。`POST /plan/cancel` 主动结束序列（没有序列时返回 `no_plan`），空中的飞机原地悬停。

`sdk/python/examples/bench_routine.py` 执行上面的序列：`python3 sdk/python/examples/bench_routine.py --vehicle sim-1`；真机加 `--arm-real`。

### 再飞（按历史飞行再飞一次）

每次飞行都会记录动作序列、轨迹与杆量（含来源：键盘、手柄、屏幕摇杆或智能体）。`GET /history/sessions/{sid}/refly` 列出该记录可用的来源，`POST /vehicles/{id}/refly` 在任意一架飞机上重新执行：

| `mode` | 行为 |
|---|---|
| `plan` | 重放记录中的动作序列，步骤与参数不变 |
| `path` | 把记录的轨迹每 0.3 m 取一个 `goto` 航点，平移到目标飞机当前位置后作为动作序列飞行；需要 `position` |
| `sticks` | 在原始时间轴上重放从解锁到上锁的杆量与模式切换，`speed`（0.25–2.0）缩放时间；只适用于手动类模式下飞行的记录 |
| `auto`（缺省） | 依次选择 `plan`、`path`、`sticks` 中第一个可用的来源 |

`plan` 与 `path` 返回 `{plan}`，`sticks` 以技能 `replay` 运行并返回 `{skill}`，之后按动作序列或技能的方式跟踪；事件 `refly_started`、`refly_finished`、`refly_failed` 标记过程。记录不存在时返回 `record_not_found`，记录中没有所需来源时返回 `refly_unavailable`。真机从地面开始需要 `confirm: "ARM"`；操作员接管、杆量输入或急停都会结束再飞。

### 设备级接口

设备级接口把飞控暴露到单个电机、输出通道与 MAVLink 消息。写入类调用都需要控制者权限（先认领）；会改变硬件状态的调用需要确认词：

| 确认词 | 用于 | 何时需要 |
|---|---|---|
| `MOTOR` | 电机测试、驱动电机输出的执行器指令 | 真机 |
| `ARM` / `FORCE` | 解锁 / 强制解锁（跳过 prearm） | 真机 |
| `REBOOT` | 重启飞控 | 真机 |
| `CALIBRATE` | 传感器校准 | 真机 |
| `TERMINATE` | 飞行终止 | 所有飞机，包括模拟机 |

电机测试、重启与校准要求飞机已上锁，否则返回 409 `must_be_disarmed`。

| 方法与路径 | 鉴权 | 用途 |
|---|---|---|
| `GET /vehicles/{id}/device` | – | 设备总览：电机数量、布局与各输出 PWM、舵机输出、遥控通道、机架类型、传感器健康、估计器状态、振动、home、参数表统计、各消息的接收频率 |
| `GET /vehicles/{id}/messages/{name}` | – | 某条 MAVLink 消息的最新一帧（字段、时间、频率），例如 `SERVO_OUTPUT_RAW`、`EKF_STATUS_REPORT` |
| `GET /vehicles/{id}/params/{name}` | – | 单个参数；参数表中没有时按名称向飞控读取（至多 2 s），读不到返回 `param_not_found` |
| `POST /vehicles/{id}/params/read` | 会话 | `{names}`（至多 32 个）按名称批量读取 → `{values, missing}` |
| `POST /vehicles/{id}/params` | 控制者 | `{name, value, confirm}`，`confirm` 须等于参数名 |
| `GET /commands` | – | 原始指令目录：MAV_CMD 名称与编号、中英文标签、参数含义与范围、危险级别、适用飞控 |
| `POST /vehicles/{id}/command` | 控制者 | `{command, params[≤7], frame?, use_int?, confirm?, timeout_s?}`；只接受目录中的指令（`command_not_allowed`），返回 `COMMAND_ACK`。`DO_MOTOR_TEST` 与 `ACTUATOR_TEST` 同样受电机测试的限制（0.2–5 s，真机不超过 30%） |
| `POST /vehicles/{id}/motors/test` | 控制者 | `{motor: 序号或 "all", throttle_pct, seconds(0.2–5), confirm}`；真机油门不超过 30% |
| `POST /vehicles/{id}/motors/stop` | 控制者 | 立即停止电机测试 |
| `POST /vehicles/{id}/actuators/set` | 控制者 | `{index 1–16, value −1..1 或 pwm 800–2200, seconds, confirm?}`；`seconds` 为 0 时保持到下次修改。输出 1 至电机数按电机处理：需要 `MOTOR`、已上锁，`seconds` 须为 0.2–5，真机不超过 30% |
| `POST /vehicles/{id}/rc` | 控制者 | `{channels: {"1".."18": 1000–2000}}` 或 `{axes}`：一帧遥控通道覆盖，0.8 s 后过期，与杆量互相替代 |
| `POST /vehicles/{id}/message-interval` | 控制者 | `{message, hz 0–50}`，0 停止该消息流 |
| `POST /vehicles/{id}/request-message` | 控制者 | `{message}`：请求飞控立即发送一次 |
| `POST /vehicles/{id}/reboot` | 控制者 | `{confirm}`：重启飞控，链路随后自动恢复 |
| `POST /vehicles/{id}/calibrate` | 控制者 | `{sensor: gyro | level | baro | accel_simple, confirm}` |
| `POST /vehicles/{id}/home` | 控制者 | `{here: true}` 或 `{lat, lon, alt}` |
| `POST /vehicles/{id}/terminate` | 控制者 | `{confirm: "TERMINATE"}`：电机立即停转并强制上锁，空中的飞机会坠落 |

模式列表与解锁、上锁、切模式沿用第 5 节的接口；`GUIDED_NOGPS` 在 ArduPilot 上可用。

### 应用控制

应用控制让智能体操作 Relay 本身：查看与切换窗口状态、管理机群与网络、修改设置。界面动作由打开的 Relay 窗口执行，因此需要窗口在运行；其余应用操作直接由后端完成。

| 方法与路径 | 鉴权 | 用途 |
|---|---|---|
| `GET /app` | – | 应用概况：版本、运行时长、端口、扫描状态，飞机、会话、手柄与告警数量，窗口是否连接，`agents_app_control` |
| `GET /app/ui` | – | 窗口当前状态：语言、工作区、选中的飞机、视图、检查器、对话框、偏好、手柄与绑定、附身、窗口尺寸、打开的记录 |
| `GET /app/gamepads` | – | 已接入的手柄及其绑定的飞机 |
| `GET /app/actions` | – | 界面动作目录及参数 |
| `POST /app/ui` | 应用操作 | `{action, args}`：执行一个界面动作，最多等待 5 s，返回 `{ok, data}` |

界面动作：`get_state`、`set_language {lang: en | zh}`、`set_workspace`、`select_vehicle {vehicle | null}`、`set_view`、`open_inspector`、`close_inspector`、`open_settings`、`open_connect`、`close_dialogs`、`set_pref`、`notify {text, level}`、`bind_gamepad`、`unbind_gamepad`、`possess`、`release`、`open_record`、`replay {play | pause | seek}`、`toggle_copilot`、`window {focus | minimize | maximize | restore | fullscreen}`、`screenshot {max_width}`。界面动作 `possess` 由窗口执行，效果等同操作员在窗口中附身：之后由人驾驶，智能体的认领被挂起。

窗口未连接返回 503 `ui_unavailable`，5 s 内无应答返回 504 `ui_timeout`，动作本身被拒绝返回 409 `ui_action_failed`。机群、网络、设置与告警等应用操作使用第 5 节与 OpenAPI 中已有的路径（`/discover`、`/vehicles`、`/sims`、`/stop-all`、`/network/retry`、`/bridges/{ip}`、`/alerts/{id}/ack`、`/courses/{id}`、`/settings`），权限按第 4 节的"应用操作"级别。

### 标定流程

标定流程在一架飞机上逐步执行校准与稳定性检查，并保存报告（S1 链路与固件、S2 电池静息、S3 参数基线、S4 传感器校准、S5 杆向、S6 电机顺序与转向、S7 地面振动、S8 装桨确认、S9 悬停测试、S10 阶跃响应、S11 悬停学习、S12 报告）。流程：`full_bench`（S1–S12）、`preflight_quick`（S1、S2、S7、悬停 10 秒的 S9、S12）、`motor_check`（S1、S6、S12）、`vibration_check`（S1、S7、悬停 20 秒的 S9、S12）与 `hover_tune`（S1、S8–S12）。流程只能在已上锁、位于地面的飞机上启动，并需要该飞机的控制权（认领，或操作员附身）。

| 方法与路径 | 鉴权 | 用途 |
|---|---|---|
| `GET /procedures` | – | 流程目录：`{procedures: [{id, title_en, title_zh, steps: [{id, title_en, title_zh, kind}], est_s}]}`；`kind` 为 `auto`、`operator`、`operator_device`、`device` 或 `flight` |
| `POST /vehicles/{id}/procedure` | 控制者 | `{procedure, options: {hover_s 5–60, skip: [步骤 id], auto_answer}}`；`auto_answer`（仅模拟机）由舵机输出自动回答 S6；S12 不可跳过 → 返回运行视图 |
| `GET /vehicles/{id}/procedure` | – | 当前运行，否则为最近保存的一次：`state`（`running`、`waiting`、`passed`、`warned`、`failed`、`aborted`）、`step`、`index`、`total`、`paused`、`reason`，以及每步的 `state`、`verdict`、`metrics`（值、单位、等级、窗口内 `mean` / `peak`、`warn` / `bad` 门限）、`findings`、`suggestions`、`prompt`、`live`（当前步骤的最近数据点，5 Hz） |
| `POST /vehicles/{id}/procedure/answer` | 控制者或操作员 | `{step, answer, confirm?}`：回答等待中的提示；`answer` 取自 `prompt.answers[].id` |
| `POST /vehicles/{id}/procedure/abort` | 控制者或操作员 | 中止：电机测试停止，空中的飞机降落 |
| `GET /test-runs?vehicle=&limit=` | – | 已保存的报告，按时间倒序，含判定计数；`vehicle` 匹配飞机 id 或地址 |
| `GET /test-runs/{run_id}` | – | 单份报告，含全部步骤、参数基线与修改记录 |
| `GET /test-runs/{run_id}/export?fmt=md\|json` | – | 以 Markdown（中英文表格，UTC 时间）或 JSON 下载报告 |

等待中的步骤带有 `prompt`：`{id, text_en, text_zh, detail_en, detail_zh, answers, confirm_word, operator_only, data}`。真机上 `confirm_word` 沿用设备级规则：S6、S7 转动电机前为 `MOTOR`（已拆桨、油门不超过 30 %、不超过 5 秒、已上锁；一次确认覆盖该步骤的全部电机），S4 每项校准为 `CALIBRATE`，S9 起飞前为 `ARM`，S11 写入参数前为该参数名（写入后回读校验）。`operator_only: true` 的提示（真机上的 S5、S6、S8 与 S7 拆桨确认）拒绝会话，返回 403 `operator_required`：必须由人在 Relay 窗口确认。模拟机上智能体可以回答全部提示。

流程由机群主循环推进，而不是由调用方推进：轮询 `GET .../procedure`（或订阅事件），在 `state` 为 `waiting` 时回答。以下情况流程中止：急停（急停优先，之后不再由流程降落）、链路丢失、坠毁、失去认领，以及在飞行步骤之外解锁或上锁。会话启动的流程在有人附身期间暂停，控制权交还且飞机上锁后从当前步骤开头继续。飞行步骤起飞至 0.6 m、保持在安全区内并自行降落；S10 在定高模式下对横滚、俯仰、偏航施加短时阶跃杆量。建议的参数修改（S10 速率环增益、S11 悬停油门）从不自动写入：S11 逐项询问。

### 飞行健康

每架飞机带有 `health`：最近 5 秒的滚动诊断，在检查器 概览 → 飞行健康 中显示。`state` 为 `ok`、`warn`、`bad` 或 `idle`（未解锁）；`items` 列出 `{key, level, value, unit, detail}`，包括 `oscillation`（横滚、俯仰角速度均方根，°/s）、`lean`（摇杆回中时保持的倾角）、`yaw_drift`、`alt_hold`（定高时垂直速度离散度）、`hover_throttle`、`motors`（各电机平均输出差、触顶比例）、`vibration`（峰值、加速度计削波）、`battery_sag`（每节压降）、`link` 和 `stick_stream`（手动飞行时 Relay 窗口发出的杆量更新）。飞行类项目在空中 1.5 秒后才评估，此前 `level` 为 `na`。各项含义与调整方法见 [`flight-tuning.zh-CN.md`](flight-tuning.zh-CN.md)。

### 杆量

超过 0.8 s 未刷新的杆量帧停止发送，飞机不会按陈旧输入飞行。连续控制请以 10–50 Hz 通过 REST，或更好地通过 WebSocket 发送（`{"type": "sticks", "vehicle", "roll", "pitch", "yaw", "throttle"}`）。关闭 WebSocket 即停止它所驱动的全部杆量流。MCP 不提供连续的杆量流：大语言模型无法闭合 50 Hz 的控制环；`relay_sticks_for` 与技能 `sticks` 只把一组杆量保持至多 10 s 后回中。

### 穿环航线

`course/start` 把航线放在飞机原点并返回，其中 `rings` 已换算到飞机坐标系：`c`（环心 `[x, y, z]`）、`yaw_deg`（环法向的罗盘朝向）与 `r`（半径）。计时从起飞开始。飞机按顺序在半径以内穿过环平面即计为通过。飞机的 `course` 字段报告 `next`、`total`、`splits`、`elapsed_s` 与 `best_s`；事件 `ring_passed` 与 `course_finished` 标记进度。`course_sim.py` 沿每个环的法向，在环前 0.35 m 与环后 0.35 m 各设一个航点。

## 6. WebSocket `/api/v1/stream`

服务端 → 客户端：

```json
{"type": "hello", "version": "0.1.0", "operator": false, "session": "s-1a2b3c4d"}
{"type": "fleet", "data": { "...机群快照..." }}
{"type": "event", "data": {"seq": 812, "kind": "ring_passed", "vehicle": "sim-1", "...": "..."}}
{"type": "error", "error": {"code": "not_claimed", "message_en": "...", "message_zh": "...", "detail": {}}, "ref": "sim-1"}
```

`fleet` 以 10 Hz 推送，`event` 实时推送。客户端 → 服务端：`sticks`（见上）与 `{"type": "ping"}`（回复 `pong`）。令牌无效时以关闭码 4401 断开；浏览器来源不被允许时以 4403 断开。

## 7. 事件与错误

事件结构为 `{seq, t, kind, vehicle, session, text_en, text_zh, data}`。智能体通常关注：

| kind | 时机 |
|---|---|
| `possession_started` / `possession_released` | 操作员接管或放手 |
| `relay_handback` | 控制权交还给挂起的认领（`data.session_id`） |
| `claim_started` / `claim_released` | 认领变化 |
| `agent_session_expired` | 租约到期 |
| `skill_started` / `skill_finished` / `skill_failed` | 技能进度，含 `data.skill_id` |
| `plan_started` / `plan_step` / `plan_finished` / `plan_failed` / `plan_cancelled` | 动作序列进度，含 `data.plan_id` |
| `armed` / `disarmed` / `mode_changed` | 飞控状态 |
| `link_delayed` / `link_lost` / `link_restored` | 链路质量；进入 `lost` 时 Relay 停止杆量与技能 |
| `battery_warn` / `battery_critical` / `crashed` | 风险 |
| `course_started` / `ring_passed` / `course_finished` / `course_aborted` | 穿环航线 |
| `stop` / `stop_all` | 急停 |
| `refly_started` / `refly_finished` / `refly_failed` | 再飞 |
| `device_command` / `motor_test` / `motor_test_stopped` / `actuator_set` / `rc_override` / `message_interval` / `home_set` / `rebooting` / `calibration_started` / `terminated` | 设备级操作 |
| `app_command` | 会话或操作员执行的界面动作（`data.action`、`data.by`） |
| `procedure_started` / `procedure_step` / `procedure_waiting` / `procedure_finished` / `procedure_aborted` | 标定流程，带 `data.run_id`、`data.step`、`data.verdict` 或 `data.reason` |

完整列表见 `web/src/api/schema.json`（`EventKind`）。错误使用统一结构，`code` 在各版本间保持稳定：

```json
{"error": {"code": "outside_safety_volume", "message_en": "The target is outside the safety volume.", "message_zh": "目标点超出安全区。", "detail": {"origin": [0, 0, 0], "volume": {"x": 3, "y": 3, "z": 2}}}}
```

| 状态码 | 智能体常见的错误码 |
|---|---|
| 400 | `invalid_request`、`confirm_required`、`outside_safety_volume`、`unknown_mode`、`command_not_allowed` |
| 401 | `auth_required`、`session_expired`（重新打开会话） |
| 403 | `operator_required`、`session_required`、`untrusted_host`、`app_control_disabled` |
| 404 | `vehicle_not_found`、`course_not_found`、`unknown_skill`、`param_not_found`、`record_not_found`、`procedure_unknown`、`procedure_not_found`、`test_run_not_found` |
| 409 | `not_claimed`、`claimed_by_other`、`possessed_by_human`、`claim_suspended`、`preflight_blocked`、`arm_rejected`、`mode_rejected`、`not_armed`、`crashed`、`capability_missing`、`no_position_estimate`、`not_airborne`、`no_plan`、`must_be_disarmed`、`message_not_seen`、`refly_unavailable`、`ui_action_failed`、`procedure_running`、`no_procedure`、`procedure_step_mismatch`、`procedure_not_waiting` |
| 429 | `rate_limited` |
| 503 | `link_not_ready`、`backend_busy`、`ui_unavailable` |
| 504 | `ui_timeout` |

## 8. MCP 服务

Relay 提供三套基于 stdio 的 Model Context Protocol 服务（JSON-RPC 2.0，只用标准库；支持协议版本 2024-11-05 至 2025-11-25），可以单独接入，也可以同时接入：

| 服务 | 文件 | 工具 | 面向 |
|---|---|---|---|
| `anet-relay` | `mcp/anet_relay_mcp.py` | 20 个 `relay_*` | 飞行：机群、认领、解锁、模式、技能、动作序列、短时杆量、再飞、穿环航线、急停 |
| `anet-relay-device` | `mcp/anet_relay_device_mcp.py` | 36 个 `device_*` | 设备级：设备总览、MAVLink 消息、参数读写、原始指令、单电机测试、执行器、遥控覆盖、消息频率、重启、校准、home、姿态与速度控制、飞行终止、标定流程与报告 |
| `anet-relay-app` | `mcp/anet_relay_app_mcp.py` | 41 个 `app_*` | 应用控制：应用与窗口状态、手柄、语言、工作区、选择飞机、检查器、对话框、截图、扫描网络、连接与移除飞机、模拟机、设置、告警、记录与导出、再飞来源、附身、Copilot 窗口、全部急停 |

每套服务在启动时打开一个智能体会话，名称取 `--name`（缺省分别为 `mcp`、`mcp-device`、`mcp-app`），自动续租，并在客户端断开时关闭会话；Relay 重启后，服务在下一次调用时重新打开会话，此时需要重新认领。认领属于会话：用某一套服务控制飞机时，请用同一套服务的认领工具（`relay_claim` 或 `device_claim`）。工具参数在发往 Relay 之前校验，缺少或多余的参数以 `invalid arguments` 返回。失败以 `isError: true` 的工具结果返回，并附错误码，模型可据此应对（例如遇到 `possessed_by_human` 时等待）。

应用控制服务的工具在 `agents_app_control` 开启时即可使用，不需要操作员令牌。`--operator-token`（或环境变量 `RELAY_OPERATOR_TOKEN`）只在需要以操作员身份调用时提供，一般不需要。

**Claude Desktop**（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "anet-relay": {
      "command": "python3",
      "args": ["/path/to/ANet-Relay/mcp/anet_relay_mcp.py", "--name", "claude-desktop"]
    },
    "anet-relay-device": {
      "command": "python3",
      "args": ["/path/to/ANet-Relay/mcp/anet_relay_device_mcp.py", "--name", "claude-desktop-device"]
    },
    "anet-relay-app": {
      "command": "python3",
      "args": ["/path/to/ANet-Relay/mcp/anet_relay_app_mcp.py", "--name", "claude-desktop-app"]
    }
  }
}
```

**Claude Code**：

```bash
claude mcp add anet-relay -- python3 /path/to/ANet-Relay/mcp/anet_relay_mcp.py --name claude-code
claude mcp add anet-relay-device -- python3 /path/to/ANet-Relay/mcp/anet_relay_device_mcp.py --name claude-code-device
claude mcp add anet-relay-app -- python3 /path/to/ANet-Relay/mcp/anet_relay_app_mcp.py --name claude-code-app
```

或项目级 `.mcp.json`：

```json
{
  "mcpServers": {
    "anet-relay": {"command": "python3", "args": ["mcp/anet_relay_mcp.py", "--name", "claude-code"]},
    "anet-relay-device": {"command": "python3", "args": ["mcp/anet_relay_device_mcp.py", "--name", "claude-code-device"]},
    "anet-relay-app": {"command": "python3", "args": ["mcp/anet_relay_app_mcp.py", "--name", "claude-code-app"]}
  }
}
```

Relay 不在 8765 端口运行时，追加 `"--base", "http://127.0.0.1:<port>"`。桌面应用从 8765 到 8775 选取第一个空闲端口，并写入其数据目录下的 `http-port` 文件。

## 9. Python SDK 参考

`sdk/python/anet_relay_client.py`，单文件，只用标准库。

| 调用 | 说明 |
|---|---|
| `RelayClient(base=DEFAULT_BASE, operator_token=None, timeout=15.0)` | `DEFAULT_BASE` 取 `$RELAY_API`，否则 `http://127.0.0.1:8765` |
| `open_session(name)` / `close()` | 会话与后台续租线程；也可作为上下文管理器 |
| `fleet()` · `vehicles()` · `vehicle(id)` · `topology()` · `courses()` · `capabilities()` · `health()` | 读取 |
| `events(after=0, limit=200, vehicle=None, tail=False)` · `last_seq()` · `wait_event(kind, vehicle, after, timeout, predicate)` | 事件轮询 |
| `stream(timeout=None)` | WebSocket 消息生成器（`hello`、`fleet`、`event`） |
| `claim(id)` · `unclaim(id)` | 控制权 |
| `sticks(id, roll, pitch, yaw, throttle)` · `arm(id, confirm=None)` · `disarm(id)` · `mode(id, name)` | 直接控制 |
| `takeoff(id, alt)` · `land(id)` · `hold(id, seconds=0)` · `rtl(id)` · `goto(id, x, y, z, yaw=None)` · `move(id, forward=0, right=0, up=0)` · `yaw(id, deg)` | 技能；`wait=True` 阻塞到完成，失败时抛出异常 |
| `plan(id, steps, name=None, confirm=None, wait=True)` · `cancel_plan(id)` | 动作序列；等待时若序列失败或被取消，抛出 `plan_failed` |
| `skill(id, name, params, wait)` · `wait_skill(id, skill_id)` | 通用技能调用与等待 |
| `course_start(id, course_id)` · `course_stop(id)` · `stop(id)` | 航线与急停 |
| `sticks_for(id, roll, pitch, yaw, throttle, seconds)` · `attitude(id, roll_deg, pitch_deg, yaw_rate_dps, thrust, seconds)` · `velocity(id, vx, vy, vz, yaw_rate_dps, seconds)` | 短时手动、姿态与速度控制（技能） |
| `refly_options(session_id)` · `refly(id, session_id, mode="auto", speed=None, confirm=None, wait=True)` | 再飞 |
| `device(id)` · `message(id, name)` · `param(id, name)` · `params_read(id, names)` · `set_param(id, name, value)` · `commands()` | 设备读取与写参数 |
| `command(id, command, params, frame, use_int, confirm, timeout_s)` · `motor_test(id, motor, throttle_pct, seconds, confirm)` · `motors_stop(id)` · `actuator(id, index, value, pwm, seconds, confirm)` · `rc(id, channels, axes)` | 原始指令、电机、执行器、遥控覆盖 |
| `message_interval(id, message, hz)` · `request_message(id, message)` · `reboot(id, confirm)` · `calibrate(id, sensor, confirm)` · `set_home(id, here, lat, lon, alt)` · `terminate(id, confirm)` | 消息频率、重启、校准、home、飞行终止 |
| `procedures()` · `procedure_start(id, procedure, hover_s, skip, auto_answer)` · `procedure(id)` · `procedure_answer(id, step, answer, confirm)` · `procedure_abort(id)` · `wait_procedure(id, timeout, waiting=True)` · `test_runs(id)` · `test_run(run_id)` · `test_run_export(run_id, fmt)` | 标定流程与报告 |
| `app()` · `ui_state()` · `gamepads()` · `app_actions()` · `ui(action, **args)` | 应用与窗口状态、界面动作 |
| `scan_network(networks)` · `connect_vehicle(host, port)` · `remove_vehicle(id)` · `add_sims(count, autopilot)` · `remove_sims(ids)` · `settings()` · `update_settings(**patch)` · `ack_alert(id)` · `stop_all()` | 应用操作：打开会话时以会话身份调用，否则使用操作员令牌 |

`sdk/python/examples/device_tour.py` 在模拟机上演示设备级接口：读取设备总览与原始指令目录，测试单个电机并读回输出 PWM，起飞后用 `attitude` 保持一个姿态，然后降落。

错误抛出 `RelayError`，带 `code`、`status`、`message`、`message_zh` 与 `detail`。等待技能时，技能失败（例如操作员接管）抛出 `skill_failed`，被其他技能替换抛出 `skill_superseded`。HTTP 429 会按 `Retry-After` 间隔自动重试两次。

## 10. 迁移到真机

1. 按 `hardware.zh-CN.md` 完成飞机与 Wi-Fi 数传的设置，确认它以 `real-<ip>` 出现在机群中，例如 `real-10-7-7-36`。
2. 在 Relay 窗口完成操作员步骤：无桨确认杆量方向，确认桨叶与防护网，通过全部阻断类起飞前检查。
3. 在代码中替换飞机 ID。解锁需要 `confirm: "ARM"`。位置类技能（`goto`、`rtl`、航线）需要本地定位源；没有定位源时，在定高类模式下使用 `takeoff`、`hold`、`yaw`、`land` 与开环 `move`，并预期飞机悬停时会缓慢漂移。
4. 窗口前始终保留操作员。附身与急停随时优先于任何智能体。

## 11. Copilot（内置智能体）

桌面应用内置 Copilot，一个基于 [pi](https://github.com/earendil-works/pi)（`@earendil-works/pi-agent-core`）的小型智能体。它运行在应用主进程中，像其他智能体一样使用本 API：打开名为 `copilot` 的会话，认领被要求操控的飞机，并出现在智能体工作区和接力时间线中。

- **浮动窗口：** Copilot 是一个独立的浮动窗口，始终位于其他应用之上，但不是模态窗口：显示期间仍可点击 Relay 主窗口和其他应用。拖动标题栏可放到屏幕任意位置（包括主窗口左侧），拖动边缘调整大小；位置与大小会被记住。顶栏的 Copilot 按钮、Ctrl/Cmd + J 或窗口自身的隐藏按钮可显示或隐藏它。每条消息会附带主窗口中当前选中的飞机。
- **配置：** 设置 → Copilot：接口类型（OpenAI 兼容端点选 `openai-completions`，另有 `anthropic-messages`、`openai-responses`）、Base URL、模型 ID、API Key、工具范围与窗口不透明度（60%–100%）。**测试连接** 会发送一次简短请求。Key 通过操作系统钥匙串（Electron `safeStorage`）加密保存在应用数据目录，不会回传到界面；端点只会收到对话内容和工具结果。
- **工具范围：** `flight`（飞行：状态、技能、动作序列、短时杆量、再飞、急停）、`flight+device`（另加设备级工具）、`all`（缺省，另加应用控制工具：语言、工作区、选择飞机、扫描网络、模拟机、设置、手柄、记录、窗口与截图）。工具与第 5 节的接口一一对应。
- **安全：** 对真机的每个运动指令与设备级写入（起飞、平移、转向、悬停、降落、动作序列、再飞、电机测试、执行器、重启、校准、终止等），聊天中都会出现审批卡片，操作员批准前不会发送任何指令（120 秒未处理视为拒绝）；需要审批时浮动窗口会自动显示并提示。确认词由审批代替，Copilot 不会自行填写。接管和急停对 Copilot 与其他智能体同样有效。
