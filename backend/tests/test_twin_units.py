"""Firmware twins without firmware (11 §3-§5, §10): identity decoding and pack matching, the parameter filter
and the parameter files, frame derivation, the HIL encoding, pack index and import, the profile store, and
the API paths that need no running firmware."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import tarfile
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from pymavlink.dialects.v20 import ardupilotmega as mavref

from relay.api.app import create_app
from relay.sim.frame import PRESETS
from relay.sim.physics import Quad, thrust_fraction
from relay.sim.sensors import SensorModel
from relay.twin import hil, wmm
from relay.twin import identity as ident
from relay.twin import params as tparams
from relay.twin import profile as tprofile
from relay.twin.autopilot import DEFAULT_HOME, prepare, shifted_home
from relay.twin.packs import PackError, PackStore, current_platform, parse_manifest
from relay.twin.process import TwinStartError

PLATFORM = current_platform() or "linux-x64"
OP = {"X-Relay-Operator": "op-token"}
API = "/api/v1"


# ---------------------------------------------------------------------------- fake packs


def _manifest(pack_id: str, **over) -> dict:
    family = "ardupilot" if pack_id.startswith("ardupilot") else "px4"
    version = pack_id.rsplit("-", 1)[1]
    body = {
        "format": 1,
        "id": pack_id,
        "family": family,
        "vehicle": "copter",
        "version": version,
        "git": over.pop("git", "fb773567" + "0" * 32 if family == "ardupilot" else "54f0455f" + "0" * 32),
        "platform": PLATFORM,
        "entry": "bin/arducopter" if family == "ardupilot" else "bin/px4",
        "defaults": ["defaults/base.parm"] if family == "ardupilot" else ["defaults/base.params"],
        "size_bytes": 1234,
        "license": "GPL-3.0" if family == "ardupilot" else "BSD-3-Clause",
        "source": "https://example.invalid/source",
        "built": "official",
    }
    body.update(over)
    return body


def _write_pack(root: Path, pack_id: str, defaults: str = "", **over) -> Path:
    """A pack directory whose 'firmware' is a shell script (never run by these tests)."""
    manifest = _manifest(pack_id, **over)
    d = root / pack_id
    (d / "bin").mkdir(parents=True)
    exe = d / manifest["entry"]
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    (d / "defaults").mkdir()
    for rel in manifest["defaults"]:
        (d / rel).write_text(defaults)
    if manifest["family"] == "px4":
        (d / "etc").mkdir()
    (d / "manifest.json").write_text(json.dumps(manifest))
    return d


def _store(tmp_path: Path, ids: list[str], **defaults: str) -> PackStore:
    bundled = tmp_path / "bundled"
    bundled.mkdir(exist_ok=True)
    for pack_id in ids:
        _write_pack(bundled, pack_id, defaults.get(pack_id.split("-")[0], ""))
    return PackStore(bundled=bundled, imported=tmp_path / "imported")


def _tarball(tmp_path: Path, pack_id: str, extra: list[tuple[tarfile.TarInfo, bytes | None]] = ()) -> Path:
    """``<id>-<platform>.tar.gz`` of a fake pack, with optional extra (hostile) members."""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    _write_pack(src, pack_id)
    archive = tmp_path / f"{pack_id}-{PLATFORM}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(src / pack_id, arcname=pack_id)
        for info, data in extra:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return archive


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------- identity (11 §4)


def test_identity_decodes_version_git_and_board():
    assert ident.decode_version((4 << 24) | (7 << 16) | (2 << 8) | 255) == ("4.7.2", "official")
    assert ident.decode_version((1 << 24) | (16 << 16) | (0 << 8) | 192) == ("1.16.0", "rc")
    assert ident.decode_version(0) == (None, None)
    # ArduPilot: the first 8 hex digits as ASCII; PX4: bytes 7..4 of the reversed binary hash
    assert ident.decode_git("ardupilot", b"fb773567") == "fb773567"
    assert ident.decode_git("px4", bytes.fromhex("000000fc5f45f054")) == "54f0455f"
    assert ident.decode_git("ardupilot", b"\0" * 8) is None
    assert ident.decode_git("ardupilot", b"nothex!!") is None
    assert ident.board_name("ardupilot", 140 << 16) == ("Cube Orange", 140)
    assert ident.board_name("ardupilot", 0) == (None, None)  # SITL
    assert ident.board_name("px4", 50) == ("Pixhawk 4 (fmu-v5)", 50)
    state = {
        "autopilot_id": 3,
        "mav_type": 2,
        "version": {
            "flight_sw_version": (4 << 24) | (5 << 16) | (7 << 8) | 255,
            "flight_custom_version": b"2a3dc4b7".hex(),
            "board_version": 1063 << 16,
        },
    }
    assert ident.identify(state) == {
        "family": "ardupilot",
        "vehicle": "copter",
        "frame": "quad",
        "version": "4.5.7",
        "type": "official",
        "git": "2a3dc4b7",
        "board": "Cube Orange+",
        "board_id": 1063,
        "identified": True,
    }
    blank = ident.identify({"autopilot_id": 12, "mav_type": 13})
    assert blank["family"] == "px4" and blank["frame"] == "hexa" and not blank["identified"]


def test_matching_ranks_exact_version_series_nearest(tmp_path):
    store = _store(
        tmp_path,
        ["ardupilot-copter-4.7.2", "ardupilot-copter-4.5.7", "ardupilot-copter-4.3.8", "px4-1.16.2"],
    )
    packs = store.list()
    assert [p.id for p in packs] == [
        "ardupilot-copter-4.7.2",
        "ardupilot-copter-4.5.7",
        "ardupilot-copter-4.3.8",
        "px4-1.16.2",
    ]

    def m(version, git=None, family="ardupilot"):
        return ident.match({"family": family, "version": version, "git": git}, packs)

    exact = m("4.7.2", "fb773567")
    assert exact["recommended"] == "ardupilot-copter-4.7.2" and exact["match"] == "exact"
    assert not exact["warning"] and exact["warning_zh"] is None
    assert [c["pack"] for c in exact["candidates"]] == [
        "ardupilot-copter-4.7.2",
        "ardupilot-copter-4.5.7",
        "ardupilot-copter-4.3.8",
    ]  # never the other firmware
    assert m("4.7.2", "deadbeef")["match"] == "version"
    series = m("4.7.0")
    assert series["recommended"] == "ardupilot-copter-4.7.2" and series["match"] == "series"
    assert series["warning"] and series["warning_en"] and series["warning_zh"]
    # nearest: the closest older version first, then the closest newer one
    assert m("4.6.0")["recommended"] == "ardupilot-copter-4.5.7"
    assert m("4.6.0")["match"] == "nearest"
    assert m("4.2.0")["recommended"] == "ardupilot-copter-4.3.8"
    assert m("1.15.4", family="px4")["recommended"] == "px4-1.16.2"
    # no pack of the firmware, and an unknown version
    none = ident.match({"family": "px4", "version": "1.16.2"}, packs[:3])
    assert none == {
        "recommended": None,
        "match": "none",
        "warning": True,
        "warning_en": ident.WARNING_EN,
        "warning_zh": ident.WARNING_ZH,
        "candidates": [],
    }
    assert m(None)["recommended"] == "ardupilot-copter-4.7.2"
    assert ident.level_for({"family": "px4", "version": "4.7.2"}, packs[0]) == "none"


def test_frame_problem_accepts_quad_x_only():
    assert ident.frame_problem("ardupilot", {"FRAME_CLASS": 1, "FRAME_TYPE": 1}, 2) is None
    en, zh = ident.frame_problem("ardupilot", {"FRAME_CLASS": 2, "FRAME_TYPE": 1})
    assert "FRAME_CLASS is 2" in en and "FRAME_CLASS" in zh
    assert "FRAME_TYPE is 0" in ident.frame_problem("ardupilot", {"FRAME_CLASS": 1, "FRAME_TYPE": 0})[0]
    assert "hexa" in ident.frame_problem("ardupilot", {}, 13)[0]
    quad = {
        "CA_AIRFRAME": 0,
        "CA_ROTOR_COUNT": 4,
        "CA_ROTOR0_PX": 0.1,
        "CA_ROTOR0_PY": 0.1,
        "CA_ROTOR1_PX": -0.1,
        "CA_ROTOR1_PY": -0.1,
        "CA_ROTOR2_PX": 0.1,
        "CA_ROTOR2_PY": -0.1,
        "CA_ROTOR3_PX": -0.1,
        "CA_ROTOR3_PY": 0.1,
    }
    assert ident.frame_problem("px4", quad) is None
    assert "CA_ROTOR_COUNT is 6" in ident.frame_problem("px4", {**quad, "CA_ROTOR_COUNT": 6})[0]
    assert "CA_AIRFRAME is 1" in ident.frame_problem("px4", {**quad, "CA_AIRFRAME": 1})[0]
    assert "rotor 2" in ident.frame_problem("px4", {**quad, "CA_ROTOR2_PY": 0.1})[0]


# ---------------------------------------------------------------------------- parameters (11 §5)


def test_parameter_filter_keeps_tuning_and_drops_hardware():
    applied, excluded = tparams.split(
        "ardupilot",
        {
            "ATC_RAT_RLL_P": 0.135,
            "MOT_THST_HOVER": 0.31,
            "PSC_ACCZ_P": 0.3,
            "SERIAL1_PROTOCOL": 2,
            "INS_ACCOFFS_X": 0.01,
            "COMPASS_DEV_ID": 97539,
            "AHRS_ORIENTATION": 4,
            "SYSID_THISMAV": 3,
            "SERVO1_FUNCTION": 33,
            "BRD_SAFETY_DEFLT": 0,
            "SIM_RATE_HZ": 400,
            "STAT_RUNTIME": 1000,
            "lower_case": 1,
            "NAN_VALUE": math.nan,
        },
    )
    assert applied == {"ATC_RAT_RLL_P": 0.135, "MOT_THST_HOVER": 0.31, "PSC_ACCZ_P": 0.3}
    reasons = {e["name"]: (e["reason_en"], e["reason_zh"]) for e in excluded}
    assert set(reasons) == {
        "SERIAL1_PROTOCOL",
        "INS_ACCOFFS_X",
        "COMPASS_DEV_ID",
        "AHRS_ORIENTATION",
        "SYSID_THISMAV",
        "SERVO1_FUNCTION",
        "BRD_SAFETY_DEFLT",
        "SIM_RATE_HZ",
        "STAT_RUNTIME",
        "lower_case",
        "NAN_VALUE",
    }
    assert reasons["INS_ACCOFFS_X"][0] == "sensor calibration of the real board"
    assert all(en and zh for en, zh in reasons.values())
    applied, excluded = tparams.split(
        "px4",
        {
            "MC_ROLLRATE_P": 0.15,
            "MPC_THR_HOVER": 0.38,
            "CA_ROTOR0_PX": 0.12,
            "CAL_ACC0_XOFF": 0.02,
            "SENS_BOARD_ROT": 2,
            "SYS_AUTOSTART": 4001,
            "MAV_0_CONFIG": 101,
            "SER_TEL1_BAUD": 57600,
            "PWM_MAIN_FUNC1": 101,
            "EKF2_MAG_ID": 1234,
        },
    )
    assert applied == {"CA_ROTOR0_PX": 0.12, "MC_ROLLRATE_P": 0.15, "MPC_THR_HOVER": 0.38}
    assert len(excluded) == 7
    assert tparams.FORCED == {"ardupilot": {"SIM_RATE_HZ": 1000.0}, "px4": {}}
    assert tparams.exclusion("ardupilot", "ATC_ANG_RLL_P") is None


def test_parameter_files_round_trip():
    params = {"ATC_RAT_RLL_P": 0.135, "FRAME_CLASS": 1.0, "PSC_ACCZ_I": 0.1, "WPNAV_SPEED": 500.0}
    text = tparams.ardupilot_parm(params, header="Relay twin profile\nnot for a real aircraft")
    lines = text.splitlines()
    assert lines[:2] == ["# Relay twin profile", "# not for a real aircraft"]
    assert "FRAME_CLASS 1" in lines and "PSC_ACCZ_I 0.1" in lines
    assert tparams.parse_parm(text) == params
    assert tparams.parse_parm("A_B,1.5\n# C 2\nD_E 3 # trailing\nbad\n") == {"A_B": 1.5, "D_E": 3.0}

    px4 = tparams.px4_params(
        {"MC_ROLLRATE_P": 0.15, "COM_DISARM_LAND": 2.0, "SYS_HAS_MAG": 1.0, "MPC_XY_VEL_MAX": 12.0},
        {"MC_ROLLRATE_P": "REAL32", "SYS_HAS_MAG": "INT32", "COM_DISARM_LAND": "REAL32"},
        baseline=["/opt/relay packs/it's/relay-quadx.params"],
        header="profile tp-00000000",
    )
    lines = px4.splitlines()
    assert lines[0] == "#!/bin/sh" and lines[1] == "# profile tp-00000000"
    assert lines[2] == ". '/opt/relay packs/it'\"'\"'s/relay-quadx.params'"
    assert "param set SYS_HAS_MAG 1" in lines and "param set MC_ROLLRATE_P 0.15" in lines
    assert "param set COM_DISARM_LAND 2" in lines
    assert tparams.parse_px4(px4) == {
        "COM_DISARM_LAND": 2.0,
        "MC_ROLLRATE_P": 0.15,
        "MPC_XY_VEL_MAX": 12.0,
        "SYS_HAS_MAG": 1.0,
    }
    assert tparams.format_value(0.1 + 0.2) == repr(0.1 + 0.2)
    assert tparams.format_value(2.7, integer=True) == "3"


# ---------------------------------------------------------------------------- frames (11 §5)


def _hover_output_ardupilot(hover: float, expo: float, spin_min: float, spin_max: float) -> float:
    """ArduPilot's thrust linearisation of a collective demand into the ESC output."""
    lin = ((expo - 1.0) + math.sqrt((1.0 - expo) ** 2 + 4.0 * expo * hover)) / (2.0 * expo)
    return spin_min + (spin_max - spin_min) * lin


