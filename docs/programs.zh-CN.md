# 机载程序、题目与对抗

[English](programs.md) · 设计契约：[06 v0.4.0](design/06-v040.zh-CN.md) §2、§3、§7

机载程序是一段受限 Python，在本机隔离运行，每 20 ms（50 Hz）读取一次观测、输出一组摇杆量。智能体不再逐帧“开飞机”，而是给飞机写“大脑”：写程序、跑测试、部署、实时调参、提交题目、组织对抗，并读日志与回放。

| 层 | 频率 | 由谁实现 |
|---|---|---|
| 反射层：姿态自稳、电机混控 | 250 Hz 以上 | 飞控（真机）/ 模拟飞控（模拟机） |
| 行为层：机载程序（状态机） | 50 Hz | 人或智能体写的程序，输入观测、输出摇杆 |
| 决策层：智能体 | 秒到分钟 | Copilot / 外部智能体：写程序、改参数、跑测试与对抗 |

程序与智能体都**只用摇杆**：输出只有横滚、俯仰、偏航、油门四个量和射击开关；“起飞到 1 m”“前进 1 m”这类高层接口对它们关闭（缺省控制等级 `sticks`，见 [智能体接口](agent-api.zh-CN.md)）。

## 1. 快速开始：实验室工作区

1. 按 **⌘4 / Ctrl+4**（或顶栏 **实验室**）打开实验室。
2. 左栏 **程序** 中点 **+** 新建程序，或选中一个示例后用 **复制** 得到可编辑的副本（示例只读）。
3. 在编辑器中修改源码。错误在行内标出（点状下划线和行号旁的方块），底部状态栏显示第一个问题，点击跳到该处。**⌘S / Ctrl+S** 保存，每次保存产生一个新版本。
4. **测试** 页一键运行单元测试，每条显示通过与否、实际输出和不满足的期望。
5. **运行** 页选择飞机（先用模拟机）与模式，点 **部署版本 N**。下方显示状态、状态名、帧率、故障数与日志尾部。
6. **参数** 页：程序运行时拖动滑块即实时生效；未部署时修改的是随程序保存的参数值。
7. **评测** 页把已保存的版本提交到题目；**对抗** 页让 2 至 8 个程序互相比赛。两处都能 **打开回放**，在飞行工作区的舞台上多机回放。

舞台上程序控制的飞机标签为 `PROGRAM · <名称> · <状态>`。飞行工作区检查器的 **控制** 页有 **程序** 分组：部署、停止、恢复与状态。

## 2. 程序结构

```python
PARAMS = {"target_alt": 1.0, "hover": 0.5, "kp": 0.3, "kd": 0.15}   # 可实时调整的参数
PARAM_RANGES = {"target_alt": [0.3, 3.0]}                            # 可选：参数范围


def init(ctx):
    """部署时调用一次。"""
    ctx.state = "climb"


def step(obs, ctx):
    """每 20 ms 调用一次，必须返回 Sticks。"""
    p = ctx.params
    if not obs.armed or obs.alt is None:
        ctx.state = "idle"
        return Sticks(throttle=0.0)
    error = p["target_alt"] - obs.alt
    ctx.state = "hold" if abs(error) < 0.1 else "climb"
    return Sticks(throttle=clamp(p["hover"] + p["kp"] * error - p["kd"] * obs.vz, 0.0, 1.0))
```

- `PARAMS`：数值、布尔或不超过 32 个字符的字符串。类型与范围按缺省值推断：布尔保持布尔，整数保持整数；范围取 `PARAM_RANGES[名称]`，未给出时为 `|值| ≤ max(10 × |缺省值|, 1)`（整数为 10）。
- `Sticks(roll=0, pitch=0, yaw=0, throttle=0, fire=False)`：roll / pitch / yaw ∈ [−1, 1]，throttle ∈ [0, 1]，与手柄同一坐标系。超界截断，非有限值视为本帧失败。
- `ctx`：`mem`（跨帧保留的字典）、`params`（只读，随实时调参更新）、`state`（当前状态名，≤ 32 个字符，显示在舞台标签与检查器中）、`log(*args)`（写入日志，每帧最多 4 行）、`t`（部署后的秒数）、`rng`（以部署种子初始化的确定性随机数）。`print()` 同样写入日志。
- 失败：`step` 返回 `None` 或抛出异常时，本帧输出安全杆量（姿态回中、油门保持上一帧）并记一次故障；连续 25 次（0.5 s）故障，程序进入 `faulted`，控制交还安全层。

## 3. 语言

Python 3 语法的子集，部署前由 AST 白名单校验：

