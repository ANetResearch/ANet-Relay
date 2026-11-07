# ANet Relay 智能体接口

[English](agent-api.md) · 简体中文

ANet Relay 把它所管理的机群开放给外部程序：脚本、自主智能体与大语言模型。本文说明控制模型、REST 与 WebSocket 接口、Python SDK 与 MCP 服务。每个接口与数据模型的完整参考由运行中的后端生成，地址为 `http://127.0.0.1:8765/docs`（OpenAPI：`/openapi.json`）。

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

3. 或者通过 MCP 接入大语言模型（第 8 节）。

`sdk/python/examples/` 下的四个示例都在模拟机上运行：

| 示例 | 内容 |
|---|---|
| `hover_sim.py` | 认领 → 解锁 → 起飞到 1.2 m → 悬停 8 s → 降落 → 释放 |
| `watch_handback.py` | 让 sim-1 绕圈飞行；操作员附身后暂停；收到 `relay_handback` 后从当前位置继续 |
| `course_sim.py` | 用 `goto` 逐环飞完 `cage-loop` 穿环航线并打印成绩 |
| `swarm_square.py` | 四架模拟机同时起飞，同步做方阵旋转，然后一起降落 |

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
| 2 | `arm`、`mode`，技能 `takeoff`、`land`、`hold` | 起飞与悬停需要 `althold` |
| 3 | 技能 `goto`、`rtl`，穿环航线 | `position`（新鲜的本地位置估计） |

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

只读接口无需令牌。API 只绑定 127.0.0.1，并且只响应发往 `127.0.0.1` 或 `localhost` 的请求。附身、释放、添加模拟机、写参数、数传配网、全部急停等操作员专属操作只属于 Relay 窗口；智能体通过会话工作。

**限速**（每会话）：控制类调用（杆量、解锁、模式、技能）每秒 60 次，其他调用每秒 20 次。超出时返回 429 `rate_limited`，并带 `Retry-After: 1`。

## 5. 面向智能体的 REST 接口

路径均以 `/api/v1` 开头，请求体为 JSON。

| 方法与路径 | 鉴权 | 用途 |
|---|---|---|
| `GET /health` | – | `{ok, version, uptime_s}` |
| `GET /capabilities` | – | 坐标系、门限、技能及参数、模式名称、确认词 |
| `GET /fleet` | – | 机群快照：飞机、会话、告警、安全区 |
| `GET /vehicles/{id}` | – | 单机状态，另含 `detail`（home、安全区原点、单节电压、起飞前检查项） |
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
| `POST /vehicles/{id}/skills/{name}` | 控制者 | `takeoff {alt}`、`land`、`hold`、`rtl`、`goto {x, y, z, yaw?}` |
| `POST /vehicles/{id}/course/start` | 控制者 | `{course_id}`（缺省 `cage-loop`） |
| `POST /vehicles/{id}/course/stop` | 控制者 | 中止航线 |
| `POST /vehicles/{id}/stop` | 控制者 | 急停 |

### 技能

技能调用立即返回 `{"skill": {"id", "name", "state": "running", "detail"}}`。之后飞机的 `skill` 字段以同一 `id` 报告 `state`：`running`、`done` 或 `failed`；事件 `skill_started`、`skill_finished`、`skill_failed` 的 `data.skill_id` 与之对应。启动新技能会替换正在运行的技能。SDK 会按 id 代为等待。

| 技能 | 所需能力 | 行为 |
|---|---|---|
| `takeoff {alt}` | althold（PX4 需 position） | 模拟机按需自动解锁，爬升到起飞点上方 `alt` 米（0.2 m 至安全区高度）并保持。地面上的真机须先解锁，或在调用中携带 `confirm: "ARM"` 并通过起飞前检查。已在空中时立即完成。 |
| `land` | 任意 | 原地降落；飞机落地并上锁后完成。 |
| `hold` | althold | 保持位置（LOITER / POSCTL）或高度（ALT_HOLD / ALTCTL）。 |
| `rtl` | position | 返回起飞点并降落。 |
| `goto {x, y, z, yaw?}` | position | 飞往飞机位置坐标系中的目标点；距离 0.15 m 以内即完成。 |

技能运行或保持期间，Relay 持续发送中位杆量（油门 0.5）作为遥控保活，使飞控的遥控丢失保护保持静默；这依赖的失控保护设置见 `hardware.zh-CN.md`。

### 杆量

超过 0.8 s 未刷新的杆量帧停止发送，飞机不会按陈旧输入飞行。连续控制请以 10–50 Hz 通过 REST，或更好地通过 WebSocket 发送（`{"type": "sticks", "vehicle", "roll", "pitch", "yaw", "throttle"}`）。关闭 WebSocket 即停止它所驱动的全部杆量流。MCP 不提供杆量工具：大语言模型无法闭合 50 Hz 的控制环。

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
| `armed` / `disarmed` / `mode_changed` | 飞控状态 |
| `link_delayed` / `link_lost` / `link_restored` | 链路质量；进入 `lost` 时 Relay 停止杆量与技能 |
| `battery_warn` / `battery_critical` / `crashed` | 风险 |
| `course_started` / `ring_passed` / `course_finished` / `course_aborted` | 穿环航线 |
| `stop` / `stop_all` | 急停 |

