"""Firmware identity of a connected aircraft and its matching runtime packs (11 §4).

``identify`` reads what Relay already has from the aircraft: HEARTBEAT ``autopilot`` (3 ArduPilot, 12 PX4)
and ``type`` (2 quadrotor), and AUTOPILOT_VERSION: ``flight_sw_version`` (major, minor, patch, release type),
``flight_custom_version`` (the git hash: ArduPilot sends the first 8 hex digits as ASCII, PX4 the leading
bytes of the binary hash in reverse order behind its vendor version) and ``board_version`` (ArduPilot: the
bootloader board id in the upper 16 bits; PX4: the USB product id of the board).

``match`` ranks the installed packs of the same firmware: ``exact`` (same version, same git), ``version``
(same version), ``series`` (same major.minor, the closest patch), ``nearest`` (the closest version, older
first), or ``none`` when no pack of that firmware is installed. Packs of the other firmware are never
candidates. ``series`` and below carry a warning: the control loop and parameter meanings may differ from
the aircraft's.

``frame_problem`` says why a twin cannot fly an aircraft's frame (only quad X is supported, 11 §4).
"""

from __future__ import annotations

from typing import Any

from .packs import Pack, version_tuple

AUTOPILOT_FAMILY = {3: "ardupilot", 12: "px4"}
RELEASE_TYPE = {0: "dev", 64: "alpha", 128: "beta", 192: "rc", 255: "official"}
# HEARTBEAT type -> (vehicle, frame)
MAV_TYPE = {
    1: ("plane", None),
    2: ("copter", "quad"),
    3: ("copter", "coaxial"),
    4: ("copter", "heli"),
    10: ("rover", None),
    11: ("boat", None),
    12: ("sub", None),
    13: ("copter", "hexa"),
    14: ("copter", "octo"),
    15: ("copter", "tri"),
    19: ("vtol", None),
    20: ("vtol", None),
    21: ("vtol", None),
    22: ("vtol", None),
    29: ("copter", "dodeca"),
}
# ArduPilot bootloader board ids (Tools/AP_Bootloader/board_types.txt) of common flight controllers.
ARDUPILOT_BOARDS = {
    9: "Pixhawk 1 / Cube Black (fmuv3)",
    11: "Pixracer",
    13: "Pixhawk 3 Pro",
    50: "Pixhawk 4",
    51: "Pixhawk 5X",
    53: "Pixhawk 6X",
    56: "Pixhawk 6C",
    120: "Cube Yellow",
    139: "Durandal",
    140: "Cube Orange",
    1013: "Matek H743",
    1063: "Cube Orange+",
}
# PX4 boards by USB product id (CONFIG_CDCACM_PRODUCTID).
PX4_BOARDS = {
    17: "Pixhawk 1 (fmu-v2/v3)",
    18: "Pixracer (fmu-v4)",
    19: "Pixhawk 3 Pro (fmu-v4pro)",
    50: "Pixhawk 4 (fmu-v5)",
    51: "Pixhawk 5X (fmu-v5x)",
    53: "Pixhawk 6X (fmu-v6x)",
    56: "Pixhawk 6C (fmu-v6c)",
}
LEVELS = ("exact", "version", "series", "nearest", "none")
WARN_LEVELS = ("series", "nearest", "none")
WARNING_EN = "The control loop and parameter meanings may differ from the aircraft's firmware."
WARNING_ZH = "控制回路与参数含义可能与真机不同。"


def decode_version(flight_sw_version: int) -> tuple[str | None, str | None]:
    """(``x.y.z``, release type) from AUTOPILOT_VERSION.flight_sw_version; (None, None) when absent."""
    value = int(flight_sw_version or 0)
    if not value:
        return None, None
    major, minor, patch = (value >> 24) & 0xFF, (value >> 16) & 0xFF, (value >> 8) & 0xFF
    kind = value & 0xFF
    return f"{major}.{minor}.{patch}", RELEASE_TYPE.get(kind, "dev" if kind < 64 else "official")


def decode_git(family: str | None, custom: bytes) -> str | None:
    """The first 8 hex digits of the firmware's git hash, or None."""
    raw = bytes(custom or b"")[:8]
    if len(raw) < 8 or not any(raw):
        return None
    if family == "ardupilot":
        text = raw.split(b"\0", 1)[0].decode("ascii", "replace").lower()
        return text[:8] if len(text) >= 8 and all(c in "0123456789abcdef" for c in text[:8]) else None
    if family == "px4":
        # PX4 copies the little-endian uint64 of the binary hash, top 5 bytes kept, then overwrites the low 3
        # bytes with its vendor version: the hash's first 4 bytes are bytes 7, 6, 5, 4.
        return raw[7:3:-1].hex()
    return None


def board_name(family: str | None, board_version: int) -> tuple[str | None, int | None]:
    """(name, numeric id) of the flight controller board; the name is None for an unknown board."""
    value = int(board_version or 0)
    if family == "ardupilot":
        board_id = value >> 16
        return ARDUPILOT_BOARDS.get(board_id), board_id or None
    if family == "px4":
        return PX4_BOARDS.get(value), value or None
    return None, None