def test_frame_derivation_from_hover_thrust():
    micro = PRESETS["micro-120"]
    assert tprofile.frame_model(None, {}, "relay").thrust_to_weight == micro.thrust_to_weight
    eff = {"MOT_THST_HOVER": 0.35, "MOT_THST_EXPO": 0.65, "MOT_SPIN_MIN": 0.15, "MOT_SPIN_MAX": 0.95}
    small = tprofile.frame_model({"preset": "small-250"}, eff, "ardupilot")
    out = _hover_output_ardupilot(0.35, 0.65, 0.15, 0.95)
    assert thrust_fraction(out) * small.thrust_to_weight == pytest.approx(1.0, rel=1e-3)
    lighter = tprofile.frame_model({"preset": "small-250"}, {**eff, "MOT_THST_HOVER": 0.25}, "ardupilot")
    assert lighter.thrust_to_weight > small.thrust_to_weight  # a lower hover thrust: more headroom
    px4 = tprofile.frame_model(None, {"MPC_THR_HOVER": 0.5, "THR_MDL_FAC": 0.0}, "px4")
    assert thrust_fraction(0.5) * px4.thrust_to_weight == pytest.approx(1.0, rel=1e-3)
    # mass or wheelbase changed: inertia from the new figures, the preset name kept
    heavy = tprofile.frame_model({"preset": "small-250", "mass_kg": 0.9}, eff, "ardupilot")
    base = tprofile.frame_model({"preset": "small-250"}, eff, "ardupilot")
    assert heavy.preset == "small-250" and heavy.mass_kg == 0.9 and heavy.ixx > base.ixx
    with pytest.raises(ValueError):
        tprofile.frame_model({"preset": "giant-900"}, {}, "ardupilot")
    with pytest.raises(ValueError):
        tprofile.frame_model({"mass_kg": "heavy"}, {}, "ardupilot")