完整列表见 `web/src/api/schema.json`（`EventKind`）。错误使用统一结构，`code` 在各版本间保持稳定：

```json
{"error": {"code": "outside_safety_volume", "message_en": "The target is outside the safety volume.", "message_zh": "目标点超出安全区。", "detail": {"origin": [0, 0, 0], "volume": {"x": 3, "y": 3, "z": 2}}}}
```

| 状态码 | 智能体常见的错误码 |
|---|---|
| 400 | `invalid_request`、`confirm_required`、`outside_safety_volume`、`unknown_mode` |
| 401 | `auth_required`、`session_expired`（重新打开会话） |
| 403 | `operator_required`、`session_required`、`untrusted_host` |
| 404 | `vehicle_not_found`、`course_not_found`、`unknown_skill` |
| 409 | `not_claimed`、`claimed_by_other`、`possessed_by_human`、`claim_suspended`、`preflight_blocked`、`arm_rejected`、`mode_rejected`、`not_armed`、`crashed`、`capability_missing`、`no_position_estimate` |
| 429 | `rate_limited` |
| 503 | `link_not_ready`、`backend_busy` |

## 8. MCP 服务

`mcp/anet_relay_mcp.py` 是基于 stdio 的 Model Context Protocol 服务（JSON-RPC 2.0，只用标准库；支持协议版本 2024-11-05 至 2025-11-25）。它在启动时打开一个智能体会话，名称取 `--name`（缺省 `mcp`），自动续租，并在客户端断开时关闭会话。Relay 重启后，服务在下一次调用时重新打开会话；此时需要重新认领。

| 工具 | 用途 |
|---|---|
| `relay_fleet` | 精简机群视图：链路、模式、位置、电池、控制者、认领、技能；安全区 |
| `relay_vehicle` | 单机完整状态 |
| `relay_topology` | 网络拓扑 |
| `relay_events` | 最近事件；传入 `after` 可从某个序号向后翻页 |
| `relay_claim` / `relay_release_claim` | 认领与释放 |
| `relay_arm` / `relay_disarm` | 解锁（真机：`confirm: "ARM"`）与上锁 |
| `relay_mode` | 按名称切换飞行模式 |
| `relay_takeoff` / `relay_land` / `relay_hold` / `relay_goto` | 技能；除非 `wait: false`，否则等待完成 |
| `relay_stop` | 急停 |
| `relay_course_start` | 启动穿环航线，返回待穿越的环 |

失败以 `isError: true` 的工具结果返回，并附错误码，模型可据此应对（例如遇到 `possessed_by_human` 时等待）。

**Claude Desktop**（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "anet-relay": {
      "command": "python3",
      "args": ["/path/to/ANet-Relay/mcp/anet_relay_mcp.py", "--name", "claude-desktop"]
    }
  }
}
```

**Claude Code**：

```bash
claude mcp add anet-relay -- python3 /path/to/ANet-Relay/mcp/anet_relay_mcp.py --name claude-code
```

或项目级 `.mcp.json`：

```json
{
  "mcpServers": {
    "anet-relay": {"command": "python3", "args": ["mcp/anet_relay_mcp.py", "--name", "claude-code"]}
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
| `takeoff(id, alt)` · `land(id)` · `hold(id)` · `rtl(id)` · `goto(id, x, y, z, yaw=None)` | 技能；`wait=True` 阻塞到完成，失败时抛出异常 |
| `skill(id, name, params, wait)` · `wait_skill(id, skill_id)` | 通用技能调用与等待 |
| `course_start(id, course_id)` · `course_stop(id)` · `stop(id)` | 航线与急停 |

错误抛出 `RelayError`，带 `code`、`status`、`message`、`message_zh` 与 `detail`。等待技能时，技能失败（例如操作员接管）抛出 `skill_failed`，被其他技能替换抛出 `skill_superseded`。HTTP 429 会按 `Retry-After` 间隔自动重试两次。

## 10. 迁移到真机

1. 按 `hardware.zh-CN.md` 完成飞机与 Wi-Fi 数传的设置，确认它以 `real-<ip>` 出现在机群中，例如 `real-10-7-7-36`。
2. 在 Relay 窗口完成操作员步骤：无桨确认杆量方向，确认桨叶与防护网，通过全部阻断类起飞前检查。
3. 在代码中替换飞机 ID。解锁需要 `confirm: "ARM"`。位置类技能（`goto`、`rtl`、航线）需要本地定位源；没有定位源时，只在定高类模式下使用 `takeoff`、`hold` 与 `land`。
4. 窗口前始终保留操作员。附身与急停随时优先于任何智能体。