- 允许：函数定义（无装饰器）、赋值、`if` / `for` / `while` / `break` / `continue` / `pass` / `return`、`try` / `except Exception`、表达式、推导式、lambda、f-string、`assert`；`import math` 或 `from math import ...` 是唯一允许的导入。
- 禁止：其他所有导入、类定义、`global` / `nonlocal`、`with`、`yield` / `async`、任何以 `_` 开头的名称或属性，以及 `exec`、`eval`、`compile`、`open`、`getattr`、`setattr`、`globals`、`locals`、`vars`、`dir`、`type`、`__import__`、`input`、`breakpoint`。
- 内置函数：`abs min max round sum len range enumerate zip sorted reversed int float bool str list dict tuple set isinstance any all map filter divmod pow print`。
- 源码不超过 64 KB。

每次部署在各自独立的程序宿主进程中运行，没有网络与文件访问，一个程序无法读取、拖慢或拖垮其他飞机的程序；单帧预算 2 ms（软限，超出计数），某帧 15 ms 未返回则输出安全杆量，连续 3 帧超时程序以 `faulted` 结束。宿主常驻内存超过 512 MB 时在所有平台上都会被终止，程序以 `memory` 故障结束。程序只来自本机（界面、Copilot、本机接口）；局域网对抗只交换飞行状态，从不交换代码。

## 4. 观测 `obs`

只包含机载能得到的量，属性访问，缺失为 `None`。

| 字段 | 单位 | 说明 |
|---|---|---|
| `t`、`dt` | s | 程序时间、本帧间隔 |
| `armed`、`airborne`、`mode` | — | 解锁、离地、飞控模式名 |
| `roll`、`pitch`、`yaw` | deg | 姿态；yaw 为航向 0–360 |
| `p`、`q`、`r` | deg/s | 机体角速度 |
| `ax`、`ay`、`az` | m/s² | 机体加速度（模拟机给出；真机有 IMU 消息时给出） |
| `alt`、`vz` | m、m/s | 气压高度（相对起飞点）与垂直速度；无气压计时为 `None` |
| `pos`、`vel` | m、m/s（ENU） | 仅有定位源时；模拟机由场景决定，带噪声，10 Hz 更新 |
| `battery` | `{v_cell, pct}` | 每节电压与估计电量 |
| `throttle_out` | 0–1 | 飞控实际输出的平均电机量 |
| `hp`、`ammo`、`reloading` | — | 对抗场中的生命值、弹药、装填中 |
| `contacts` | 列表 | 传感器看到的其他飞机：`id, team, dist, bearing, elevation, rel, vrel, hp, closing`（机体航向坐标系，30 m 内，最多 16 个；`closing` 为接近速度，m/s） |
| `collision` | `{t_ago, with, v_rel, normal}` 或 `None` | 最近 1 s 内的碰撞：撞到的对象（飞机 id、远端键或 `ring:<n>`）、接近速度（m/s）与指向本机的接触法向（前、右、上） |
| `arena` | `{radius, floor, ceiling}` | 场地边界（对抗场与题目中） |
| `sticks_in` | Sticks | 人的手柄当前量（副驾模式） |

模拟机的机体缺陷（重心偏移、水平校准误差、电机效率、桨损、噪声，见 06 §4）**不会**出现在观测中，只能通过飞行表现体现出来——就像真机一样，要靠飞出来。

## 5. `relay` 库

程序中直接可用，无需导入：

| 名称 | 说明 |
|---|---|
| `clamp(x, lo, hi)`、`wrap180(deg)`、`lerp(a, b, t)`、`deadband(x, d)`、`lowpass(prev, x, alpha)` | 常用数学 |
| `PID(kp, ki, kd, i_limit, out_limit)` | `pid.step(error, dt)`、`pid.reset()`；`i_limit` 限制积分项，`out_limit` 限制输出 |
| `StateMachine(initial)` | `sm.add(name, fn)`，`fn(obs, ctx) -> (Sticks, next_state | None)`（只返回 Sticks 表示停留）；`sm.step(obs, ctx)` 运行当前状态、处理转移、维护 `ctx.state`，并记录转移次数与驻留时间 |
| `Timer` | `Timer.start(ctx, name)`、`Timer.elapsed(ctx, name)`、`Timer.done(ctx, name, seconds)`，计时保存在 `ctx.mem` |

常见做法：先测出悬停油门（积分项学习配平）；高度用 PD 加积分；水平用小倾角配合航向；复杂行为写成状态机（搜索 → 接近 → 射击 → 规避）。

## 6. 单元测试

```json
{"name": "Below target climbs", "obs": {"alt": 0.5, "vz": 0.0},
 "expect": {"throttle": [0.55, 1.0], "roll": [0, 0], "state": "climb"}}
```