def test_frame_normalisation_and_preset_change():
    frame = tprofile.normalise_frame({"preset": "small-250", "mass_kg": 0.6})
    assert frame["preset"] == "small-250" and frame["mass_kg"] == 0.6
    assert set(frame) == set(tprofile.FRAME_KEYS)
    # another preset starts from its own values; the same preset keeps the edits
    assert tprofile.normalise_frame({"preset": "large-450"}, frame)["mass_kg"] == PRESETS["large-450"].mass_kg
    assert tprofile.normalise_frame({"cells": 4}, frame)["mass_kg"] == 0.6
    with pytest.raises(tprofile.ProfileError):
        tprofile.normalise_frame({"rotors": 6})
    with pytest.raises(tprofile.ProfileError):
        tprofile.normalise_frame({"mass_kg": -1.0})
    assert tprofile.guess_preset(4, None) == "large-450"
    assert tprofile.guess_preset(3, 1300) == "small-250"
    assert tprofile.guess_preset(None, 2200) == "large-450"


def test_prepare_filters_profile_and_forces_lockstep(tmp_path):
    store = _store(
        tmp_path, ["ardupilot-copter-4.7.2"], ardupilot="MOT_THST_HOVER 0.25\nMOT_THST_EXPO 0.65\n"
    )
    pack = store.get("ardupilot-copter-4.7.2")
    plain = prepare(pack, None, None, None)
    assert plain.home == DEFAULT_HOME and plain.applied == {} and plain.forced == {"SIM_RATE_HZ": 1000.0}
    assert plain.effective["MOT_THST_HOVER"] == 0.25
    profile = {
        "params": {"MOT_THST_HOVER": 0.4, "SERIAL2_PROTOCOL": 2, "SIM_RATE_HZ": 400},
        "param_types": {"MOT_THST_HOVER": "REAL32"},
        "frame": {"preset": "small-250"},
        "home": {"lat": 31.2, "lon": 121.5, "alt_m": 4.0},
    }
    cfg = prepare(pack, profile, None, None, "series")
    assert cfg.applied == {"MOT_THST_HOVER": 0.4}
    assert [e["name"] for e in cfg.excluded] == ["SERIAL2_PROTOCOL", "SIM_RATE_HZ"]
    assert cfg.effective["MOT_THST_HOVER"] == 0.4 and cfg.effective["SIM_RATE_HZ"] == 1000.0
    assert cfg.home == (31.2, 121.5, 4.0) and cfg.frame.preset == "small-250" and cfg.match == "series"
    assert (
        cfg.frame.thrust_to_weight < plain.frame.thrust_to_weight or cfg.frame.mass_kg != plain.frame.mass_kg
    )
    lat, lon, alt = shifted_home((47.0, 8.0, 400.0), 0.0, 111.2)
    assert lat == pytest.approx(47.001, abs=2e-5) and lon == 8.0 and alt == 400.0


class _FakePorts:
    def __init__(self, n):
        self.n = n
        self.closed = False

    def close(self):
        self.closed = True


