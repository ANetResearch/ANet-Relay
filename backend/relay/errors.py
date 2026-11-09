"""Domain errors and the unified error body (03 §10.1).

Every failure the caller can act on is a ``RelayError`` with a stable ``code``.
Messages come from one catalogue so wording stays consistent in both languages.
"""

from __future__ import annotations

from typing import Any

# code -> (HTTP status, English template, Chinese template). Templates use str.format fields from detail.
CATALOGUE: dict[str, tuple[int, str, str]] = {
    "invalid_request": (400, "The request is not valid: {reason}.", "请求无效：{reason}。"),
    "confirm_required": (
        400,
        "Type {expected} to confirm this action.",
        "须输入 {expected} 以确认此操作。",
    ),
    "outside_safety_volume": (
        400,
        "The target is outside the safety volume.",
        "目标点超出安全区。",
    ),
    "unknown_mode": (400, "Mode {mode} is not available on this aircraft.", "这架飞机不支持模式 {mode}。"),
    "unknown_skill": (404, "There is no skill named {skill}.", "没有名为 {skill} 的技能。"),
    "param_unknown": (404, "Parameter {name} is not in the parameter table.", "参数表中没有参数 {name}。"),
    "param_type": (
        400,
        "Parameter {name} needs a value of type {type}.",
        "参数 {name} 需要 {type} 类型的值。",
    ),
    "invalid_address": (400, "{host} is not a valid IPv4 address.", "{host} 不是有效的 IPv4 地址。"),
    "invalid_course": (400, "The course is not valid: {reason}.", "航线无效：{reason}。"),
    "auth_required": (
        401,
        "This action needs an operator or session token.",
        "此操作需要操作员令牌或会话令牌。",
    ),
    "invalid_operator_token": (401, "The operator token is not valid.", "操作员令牌无效。"),
    "session_expired": (
        401,
        "The agent session does not exist or its lease has expired.",
        "智能体会话不存在或租约已到期。",
    ),
    "app_control_disabled": (
        403,
        "Agent control of the Relay application is turned off in Settings.",
        "设置中已关闭智能体对 Relay 应用的控制。",
    ),
    "operator_required": (
        403,
        "Only the Relay window can perform this action.",
        "只有 Relay 窗口可以执行此操作。",
    ),
    "untrusted_host": (
        403,
        "Relay only answers requests addressed to 127.0.0.1 or localhost.",
        "Relay 只响应发往 127.0.0.1 或 localhost 的请求。",
    ),
    "session_required": (403, "This action needs an agent session.", "此操作需要智能体会话。"),
    "not_found": (404, "Nothing is available at this path.", "此路径下没有资源。"),
    "vehicle_not_found": (404, "There is no aircraft with id {vehicle}.", "没有 ID 为 {vehicle} 的飞机。"),
    "course_not_found": (404, "There is no course with id {course}.", "没有 ID 为 {course} 的航线。"),
    "record_not_found": (404, "There is no record session {record}.", "没有编号为 {record} 的记录。"),
    "alert_not_found": (404, "There is no active alert {alert}.", "没有编号为 {alert} 的告警。"),
    "not_possessed": (409, "Possess this aircraft before flying it.", "须先附身这架飞机才能操控。"),
    "possessed_by_human": (
        409,
        "An operator is flying this aircraft. Agent control is suspended until release.",
        "操作员正在附身控制这架飞机，释放前智能体控制处于挂起状态。",
    ),
    "claim_suspended": (
        409,
        "Your claim is suspended while an operator flies this aircraft.",
        "操作员附身期间，你的认领处于挂起状态。",
    ),
    "claimed_by_other": (
        409,
        "Agent session {name} holds this aircraft.",
        "智能体会话 {name} 已认领这架飞机。",
    ),
    "not_claimed": (
        409,
        "Claim this aircraft before sending control commands.",
        "发送控制指令前须先认领这架飞机。",
    ),
    "preflight_blocked": (
        409,
        "Preflight checks block arming: {items}.",
        "起飞前检查未通过，禁止解锁：{items}。",
    ),
    "arm_rejected": (409, "The autopilot did not arm: {reason}.", "飞控未解锁：{reason}。"),
    "mode_rejected": (409, "The autopilot did not switch to {mode}.", "飞控未切换到 {mode}。"),
    "must_be_disarmed": (
        409,
        "Disarm the aircraft before {action}.",
        "{action}前须先给飞机上锁。",
    ),
    "command_not_allowed": (
        400,
        "Command {command} is not in the command catalogue.",
        "命令 {command} 不在命令目录中。",
    ),
    "message_not_seen": (
        404,
        "No {message} message has been received from this aircraft.",
        "尚未从这架飞机收到 {message} 消息。",
    ),
    "param_not_found": (
        404,
        "The autopilot did not return parameter {name}.",
        "飞控未返回参数 {name}。",
    ),
    "not_armed": (409, "Arm the aircraft first.", "须先解锁飞机。"),
    "armed": (409, "Disarm the aircraft first.", "须先给飞机上锁。"),
    "not_airborne": (409, "The aircraft must be in the air first.", "飞机须先起飞。"),
    "no_plan": (409, "No plan is running on this aircraft.", "这架飞机没有正在执行的动作序列。"),
    "no_sequence": (
        409,
        "No timeline sequence is flying on this aircraft.",
        "这架飞机没有正在执行的时间轴序列。",
    ),
    "crashed": (409, "The aircraft has crashed. Reset it before flying.", "飞机已坠毁，复位后才能继续飞行。"),
    "capability_missing": (
        409,
        "This aircraft lacks the {required} capability.",
        "这架飞机不具备 {required} 能力。",
    ),
    "no_position_estimate": (
        409,
        "This aircraft reports no local position estimate.",
        "这架飞机没有报告本地位置估计。",
    ),
    "sim_only": (409, "This action is only available for simulated aircraft.", "此操作仅适用于模拟机。"),
    # LAN arena (05 §7.4, WP-D)
    "arena_off": (409, "Join the arena before firing.", "须先加入联机场地才能射击。"),
    "arena_down": (
        409,
        "This aircraft was shot down. Reset it before firing.",
        "这架飞机已被击落，复位后才能射击。",
    ),
    "real_only": (409, "This action is only available for real aircraft.", "此操作仅适用于真机。"),
    "sim_limit": (409, "Relay runs at most {limit} simulated aircraft.", "Relay 最多运行 {limit} 架模拟机。"),
    "course_preset_readonly": (409, "Built-in courses cannot be changed.", "内置航线不可修改。"),
    "course_not_running": (409, "No course is running on this aircraft.", "这架飞机没有正在进行的航线。"),
    "param_readback_mismatch": (
        409,
        "Parameter {name} reads back {readback}, not {value}.",
        "参数 {name} 回读值为 {readback}，不是 {value}。",
    ),
    "param_timeout": (409, "The autopilot did not confirm parameter {name}.", "飞控未确认参数 {name}。"),
    "ui_action_failed": (409, "The window could not do that: {error}.", "窗口未能执行该操作：{error}。"),
    "refly_unavailable": (
        409,
        "This record cannot be re-flown in {mode} mode: {reason}.",
        "这条记录无法以 {mode} 方式再飞：{reason}。",
    ),
    "scan_running": (409, "A network scan is already running.", "网络扫描已在进行。"),
    "bridge_unknown_firmware": (
        409,
        "This bridge does not expose a known setup interface.",
        "这块数传没有提供可识别的设置接口。",
    ),
    "rate_limited": (
        429,
        "Too many requests from this session. Retry shortly.",
        "该会话请求过于频繁，请稍后重试。",
    ),
    "link_not_ready": (503, "The link to this aircraft is {state}.", "与这架飞机的链路处于 {state} 状态。"),
    "bridge_unreachable": (
        503,
        "The bridge at {host} did not answer: {reason}.",
        "{host} 的数传无响应：{reason}。",
    ),
    "ui_unavailable": (
        503,
        "No Relay window is connected to receive this command.",
        "没有已连接的 Relay 窗口可接收该指令。",
    ),
    "ui_timeout": (504, "The Relay window did not answer in time.", "Relay 窗口未能及时响应。"),
    "backend_busy": (503, "The fleet service did not answer in time.", "机群服务未能及时响应。"),
    "listen_unavailable": (503, "UDP {port} is not available: {reason}.", "UDP {port} 不可用：{reason}。"),
    # bench procedures (WP-B, 05 §4.4)
    "procedure_unknown": (404, "There is no procedure named {procedure}.", "没有名为 {procedure} 的流程。"),
    "procedure_running": (
        409,
        "A procedure is already running on this aircraft.",
        "这架飞机已有流程在运行。",
    ),
    "procedure_not_found": (
        404,
        "No procedure has run on aircraft {vehicle}.",
        "飞机 {vehicle} 尚未运行过流程。",
    ),
    "no_procedure": (409, "No procedure is running on this aircraft.", "这架飞机没有正在运行的流程。"),
    "procedure_step_mismatch": (
        409,
        "The procedure is at step {current}, not {step}.",
        "流程当前处于 {current}，不是 {step}。",
    ),
    "procedure_not_waiting": (
        409,
        "Step {step} is not waiting for an answer.",
        "步骤 {step} 未在等待回答。",
    ),
    "test_run_not_found": (404, "There is no bench report {run}.", "没有编号为 {run} 的标定报告。"),
    # WP-1 onboard programs and the agent flight level (06 §2, §5)
    "sticks_only": (
        403,
        "Agents fly with sticks and programs only at this control level; {action} is not available.",
        "当前控制等级下智能体只能通过摇杆或程序飞行，{action} 不可用。",
    ),
    "program_not_found": (404, "There is no program {program}.", "没有程序 {program}。"),
    "revision_not_found": (
        404,
        "Program {program} has no revision {rev}.",
        "程序 {program} 没有版本 {rev}。",
    ),
    "program_readonly": (
        409,
        "Built-in programs cannot be changed; copy it first.",
        "内置程序不可修改，请先复制。",
    ),
    "program_invalid": (400, "The program is not valid: {reason}.", "程序无效：{reason}。"),
    "program_param_invalid": (
        400,
        "Parameter {name} is not valid: {reason}.",
        "参数 {name} 无效：{reason}。",
    ),
    "program_load_failed": (409, "The program could not start: {reason}.", "程序无法启动：{reason}。"),
    "no_program": (409, "No program is deployed on this aircraft.", "这架飞机没有部署程序。"),
    "program_not_paused": (
        409,
        "The program is {status}; deploy it again.",
        "程序处于 {status} 状态，请重新部署。",
    ),
    "programs_not_allowed": (
        409,
        "Allow programs on this aircraft in the inspector first.",
        "须先在检查器中允许这架飞机运行程序。",
    ),
    "program_host_unavailable": (
        503,
        "The program host is not available: {reason}.",
        "程序宿主不可用：{reason}。",
    ),
    # WP-2b challenges, duels and the headless evaluator (06 §3.2-§3.4)
    "challenge_not_found": (404, "There is no challenge {challenge}.", "没有题目 {challenge}。"),
    "job_not_found": (404, "There is no evaluation job {job}.", "没有评测任务 {job}。"),
    "trace_not_found": (404, "There is no trace {trace}.", "没有轨迹 {trace}。"),
    "judge_queue_full": (
        429,
        "{limit} evaluation jobs are already waiting; try again when one has finished.",
        "已有 {limit} 个评测任务在排队，请待其中一个完成后再提交。",
    ),
    "judge_unavailable": (503, "The evaluator is not available: {reason}.", "评测器不可用：{reason}。"),
    "judge_failed": (500, "The evaluation failed: {reason}.", "评测失败：{reason}。"),
    "judge_timeout": (504, "The evaluation took too long and was stopped.", "评测耗时过长，已停止。"),
    "judge_setup_failed": (500, "The scenario could not be set up: {reason}.", "场景无法建立：{reason}。"),
    # WP-B dogfight scenario and matches (07 §3-§4)
    "dogfight_real_aircraft": (
        400,
        "Real aircraft never take part in a dogfight: {vehicle}.",
        "真机永不参与狗斗：{vehicle}。",
    ),
    "dogfight_running": (409, "A dogfight is already running.", "已有一场狗斗在进行。"),
    "dogfight_not_running": (409, "No dogfight is running.", "当前没有进行中的狗斗。"),
    "dogfight_no_aircraft": (
        409,
        "Not enough free simulators: {needed} needed, {free} free, at most {limit} in all.",
        "可用模拟机不足：需要 {needed} 架，空闲 {free} 架，最多 {limit} 架。",
    ),
    "match_running": (409, "A match is already running.", "已有一局对局在进行。"),
    # firmware twins (11 §3-§6, §10)
    "pack_missing": (404, "Autopilot pack {pack} is not installed.", "未安装飞控运行包 {pack}。"),
    "pack_unsupported": (
        400,
        "The autopilot pack cannot be used: {reason}.",
        "无法使用该飞控运行包：{reason_zh}。",
    ),
    "twin_start_failed": (
        503,
        "The firmware twin did not start: {reason}.",
        "固件孪生未能启动：{reason_zh}。",
    ),
    "twin_limit": (
        409,
        "Relay runs at most {limit} firmware twins at a time.",
        "Relay 同时最多运行 {limit} 个固件孪生。",
    ),
    "frame_unsupported": (
        409,
        "A firmware twin cannot fly this frame: {reason}.",
        "固件孪生不支持该机型：{reason_zh}。",
    ),
    "twin_unsupported": (
        409,
        "A firmware twin has no {operation}.",
        "固件孪生不支持{operation_zh}。",
    ),
    "profile_not_found": (404, "There is no twin profile {profile}.", "没有孪生配置 {profile}。"),
    "params_incomplete": (
        409,
        "Read the aircraft's whole parameter table first ({received} of {total} received).",
        "须先读完飞机的全部参数（已收到 {received}/{total}）。",
    ),
    # serial links and hardware in the loop (11 §7, §10); serial_unavailable is 409 when the port is busy
    "serial_unavailable": (
        400,
        "The serial port {device} cannot be used: {reason}.",
        "无法使用串口 {device}：{reason_zh}。",
    ),
    "hitl_unavailable": (
        409,
        "Hardware in the loop is not available: {reason}.",
        "硬件在环不可用：{reason_zh}。",
    ),
    "hitl_restore_mismatch": (
        409,
        "{count} parameters differ from the hardware-in-the-loop backup; the backup file is kept.",
        "{count} 项参数与硬件在环前的备份不一致，备份文件已保留。",
    ),
}


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "-"


class RelayError(Exception):
    """An operation failed for a reason the caller can act on."""

    def __init__(
        self,
        code: str,
        status: int | None = None,
        message_en: str | None = None,
        message_zh: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        detail = dict(detail or {})
        entry = CATALOGUE.get(code)
        values = _SafeDict({k: v for k, v in detail.items() if isinstance(v, (str, int, float))})
        if entry is not None:
            status = status or entry[0]
            message_en = message_en or entry[1].format_map(values)
            message_zh = message_zh or entry[2].format_map(values)
        super().__init__(message_en or code)
        self.code = code
        self.status = status or 400
        self.message_en = message_en or code
        self.message_zh = message_zh or code
        self.detail = detail

    def body(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message_en": self.message_en,
                "message_zh": self.message_zh,
                "detail": self.detail,
            }
        }


def err(code: str, **detail: Any) -> RelayError:
    return RelayError(code, detail=detail)
