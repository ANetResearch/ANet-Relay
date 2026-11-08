"""Event bus: monotonic sequence numbers in a 5000-entry ring (03 §10.5)."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from .config import EVENT_RING

# kind -> (English, Chinese). Fields come from the event data plus {vehicle} (label) and {session} (name).
TEMPLATES: dict[str, tuple[str, str]] = {
    "vehicle_discovered": ("{vehicle} discovered via {via}.", "经 {via} 发现 {vehicle}。"),
    "vehicle_online": ("{vehicle} is online.", "{vehicle} 已上线。"),
    "vehicle_offline": ("{vehicle} is offline.", "{vehicle} 已离线。"),
    "link_delayed": ("{vehicle} heartbeat is delayed.", "{vehicle} 心跳延迟。"),
    "link_lost": (
        "{vehicle} link lost. Sticks and skills stopped.",
        "{vehicle} 链路丢失，已停止杆量与技能。",
    ),
    "link_restored": (
        "{vehicle} link restored. Control does not resume automatically.",
        "{vehicle} 链路恢复，控制不会自动恢复。",
    ),
    "armed": ("{vehicle} armed.", "{vehicle} 已解锁。"),
    "disarmed": ("{vehicle} disarmed.", "{vehicle} 已上锁。"),
    "mode_changed": ("{vehicle} mode is {mode}.", "{vehicle} 模式切换为 {mode}。"),
    "statustext": ("{vehicle}: {text}", "{vehicle}：{text}"),
    "command_ack": ("{vehicle} {command}: {result}.", "{vehicle} {command}：{result}。"),
    "possession_started": (
        "Operator possessed {vehicle} ({input_label}).",
        "操作员附身 {vehicle}（{input_label}）。",
    ),
    "possession_released": (
        "Released {vehicle}. No agent holds it.",
        "已释放 {vehicle}，没有智能体认领它。",
    ),
    "relay_handback": ("{vehicle} handed back to {name}.", "{vehicle} 已交还给 {name}。"),
    "claim_started": ("{name} claimed {vehicle}.", "{name} 认领了 {vehicle}。"),
    "claim_released": ("{name} released its claim on {vehicle}.", "{name} 放弃了对 {vehicle} 的认领。"),
    "agent_session_started": ("Agent session {name} started.", "智能体会话 {name} 已开始。"),
    "agent_session_closed": ("Agent session {name} closed.", "智能体会话 {name} 已结束。"),
    "agent_session_expired": ("Agent session {name} lease expired.", "智能体会话 {name} 租约到期。"),
    "skill_started": ("{vehicle} started {skill}.", "{vehicle} 开始执行 {skill}。"),
    "skill_finished": ("{vehicle} finished {skill}.", "{vehicle} 完成 {skill}。"),
    "skill_failed": ("{vehicle} {skill} stopped: {detail}.", "{vehicle} 的 {skill} 已中止：{detail}。"),
    "plan_started": ("{vehicle} started a {count}-step plan.", "{vehicle} 开始执行 {count} 步动作序列。"),
    "plan_step": ("{vehicle} completed step {step} of {count}.", "{vehicle} 完成第 {step}/{count} 步。"),
    "plan_finished": ("{vehicle} finished its plan.", "{vehicle} 完成动作序列。"),
    "plan_failed": (
        "{vehicle} plan stopped at step {step}: {detail}.",
        "{vehicle} 的动作序列在第 {step} 步中止：{detail}。",
    ),
    "plan_cancelled": ("{vehicle} plan cancelled.", "{vehicle} 的动作序列已取消。"),
    "stop": ("Stop on {vehicle}: throttle cut and disarm requested.", "{vehicle} 急停：油门归零并申请上锁。"),
    "stop_all": ("Stop on all aircraft.", "全部飞机急停。"),
    "battery_warn": ("{vehicle} battery low: {voltage} V.", "{vehicle} 电压偏低：{voltage} V。"),
    "battery_critical": ("{vehicle} battery critical: {voltage} V.", "{vehicle} 电压严重不足：{voltage} V。"),
    "crashed": ("{vehicle} crashed{detail}.", "{vehicle} 坠毁{detail}。"),  # WP-A: reason_code, point (07 §2)
    "airborne_no_controller": ("{vehicle} is airborne with no controller.", "{vehicle} 在空中且无人控制。"),
    "param_set": ("{vehicle} parameter {name} set to {value}.", "{vehicle} 参数 {name} 已设为 {value}。"),
    "sim_added": ("Simulated aircraft {vehicle} added.", "已添加模拟机 {vehicle}。"),
    "sim_removed": ("Simulated aircraft {vehicle} removed.", "已删除模拟机 {vehicle}。"),
    "bridge_configured": (
        "Bridge {host} settings written. The bridge is restarting.",
        "数传 {host} 设置已写入，正在重启。",
    ),
    "course_started": ("{vehicle} started {course}.", "{vehicle} 开始穿环航线 {course}。"),
    "ring_passed": ("{vehicle} passed ring {ring} of {total}.", "{vehicle} 通过第 {ring}/{total} 个环。"),
    "course_finished": (
        "{vehicle} finished {course} in {total_text}.",
        "{vehicle} 完成 {course}，用时 {total_text}。",
    ),
    "course_aborted": ("{vehicle} {course} aborted: {reason}.", "{vehicle} 的 {course} 已中止：{reason}。"),
    "device_command": (
        "{vehicle} sent {command}: {result}.",
        "{vehicle} 发送 {command}：{result}。",
    ),
    "motor_test": (
        "{vehicle} motor test: {motor} at {throttle_pct}% for {seconds} s.",
        "{vehicle} 电机测试：{motor}，{throttle_pct}%，{seconds} 秒。",
    ),
    "motor_test_stopped": ("{vehicle} motor test stopped.", "{vehicle} 电机测试已停止。"),
    "actuator_set": (
        "{vehicle} output {index} set to {setting}.",
        "{vehicle} 输出 {index} 设为 {setting}。",
    ),
    "rc_override": (
        "{vehicle} RC channel override started.",
        "{vehicle} 开始覆盖遥控通道。",
    ),
    "message_interval": (
        "{vehicle} {message} interval set to {rate}.",
        "{vehicle} 的 {message} 间隔设为 {rate}。",
    ),
    "home_set": ("{vehicle} home position set.", "{vehicle} 已设置返航点。"),
    "rebooting": ("{vehicle} flight controller is rebooting.", "{vehicle} 飞控正在重启。"),
    "calibration_started": (
        "{vehicle} {sensor} calibration started.",
        "{vehicle} 开始 {sensor} 校准。",
    ),
    "terminated": (
        "{vehicle} flight terminated: motors stopped.",
        "{vehicle} 飞行已终止：电机停转。",
    ),
    "app_command": (
        "{by} ran {action} in the Relay window.",
        "{by} 在 Relay 窗口执行了 {action}。",
    ),
    "refly_started": (
        "{vehicle} started re-flying record {session_id} ({mode}).",
        "{vehicle} 开始再飞记录 {session_id}（{mode}）。",
    ),
    "refly_finished": (
        "{vehicle} finished re-flying record {session_id}.",
        "{vehicle} 完成再飞记录 {session_id}。",
    ),
    "refly_failed": (
        "{vehicle} stopped re-flying record {session_id}: {detail}.",
        "{vehicle} 的再飞记录 {session_id} 已中止：{detail}。",
    ),
    "listen_error": ("UDP listen failed: {error}", "UDP 监听失败：{error}"),
    # WP-C simulator flight model (05 §5.2).
    "sim_assist_changed": ("{vehicle} hover assist {detail}.", "{vehicle} 悬停辅助{detail}。"),
    # WP-2a airframe defects (06 §4).
    "sim_airframe_changed": (
        "{vehicle} airframe set to {preset_en} (seed {seed}).",
        "{vehicle} 机体已设为{preset_zh}（种子 {seed}）。",
    ),
    # bench procedures (WP-B, 05 §4.4)
    "procedure_started": ("{vehicle} started {title_en}.", "{vehicle} 开始{title_zh}。"),
    "procedure_step": (
        "{vehicle} {title_en}: {step} {step_en} {verdict_en}.",
        "{vehicle} {title_zh}：{step} {step_zh}{verdict_zh}。",
    ),
    "procedure_waiting": (
        "{vehicle} {title_en} waits at {step} {step_en}: {prompt_en}",
        "{vehicle} {title_zh}在 {step} {step_zh}等待：{prompt_zh}",
    ),
    "procedure_finished": ("{vehicle} {title_en} {verdict_en}.", "{vehicle} {title_zh}完成：{verdict_zh}。"),
    "procedure_aborted": (
        "{vehicle} {title_en} aborted: {detail}.",
        "{vehicle} {title_zh}已中止：{detail}。",
    ),
    # LAN arena (05 §7.4, WP-D); {detail} is filled from detail_en / detail_zh
    "peer_joined": ("{callsign} joined the arena.", "{callsign} 已加入联机场地。"),
    "peer_left": ("{callsign} left the arena.", "{callsign} 已离开联机场地。"),
    "arena_hit": ("{vehicle} {detail}.", "{vehicle} {detail}。"),
    "arena_down": ("{vehicle} {detail}.", "{vehicle} {detail}。"),
    # WP-1 onboard programs (06 §2.6); {detail} is filled from detail_en / detail_zh
    "program_started": (
        "{vehicle} started program {name} (revision {rev}).",
        "{vehicle} 开始运行程序 {name}（版本 {rev}）。",
    ),
    "program_stopped": (
        "{vehicle} program {name} stopped: {detail}.",
        "{vehicle} 的程序 {name} 已停止：{detail}。",
    ),
    "program_paused": (
        "{vehicle} program {name} paused: {detail}.",
        "{vehicle} 的程序 {name} 已暂停：{detail}。",
    ),
    "program_resumed": ("{vehicle} resumed program {name}.", "{vehicle} 恢复运行程序 {name}。"),
    "program_faulted": (
        "{vehicle} program {name} faulted: {detail}. Control returned to the safety layer.",
        "{vehicle} 的程序 {name} 故障：{detail}。控制已交还安全层。",
    ),
    "program_host_restarted": (
        "The program host of {vehicle} was restarted: {reason}.",
        "{vehicle} 的程序宿主已重启：{reason}。",
    ),
    "program_gate": ("{vehicle}: onboard programs {detail}.", "{vehicle}：机载程序{detail}。"),
    # WP-2b challenges and duels (06 §3.4); {detail} is filled from detail_en / detail_zh
    "judge_finished": ("{detail}.", "{detail}。"),
    # WP-A collisions (07 §2); {detail} is filled from detail_en / detail_zh
    "collision": (
        "{vehicle} collided with {other} at {v_rel} m/s.{detail}",
        "{vehicle} 与{other_zh}相撞，接近速度 {v_rel} m/s。{detail}",
    ),
    "ring_touch": (
        "{vehicle} touched ring {ring} while passing it.",
        "{vehicle} 穿过第 {ring} 个环时擦碰环体。",
    ),
    # WP-B dogfight scenario and matches (07 §3-§4); {detail} from detail_en / detail_zh
    "dogfight_started": (
        "Dogfight started: {aircraft} aircraft for {duration} s ({where}).",
        "狗斗开始：{aircraft} 架飞机，{duration} s（{where_zh}）。",
    ),
    "dogfight_finished": ("Dogfight ended: {detail}.", "狗斗结束：{detail}。"),
    "match_started": ("Match started for {duration} s.", "对局开始，时长 {duration} s。"),
    "match_finished": ("Match ended: {detail}.", "对局结束：{detail}。"),
}

INPUT_LABELS = {"keyboard": ("keyboard", "键盘"), "screen": ("on-screen sticks", "屏幕摇杆")}


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "-"


def render(kind: str, values: dict[str, Any]) -> tuple[str, str]:
    en, zh = TEMPLATES.get(kind, (kind, kind))
    safe_en = _SafeDict(values)
    safe_zh = _SafeDict(values)
    if "input" in values:
        if values["input"] == "gamepad":
            safe_en["input_label"] = f"controller {values.get('pad') or 1}"
            safe_zh["input_label"] = f"手柄 {values.get('pad') or 1}"
        else:
            labels = INPUT_LABELS.get(values["input"], (values["input"], values["input"]))
            safe_en["input_label"], safe_zh["input_label"] = labels
    for key in ("reason_en", "detail_en"):
        if key in values:
            safe_en[key[:-3]] = values[key]
    for key in ("reason_zh", "detail_zh"):
        if key in values:
            safe_zh[key[:-3]] = values[key]
    return en.format_map(safe_en), zh.format_map(safe_zh)


class EventBus:
    def __init__(self, size: int = EVENT_RING, wall: Callable[[], float] = time.time) -> None:
        self._ring: deque[dict[str, Any]] = deque(maxlen=size)
        self.size = size
        self._seq = 0
        self._lock = threading.Lock()
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._wall = wall

    @property
    def last_seq(self) -> int:
        return self._seq

    def add_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def emit(
        self,
        kind: str,
        vehicle: str | None = None,
        session: str | None = None,
        data: dict[str, Any] | None = None,
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Publish an event. ``labels`` supplies display names ({vehicle}, {name}) for the text only."""
        data = dict(data or {})
        values = {**data, **(labels or {})}
        values.setdefault("vehicle", vehicle or "-")
        text_en, text_zh = render(kind, values)
        with self._lock:
            self._seq += 1
            event = {
                "seq": self._seq,
                "t": self._wall(),
                "kind": kind,
                "vehicle": vehicle,
                "session": session,
                "text_en": text_en,
                "text_zh": text_zh,
                "data": data,
            }
            self._ring.append(event)
        for callback in list(self._listeners):
            try:
                callback(event)
            except Exception:  # a broken listener must not break the fleet loop
                pass
        return event

    def after(self, seq: int, limit: int = 500, vehicle: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = [e for e in self._ring if e["seq"] > seq and (vehicle is None or e["vehicle"] == vehicle)]
        return items[:limit]