class _FakeBridge:
    made: ClassVar[list] = []

    def __init__(self, pack, n, ports, workdir):
        self.ports, self.launched, self.closed = ports, False, False
        _FakeBridge.made.append(self)

    def launch(self, *args):
        self.launched = True

    def boot(self, twin, deadline):
        raise TwinStartError("the firmware did not connect its MAVLink link")

    def close(self):
        self.closed = True
        self.ports.close()


def test_twin_reset_and_removal_during_a_restart(tmp_path, monkeypatch):
    """Review fixes (v0.5.4): a second reset during a restart does not start a second boot, and a twin
    removed before its restart thread got going leaves no process, files or instance number behind."""
    import threading
    import time

    from relay.twin import autopilot as tap

    store = _store(tmp_path, ["ardupilot-copter-4.7.2"])
    twin = tap.TwinAutopilot(prepare(store.get("ardupilot-copter-4.7.2"), None, None, None), name="T")
    monkeypatch.setattr(twin, "_open", _FakePorts)
    monkeypatch.setattr(tap.ardupilot, "ArduPilotBridge", _FakeBridge)
    gate, calls = threading.Event(), []

    def slow_restart():
        calls.append(1)
        gate.wait(5)

    monkeypatch.setattr(twin, "_restart", slow_restart)
    twin.state = "running"
    twin.reset()
    twin.reset()
    assert twin.state == "restarting" and twin.restarts == 1
    gate.set()
    for _ in range(50):
        if calls:
            break
        time.sleep(0.01)
    assert calls == [1]
    monkeypatch.undo()

    # removed before the restart thread acquired anything: it boots nothing and frees what it took
    monkeypatch.setattr(twin, "_open", _FakePorts)
    monkeypatch.setattr(tap.ardupilot, "ArduPilotBridge", _FakeBridge)
    _FakeBridge.made = []
    twin.close()
    assert twin.state == "stopped" and twin.instance is None
    twin._restart()
    (bridge,) = _FakeBridge.made
    assert not bridge.launched and bridge.closed and bridge.ports.closed
    assert twin.instance is None and twin.workdir is None and twin.bridge is None and not twin._pending
    # a boot that fails after removal is torn down as well
    _FakeBridge.made = []
    twin.state = "restarting"
    real_publish = twin._publish

    def publish_then_remove(b):
        real_publish(b)
        twin.state = "stopped"

    monkeypatch.setattr(twin, "_publish", publish_then_remove)
    twin._restart()
    (bridge,) = _FakeBridge.made
    assert bridge.launched and bridge.closed and twin.instance is None and twin.workdir is None


# ---------------------------------------------------------------------------- HIL (PX4) and WMM


def test_hil_frames_round_trip_through_pymavlink():
    sensors = SensorModel(DEFAULT_HOME, noise=0.0, field=wmm.field(DEFAULT_HOME[0], DEFAULT_HOME[1]))
    quad = Quad()
    enc = hil.HilEncoder(sensors)
    reference = mavref.MAVLink(None)
    first = reference.parse_buffer(enc.frame(quad, 1_000_000, 0.004)) or []
    assert [m.get_type() for m in first] == ["HIL_SENSOR", "HIL_GPS"]
    sensor, gps = first
    assert sensor.time_usec == 1_000_000 and sensor.fields_updated == hil.FIELDS_UPDATED
    assert sensor.zacc == pytest.approx(-9.81, abs=0.1) and abs(sensor.xacc) < 0.1
    assert 940.0 < sensor.abs_pressure < 970.0  # hPa at 488 m
    assert sensor.get_srcComponent() == hil.SIM_COMPID
    assert abs(gps.lat - round(DEFAULT_HOME[0] * 1e7)) <= 2 and gps.fix_type == 3 and gps.cog == 65535
    second = reference.parse_buffer(enc.frame(quad, 1_004_000, 0.004)) or []
    assert [m.get_type() for m in second] == ["HIL_SENSOR"]  # GPS at 10 Hz
    # the autopilot's reply: outputs clamped to 0..1, armed from the mode flags
    controls = [1.5, -0.2, math.nan, 0.4] + [0.0] * 12
    reply = mavref.MAVLink(None, srcSystem=1, srcComponent=1)
    data = reply.hil_actuator_controls_encode(5, controls, 128 | 64, 0).pack(reply)
    (msg,) = enc.parse(data)
    outputs, armed = hil.decode_actuators(msg)
    assert outputs == pytest.approx([1.0, 0.0, 0.0, 0.4]) and armed is True
    msg.mode = 64
    assert hil.decode_actuators(msg)[1] is False


def test_wmm_matches_px4_table():
    gauss, decl, incl = wmm.field(47.397742, 8.545594)
    assert gauss == pytest.approx(0.482, abs=0.01) and decl == pytest.approx(3.41, abs=0.2)
    assert incl == pytest.approx(63.16, abs=0.3)
    assert wmm.declination_deg(31.23, 121.47) == pytest.approx(-6.5, abs=0.3)


# ---------------------------------------------------------------------------- packs (11 §3)


def test_manifest_validation():
    assert parse_manifest(_manifest("px4-1.16.2"))["romfs"] == "etc"
    for bad, word in (
        ({"format": 2}, "format"),
        ({"id": "px4-1.16.3"}, "does not match"),
        ({"family": "inav"}, "inav"),
        ({"entry": "/usr/bin/px4"}, "inside the pack"),
        ({"defaults": ["../../etc/passwd"]}, "inside the pack"),
        ({"git": "XYZ"}, "hex"),
        ({"platform": "amiga"}, "amiga"),
    ):
        with pytest.raises(PackError) as exc:
            parse_manifest({**_manifest("px4-1.16.2"), **bad})
        assert word in exc.value.reason and exc.value.reason_zh


def test_pack_store_lists_and_reports_problems(tmp_path):
    store = _store(tmp_path, ["px4-1.16.2", "ardupilot-copter-4.5.7", "ardupilot-copter-4.7.2"])
    assert [p.id for p in store.list()] == ["ardupilot-copter-4.7.2", "ardupilot-copter-4.5.7", "px4-1.16.2"]
    pack = store.get("px4-1.16.2")
    assert pack.problem() is None and pack.view()["usable"] and pack.git8 == "54f0455f"
    pack.executable.chmod(0o644)
    assert store.get("px4-1.16.2").view()["problem"] == "bin/px4 is not executable"
    pack.executable.unlink()
    assert store.get("px4-1.16.2").problem() == ("bin/px4 is missing", "缺少 bin/px4")
    # an index lists the packs and their digests
    root = store.bundled_dir
    sha = "a" * 64
    (root / "index.json").write_text(
        json.dumps({"format": 1, "packs": [{"id": "ardupilot-copter-4.7.2", "sha256": sha}]})
    )
    assert [(p.id, p.sha256) for p in store.list()] == [("ardupilot-copter-4.7.2", sha)]