未给出的观测字段取“悬停在 1 m、姿态水平”的缺省值。可选 `mem`、`state`（初始状态）、`params`。`expect` 中 roll / pitch / yaw / throttle 为 `[下限, 上限]`，`fire` 为布尔，`state` 为文本。实验室的 **测试** 页对编辑器中的当前源码运行测试，无需先保存。

## 7. 部署与安全

- 模式：`pilot` 程序独占摇杆；`copilot` 人的手柄量作为 `obs.sticks_in` 输入，程序输出替代之。
- 权限：操作员或该飞机的控制者。部署后程序以“程序控制者”持有摇杆，时间线显示为 `PROGRAM · <名称>`。
- 人永远优先：人附身 → 程序立即暂停，释放后不会自动恢复，需要 **恢复**。急停、链路丢失、坠毁 → 程序停止。摇杆 0.8 s 超时规则不变。
- 真机缺省禁止。操作员在检查器 **控制 → 程序** 中开启 **Allow programs on this aircraft**（确认词 `PROGRAM`，每次连接重置），且起飞前检查通过后才可部署；真机上程序的射击输出被忽略。
- 实时调参：`PATCH /vehicles/{id}/program/params {名称: 值}` 是智能体在慢时间尺度上影响快回路的主通道。

## 8. 示例程序

| id | 说明 |
|---|---|
| `hover_trim` | 带积分修正的悬停：积分项学习悬停油门，姿态保持水平 |
| `liftoff` | 油门搜索起飞 |
| `patrol` | 无定位源的定时转向巡逻 |
| `hunter_easy` / `hunter_medium` / `hunter_hard` | 三档对抗机器人状态机：搜索 → 接近 → 射击 → 规避；目标快速逼近时闪避（`dodge_s`、`threat_dist`、`threat_closing`） |
| `rammer` | 撞击对手、从不开火：爬升 → 搜索 → 追击（对最近目标做比例导引）→ 撞击 → 恢复；模拟器报告碰撞时读取 `obs.collision` |

示例只读；复制后即可修改。

## 9. 题目

题目是确定性场景加判分规则，在无头评测器中以快于实时的速度运行。每题有可见样例种子与隐藏种子（隐藏种子的机体缺陷不同，防止只对一个种子调参）。分数 0–100，每题记录最好成绩与提交次数。

| id | 场景 | 通过条件 |
|---|---|---|
| `liftoff` | 载重使悬停油门约 0.62；无定位源 | 5 s 内离地并在 1.0 ± 0.2 m 保持 3 s |
| `hover` | 重心前偏 + 水平校准误差；有气压计、无定位源 | 20 s 内水平漂移 ≤ 0.8 m、高度 1.0 ± 0.25 m |
| `heading` | 电机效率不一致导致偏航漂移 | 15 s 航向误差 ≤ 10° |
| `square` | UWB 式定位（10 Hz，σ 5 cm） | 按序经过四角（半径 0.4 m），用时越短分越高 |
| `rings` | 现有穿环航线 | 全部过环，计时 |
| `pursuit` | 一架按脚本飞行的目标机 | 60 s 内保持在目标 2 m 内的时间比例 |
| `duel_easy` / `duel_medium` / `duel_hard` | 对手为内置机器人程序 | 胜利（击落对手且自身存活），平局按剩余生命值 |
| `ram_easy` | 出生环上悬停的靶机；不开火；有接触与 UWB 式定位 | 60 s 内撞击靶机使其坠毁；越快、撞击越重得分越高 |

实验室 **评测** 页：选择题目 → **提交版本 N** → 进度 → 分数、是否通过、各项指标与每个种子的结果；每个种子都可 **回放**。

## 10. 对抗与 Elo

**对抗** 页选择 2 至 8 个程序、局数与种子，**开始对抗**。结果为 Elo 排名表（名次、Elo、本次变化、胜–负–平）与各局结果（胜者、命中、存活时间），每局都可回放。同一（场景、种子、程序版本）结果完全确定。

实时对抗：把程序部署到本机多架模拟机，在对抗场中实时进行；局域网中其他终端部署的程序也在同一场地。

**狗斗**：`POST /scenarios/dogfight`（Copilot 与 MCP 的 `dogfight_start`，SDK 的 `dogfight_start`）一次调用布置 2 至 8 架模拟机的限时对战：出生环、机头朝向中心，每种角色一段程序（射手缺省 `hunter_medium` 开火，撞击手运行 `rammer`、从不开火），混战或 2 / 3 队（各程序的 `team` 参数），5 s 后重生，结束时降落，最终成绩保留在排行榜上（`GET /arena/leaderboard`：击落、存活、命中率、撞落）。程序只用摇杆级控制；操作员可随时附身任意一架。局域网中加入共享对局，否则场地以本机练习模式运行。仅限模拟机；详见 `agent-api.zh-CN.md`（狗斗、对局与排行榜）。

