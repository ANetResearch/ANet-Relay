"""Flight health (03 §5.9): rolling diagnostics for "the aircraft is not steady after take-off".

Sampled from the fleet thread at SAMPLE_HZ over the last WINDOW_S seconds. Each item has a level
(ok | warn | bad | na) and a value; the renderer turns key + level into an explanation and a fix.
Flight items (oscillation, lean, yaw drift, altitude hold, hover throttle, motors) are only judged
while the aircraft is airborne; link, stick stream, vibration and battery are judged whenever armed.
Thresholds are deliberately coarse: they point at the likely cause, they are not a tuning tool.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections import deque
from dataclasses import dataclass
from typing import Any

SAMPLE_HZ = 10.0
WINDOW_S = 5.0
MIN_SAMPLES = 15  # 1.5 s of airborne samples before flight items are judged
CENTRED = 0.06  # stick deflection treated as "hands off" for lean and yaw drift
MOTOR_MIN_US = 1050  # SERVO_OUTPUT_RAW above this counts as a spinning motor
MOTOR_SAT_US = 1950

# (warn, bad) thresholds
RATE_RMS_DPS = (18.0, 40.0)  # roll/pitch rate RMS: oscillation or a loose airframe
LEAN_DEG = (3.0, 6.0)  # mean tilt with centred sticks: level calibration, CG, trims
YAW_DRIFT_DPS = (6.0, 15.0)  # mean yaw rate with centred yaw stick: motor direction, mag, yaw trim
CLIMB_STD_MS = (0.18, 0.4)  # vertical speed spread in altitude hold: baro noise, prop wash, ground effect
HOVER_LOW_PCT, HOVER_HIGH_PCT, HOVER_BAD_PCT = 20.0, 70.0, 82.0
MOTOR_SPREAD_US = (120.0, 220.0)  # mean output spread between motors: CG, prop, motor, frame twist
MOTOR_SAT_FRAC = (0.03, 0.12)
VIBRATION_MS2 = (15.0, 30.0)
SAG_PER_CELL_V = (0.45, 0.7)
LOSS_PCT = (3.0, 15.0)
RTT_MS = (120.0, 300.0)
STICK_IN_HZ = (20.0, 10.0)  # renderer → Relay stick updates while a person flies (lower is worse)
STICK_GAP_S = (0.15, 0.35)


@dataclass
class Sample:
    t: float
    airborne: bool
    roll: float  # deg
    pitch: float
    rollspeed: float  # deg/s
    pitchspeed: float
    yawspeed: float
    climb: float | None
    throttle_pct: float | None
    motors: list[int]
    stick_roll: float
    stick_pitch: float
    stick_yaw: float
    hold: bool  # altitude-holding mode with the throttle stick near centre


def _level(value: float | None, warn: float, bad: float, lower_is_worse: bool = False) -> str:
    if value is None:
        return "na"
    if lower_is_worse:
        return "bad" if value < bad else "warn" if value < warn else "ok"
    return "bad" if value >= bad else "warn" if value >= warn else "ok"


def _item(key: str, level: str, value: float | None, unit: str, detail: dict[str, Any] | None = None) -> dict:
    out: dict[str, Any] = {
        "key": key,
        "level": level,
        "value": None if value is None else round(value, 2),
        "unit": unit,
    }
    if detail:
        out["detail"] = detail
    return out


class FlightHealth:
    def __init__(self) -> None:
        self.samples: deque[Sample] = deque(maxlen=int(SAMPLE_HZ * WINDOW_S) + 2)
        self.stick_in: deque[float] = deque(maxlen=256)
        self.vibration_peak: dict[str, float] = {}
        self.clip_base: int | None = None
        self.clip_now: int | None = None
        self.idle_volts: float | None = None
        self.min_volts: float | None = None
        self.next_at = 0.0
        self.since: float | None = None

    def reset(self) -> None:
        """New flight (arming): forget the previous one but keep the resting voltage measured just before."""
        idle = self.idle_volts
        self.__init__()
        self.idle_volts = idle

    def note_stick_input(self, now: float) -> None:
        self.stick_in.append(now)

    def sample(self, v: Any, now: float, sticks: Any, hold: bool) -> None:
        if now < self.next_at:
            return
        self.next_at = now + 1.0 / SAMPLE_HZ
        st = v.state
        if not v.armed:
            volts = (st["battery"] or {}).get("voltage")
            if volts:
                self.idle_volts = float(volts)
            return
        if self.since is None:
            self.since = now
        att = st["attitude"]
        if att is None:
            return
        hud = st["hud"] or {}
        volts = (st["battery"] or {}).get("voltage")
        if volts:
            self.min_volts = float(volts) if self.min_volts is None else min(self.min_volts, float(volts))
        vib = st["vibration"]
        if vib:
            for k in ("vibration_x", "vibration_y", "vibration_z"):
                if vib.get(k) is not None:
                    self.vibration_peak[k] = max(self.vibration_peak.get(k, 0.0), float(vib[k]))
            clip = sum(int(vib.get(k) or 0) for k in ("clipping_0", "clipping_1", "clipping_2"))
            if self.clip_base is None:
                self.clip_base = clip
            self.clip_now = clip
        servo = st["servo"] or []
        frame_motors = 4 if v.state.get("mav_type") in (None, 2) else 8
        motors = [int(x) for x in servo[:frame_motors] if x and int(x) > MOTOR_MIN_US]
        climb = hud.get("climb")
        self.samples.append(
            Sample(
                t=now,
                airborne=v.landed == "air",  # takeoff and landing transients are not judged
                roll=math.degrees(att["roll"]),
                pitch=math.degrees(att["pitch"]),
                rollspeed=math.degrees(att["rollspeed"]),
                pitchspeed=math.degrees(att["pitchspeed"]),
                yawspeed=math.degrees(att["yawspeed"]),
                climb=None if climb is None else float(climb),
                throttle_pct=None if hud.get("throttle") is None else float(hud["throttle"]),
                motors=motors,
                stick_roll=0.0 if sticks is None else float(sticks.roll),
                stick_pitch=0.0 if sticks is None else float(sticks.pitch),
                stick_yaw=0.0 if sticks is None else float(sticks.yaw),
                hold=hold and (sticks is None or abs(float(sticks.throttle) - 0.5) < CENTRED),
            )
        )

    def view(self, v: Any, now: float, link: dict[str, Any], human_sticks: bool) -> dict[str, Any]:
        recent = [s for s in self.samples if now - s.t <= WINDOW_S]
        air = [s for s in recent if s.airborne]
        judged = v.armed and len(air) >= MIN_SAMPLES
        items: list[dict[str, Any]] = []

        # attitude oscillation: RMS of roll/pitch rates about their mean
        rms = None
        if judged:
            rr = [s.rollspeed for s in air]
            pr = [s.pitchspeed for s in air]
            rms = math.sqrt(statistics.pvariance(rr) + statistics.pvariance(pr))
        items.append(_item("oscillation", _level(rms, *RATE_RMS_DPS) if judged else "na", rms, "deg/s"))

        # lean with hands off: the controller is holding a tilt the operator did not ask for
        hands = [s for s in air if abs(s.stick_roll) < CENTRED and abs(s.stick_pitch) < CENTRED]
        lean = None
        detail = None
        if judged and len(hands) >= MIN_SAMPLES:
            mr = statistics.fmean(s.roll for s in hands)
            mp = statistics.fmean(s.pitch for s in hands)
            lean = math.hypot(mr, mp)
            detail = {"roll": round(mr, 1), "pitch": round(mp, 1)}
        items.append(
            _item("lean", _level(lean, *LEAN_DEG) if lean is not None else "na", lean, "deg", detail)
        )

        # yaw drift with the yaw stick centred
        still = [s for s in air if abs(s.stick_yaw) < CENTRED]
        drift = (
            abs(statistics.fmean(s.yawspeed for s in still)) if judged and len(still) >= MIN_SAMPLES else None
        )
        items.append(
            _item("yaw_drift", _level(drift, *YAW_DRIFT_DPS) if drift is not None else "na", drift, "deg/s")
        )

        # altitude hold: spread of vertical speed while holding
        holding = [s.climb for s in air if s.hold and s.climb is not None]
        spread = statistics.pstdev(holding) if judged and len(holding) >= MIN_SAMPLES else None
        items.append(
            _item("alt_hold", _level(spread, *CLIMB_STD_MS) if spread is not None else "na", spread, "m/s")
        )

        # hover throttle
        thr = [s.throttle_pct for s in air if s.throttle_pct is not None]
        hover = statistics.fmean(thr) if judged and len(thr) >= MIN_SAMPLES else None
        if hover is None:
            level = "na"
        elif hover >= HOVER_BAD_PCT:
            level = "bad"
        elif hover >= HOVER_HIGH_PCT or hover < HOVER_LOW_PCT:
            level = "warn"
        else:
            level = "ok"
        items.append(
            _item(
                "hover_throttle", level, hover, "%", None if hover is None else {"low": hover < HOVER_LOW_PCT}
            )
        )

        # motor outputs: steady imbalance and saturation
        motor_rows = [s.motors for s in air if len(s.motors) >= 4]
        spread_us = None
        sat = None
        detail = None
        if judged and len(motor_rows) >= MIN_SAMPLES:
            n = min(len(m) for m in motor_rows)
            means = [statistics.fmean(m[i] for m in motor_rows) for i in range(n)]
            spread_us = max(means) - min(means)
            sat = sum(1 for m in motor_rows if max(m) >= MOTOR_SAT_US) / len(motor_rows)
            detail = {
                "means": [round(x) for x in means],
                "high": means.index(max(means)) + 1,
                "saturated": round(sat, 2),
            }
        level = "na"
        if spread_us is not None and sat is not None:
            order = ("ok", "warn", "bad")
            level = max(_level(spread_us, *MOTOR_SPREAD_US), _level(sat, *MOTOR_SAT_FRAC), key=order.index)
        items.append(_item("motors", level, spread_us, "us", detail))

        # vibration (VIBRATION message; ArduPilot streams it, PX4 is asked for it)
        peak = max(self.vibration_peak.values()) if self.vibration_peak else None
        clipped = None if self.clip_now is None or self.clip_base is None else self.clip_now - self.clip_base
        level = _level(peak, *VIBRATION_MS2) if v.armed else "na"
        if level != "na" and clipped:
            level = "bad"
        items.append(
            _item("vibration", level, peak, "m/s2", None if clipped is None else {"clipping": clipped})
        )

        # battery sag under load, per cell
        sag = None
        cells = v.cells()
        if v.armed and self.idle_volts and self.min_volts and cells:
            sag = max(0.0, (self.idle_volts - self.min_volts) / cells)
        items.append(
            _item("battery_sag", _level(sag, *SAG_PER_CELL_V) if sag is not None else "na", sag, "V/cell")
        )

        # link quality
        loss = link.get("loss_pct")
        rtt = link.get("rtt_ms")
        order = ("na", "ok", "warn", "bad")
        level = max(_level(loss, *LOSS_PCT), _level(rtt, *RTT_MS), key=order.index)
        items.append(_item("link", level, loss, "%", {"rtt_ms": rtt}))

        # stick input stream from the renderer while a person flies
        ins = [t for t in self.stick_in if now - t <= 2.0]
        rate = gap = None
        level = "na"
        if human_sticks and len(ins) >= 2:
            rate = len(ins) / 2.0
            gaps = [b - a for a, b in itertools.pairwise(ins)] + [now - ins[-1]]
            gap = max(gaps)
            level = max(
                _level(rate, *STICK_IN_HZ, lower_is_worse=True), _level(gap, *STICK_GAP_S), key=order.index
            )
        items.append(
            _item("stick_stream", level, rate, "Hz", None if gap is None else {"max_gap_s": round(gap, 2)})
        )

        levels = [i["level"] for i in items]
        overall = "bad" if "bad" in levels else "warn" if "warn" in levels else "ok" if v.armed else "idle"
        return {
            "state": overall,
            "window_s": WINDOW_S,
            "airborne_samples": len(air),
            "items": items,
        }