def test_pack_import_verifies_and_installs(tmp_path):
    store = _store(tmp_path, ["ardupilot-copter-4.7.2"])
    archive = _tarball(tmp_path, "px4-1.15.4")
    with pytest.raises(PackError) as exc:
        store.import_tarball(archive)
    assert "no sha256" in exc.value.reason
    with pytest.raises(PackError) as exc:
        store.import_tarball(archive, "0" * 64)
    assert exc.value.reason == "the file's sha256 does not match"
    # the digest beside the file
    archive.with_name(archive.name + ".sha256").write_text(f"{_digest(archive)}  {archive.name}\n")
    pack = store.import_tarball(archive)
    assert pack.id == "px4-1.15.4" and pack.origin == "imported" and pack.sha256 == _digest(archive)
    assert pack.problem() is None and (store.imported_dir / "px4-1.15.4" / "bin" / "px4").is_file()
    index = json.loads((store.imported_dir / "index.json").read_text())
    assert [e["id"] for e in index["packs"]] == ["px4-1.15.4"] and index["packs"][0]["sha256"] == pack.sha256
    assert [p.id for p in store.list()] == ["ardupilot-copter-4.7.2", "px4-1.15.4"]
    assert not [p for p in store.imported_dir.iterdir() if p.name.startswith(".import-")]
    # bundled packs stay; imported ones go
    with pytest.raises(PackError):
        store.delete("ardupilot-copter-4.7.2")
    with pytest.raises(KeyError):
        store.delete("px4-1.14.0")
    store.delete("px4-1.15.4")
    assert store.get("px4-1.15.4") is None
    assert json.loads((store.imported_dir / "index.json").read_text())["packs"] == []
    # a pack that comes with Relay cannot be imported over it
    again = tmp_path / "again"
    again.mkdir()
    twin = _tarball(again, "ardupilot-copter-4.7.2")
    with pytest.raises(PackError) as exc:
        store.import_tarball(twin, _digest(twin))
    assert "already installed" in exc.value.reason


def _member(name: str, kind: bytes = tarfile.REGTYPE, link: str = "", data: bytes = b"x"):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = link
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    return info, data if kind == tarfile.REGTYPE else None


@pytest.mark.parametrize(
    ("member", "word"),
    [
        (_member("../evil.sh"), "outside"),
        (_member("/tmp/evil.sh"), "outside"),
        (_member("px4-1.15.4/../../evil.sh"), "outside"),
        (_member("px4-1.15.4/bin/link", tarfile.SYMTYPE, "../../../etc/passwd"), "points outside"),
        (_member("px4-1.15.4/bin/abs", tarfile.SYMTYPE, "/etc/passwd"), "points outside"),
        (_member("px4-1.15.4/dev", tarfile.CHRTYPE), "not a file"),
        (_member("px4-1.15.4/bin/hard", tarfile.LNKTYPE, "px4-1.15.4/bin/px4"), "not a file"),
    ],
)
def test_pack_import_refuses_hostile_archives(tmp_path, member, word):
    store = _store(tmp_path, [])
    archive = _tarball(tmp_path, "px4-1.15.4", [member])
    with pytest.raises(PackError) as exc:
        store.import_tarball(archive, _digest(archive))
    assert word in exc.value.reason and exc.value.reason_zh
    assert store.get("px4-1.15.4") is None
    assert not (tmp_path / "evil.sh").exists()


@pytest.mark.parametrize(
    "chain",
    [
        # each link stays inside lexically, but resolves through the one before it
        [("a", "."), ("b", "a/.."), ("c", "b/.."), ("d", "c/..")],
        [("d/l", ".."), ("l2", "d/l/.."), ("l3", "l2/..")],
    ],
)
def test_pack_import_refuses_chained_links_out_of_the_pack(tmp_path, chain):
    """Review fix (v0.5.4): links are resolved on disk, so a chain of them cannot write outside the pack."""
    store = _store(tmp_path, [])
    extra = [_member("px4-1.15.4/d", tarfile.DIRTYPE)] if chain[0][0].startswith("d/") else []
    extra += [_member(f"px4-1.15.4/{name}", tarfile.SYMTYPE, link) for name, link in chain]
    extra.append(_member(f"px4-1.15.4/{chain[-1][0]}/pwned.txt", data=b"pwned"))
    archive = _tarball(tmp_path, "px4-1.15.4", extra)
    with pytest.raises(PackError) as exc:
        store.import_tarball(archive, _digest(archive))
    assert "outside the pack" in exc.value.reason and exc.value.reason_zh
    assert not list(tmp_path.rglob("pwned.txt"))
    # a dangling chain written in the other order (the link before the one it climbs through) is refused too
    (tmp_path / "late").mkdir()
    late = _tarball(
        tmp_path / "late",
        "px4-1.15.4",
        [
            _member("px4-1.15.4/x", tarfile.SYMTYPE, "y/.."),
            _member("px4-1.15.4/y", tarfile.SYMTYPE, "."),
        ],
    )
    with pytest.raises(PackError) as exc:
        store.import_tarball(late, _digest(late))
    assert "entry px4-1.15.4/x resolves outside" in exc.value.reason
    # links inside the pack (PX4's px4-* command aliases) still import
    (tmp_path / "good").mkdir()
    alias = _member("px4-1.15.4/bin/px4-commander", tarfile.SYMTYPE, "px4")
    good = _tarball(tmp_path / "good", "px4-1.15.4", [alias])
    assert store.import_tarball(good, _digest(good)).problem() is None
    assert os.readlink(store.imported_dir / "px4-1.15.4" / "bin" / "px4-commander") == "px4"


def test_pack_import_refuses_another_platform(tmp_path):
    store = _store(tmp_path, [])
    src = tmp_path / "src"
    src.mkdir()
    other = "win-x64" if PLATFORM != "win-x64" else "linux-x64"
    _write_pack(src, "px4-1.15.4", platform=other)
    archive = tmp_path / f"px4-1.15.4-{other}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(src / "px4-1.15.4", arcname="px4-1.15.4")
    with pytest.raises(PackError) as exc:
        store.import_tarball(archive, _digest(archive))
    assert f"built for {other}" in exc.value.reason


