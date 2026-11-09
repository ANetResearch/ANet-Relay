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

> 上面的高层技能需要智能体控制等级为 `full`（设置 → 安全，仅操作员可改）。在缺省等级 `sticks` 下，智能体只能通过摇杆与机载程序飞行：见[智能体控制等级](#智能体控制等级)与[程序工作流](#程序工作流)。

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

### 智能体控制等级

设置 `agent_flight_level`（设计 06 §5）决定智能体会话可以如何飞行，只有操作员可以修改（设置 → 安全，或携带操作员令牌调用 `PUT /settings`）。窗口中的操作员不受此等级限制。

| 等级 | 智能体会话可以 |
|---|---|
| `sticks`（缺省） | 推杆（WebSocket 流、`POST /vehicles/{id}/sticks`、`sticks` 技能）、解锁与上锁、切换到手动类模式（ArduPilot STABILIZE / ACRO，PX4 MANUAL / STABILIZED / ACRO；DRIFT 带速度阻尼辅助，不属于此类）、关闭模拟机的悬停辅助（不能开启）、射击、部署并运行机载程序，以及安全动作：急停与 `land`（其事件标注 `safety`） |
| `full` | 本文的全部接口，与 0.3 相同 |

在 `sticks` 等级下，技能 `takeoff`、`hold`、`move`、`yaw`、`goto`、`rtl`、`velocity`、`attitude`，包含这些动作的动作序列，以动作序列或轨迹方式再飞，定高、定点与自动类模式，设备级接口中的飞行类命令，以及 4 通道以上的 RC 覆盖，均返回 403 `sticks_only`（`detail.level = "sticks"`，`detail.action` 指明被拒绝的操作）。飞行 MCP 服务只列出仍可用的工具（21 个中的 13 个；每次 `tools/list` 都重新读取设置），Copilot 同样去掉这些工具，并提示模型编写程序。在这一等级下，稳定飞行由机载程序完成：智能体按自己的节奏修改程序、调整参数、查看结果，程序负责 50 Hz 的闭环。

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
| `POST /vehicles/{id}/plan` | 控制者 | `{steps, name?, confirm?, tuning?}`：执行动作序列（见下文） |
| `PATCH /vehicles/{id}/plan/tuning` | 控制者 | `{climb?, hover?}`：飞行中修改序列的油门参数 |
| `POST /vehicles/{id}/plan/cancel` | 控制者 | 取消正在执行的序列；空中的飞机原地悬停 |
| `GET /history/sessions/{sid}/refly` | – | 某次飞行记录可用于再飞的来源：`plan`、`path`、`sticks` 与 `recommended` |
| `POST /vehicles/{id}/refly` | 控制者 | `{session_id, mode?, speed?, confirm?}`：按记录再飞一次（见下文） |
| `POST /vehicles/{id}/course/start` | 控制者 | `{course_id}`（缺省 `cage-loop`） |
| `POST /vehicles/{id}/course/stop` | 控制者 | 中止航线 |
| `POST /vehicles/{id}/stop` | 控制者 | 急停 |
| `POST /sims/{id}/assist` | 控制者或操作员 | `{on}`：模拟机悬停辅助（见下）；真机返回 `sim_only` |
| `PUT /sims/{id}/airframe` | 控制者或操作员 | `{preset \| params, seed?}`：模拟机机体缺陷（见下） |
| `GET /sims/{id}/airframe` | 控制者或操作员 | 机体缺陷的全部数值、取值范围与悬停油门 |

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

飞机的 `plan` 字段报告 `{id, name, state, index, count, steps, results, detail, tuning}`，`state` 为 `running`、`done`、`failed` 或 `cancelled`；事件 `plan_started`、`plan_step`、`plan_finished`、`plan_failed`、`plan_cancelled` 标记进度，`plan_tuned` 标记油门参数的修改。任一步失败、新的技能、控制者发送杆量、操作员拨杆或接管、急停，都会结束序列。`POST /plan/cancel` 主动结束序列（没有序列时返回 `no_plan`），空中的飞机原地悬停。

**油门参数（0.5.2）。** 没有定位估计的飞机在定高模式下用油门杆执行 `takeoff`、`hover`、`move`、`yaw` 以及 PX4 的 `land`。`tuning: {climb, hover}` 设置这些杆量：`climb`（0.55–1，缺省 0.72）用于离地与爬升，`hover`（0.35–0.65，缺省 0.5）用于保持高度；接近、下降与落地缓冲的杆量随 `hover` 平移，`climb` 须比 `hover` 至少高 0.05。飞机飞不起来时调高 `climb`，悬停时下沉则调高 `hover`。`PATCH /vehicles/{id}/plan/tuning` 可在序列飞行中修改任一值，最后一步或取消后留下的悬停期间同样有效；正在执行的步骤立即采用（没有序列时返回 `no_plan`）。有定位估计时起飞与悬停由飞控完成，不应用油门参数。

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

流程由机群主循环推进，而不是由调用方推进：轮询 `GET .../procedure`（或订阅事件），在 `state` 为 `waiting` 时回答。以下情况流程中止：急停（急停优先，之后不再由流程降落）、链路丢失、坠毁、失去认领，以及在飞行步骤之外解锁或上锁。会话启动的流程在有人附身期间暂停，控制权交还且飞机上锁后从当前步骤开头继续。飞行步骤起飞至 0.6 m、保持在安全区内并自行降落；S10 在定高模式下对横滚、俯仰、偏航施加短时阶跃杆量。模拟机关闭悬停辅助时（无气压计与定位源），S9 与 S10 立即以发现项 `hover_assist_off` 判定失败；飞行段中模拟机的悬停辅助被关闭时，飞机降落且该步失败。建议的参数修改（S10 速率环增益、S11 悬停油门）从不自动写入：S11 逐项询问。

### 飞行健康

每架飞机带有 `health`：最近 5 秒的滚动诊断，在检查器 概览 → 飞行健康 中显示。`state` 为 `ok`、`warn`、`bad` 或 `idle`（未解锁）；`items` 列出 `{key, level, value, unit, detail}`，包括 `oscillation`（横滚、俯仰角速度均方根，°/s）、`lean`（摇杆回中时保持的倾角）、`yaw_drift`、`alt_hold`（定高时垂直速度离散度）、`hover_throttle`、`motors`（各电机平均输出差、触顶比例）、`vibration`（峰值、加速度计削波）、`battery_sag`（每节压降）、`link` 和 `stick_stream`（手动飞行时 Relay 窗口发出的杆量更新）。飞行类项目在空中 1.5 秒后才评估，此前 `level` 为 `na`。各项含义与调整方法见 [`flight-tuning.zh-CN.md`](flight-tuning.zh-CN.md)。

### 杆量

超过 0.8 s 未刷新的杆量帧停止发送，飞机不会按陈旧输入飞行。连续控制请以 10–50 Hz 通过 REST，或更好地通过 WebSocket 发送（`{"type": "sticks", "vehicle", "roll", "pitch", "yaw", "throttle"}`）。关闭 WebSocket 即停止它所驱动的全部杆量流。MCP 不提供连续的杆量流：大语言模型无法闭合 50 Hz 的控制环；`relay_sticks_for` 与技能 `sticks` 只把一组杆量保持至多 10 s 后回中。

### 穿环航线

`course/start` 把航线放在飞机原点并返回，其中 `rings` 已换算到飞机坐标系：`c`（环心 `[x, y, z]`）、`yaw_deg`（环法向的罗盘朝向）与 `r`（半径）。计时从起飞开始。飞机按顺序在半径以内穿过环平面即计为通过。飞机的 `course` 字段报告 `next`、`total`、`splits`、`elapsed_s` 与 `best_s`；事件 `ring_passed` 与 `course_finished` 标记进度。`course_sim.py` 沿每个环的法向，在环前 0.35 m 与环后 0.35 m 各设一个航点。

### 模拟机悬停辅助

每架模拟机有 `hover_assist`（缺省取设置 `sim_hover_assist`，开）。开：与以前相同，ALT_HOLD 保持高度、LOITER 保持位置，GUIDED、RTL、LAND 自主飞行。关：模拟一架没有气压计与定位源的穿越机，姿态自稳仍在，但油门就是推力（无定高、无倾斜与电压补偿，电压下降后悬停点升高），地效、电机响应、湍流（设置 `sim_turbulence`，0–1）与桨洗生效；飞机报告无气压计、无位置估计（`capabilities.althold` 与 `position` 为 false），请求 ALT_HOLD、LOITER、GUIDED、RTL、AUTO 被拒绝（`Mode requires hover assist`）。LAND 保留为失控保护，以开环推力下降。开关可在解锁与飞行中切换：关闭时空中的飞机转 STABILIZE，推力以 0.3 s 斜坡过渡；开启时以当前高度进入 ALT_HOLD。

创建时用 `POST /sims {count, autopilot, position, hover_assist}` 设定；`POST /sims/{id}/assist {on}` 切换（操作员、控制该机的会话，或在无人控制该机且操作员允许应用控制时的任意会话）。快照携带 `vehicle.sim = {hover_assist, turbulence}`（真机为 null）；事件 `sim_assist_changed` 报告变化。MCP：`app_sim_assist`；SDK：`sim_assist(id, on)` 与 `add_sims(count, autopilot, hover_assist)`。

### 模拟机机体缺陷

悬停辅助关闭时，模拟机还带有真实飞机的装配缺陷（设计 06 §4）；悬停辅助开启时这些缺陷不生效，辅助模型保持不变。

| 参数 | 范围 | 效果 |
|---|---|---|
| `mass_kg`、`payload_kg` | 0.08–0.30、0–0.20 kg | 起飞重量：悬停油门上升（0.12 kg 为 0.50，0.16 kg 约 0.63）；超过推力上限（0.288 kg）则无法起飞 |
| `cg_offset_cm` | [前, 右]，±3 cm | 姿态环向重心一侧倾斜（每厘米 2.5°）；离地后积分项按 2 s 时间常数修正其 70 %，剩余 30 % 为稳态倾角，飞机持续向重心偏移方向漂移；落地后积分清零。偏移过大时电机饱和，未修正部分增大 |
| `level_trim_deg` | [横滚, 俯仰]，±5° | 水平校准误差：杆量回中时实际倾斜该角度而 ATTITUDE 读数为水平，因此漂移；加速度计（SCALED_IMU）在阻力建立前仍能感到推力分量 |
| `motor_gain` | 4 × ±0.08 | 电机 1–4（ArduPilot X 型顺序）的效率偏差：偏航漂移（同样按 70 % / 2 s 修正）与轻微倾斜扰动；SERVO_OUTPUT_RAW 中各电机输出不同 |
| `prop_damage`、`damaged_prop` | 0–1、电机 1–4 | 该桨推力最多下降 30 %；满油门时 VIBRATION 最多增加 50 m/s²，加速度计与陀螺噪声增加，姿态抖动 |
| `imu_noise` | 0–5 | ATTITUDE（倍数为 1 时横滚俯仰 0.08°、航向 0.15°、角速度 0.6°/s）与 SCALED_IMU（0.06 m/s²）的噪声倍数 |

预设按种子（0 – 2^31−1）确定性生成：`ideal`（全零，即悬停辅助关闭时的原模型）、`typical`（缺省：重心偏移 0.25 cm 以内、水平误差 0.4° 以内、电机 2 % 以内、噪声 ×1）、`rough`（重心偏移 0.7–1.4 cm、水平误差 1–2°、电机 8 % 以内、一支桨受损 20–50 %、载重 5–20 g、噪声 ×2：朴素悬停 20 s 漂移数米）与 `custom`（显式数值）。

`PUT /sims/{id}/airframe` 接受 `{preset}`（新种子）、`{preset, seed}`、`{seed}`（按当前预设重新生成；自定义机体保留数值）或 `{params: {...}}`（任意子集，叠加在所给预设或当前数值上，结果为 `custom`）。允许已解锁、飞行中修改，积分项从零开始。返回值与 `GET` 相同：`{id, preset, seed, params, active, hover_throttle, limits}`；`hover_throttle` 为满电、离开地效时的水平悬停油门，无法起飞时为 null。权限：操作员、控制该机的会话，或在无人控制该机且操作员允许应用控制时的任意会话；真机返回 `sim_only`，`invalid_request` 指出出错的字段。`POST /sims` 以 `airframe` 接受同样的结构（本次新增的每架模拟机都使用它）；未给出时每架新模拟机采用设置 `sim_airframe_default`（`ideal` | `typical` | `rough`，缺省 `typical`）并使用新的随机种子。

快照只携带 `vehicle.sim.airframe = {preset, seed}`：具体数值不出现在快照与程序观测中，只能在飞行中体现。事件 `sim_airframe_changed`（`data.preset`、`data.seed`、`data.by`）。模拟机在收到 `SET_MESSAGE_INTERVAL` 或 `REQUEST_MESSAGE` 时发送 `SCALED_IMU`（机载板 FRD 坐标的比力，单位 mG；角速度 mrad/s），它不属于任何数据流分组。

### 机载程序

机载程序（设计 06 §2）是一段受限 Python 程序，包含 `PARAMS`、`init(ctx)` 与 `step(obs, ctx) -> Sticks`，由 Relay 在隔离的程序宿主中以 50 Hz 运行在飞机的杆量通路上。它只能看到飞机自身测得的量（`obs`：姿态、角速度、加速度、气压高度、可选的位置、电池、电机输出、对抗状态、目标），输出四个杆量与扳机。[`programs-guide.md`](programs-guide.md) 是编写程序的精简参考（Copilot 的系统提示与 MCP 工具 `program_guide` 使用同一文本）；完整文档见 [`programs.zh-CN.md`](programs.zh-CN.md)。

| 方法与路径 | 权限 | 用途 |
|---|---|---|
| `GET /programs` | – | 内置示例（`hover_trim`、`liftoff`、`patrol`、`hunter_easy`、`hunter_medium`、`hunter_hard`、`rammer`、`figure8_evader`，只读）与已保存的程序 |
| `POST /programs` | 会话或操作员 | `{name, source?, description?, params?, tests?, note?, copy_of?}` → `{program, validation}` |
| `GET /programs/{pid}` · `GET /programs/{pid}/revisions` | – | 单个程序（`source`、`params`、`tests`、`rev`）；最近 50 个版本，最新在前，含作者与说明 |
| `PUT /programs/{pid}` | 会话或操作员 | `{source}` 或 `{patch: [{start, end, text}]}`（行号从 1 起、含首尾，均按当前版本计；`end = start - 1` 表示在 `start` 前插入），另可带 `name`、`description`、`params`、`tests`、`note`：产生新版本，返回 `{program, validation}` |
| `DELETE /programs/{pid}` | 会话或操作员 | 删除已保存的程序 |
| `POST /programs/{pid}/validate` | 会话或操作员 | `{source?}`：只做语言校验，返回 `{ok, errors: [{line, col, code, message_en, message_zh}], params, param_ranges}` |
| `POST /programs/{pid}/test` | 会话或操作员 | `{rev?, source?, tests?, seed?}`：运行单元测试，返回 `{results, passed, total}` |
| `POST /vehicles/{id}/program` | 控制者或操作员 | `{program, rev?, params?, mode: pilot \| copilot, sensors?, arena?, seed?, airframe?, team?, arm?, confirm?}`：部署；`arm: true` 在程序就位后切换到 STABILIZE（PX4 为 STABILIZED）并解锁，程序立即开始飞行（真机须 `confirm: "ARM"`，在任何改动之前检查），应答中的 `arm {ok, code?, reason?}` 说明飞控是否已解锁；被拒绝时程序仍然就位，在地面等待。由部署解锁的飞机在地面停止程序时自动上锁；`team`（1–16 字符）为该机在所有接触表中的队伍，程序声明了 `team` 参数时一并填入；程序以 `PROGRAM · <名称>` 的身份持有摇杆 |
| `POST /vehicles/{id}/program/stop` · `.../resume` | 控制者或操作员 | 停止；恢复已暂停的程序 |
| `PATCH /vehicles/{id}/program/params` | 控制者或操作员 | `{name: value}`：实时调参，类型与范围取自 `PARAMS` 与 `PARAM_RANGES` |
| `GET /vehicles/{id}/program/log?after=&limit=` | – | 日志环（500 行）：`{lines: [{seq, t, level, text}], last_seq}` |
| `POST /vehicles/{id}/program/allow` | 操作员 | `{allow, confirm: "PROGRAM"}`：允许真机运行程序（链路断开后重置） |

快照携带 `vehicle.program`（`{id, name, rev, state, status: running | paused | faulted | stopped, fps, faults, last_error, mode, ...}`）与 `programs_allowed`。`ctx.disarm()` 请求在本帧后上锁，仅当着陆状态表明飞机在地面时执行（否则拒绝并记录一行日志）。程序飞行中部署新版本会在空中无缝接管。始终成立的规则：人附身会暂停 `pilot` 模式的程序，释放后不会自动恢复；急停、链路丢失与坠毁会停止程序；认领结束、会话自己推杆以及任何技能或动作序列都会取代程序；杆量 0.8 s 超时规则不变。真机只有在操作员打开 **允许这架飞机运行程序** 且起飞前检查通过时才接受程序，其 `fire` 输出被忽略。

### 题目与对抗

机载程序（设计 06 §2）可以像刷题一样评测：**题目**是固定场景加判分规则，**对抗**让 2–8 个程序在对抗场中比赛。两者都在**无头评测器**中运行：独立的工作进程构造一个隔离的机群（不开端口、不写磁盘、注入时钟），以机器允许的最快速度推进与实时飞行相同的模拟飞控、机体缺陷、摇杆路径、对抗规则与程序引擎（单机约 13 倍实时，双机约 5–6 倍，四机约 3.5–4 倍）。悬停辅助一律关闭。同一场景、种子与程序版本的结果完全相同。同时最多运行 2 个任务，其余在最多 16 个的队列中等待。

| 方法与路径 | 认证 | 用途 |
|---|---|---|
| `GET /challenges` | – | `{challenges: [{id, kind, name_en, name_zh, goal_en, goal_zh, scene_en, scene_zh, duration_s, visible_seeds, hidden_seeds, opponent, best, attempts}]}` |
| `GET /challenges/{id}` | – | 另含 `scoring_*`、`sensors`、`hover_assist`、`info`（角点、圆环等公开参数）与最近 20 次提交 `history` |
| `POST /challenges/{id}/submit` | 会话或操作员 | `{program, rev?, params?, visible_only?}` → `{job}`；运行全部可见与隐藏种子，`visible_only` 时只运行可见种子（不计入最好成绩） |
| `POST /duels` | 会话或操作员 | `{programs: [id 或 {program, rev?, params?}，2–8 个], rounds?（1–20，缺省 3）, seed?（缺省 1）, scenario?: {duration_s（10–180）, arena, airframe（ideal / typical / rough）, sensors, turbulence}}` → `{job}` |
| `GET /jobs`、`GET /jobs/{id}` | – | `{job: {id, kind, state（queued / running / done / failed / cancelled）, progress, queue_position, programs, result, error}}` |
| `DELETE /jobs/{id}` | 会话或操作员 | 取消排队或运行中的任务（停止其工作进程） |
| `GET /ladder` | – | `{ladder: [{rank, program, name, rating, games, wins, draws, losses}], duels}`（Elo，初始 1200） |
| `GET /traces/{id}` | – | 回放轨迹（见下） |

| 题目 | 场景 | 通过条件 |
|---|---|---|
| `liftoff` | 载重使悬停油门约 0.62；有气压计、无定位源 | 5 s 内离地，并在 1.0 ± 0.2 m 连续保持 3 s |
| `hover` | 重心前偏与水平校准误差；有气压计、无定位源 | 6–26 s 内漂移 ≤ 0.8 m、高度 1.0 ± 0.25 m（按真值判分） |
| `heading` | 电机效率不一致（偏航漂移） | 5–20 s 内航向与出生航向相差 ≤ 10°，高度不低于 0.4 m |
| `square` | UWB 式定位（10 Hz，σ 5 cm） | 40 s 内按序经过 `info.corners` 四个角点（半径 0.4 m），越快分越高 |
| `rings` | `cage-loop` 穿环航线，UWB 式定位 | 60 s 内按序穿过全部圆环，计时 |
| `pursuit` | 脚本目标机绕中心飞行（半径 3 m、0.5 m/s、高 1.2 m）；有接触传感器与 UWB 式定位 | 60 s 内至少一半时间与目标相距 2 m 以内；分数即该比例 |
| `duel_easy` / `duel_medium` / `duel_hard` | 与 `hunter_easy` / `hunter_medium` / `hunter_hard` 一对一（场地半径 8 m、天花板 4 m） | 胜利：对手被击落、坠毁或出界而自身仍在飞行；到时按剩余生命值计分 |
| `ram_easy` | 靶机悬停在出生环上（`judge_hover_target`）；不开火；有接触与类 UWB 定位 | 60 s 内撞击靶机使其坠毁；越快、撞击越重得分越高，未撞上时按最近距离计分 |

分数 0–100。一次提交运行全部种子，全部通过才算通过，分数为各种子平均；`result = {score, passed, metrics, seeds: [{seed（隐藏种子为 null）, hidden, score, passed, metrics, trace_id}], trace_id, best, speed}`。隐藏种子的机体缺陷不同，不保存轨迹。每题的最好成绩与最近 50 次提交保存在 `<data_dir>/programs/results.json`。

对抗的每一局把每个程序的一架飞机放在场地中心周围的出生环上（位置打乱、机体缺陷按 `seed + 局号 − 1` 生成），均面向中心。被击落、坠毁或飞出场地边界即出局；只剩一架（再等 1 s）或时间到时本局结束。名次：仍在飞行者优先，其次按存活时间、剩余生命值、命中数。最后仍在飞行的飞机赢得本局，否则为平局。排名表（`<data_dir>/programs/ladder.json`）按每局名次两两计算 Elo（K = 32，按其他参赛者数均分）。`result = {rounds: [{round, seed, winner, draw, duration_s, aircraft: [{id, program, alive, hp, hits, hits_taken, survived_s, reason, place}], trace_id}], standings, ladder: [{program, rating, delta}], speed}`。任务结束时发出事件 `judge_finished`（`data.job`、`data.state`、`data.score`、`data.passed`）。

**轨迹**（`GET /traces/{id}`，磁盘上 gzip 压缩，保留最近 300 条）以 10 Hz 回放一个种子或一局中的全部飞机：`{id, job, version: 1, kind, challenge?, round?, scenario, seed, hz, duration_s, fields: ["x", "y", "z", "roll", "pitch", "yaw", "throttle", "hp", "flags"], arena, course, markers, t: [...], aircraft: [{id, label, program, rev, name, role, team, spawn, frames: [[…每个 t 一行…]], states: [[t, 状态名]]}], shots: [{id, shooter, t0, p, v}], events: [{t, kind, vehicle, …}], result}`。位置为世界 ENU 坐标（米，场地中心为原点，与快照相同），姿态为弧度（yaw 为罗盘航向 0..2π），`flags` 位：1 已解锁、2 离地、4 被击落、8 坠毁、16 出界。子弹在 `t0` 时刻以速度 `v` 从 `p` 射出，之后按对抗场弹道运动（`web/src/arena/ballistics.ts`）。事件：`hit`、`down`、`crash`、`out`、`liftoff`、`corner`、`ring`、`course_finished`、`program_faulted`、`program_stopped`。完整约定见 `relay.judge` 模块说明与 `web/src/api/schema.json` 中的 `Trace`。

连接 0.4.0 之前、没有这些路径的 Relay 时，SDK、MCP 工具与 Copilot 报告 `judge_unavailable`。

### 程序工作流

智能体的工作循环，这里以缺省 `sticks` 等级下的 SDK 为例（MCP 工具与 Copilot 工具同名：`program_create`、`program_update`、`program_validate`、`program_test`、`challenge_submit`、`program_deploy`、`program_params` 等）：

```python
with RelayClient() as relay:
    relay.open_session("my-agent")
    # 1. 编写：以内置的 hover_trim 为起点
    pid = relay.program_create("My hover", copy_of="hover_trim", note="start")["program"]["id"]
    relay.program_update(pid, patch=[{"start": 14, "end": 14, "text": '    "target_alt": 1.2,'}], note="higher")
    # 2. 校验：语言规则，逐条给出行列
    assert relay.program_validate(pid)["ok"]
    # 3. 测试：在程序宿主中运行单元测试
    print(relay.program_test(pid))
    # 4. 提交题目（无头评测，含隐藏种子）
    print(relay.challenge_submit("hover", pid)["result"])
    # 5. 部署到关闭悬停辅助、机体粗糙的模拟机
    relay.claim("sim-1")
    relay.sim_assist("sim-1", False)
    relay.mode("sim-1", "STABILIZE")
    relay.arm("sim-1")
    relay.program_deploy("sim-1", pid, airframe={"preset": "rough", "seed": 7})
    # 6. 观察并实时调参，结束后停止
    print(relay.program_status("sim-1"), relay.program_log("sim-1")["lines"][-5:])
    relay.program_params("sim-1", ki=0.2)
    relay.program_stop("sim-1")
```

每次修改都是一个新版本，记录作者（`operator` 或会话名）与说明，因此任何成绩都能追溯到产生它的代码。先实时调参；结构不对时再改代码；然后重新提交题目——隐藏种子会暴露只对一个机体调好的程序。

### 局域网联机

联机场地（05 §7）让同一广播域内的 Relay 终端互见彼此的模拟机、发射虚拟子弹并计分。在窗口中加入（顶栏 **联机**，设置 `peer_enabled`），只使用一个独立的 UDP 套接字（设置 `peer_port`，缺省 47114，组播 `239.255.71.14`、TTL 1，另加子网定向广播）。来自网络的任何数据都不会进入控制路径：杆量、解锁、认领、参数、技能、动作序列与设备级接口均不受对端数据包驱动。**真机永不加入联机场地，也永不受虚拟子弹影响**：只有模拟机会被共享、可以射击、可以被命中。

| 方法与路径 | 权限 | 用途 |
|---|---|---|
| `GET /arena` | – | `{enabled, instance, seat, callsign, error, peers: [{id, callsign, seat, rtt_ms, stale, score, vehicles}], shots, local: {ammo, hp, reloading, reload, down, max_ammo, max_hp, score}}`；坐标已换算到本终端的局部 ENU |
| `POST /vehicles/{id}/fire` | 控制者 | `{seconds}`（0–3，缺省 1；0 为松开）：按住已解锁、在空中的模拟机扳机；每秒 8 发，弹匣 24 发，装填 1.5 s；未加入场地时 `arena_off`，被击落后 `arena_down`，真机 `sim_only` |

每发伤害 20（5 发击落），并对机体施加冲量；被击落的模拟机电机停转、坠落、触地后上锁，5 s 后回到座位出生点（设置 `arena_auto_respawn`）或在复位时恢复。命中不是安全事件，不产生告警。设置：`peer_enabled`、`peer_port`、`peer_callsign`（≤ 16 字符，留空使用计算机名）、`arena_auto_respawn`。SDK：`arena()`、`fire(id, seconds)`。

### 狗斗、对局与排行榜

**狗斗**（07 §3）是场地内 2–8 架模拟机的限时对战，每架由一段机载程序以 `sticks` 级控制：**射手**开火，**撞击手**冲向目标、从不开火。一次调用即可布置：Relay 选取空闲模拟机（不足时自动添加），放到距场地边缘 3 m 的出生环上、机头朝向中心，设置悬停辅助与机体，开启名为 `dogfight` 的会话认领这些飞机，为每种角色部署程序（缺省 `hunter_medium` 与 `rammer`），解锁后由程序自行起飞。操作员可随时附身任意一架：其程序暂停（状态 `human`），松开后继续。开启 `respawn` 时，被击落或坠毁的飞机（状态 `down`）5 s 后回到出生点重新起飞。结束时（到时、`DELETE` 或全部飞机失败）程序停止、空中飞机降落；20 s 后仍在空中、或已落地但着陆检测未触发的模拟机跳过检查强制上锁；随后释放认领，最终成绩保留在排行榜上。局域网中狗斗加入正在进行的对局，或在所有协议版本 2 的终端上开启新对局；未加入场地时以**练习模式**运行（场地规则照常，但不开套接字、不发送任何数据），结束时退出练习模式。**真机一律拒绝**（`dogfight_real_aircraft`），真机从不参与对局、碰撞与场景。

| 方法与路径 | 权限 | 用途 |
|---|---|---|
| `POST /scenarios/dogfight` | 应用控制（开启 `agents_app_control` 的会话，或操作员） | `{aircraft（2–12，缺省 6；超过 8 返回 dogfight_no_aircraft），teams（"ffa" / 2 / 3），roles: {gunner, rammer}（之和等于 aircraft；缺省射手与撞击手 2 : 1），programs: {gunner, rammer}，arena: {radius（3–30，缺省 8），ceiling（2–10，缺省 4）}，airframe（ideal / typical / rough），hover_assist（缺省 false），duration_s（30–1800，缺省 180），respawn（缺省 true），seed，vehicles，entrants}` → 狗斗视图。`entrants`（2–8 项）逐机设定并取代 `aircraft`、`roles`、`teams`：`{role: gunner \| rammer \| pilot（从不开火），program（pilot 必填，其余缺省同角色），team（≤ 16 字符，缺省为自身 id），position: none \| uwb \| gps（缺省 none），params，label}`；已有狗斗时 `dogfight_running` |
| `GET /scenarios/dogfight` | – | `{running, phase（idle / booting / arming / deploying / running / ending / done）, started_at, ends_at, config, aircraft: [{vid, role, program, team, position, label, state（booting / arming / deploying / flying / human / down / stopped / failed）}], match, by, reason（time / stopped / error）, error}` |
| `DELETE /scenarios/dogfight` | 应用控制 | 提前结束；没有狗斗时 `dogfight_not_running` |
| `POST /arena/match` | 应用控制 | `{duration_s}`（30–1800，缺省 180）：在本机与所有版本 2 对端开启对局；`arena_off`、`match_running` |
| `DELETE /arena/match` | 应用控制 | 在所有终端结束当前对局（`ended: true`）；没有对局时清零统计（`ended: false`） |
| `GET /arena/leaderboard?by=` | – | `{by, match, rows: [{key, aircraft, callsign, local, controller: {kind（program / agent / human / none）, name}, downs, hits, shots, accuracy, rams, deaths, crashes, damage, alive, alive_s, best_alive_s}]}`，按 `downs`（缺省）、`survival`、`accuracy` 或 `rams` 排序 |

**统计。** 场地内每架模拟机一行：击落数（其子弹击落了另一架）、命中、射击、撞落数（其造成的碰撞使对方坠毁）、被击落、坠毁（碰撞或触地）、造成伤害、当前存活时间与最长存活时间。对局（`{id, origin, started_at, duration_s, running, ended_at, reason（time / stopped / conflict）}`）开始时清零、结束时冻结；场地快照携带 `match` 与 `board_rev`。对端飞机的行来自该终端自己的统计（以其所见为准）；Relay 0.4 对端的行统计为空。

**协议版本 2。** Relay 0.5 使用场地协议版本 2，并与 0.4 保持兼容：0.4 认识的每种报文仍以版本字节 1 逐字节不变地发送；只有 `COLLIDE`、`MATCH` 以及约每秒一次携带统计块的 `STATE` 使用版本 2（0.4 终端将其视为畸形报文丢弃，其余状态照常接收）。对端在收到其第一个合法的版本 2 报文后视为版本 2；对局与碰撞只涉及版本 2 对端。`MATCH` 在所有终端开启或结束对局（冲突时最早开始者优先；运行期间每 2 s 重发）。**局域网碰撞**：每个终端用对端最近一次上报的状态（最多外推 0.15 s）与本机飞机做扫掠检测；发现接触时只对本机飞机施加响应，并向另一架飞机的所属终端发送 `COLLIDE`。所属终端重新核验后才施加：报文不超过 0.3 s（时钟差加半个往返）、按其自身视图在接触时刻两机相距不超过 0.6 m、每个对端飞机每秒最多 4 个、同一对飞机 0.5 s 内只施加一次；并回复一次（回复不再被回复），使发送方得知自己造成的坠毁。被拒绝的 `COLLIDE` 不产生任何影响。事件 `collision`（`data.a`、`data.b`、`data.b_label`、`data.point`、`data.v_rel`、`data.crashed`、`data.kind`、`data.remote`）。

SDK：`dogfight_start(...)`、`dogfight_stop()`、`dogfight_status()`、`leaderboard(by)`、`match_start(duration_s)`、`match_end()`。MCP（应用服务器）与 Copilot：`dogfight_start`、`dogfight_stop`、`dogfight_status`、`leaderboard`。例如"开一场 6 机混战，4 架开火 2 架撞击"对应 `dogfight_start {aircraft: 6, teams: "ffa", roles: {gunner: 4, rammer: 2}}`。"三架飞机：A 飞 8 字并躲避、可获取全局位置，一架撞 A，一架用子弹打 A"对应 `dogfight_start {entrants: [{role: "pilot", program: "figure8_evader", team: "A", position: "gps", label: "A"}, {role: "rammer", team: "B"}, {role: "gunner", team: "B"}]}`：两架攻击机只把 A 当作接触对象，A 获得位置与速度。

## 6. WebSocket `/api/v1/stream`

服务端 → 客户端：

```json
{"type": "hello", "version": "0.1.0", "operator": false, "session": "s-1a2b3c4d"}
{"type": "fleet", "data": { "...机群快照..." }}
{"type": "event", "data": {"seq": 812, "kind": "ring_passed", "vehicle": "sim-1", "...": "..."}}
{"type": "error", "error": {"code": "not_claimed", "message_en": "...", "message_zh": "...", "detail": {}}, "ref": "sim-1"}
```

`fleet` 以 10 Hz 推送，`arena`（即 `GET /arena` 视图）以 20 Hz 推送，`event` 实时推送。客户端 → 服务端：`sticks`（见上）、`{"type": "fire", "vehicle", "on"}`（控制者的扳机，按住期间每 100 ms 刷新，300 ms 未刷新视为松开）与 `{"type": "ping"}`（回复 `pong`）。令牌无效时以关闭码 4401 断开；浏览器来源不被允许时以 4403 断开。

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
| `plan_tuned` | 动作序列的油门参数已修改，含 `data.tuning` |
| `armed` / `disarmed` / `mode_changed` | 飞控状态 |
| `link_delayed` / `link_lost` / `link_restored` | 链路质量；进入 `lost` 时 Relay 停止杆量与技能 |
| `battery_warn` / `battery_critical` / `crashed` | 风险 |
| `course_started` / `ring_passed` / `course_finished` / `course_aborted` | 穿环航线 |
| `stop` / `stop_all` | 急停 |
| `refly_started` / `refly_finished` / `refly_failed` | 再飞 |
| `sim_assist_changed` | 模拟机悬停辅助已切换（`data.hover_assist`） |
| `sim_airframe_changed` | 模拟机机体缺陷已设置（`data.preset`、`data.seed`、`data.by`） |
| `judge_finished` | 题目或对抗评测任务结束（`data.job`、`data.kind`、`data.state`、`data.score`、`data.passed`） |
| `peer_joined` / `peer_left` / `arena_hit` / `arena_down` | 局域网联机：终端加入或离开、我方命中或被命中、模拟机被击落 |
| `collision` | 两架飞机，或飞机与环、地面发生接触（`data.a`、`data.b`、`data.crashed`、`data.kind`；局域网接触带 `data.remote`） |
| `dogfight_started` / `dogfight_finished` / `match_started` / `match_finished` | 狗斗场景与场地对局（`data.reason`；结束文本中给出领先者） |
| `device_command` / `motor_test` / `motor_test_stopped` / `actuator_set` / `rc_override` / `message_interval` / `home_set` / `rebooting` / `calibration_started` / `terminated` | 设备级操作 |
| `app_command` | 会话或操作员执行的界面动作（`data.action`、`data.by`） |
| `procedure_started` / `procedure_step` / `procedure_waiting` / `procedure_finished` / `procedure_aborted` | 标定流程，带 `data.run_id`、`data.step`、`data.verdict` 或 `data.reason` |
| `program_started` / `program_stopped` / `program_paused` / `program_resumed` / `program_faulted` / `program_host_restarted` / `program_gate` | 机载程序：部署、停止及原因、附身暂停、故障、宿主重启、真机开关 |

完整列表见 `web/src/api/schema.json`（`EventKind`）。错误使用统一结构，`code` 在各版本间保持稳定：

```json
{"error": {"code": "outside_safety_volume", "message_en": "The target is outside the safety volume.", "message_zh": "目标点超出安全区。", "detail": {"origin": [0, 0, 0], "volume": {"x": 3, "y": 3, "z": 2}}}}
```

| 状态码 | 智能体常见的错误码 |
|---|---|
| 400 | `invalid_request`、`confirm_required`、`outside_safety_volume`、`unknown_mode`、`command_not_allowed` |
| 401 | `auth_required`、`session_expired`（重新打开会话） |
| 403 | `operator_required`、`session_required`、`untrusted_host`、`app_control_disabled` |
| 404 | `vehicle_not_found`、`course_not_found`、`unknown_skill`、`param_not_found`、`record_not_found`、`procedure_unknown`、`procedure_not_found`、`test_run_not_found`、`challenge_not_found`、`job_not_found`、`trace_not_found` |
| 409 | `not_claimed`、`claimed_by_other`、`possessed_by_human`、`claim_suspended`、`preflight_blocked`、`arm_rejected`、`mode_rejected`、`not_armed`、`crashed`、`capability_missing`、`no_position_estimate`、`not_airborne`、`no_plan`、`must_be_disarmed`、`message_not_seen`、`refly_unavailable`、`ui_action_failed`、`procedure_running`、`no_procedure`、`procedure_step_mismatch`、`procedure_not_waiting`、`arena_off`、`arena_down` |
| 429 | `rate_limited`、`judge_queue_full` |
| 503 | `link_not_ready`、`backend_busy`、`ui_unavailable`、`judge_unavailable` |
| 504 | `ui_timeout` |

机载程序与控制等级新增：403 `sticks_only`；400 `program_invalid`、`program_param_invalid`；404 `program_not_found`、`revision_not_found`；409 `program_readonly`、`program_load_failed`、`no_program`、`program_not_paused`、`programs_not_allowed`；503 `program_host_unavailable`。狗斗与对局新增 400 `dogfight_real_aircraft`；409 `dogfight_running`、`dogfight_not_running`、`dogfight_no_aircraft`、`match_running`。客户端在没有评测接口的 Relay 上另报 `judge_unavailable`（404）。

## 8. MCP 服务

Relay 提供三套基于 stdio 的 Model Context Protocol 服务（JSON-RPC 2.0，只用标准库；支持协议版本 2024-11-05 至 2025-11-25），可以单独接入，也可以同时接入：

| 服务 | 文件 | 工具 | 面向 |
|---|---|---|---|
| `anet-relay` | `mcp/anet_relay_mcp.py` | 21 个 `relay_*`（`sticks` 等级下 13 个） | 飞行：机群、认领、解锁、模式、技能、动作序列及其油门参数、短时杆量、再飞、穿环航线、急停 |
| `anet-relay-device` | `mcp/anet_relay_device_mcp.py` | 36 个 `device_*` | 设备级：设备总览、MAVLink 消息、参数读写、原始指令、单电机测试、执行器、遥控覆盖、消息频率、重启、校准、home、姿态与速度控制、飞行终止、标定流程与报告 |
| `anet-relay-app` | `mcp/anet_relay_app_mcp.py` | 42 个 `app_*`、19 个程序工具与 4 个狗斗工具 | 应用控制：应用与窗口状态、手柄、语言、工作区、选择飞机、检查器、对话框、截图、扫描网络、连接与移除飞机、模拟机、设置、告警、记录与导出、再飞来源、附身、Copilot 窗口、全部急停；机载程序：`program_guide`、`program_*`、`challenge_list`、`challenge_submit`、`job_status`、`duel_run`、`ladder`、`airframe_set`；狗斗：`dogfight_start`、`dogfight_stop`、`dogfight_status`、`leaderboard` |

每套服务在启动时打开一个智能体会话，名称取 `--name`（缺省分别为 `mcp`、`mcp-device`、`mcp-app`），自动续租，并在客户端断开时关闭会话；Relay 重启后，服务在下一次调用时重新打开会话，此时需要重新认领。认领属于会话：用某一套服务控制飞机时，请用同一套服务的认领工具（`relay_claim` 或 `device_claim`）。工具参数在发往 Relay 之前校验，缺少或多余的参数以 `invalid arguments` 返回。失败以 `isError: true` 的工具结果返回，并附错误码，模型可据此应对（例如遇到 `possessed_by_human` 时等待）。

应用控制服务的工具在 `agents_app_control` 开启时即可使用，不需要操作员令牌。`--operator-token`（或环境变量 `RELAY_OPERATOR_TOKEN`）只在需要以操作员身份调用时提供，一般不需要。

应用控制服务中作用于飞机的程序工具（`program_deploy`、`program_stop`、`program_resume`、`program_params`）会以该服务的会话认领飞机；若飞行服务的会话已持有认领，请先在那里释放（`relay_release_claim`）。`program_deploy` 带 `arm: true` 时会先切换到手动类模式并解锁再部署（真机还需 `confirm: "ARM"`）。

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
| `arena()` · `fire(id, seconds)` | 联机场地视图与模拟机扳机 |
| `dogfight_start(aircraft, teams, roles, programs, arena, airframe, hover_assist, duration_s, respawn, seed, vehicles)` · `dogfight_stop()` · `dogfight_status()` · `leaderboard(by)` · `match_start(duration_s)` · `match_end()` | 狗斗场景、对局与排行榜（应用操作） |
| `program_list()` · `program_get(pid)` · `program_revisions(pid)` · `program_create(name, source, description, params, tests, note, copy_of)` · `program_update(pid, source, patch, note, ...)` · `program_delete(pid)` · `program_validate(pid, source)` · `program_test(pid, rev, source, tests, seed)` | 机载程序：存储、版本、校验与单元测试 |
| `program_deploy(id, pid, rev, params, mode, sensors, arena, seed, airframe)` · `program_stop(id)` · `program_resume(id)` · `program_params(id, **values)` · `program_status(id)` · `program_log(id, after, limit)` · `program_allow(id, allow, confirm)` | 运行中的程序（`program_allow` 需要操作员令牌） |
| `challenge_list()` · `challenge_submit(challenge, pid, rev, wait=True)` · `job_status(job)` · `wait_job(job)` · `duel_run(pids, rounds, seed, scenario, wait=True)` · `ladder()` · `trace(id)` | 评测、对抗与排名；没有评测接口时报 `judge_unavailable` |
| `airframe_get(id)` · `airframe_set(id, preset, params, seed)` | 模拟机机体缺陷 |
| `app()` · `ui_state()` · `gamepads()` · `app_actions()` · `ui(action, **args)` | 应用与窗口状态、界面动作 |
| `scan_network(networks)` · `connect_vehicle(host, port)` · `remove_vehicle(id)` · `add_sims(count, autopilot, hover_assist)` · `remove_sims(ids)` · `sim_assist(id, on)` · `settings()` · `update_settings(**patch)` · `ack_alert(id)` · `stop_all()` | 应用操作：打开会话时以会话身份调用，否则使用操作员令牌 |

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
- **配置：** 设置 → Copilot：接口类型（OpenAI 兼容端点选 `openai-completions`，另有 `anthropic-messages`、`openai-responses`）、Base URL、模型 ID、API Key、工具范围与窗口不透明度（60%–100%）。**测试连接** 会发送一次简短请求。Key 保存在应用数据目录（`copilot-secrets.json`，以每次安装随机生成、存于 `copilot-secrets.key` 的密钥做 AES-256-GCM 加密，两个文件均仅当前系统用户可读），不会回传到界面；端点只会收到对话内容和工具结果。开启 **使用系统钥匙串保存密钥**（缺省关闭）后改存于 macOS 钥匙串、Windows 凭据加密或 Linux Secret Service，只有此时 Relay 才调用 Electron `safeStorage`。Relay 不读取、也不需要系统登录密码：钥匙串对话框来自操作系统，只有在选择用钥匙串保存密钥时才会出现。0.3 及更早版本保存的密钥留在钥匙串中、不会被自动解密，直到在 Copilot 窗口中重新输入或选择 **继续使用钥匙串**。
- **思考与输出上限：** **思考**（`auto` 为缺省，交给端点决定；`off`、`low`、`high` 为 DeepSeek V4 等推理模型设定）与 **最大输出 tokens**（1024–131072，缺省 16384，单次模型调用、含思考）。推理模型默认开启思考，思考与回答共用输出上限：输出耗尽的调用以 `length` 结束，对话中会给出说明，而不是没有任何反应。
- **复制与排版（0.5.2）：** 回复按 Markdown 渲染：标题、嵌套列表与任务列表、带列对齐的表格（过宽时可横向滚动）、代码块、引用与链接（在系统浏览器中打开）。对话内容可以选择复制；所有 Relay 窗口的右键菜单提供复制、粘贴与全选；每条回复有复制按钮（复制 Markdown 原文），表格复制为制表符分隔的文本，代码块复制其代码。
- **运行轨迹：** 每条消息下方有一架小小的红色像素飞机，Copilot 工作时它在飞，旁边是阶段（等待模型、思考中及已思考字数、撰写回复、准备调用工具及参数字数、工具执行中、等待批准）与计时；该条目滚出视野时，输入框上方显示同样的条目。点击打开本次运行的轨迹：每次模型调用的耗时、输入 / 输出 / 思考 tokens 与结束原因，可展开查看完整思考、回复与请求的工具调用；每个工具的参数与完整结果。
- **工具范围：** `flight`（飞行：状态、技能、动作序列、短时杆量、再飞、急停）、`flight+device`（另加设备级工具）、`all`（缺省，另加应用控制工具：语言、工作区、选择飞机、扫描网络、模拟机、设置、手柄、记录、窗口与截图）。工具与第 5 节的接口一一对应。
- **安全：** 对真机的每个运动指令与设备级写入（起飞、平移、转向、悬停、降落、动作序列、再飞、电机测试、执行器、重启、校准、终止等），聊天中都会出现审批卡片，操作员批准前不会发送任何指令（120 秒未处理视为拒绝）；需要审批时浮动窗口会自动显示并提示。确认词由审批代替，Copilot 不会自行填写。接管和急停对 Copilot 与其他智能体同样有效。
- **全部允许（0.5.2）：** 审批卡片上的**全部允许**按钮（或 设置 → Copilot → **全部允许**）批准当前命令，之后真机上的命令都不再询问。每条命令仍在对话中记录为“已自动批准”；开启期间 Copilot 标题栏始终显示红框的**全部允许**标记，点击即可关闭。Relay 自身的保护不变：预检、操作员的**允许在这架飞机上运行程序**开关、附身与急停。
- **程序与控制等级：** Copilot 在每条消息前读取 `agent_flight_level`。在 `sticks`（缺省）等级下，它没有起飞、平移、转向、悬停、动作序列与再飞工具，系统提示会说明高层飞行不可用、稳定飞行需要编写程序；在 `full` 等级下这些工具恢复。在任何工具范围内它都具备程序工具（`program_list`、`program_get`、`program_create`、`program_update`、`program_validate`、`program_test`、`program_deploy`、`program_stop`、`program_resume`、`program_params`、`program_status`、`program_log`、`challenge_list`、`challenge_submit`、`job_status`、`duel_run`、`ladder`、`airframe_set`）以及 `relay_arm`、`relay_disarm`、`relay_mode`，系统提示末尾附有 [`programs-guide.md`](programs-guide.md)。编写与修改程序无需审批；在真机上部署或恢复程序、解锁与切换模式，与其他运动指令一样需要操作员批准。`program_deploy {arm: true}` 随部署一并解锁；在 `full` 等级下，动作序列工具接受 `tuning`，`relay_plan_tune` 可在飞行中调整序列油门，Copilot 因此能完成编写、测试、试飞与调参的完整流程。
