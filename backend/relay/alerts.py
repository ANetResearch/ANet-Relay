"""Alert computation and acknowledgement (R-16, 02 §14).

The fleet service reports the set of active conditions every housekeeping
tick; the book keeps each alert's first-seen time and acknowledgement until
the condition clears. Acknowledging only silences the visual interruption.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

TEXT: dict[str, tuple[str, str]] = {
    "link_delayed": ("{vehicle} heartbeat delayed {age} s", "{vehicle} 心跳延迟 {age} 秒"),
    "link_lost": ("{vehicle} link lost {age} s. Sticks stopped", "{vehicle} 链路丢失 {age} 秒，已停止杆量"),
    "battery_warn": ("{vehicle} battery low: {voltage} V", "{vehicle} 电压偏低：{voltage} V"),
    "battery_critical": ("{vehicle} battery critical: {voltage} V", "{vehicle} 电压严重不足：{voltage} V"),
    "crashed": ("{vehicle} crashed", "{vehicle} 坠毁"),
    "airborne_no_controller": ("{vehicle} is airborne with no controller", "{vehicle} 在空中且无人控制"),
    "mode_switch_failed": (
        "{vehicle} did not switch to {mode} for manual control",
        "{vehicle} 未能切换到手动控制模式 {mode}",
    ),
    "port_in_use": (
        "UDP {port} is in use by another ground station. Close it and retry",
        "UDP {port} 已被其他地面站占用，请关闭后重试",
    ),
}


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "-"


@dataclass
class Condition:
    code: str
    level: str  # info | warn | critical
    vehicle: str | None = None
    values: dict[str, Any] | None = None

    @property
    def id(self) -> str:
        return f"{self.code}:{self.vehicle or 'station'}"


class AlertBook:
    def __init__(self, wall: Callable[[], float]) -> None:
        self._wall = wall
        self._alerts: dict[str, dict[str, Any]] = {}

    def sync(self, conditions: list[Condition]) -> list[dict[str, Any]]:
        """Replace the active set; return alerts that are new (or escalated) in this call."""
        fresh: list[dict[str, Any]] = []
        seen: set[str] = set()
        for cond in conditions:
            seen.add(cond.id)
            en, zh = TEXT.get(cond.code, (cond.code, cond.code))
            values = _SafeDict(cond.values or {})
            text_en, text_zh = en.format_map(values), zh.format_map(values)
            current = self._alerts.get(cond.id)
            if current is None:
                current = {
                    "id": cond.id,
                    "vehicle": cond.vehicle,
                    "level": cond.level,
                    "code": cond.code,
                    "text_en": text_en,
                    "text_zh": text_zh,
                    "since": self._wall(),
                    "acked": False,
                }
                self._alerts[cond.id] = current
                fresh.append(current)
                continue
            if _rank(cond.level) > _rank(current["level"]):
                current["acked"] = False
                fresh.append(current)
            current.update(level=cond.level, text_en=text_en, text_zh=text_zh)
        for alert_id in [a for a in self._alerts if a not in seen]:
            del self._alerts[alert_id]
        return fresh

    def ack(self, alert_id: str) -> bool:
        alert = self._alerts.get(alert_id)
        if alert is None:
            return False
        alert["acked"] = True
        return True

    def view(self) -> list[dict[str, Any]]:
        return sorted(
            (dict(a) for a in self._alerts.values()), key=lambda a: (-_rank(a["level"]), a["since"])
        )


def _rank(level: str) -> int:
    return {"info": 0, "warn": 1, "critical": 2}.get(level, 0)