# ---------------------------------------------------------------------------- profiles (11 §5)


def _captured(**over):
    identity = {
        "family": "ardupilot",
        "vehicle": "copter",
        "version": "4.5.7",
        "type": "official",
        "git": "2a3dc4b7",
        "board": "Cube Orange",
        "board_id": 140,
    }
    args = {
        "vehicle_id": "fc-1",
        "label": "FC-1",
        "identity": identity,
        "params": {"ATC_RAT_RLL_P": 0.135, "BATT_CAPACITY": 1500.0, "SERIAL1_PROTOCOL": 2.0},
        "types": {"ATC_RAT_RLL_P": "REAL32", "BATT_CAPACITY": "INT32", "SERIAL1_PROTOCOL": "INT8"},
        "pack": "ardupilot-copter-4.7.2",
        "match_level": "nearest",
        "home": None,
        "cells": 4,
        "captured_at": 1000.0,
    }
    args.update(over)
    return tprofile.capture(**args)


def test_profile_capture_edit_and_store(tmp_path):
    p = _captured()
    assert tprofile.ID_RE.match(p["id"]) and p["name"] == "Cube Orange ArduCopter 4.5.7"
    assert (
        p["frame"]["preset"] == "large-450"
        and p["frame"]["cells"] == 4
        and p["frame"]["capacity_mah"] == 1500
    )
    summary = tprofile.summary(p)
    assert (summary["params_total"], summary["params_applied"], summary["params_excluded"]) == (3, 2, 1)
    detail = tprofile.detail(p)
    assert detail["excluded"] == [
        {"name": "SERIAL1_PROTOCOL", "reason_en": tparams._SERIAL[0], "reason_zh": tparams._SERIAL[1]}
    ]
    edited = tprofile.edit(
        p,
        {"name": "  bench   quad ", "frame": {"mass_kg": 1.2}, "home": {"lat": 1, "lon": 2, "alt_m": 3}},
        2000.0,
    )
    assert edited["name"] == "bench quad" and edited["frame"]["mass_kg"] == 1.2
    assert edited["home"] == {"lat": 1.0, "lon": 2.0, "alt_m": 3.0} and edited["updated_at"] == 2000.0
    assert tprofile.edit(edited, {"home": None}, 2001.0)["home"] is None
    with pytest.raises(tprofile.ProfileError):
        tprofile.edit(p, {"home": {"lat": 95, "lon": 0, "alt_m": 0}}, 2002.0)
    again = tprofile.recapture(edited, _captured(params={"ATC_RAT_RLL_P": 0.2}, captured_at=3000.0))
    assert (
        again["id"] == p["id"] and again["name"] == "bench quad" and again["params"] == {"ATC_RAT_RLL_P": 0.2}
    )
    for store in (tprofile.ProfileStore(tmp_path / "twins"), tprofile.ProfileStore(None)):
        store.save(again)
        assert store.get(p["id"])["params"] == {"ATC_RAT_RLL_P": 0.2}
        assert [x["id"] for x in store.list()] == [p["id"]]
        assert store.delete(p["id"]) and store.get(p["id"]) is None and not store.delete(p["id"])
    disk = tprofile.ProfileStore(tmp_path / "twins")
    disk.save(again)
    reopened = tprofile.ProfileStore(tmp_path / "twins")  # a Relay restart
    assert reopened.get(p["id"])["name"] == "bench quad"
    assert disk.get("../../etc/passwd") is None


# ---------------------------------------------------------------------------- API without firmware (11 §10)