def identify(state: dict[str, Any]) -> dict[str, Any]:
    """The firmware identity from a vehicle's decoded state (``mav.decode``): family, vehicle, version, type,
    git, board. Fields Relay has not received yet are None; ``identified`` says AUTOPILOT_VERSION arrived."""
    family = AUTOPILOT_FAMILY.get(state.get("autopilot_id") or -1)
    vehicle, frame = MAV_TYPE.get(state.get("mav_type") or -1, (None, None))
    version_info = state.get("version") or {}
    version, kind = decode_version(int(version_info.get("flight_sw_version") or 0))
    custom = bytes.fromhex(version_info.get("flight_custom_version") or "")
    board, board_id = board_name(family, int(version_info.get("board_version") or 0))
    return {
        "family": family,
        "vehicle": vehicle,
        "frame": frame,
        "version": version,
        "type": kind,
        "git": decode_git(family, custom),
        "board": board,
        "board_id": board_id,
        "identified": version is not None,
    }


def _level(identity: dict[str, Any], pack: Pack) -> str:
    version = identity.get("version")
    if version is None:
        return "nearest"
    if pack.version == version:
        git = identity.get("git")
        return "exact" if git and pack.git8 == git[:8] else "version"
    if pack.version_key[:2] == version_tuple(version)[:2]:
        return "series"
    return "nearest"


def _flat(v: tuple[int, int, int]) -> int:
    return v[0] * 1_000_000 + v[1] * 1_000 + v[2]


def _distance(identity: dict[str, Any], pack: Pack, level: str) -> tuple[int, int]:
    """Sort key within a level (11 §4): ``series`` the closest patch (the older on a tie); ``nearest`` the
    closest older version, then the closest newer one; an unknown version takes the newest pack."""
    version = identity.get("version")
    if version is None:
        return (0, -_flat(pack.version_key))
    gap = _flat(pack.version_key) - _flat(version_tuple(version))
    newer = 0 if gap <= 0 else 1
    if level == "nearest":
        return (newer, abs(gap))
    return (abs(gap), newer)


def match(identity: dict[str, Any], packs: list[Pack]) -> dict[str, Any]:
    """The recommended pack, its level and every candidate of the same firmware, best first."""
    family = identity.get("family")
    same = [p for p in packs if p.family == family and p.problem() is None]
    ranked = sorted(
        same,
        key=lambda p: (LEVELS.index(_level(identity, p)), _distance(identity, p, _level(identity, p))),
    )
    candidates = [
        {"pack": p.id, "version": p.version, "git": p.git8, "match": _level(identity, p)} for p in ranked
    ]
    level = candidates[0]["match"] if candidates else "none"
    return {
        "recommended": candidates[0]["pack"] if candidates else None,
        "match": level,
        "warning": level in WARN_LEVELS,
        "warning_en": WARNING_EN if level in WARN_LEVELS else None,
        "warning_zh": WARNING_ZH if level in WARN_LEVELS else None,
        "candidates": candidates,
    }


def level_for(identity: dict[str, Any], pack: Pack) -> str:
    """The match level of one pack (a manual choice), ``none`` across firmware families."""
    if identity.get("family") != pack.family:
        return "none"
    return _level(identity, pack)


def frame_problem(
    family: str | None, params: dict[str, float], mav_type: int | None = None
) -> tuple[str, str] | None:
    """Why a twin cannot fly this aircraft's frame (11 §4), as (English, Chinese), or None for a quad X."""
    if mav_type is not None and mav_type != 2:
        vehicle, frame = MAV_TYPE.get(mav_type, (None, None))
        what = frame or vehicle or f"type {mav_type}"
        return (
            f"the aircraft is a {what}, twins fly quad X only",
            f"该飞机为 {what}，固件孪生只支持四旋翼 X",
        )
    if family == "ardupilot":
        for name, want in (("FRAME_CLASS", 1), ("FRAME_TYPE", 1)):
            value = params.get(name)
            if value is not None and int(value) != want:
                return (
                    f"{name} is {int(value)}, twins fly quad X ({name} {want}) only",
                    f"{name} 为 {int(value)}，固件孪生只支持四旋翼 X（{name} {want}）",
                )
    elif family == "px4":
        airframe = params.get("CA_AIRFRAME")
        if airframe is not None and int(airframe) != 0:
            return (
                f"CA_AIRFRAME is {int(airframe)}, twins fly a multicopter only",
                f"CA_AIRFRAME 为 {int(airframe)}，固件孪生只支持多旋翼",
            )
        count = params.get("CA_ROTOR_COUNT")
        if count is not None and int(count) != 4:
            return (
                f"CA_ROTOR_COUNT is {int(count)}, twins fly quad X only",
                f"CA_ROTOR_COUNT 为 {int(count)}，固件孪生只支持四旋翼 X",
            )
        # PX4 quad X order: 0 front right, 1 rear left, 2 front left, 3 rear right
        signs = ((1, 1), (-1, -1), (1, -1), (-1, 1))
        for i, (sx, sy) in enumerate(signs):
            px, py = params.get(f"CA_ROTOR{i}_PX"), params.get(f"CA_ROTOR{i}_PY")
            if px is None or py is None:
                continue
            if px * sx <= 0 or py * sy <= 0:
                return (
                    f"rotor {i} is not where quad X has it (CA_ROTOR{i}_PX/PY), twins fly quad X only",
                    f"电机 {i} 的位置不符合四旋翼 X（CA_ROTOR{i}_PX/PY），固件孪生只支持四旋翼 X",
                )
    return None


__all__ = ["LEVELS", "board_name", "decode_git", "decode_version", "frame_problem", "identify", "match"]
