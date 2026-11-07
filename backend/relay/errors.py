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
    "backend_busy": (503, "The fleet service did not answer in time.", "机群服务未能及时响应。"),
    "listen_unavailable": (503, "UDP {port} is not available: {reason}.", "UDP {port} 不可用：{reason}。"),
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