@pytest.fixture
def api(svc, tmp_path):
    svc.packs = _store(tmp_path, ["ardupilot-copter-4.7.2", "px4-1.16.2"])
    svc.twin_profiles = tprofile.ProfileStore(tmp_path / "twins")
    app = create_app(svc, manage_service=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


def _code(response) -> str:
    return response.json()["error"]["code"]


def test_api_packs_import_and_delete(api, svc, tmp_path):
    listing = api.get(f"{API}/autopilots").json()
    assert listing["platform"] == current_platform()
    assert [(p["id"], p["origin"], p["usable"], p["twins"]) for p in listing["packs"]] == [
        ("ardupilot-copter-4.7.2", "bundled", True, 0),
        ("px4-1.16.2", "bundled", True, 0),
    ]
    archive = _tarball(tmp_path, "ardupilot-copter-4.5.7")
    body = {"path": str(archive), "sha256": _digest(archive)}
    assert api.post(f"{API}/autopilots/import", json=body).status_code in (401, 403)
    bad = api.post(f"{API}/autopilots/import", headers=OP, json={**body, "sha256": "1" * 64})
    assert bad.status_code == 400 and _code(bad) == "pack_unsupported"
    assert bad.json()["error"]["detail"]["reason_zh"] == "文件的 sha256 不一致"
    done = api.post(f"{API}/autopilots/import", headers=OP, json=body)
    assert done.status_code == 200 and done.json()["origin"] == "imported" and done.json()["usable"]
    events = [e for e in svc.events.after(0, 500) if e["kind"] == "autopilot_pack_imported"]
    assert events and events[-1]["data"] == {
        "pack": "ardupilot-copter-4.5.7",
        "family": "ardupilot",
        "version": "4.5.7",
    }
    assert api.delete(f"{API}/autopilots/ardupilot-copter-4.7.2", headers=OP).status_code == 400
    missing = api.delete(f"{API}/autopilots/px4-1.14.0", headers=OP)
    assert missing.status_code == 404 and _code(missing) == "pack_missing"
    # review fix (v0.5.4): a pack a twin boots from is neither deleted nor replaced under it
    svc._twin_reserved_packs["sim-9"] = "ardupilot-copter-4.5.7"
    for busy in (
        api.delete(f"{API}/autopilots/ardupilot-copter-4.5.7", headers=OP),
        api.post(f"{API}/autopilots/import", headers=OP, json=body),
    ):
        assert busy.status_code == 400 and _code(busy) == "pack_unsupported"
        assert busy.json()["error"]["detail"]["reason_zh"] == "运行包正被固件孪生使用"
    assert svc.packs.get("ardupilot-copter-4.5.7").problem() is None
    del svc._twin_reserved_packs["sim-9"]
    again = api.post(f"{API}/autopilots/import", headers=OP, json=body)
    assert again.status_code == 200 and not [
        p for p in svc.packs.imported_dir.iterdir() if p.name.startswith(".")
    ]
    gone = api.delete(f"{API}/autopilots/ardupilot-copter-4.5.7", headers=OP)
    assert gone.json() == {"id": "ardupilot-copter-4.5.7", "deleted": True}
    assert sorted(p.name for p in svc.packs.imported_dir.iterdir()) == ["index.json"]


def test_api_sims_refuse_bad_twin_requests(api, svc):
    missing = api.post(f"{API}/sims", headers=OP, json={"engine": "sitl", "pack": "px4-1.13.0"})
    assert missing.status_code == 404 and _code(missing) == "pack_missing"
    other = api.post(
        f"{API}/sims",
        headers=OP,
        json={"engine": "sitl", "autopilot": "px4", "pack": "ardupilot-copter-4.7.2"},
    )
    assert other.status_code == 400 and _code(other) == "pack_unsupported"
    profile = api.post(f"{API}/sims", headers=OP, json={"engine": "sitl", "profile": "tp-00000000"})
    assert profile.status_code == 404 and _code(profile) == "profile_not_found"
    relay = api.post(f"{API}/sims", headers=OP, json={"engine": "relay", "pack": "px4-1.16.2"})
    assert relay.status_code == 400 and _code(relay) == "invalid_request"
    svc.update_settings({"twin_limit": 1})
    limited = api.post(f"{API}/sims", headers=OP, json={"engine": "sitl", "count": 2})
    assert limited.status_code == 409 and _code(limited) == "twin_limit"
    assert not svc.vehicles  # nothing was started


def test_api_fast_sim_frame_and_twin_detail(api, svc, stepper):
    (vid,) = api.post(f"{API}/sims", headers=OP, json={"frame": {"preset": "small-250"}}).json()["added"]
    stepper.run(1.0)
    v = svc.vehicles[vid]
    assert v.driver.sim.quad.frame.preset == "small-250"
    view = svc._vehicle_view(v, stepper.clock.t, svc.wall())
    assert view["sim"]["engine"] == "relay" and view["sim"]["firmware"] is None
    assert view["sim"]["frame"] == "small-250"
    not_twin = api.get(f"{API}/sims/{vid}/twin")
    assert not_twin.status_code == 404
    changed = api.put(f"{API}/sims/{vid}/frame", headers=OP, json={"preset": "large-450", "mass_kg": 1.4})
    assert changed.status_code == 200, changed.text
    assert changed.json()["engine"] == "relay" and changed.json()["frame"]["mass_kg"] == 1.4
    bad = api.put(f"{API}/sims/{vid}/frame", headers=OP, json={"preset": "giant-900"})
    assert bad.status_code == 400 and _code(bad) == "invalid_request"
    firmware = api.get(f"{API}/vehicles/{vid}/firmware").json()
    assert firmware["kind"] == "sim" and firmware["match"]["candidates"]
    v.state["armed"] = True  # as the heartbeat reports it
    armed = api.put(f"{API}/sims/{vid}/frame", headers=OP, json={"preset": "micro-120"})
    assert armed.status_code == 409 and _code(armed) == "armed"


def test_api_profiles_capture_from_a_real_aircraft(api, svc, stepper):
    (vid,) = api.post(f"{API}/sims", headers=OP, json={}).json()["added"]
    stepper.run(1.0)
    refused = api.post(f"{API}/twins/profiles", headers=OP, json={"from_vehicle": vid})
    assert refused.status_code == 409 and _code(refused) == "real_only"
    v = svc.vehicles[vid]
    v.kind = "real"  # stands in for a connected flight controller
    v.state["autopilot_id"] = 3
    v.state["mav_type"] = 2
    v.state["version"] = {
        "flight_sw_version": (4 << 24) | (5 << 16) | (7 << 8) | 255,
        "flight_custom_version": b"2a3dc4b7".hex(),
        "board_version": 140 << 16,
    }
    v.state["params"] = {
        "ATC_RAT_RLL_P": {"value": 0.135, "type": "REAL32"},
        "SERIAL1_PROTOCOL": {"value": 2.0, "type": "INT8"},
    }
    v.state["param_count"] = 3
    partial = api.post(f"{API}/twins/profiles", headers=OP, json={"from_vehicle": vid})
    assert partial.status_code == 409 and _code(partial) == "params_incomplete"
    v.state["params"]["FRAME_CLASS"] = {"value": 1.0, "type": "INT8"}
    firmware = api.get(f"{API}/vehicles/{vid}/firmware").json()
    assert firmware["identity"]["board"] == "Cube Orange" and firmware["params"]["complete"]
    assert (
        firmware["match"]["recommended"] == "ardupilot-copter-4.7.2"
        and firmware["match"]["match"] == "nearest"
    )
    assert firmware["frame_problem"] is None
    captured = api.post(f"{API}/twins/profiles", headers=OP, json={"from_vehicle": vid, "name": "Bench"})
    assert captured.status_code == 200, captured.text
    profile = captured.json()
    pid = profile["id"]
    assert profile["name"] == "Bench" and profile["pack"] == "ardupilot-copter-4.7.2"
    assert profile["params_total"] == 3 and profile["params_excluded"] == 1
    assert profile["excluded"][0]["name"] == "SERIAL1_PROTOCOL"
    assert profile["firmware"]["git"] == "2a3dc4b7"
    assert [p["id"] for p in api.get(f"{API}/twins/profiles").json()["profiles"]] == [pid]
    patched = api.put(
        f"{API}/twins/profiles/{pid}",
        headers=OP,
        json={
            "name": "Bench 2",
            "frame": {"preset": "small-250"},
            "home": {"lat": 31.2, "lon": 121.5, "alt_m": 4},
        },
    ).json()
    assert patched["name"] == "Bench 2" and patched["frame"]["preset"] == "small-250"
    assert patched["home"] == {"lat": 31.2, "lon": 121.5, "alt_m": 4.0}
    wrong = api.put(f"{API}/twins/profiles/{pid}", headers=OP, json={"pack": "px4-1.16.2"})
    assert wrong.status_code == 400 and _code(wrong) == "pack_unsupported"
    assert api.get(f"{API}/twins/profiles/{pid}").json()["params"]["ATC_RAT_RLL_P"] == 0.135
    assert api.delete(f"{API}/twins/profiles/{pid}", headers=OP).json() == {"id": pid, "deleted": True}
    missing = api.get(f"{API}/twins/profiles/{pid}")
    assert missing.status_code == 404 and _code(missing) == "profile_not_found"
    # a frame a twin cannot fly
    v.state["params"]["FRAME_CLASS"] = {"value": 2.0, "type": "INT8"}
    hexa = api.post(f"{API}/twins/profiles", headers=OP, json={"from_vehicle": vid}).json()
    started = api.post(f"{API}/sims", headers=OP, json={"engine": "sitl", "profile": hexa["id"]})
    assert started.status_code == 409 and _code(started) == "frame_unsupported"
    assert "FRAME_CLASS" in started.json()["error"]["detail"]["reason_zh"]


def test_api_profile_capture_and_edit_review_fixes(api, svc, stepper, tmp_path):
    """Review fixes (v0.5.4): capture waits for the firmware version, a battery guess the physics cannot take
    falls back to the preset, a PUT refuses an unusable pack, and edits made while a recapture reads the
    aircraft are kept."""
    (vid,) = api.post(f"{API}/sims", headers=OP, json={}).json()["added"]
    stepper.run(1.0)
    v = svc.vehicles[vid]
    v.kind = "real"
    v.state["autopilot_id"] = 3
    v.state["mav_type"] = 2
    v.state["params"] = {
        "ATC_RAT_RLL_P": {"value": 0.135, "type": "REAL32"},
        "BATT_CAPACITY": {"value": 20.0, "type": "INT32"},
        "FRAME_CLASS": {"value": 1.0, "type": "INT8"},
    }
    v.state["param_count"] = 3
    v.state["version"] = None  # HEARTBEAT only: the family is known, the version is not
    early = api.post(f"{API}/twins/profiles", headers=OP, json={"from_vehicle": vid})
    assert early.status_code == 400 and _code(early) == "pack_unsupported"
    assert early.json()["error"]["detail"]["reason_zh"] == "尚未识别飞机的固件"
    v.state["version"] = {"flight_sw_version": (4 << 24) | (7 << 16) | (2 << 8) | 255}
    captured = api.post(f"{API}/twins/profiles", headers=OP, json={"from_vehicle": vid})
    assert captured.status_code == 200, captured.text
    profile = captured.json()
    pid = profile["id"]
    assert profile["frame"]["capacity_mah"] == PRESETS[profile["frame"]["preset"]].capacity_mah
    assert profile["firmware"]["version"] == "4.7.2"
    # an unusable pack of the same firmware is refused, not stored as the recommendation
    broken = svc.packs.get("ardupilot-copter-4.7.2")
    broken.executable.chmod(0o644)
    refused = api.put(f"{API}/twins/profiles/{pid}", headers=OP, json={"pack": "ardupilot-copter-4.7.2"})
    assert refused.status_code == 400 and _code(refused) == "pack_unsupported"
    assert refused.json()["error"]["detail"]["reason"] == "bin/arducopter is not executable"
    broken.executable.chmod(0o755)
    # a rename while the recapture reads the aircraft is not overwritten by the recapture
    identity = svc._identity

    def read_and_rename(vehicle):
        svc.update_twin_profile(pid, {"name": "Renamed meanwhile"})
        return identity(vehicle)

    svc._identity = read_and_rename
    try:
        again = api.post(f"{API}/twins/profiles", headers=OP, json={"from_vehicle": vid, "profile": pid})
    finally:
        svc._identity = identity
    assert again.status_code == 200, again.text
    assert again.json()["name"] == "Renamed meanwhile"
    # an edit of a profile deleted meanwhile does not write it back
    assert svc.twin_profiles.delete(pid)
    assert svc.twin_profiles.update(pid, lambda p: {**p, "name": "ghost"}) is None
    assert svc.twin_profiles.get(pid) is None and not list((tmp_path / "twins").glob("tp-*.json"))
    gone = api.put(f"{API}/twins/profiles/{pid}", headers=OP, json={"name": "ghost"})
    assert gone.status_code == 404 and _code(gone) == "profile_not_found"


@pytest.mark.skipif(not __import__("sys").platform.startswith("linux"), reason="PR_SET_PDEATHSIG is Linux")
def test_firmware_dies_with_a_killed_relay_and_stale_workdirs_are_swept(tmp_path):
    """Review fix (v0.5.4): firmware started on a short-lived thread survives that thread, dies with a
    SIGKILLed Relay, and the working directories of a dead Relay are removed by the next one."""
    import os
    import signal
    import subprocess
    import sys
    import time

    from relay.twin import process as tproc

    script = (
        "import threading, time\n"
        "from pathlib import Path\n"
        "from relay.twin.process import FirmwareProcess\n"
        "box = {}\n"
        "def start():\n"
        "    p = FirmwareProcess(['sleep', '60'], Path('.'))\n"
        "    p.start()\n"
        "    box['pid'] = p.pid\n"
        "t = threading.Thread(target=start)\n"
        "t.start()\n"
        "t.join()\n"
        "time.sleep(0.3)\n"
        "print(box['pid'], flush=True)\n"
        "time.sleep(60)\n"
    )
    relay = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=str(Path(__file__).resolve().parents[1]),
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        child = int(relay.stdout.readline())
        os.kill(child, 0)  # alive after the starting thread ended
        relay.send_signal(signal.SIGKILL)
        relay.wait(5)
        for _ in range(100):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            if Path(f"/proc/{child}/stat").read_text().split()[2] == "Z":
                break  # killed, waiting for its reaper
            time.sleep(0.05)
        else:
            os.kill(child, signal.SIGKILL)
            pytest.fail("the firmware outlived a killed Relay")
    finally:
        if relay.poll() is None:
            relay.kill()
        relay.stdout.close()

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    stale = tmp_path / "relay-twin-stale"
    stale.mkdir()
    (stale / tproc.OWNER_FILE).write_text(str(dead.pid))
    mine = tmp_path / "relay-twin-mine"
    mine.mkdir()
    (mine / tproc.OWNER_FILE).write_text(str(os.getpid()))
    unknown = tmp_path / "relay-twin-unknown"
    unknown.mkdir()
    assert tproc.sweep_stale(tmp_path) == [stale]
    assert mine.is_dir() and unknown.is_dir()