**回放**：打开轨迹后切到飞行工作区，舞台进入多机回放（所有飞机与子弹），底部为回放控制：播放 / 暂停、1x / 4x / 16x、拖动进度、退出。有飞机开始被手动飞行时回放自动关闭，舞台回到实时机群。

## 11. 接口

所有路径位于 `/api/v1`，错误体沿用 `{"error": {code, message_en, message_zh, detail}}`。读取匿名可用；修改需要操作员或智能体会话（版本作者为 `operator` 或会话名）。

| 方法与路径 | 说明 |
|---|---|
| `GET /programs` | 程序列表（含示例） |
| `POST /programs` | 新建 `{name, description?, source?, params?, tests?, note?, copy_of?}` |
| `GET /programs/{id}` · `PUT /programs/{id}` · `DELETE /programs/{id}` | 读取 / 修改（整体替换 `source` 或行补丁 `patch: [{start, end, text}]`；源码或参数变化产生新版本）/ 删除 |
| `GET /programs/{id}/revisions` | 版本（最近 50 个）`[{rev, source, params, author, note, t}]` |
| `POST /programs/{id}/validate` | 只做 AST 校验 `{source?}` → `{ok, errors: [{line, col, end_line, end_col, code, message_en, message_zh}], params, param_ranges}` |
| `POST /programs/{id}/test` | 运行单元测试 `{source?, tests?, rev?, seed?}` |
| `POST /vehicles/{id}/program` | 部署 `{program, rev?, params?, mode: pilot | copilot}` |
| `POST /vehicles/{id}/program/stop` · `.../resume` | 停止 / 恢复 |
| `PATCH /vehicles/{id}/program/params` | 实时调参 `{名称: 值}` |
| `GET /vehicles/{id}/program/log?after=` | 日志（环 500 行）`{lines: [{seq, t, level, text}], last_seq}` |
| `POST /vehicles/{id}/program/allow` | 真机允许程序 `{allow, confirm: "PROGRAM"}`（仅操作员） |
| `GET /challenges` | 题目与最好成绩 |
| `POST /challenges/{id}/submit` | 提交 `{program, rev?}` → 评测任务 |
| `GET /jobs/{id}` | `{state: queued | running | done | failed | cancelled, progress, result}` |
| `POST /duels` | 对抗 `{programs: [2..8], rounds, seed, scenario?}` → 评测任务 |
| `GET /ladder` | Elo 排名表 |
| `GET /traces/{id}` | 轨迹（格式见下） |

快照中每架飞机的 `vehicle.program` 为运行视图 `{id, name, rev, state, status: running | paused | faulted | stopped, reason, mode, fps, faults, overruns, last_error, params, log_seq}`，`vehicle.programs_allowed` 表示真机是否已允许程序。

Copilot、应用 MCP 服务器与 SDK 提供同名工具：`program_list`、`program_get`、`program_create`、`program_update`、`program_validate`、`program_test`、`program_deploy`、`program_stop`、`program_resume`、`program_params`、`program_status`、`program_log`、`challenge_list`、`challenge_submit`、`job_status`、`duel_run`、`ladder`、`airframe_set`。应用 MCP 的 `set_workspace` 接受 `lab`，可直接打开实验室。

### 轨迹格式

```json
{
  "id": "tr-…", "kind": "duel", "title": "duel_easy · seed 3", "hz": 10, "duration_s": 42.3,
  "arena": {"radius": 8, "floor": 0.3, "ceiling": 4},
  "aircraft": [{
    "id": "a", "name": "Hunter (easy)", "program": "hunter_easy", "rev": 1, "team": "a",
    "samples": {"t": [], "x": [], "y": [], "z": [], "roll": [], "pitch": [], "yaw": [],
                "throttle": [], "hp": [], "state": [], "armed": []}
  }],
  "shots": [{"id": "s1", "owner": "a", "t0": 3.2, "p": [0, 0, 1.5], "v": [35, 0, 0], "t_end": 3.6}],
  "events": [{"t": 3.6, "kind": "hit", "vehicle": "b", "by": "a"}]
}
```

时间为距开始的秒数；位置为场地坐标系 ENU 米（原点在场地中心）；角度为度，yaw 为顺时针航向；油门 0–1。`samples` 也可以是按同样顺序排列的行数组。子弹按对抗场积分器（1/240 s，阻力与重力）从 `(p, v)` 在 `t0` 起飞行，直到 `t_end`（命中）或 2.5 s 寿命结束，因此轨迹无需保存每颗子弹的采样。
