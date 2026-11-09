"""Firmware twins through the fleet service (11 §2-§6, §10).

A mixin for ``FleetService``: the runtime packs (``GET /autopilots``, import, delete), a connected aircraft's
firmware identity and matching packs (``GET /vehicles/{id}/firmware``), twin profiles (``/twins/profiles``),
twin simulators (``POST /sims`` with ``engine: "sitl"``, ``GET /sims/{id}/twin``) and the frame of any
simulator (``PUT /sims/{id}/frame``). The runtime lives in ``relay.twin``.

Threads: pack and profile files are read and written on the calling (API) thread; vehicle state is read
and changed on the fleet thread through ``self.call``. A twin's firmware boots on the calling thread before
its simulator joins the fleet (up to 15 s, longer than ``call`` may block); ids are reserved meanwhile.

Twins are simulators (``kind`` ``sim``, ids ``sim-n``) and follow the simulator rules: the operator, or an
agent session while app control is on, adds and removes them; the frame follows the airframe rule (the
operator, the controller, or an agent on an uncontrolled simulator). A twin has no hover assist
(``twin_unsupported``) and does not take part in dogfights; its airframe defects all act, physically.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .authority import Actor
from .config import SIM_LIMIT
from .errors import RelayError, err
from .twin import identity as ident
from .twin import profile as tprofile
from .twin.autopilot import DEFAULT_HOME, TwinAutopilot, prepare, shifted_home
from .twin.packs import Pack, PackError, PackStore, current_platform
from .twin.process import TwinStartError, stop_all

log = logging.getLogger("relay.fleet")

FAMILY_TEXT = {"ardupilot": "ArduCopter", "px4": "PX4"}
MATCH_ZH = {
    "exact": "精确匹配",
    "version": "同版本",
    "series": "同系列",
    "nearest": "最近版本",
    "none": "无匹配",
}
REASON_ZH = {
    "the aircraft's firmware is not identified yet": "尚未识别飞机的固件",
    "the pack and the profile are for different firmware": "运行包与孪生配置属于不同固件",
    "the pack belongs to a different firmware than the requested autopilot": "运行包与所选飞控固件不一致",
    "the pack runs a firmware twin": "运行包正被固件孪生使用",
}


def _is_twin(sim: Any) -> bool:
    return getattr(sim, "engine", "relay") == "sitl"


def _pack_error(exc: PackError) -> RelayError:
    return err("pack_unsupported", reason=exc.reason, reason_zh=exc.reason_zh)


def _reason(code: str, reason: str, **detail: Any) -> RelayError:
    return err(code, reason=reason, reason_zh=REASON_ZH.get(reason, reason), **detail)


def frame_view(frame: Any) -> dict[str, Any]:
    """A physics frame (``sim.frame.FrameModel``) as the API shows it."""
    return {
        "preset": frame.preset,
        "mass_kg": round(frame.mass_kg, 4),
        "wheelbase_m": round(frame.wheelbase_m, 4),
        "prop_in": round(frame.prop_in, 2),
        "cells": frame.cells,
        "capacity_mah": frame.capacity_mah,
        "thrust_to_weight": round(frame.thrust_to_weight, 3),
    }


class TwinOps:
    def _twin_setup(self) -> None:
        data: Path | None = self.data_dir  # type: ignore[attr-defined]
        self.packs = PackStore(imported=data / "autopilots" if data is not None else None)
        self.twin_profiles = tprofile.ProfileStore(data / "twins" if data is not None else None)
        self._twin_reserved: set[str] = set()

    # ------------------------------------------------------------------ helpers

    def _twins(self) -> dict[str, TwinAutopilot]:
        return {
            vid: v.driver.sim
            for vid, v in self.vehicles.items()  # type: ignore[attr-defined]
            if v.kind == "sim" and _is_twin(getattr(v.driver, "sim", None))
        }

    def _twin(self, vid: str) -> tuple[Any, TwinAutopilot]:
        v = self._vehicle(vid)  # type: ignore[attr-defined]
        sim = getattr(v.driver, "sim", None)
        if v.kind != "sim":
            raise err("sim_only")
        if not _is_twin(sim):
            raise err("not_found")
        return v, sim

    def _twin_shutdown(self) -> None:
        """Kill every firmware process (fleet shutdown); also registered at interpreter exit."""
        for twin in self._twins().values():
            twin.close()
        stop_all()

    def _close_twin(self, v: Any) -> None:
        sim = getattr(v.driver, "sim", None)
        if _is_twin(sim):
            sim.close()

    # ------------------------------------------------------------------ runtime packs (11 §3)

    def autopilots(self) -> dict[str, Any]:
        in_use: dict[str, int] = {}
        for twin in self.call(lambda: list(self._twins().values())):  # type: ignore[attr-defined]
            in_use[twin.pack.id] = in_use.get(twin.pack.id, 0) + 1
        imported = self.packs.imported_dir
        return {
            "platform": current_platform(),
            "bundled_dir": str(self.packs.bundled_dir),
            "imported_dir": None if imported is None else str(imported),
            "packs": [{**p.view(), "twins": in_use.get(p.id, 0)} for p in self.packs.list()],
        }

    def import_autopilot(self, path: str, sha256: str | None = None) -> dict[str, Any]:
        try:
            pack = self.packs.import_tarball(Path(path), sha256)
        except PackError as exc:
            raise _pack_error(exc) from None
        self.events.emit(  # type: ignore[attr-defined]
            "autopilot_pack_imported",
            data={"pack": pack.id, "family": pack.family, "version": pack.version},
        )
        return pack.view()

    def delete_autopilot(self, pack_id: str) -> dict[str, Any]:
        running = self.call(  # type: ignore[attr-defined]
            lambda: [vid for vid, t in self._twins().items() if t.pack.id == pack_id and t.state != "stopped"]
        )
        if running:
            raise _reason("pack_unsupported", "the pack runs a firmware twin", vehicles=running)
        try:
            self.packs.delete(pack_id)
        except KeyError:
            raise err("pack_missing", pack=pack_id) from None
        except PackError as exc:
            raise _pack_error(exc) from None
        return {"id": pack_id, "deleted": True}

    def _family_pack(self, family: str) -> Pack:
        """The newest usable pack of ``family``."""
        for pack in self.packs.list():
            if pack.family == family and pack.problem() is None:
                return pack
        raise err("pack_missing", pack=FAMILY_TEXT.get(family, family))

    # ------------------------------------------------------------------ firmware identity (11 §4)

    def _identity(self, v: Any) -> dict[str, Any]:
        st = v.state
        out = ident.identify(st)
        params = {name: float(entry["value"]) for name, entry in st["params"].items()}
        total = int(st["param_count"] or 0)
        out_params = {"received": len(params), "total": total, "complete": total > 0 and len(params) >= total}
        problem = ident.frame_problem(out["family"], params, st.get("mav_type"))
        return {
            "id": v.id,
            "kind": v.kind,
            "label": v.label,
            "firmware": st["firmware"],
            "identity": out,
            "params": out_params,
            "frame_problem": None if problem is None else problem[0],
            "frame_problem_zh": None if problem is None else problem[1],
        }

    def vehicle_firmware(self, vid: str) -> dict[str, Any]:
        """``GET /vehicles/{id}/firmware``: the identity, the matching packs and whether a twin can fly it."""
        view = self.call(lambda: self._identity(self._vehicle(vid)))  # type: ignore[attr-defined]
        view["match"] = ident.match(view["identity"], self.packs.list())
        return view

    def _firmware_info(self, v: Any) -> dict[str, Any] | None:
        """The snapshot's ``firmware_info`` (11 §10): None until AUTOPILOT_VERSION arrived."""
        if not v.state.get("version"):
            return None
        out = ident.identify(v.state)
        return {k: out[k] for k in ("family", "vehicle", "version", "type", "git", "board")}

    # ------------------------------------------------------------------ twin profiles (11 §5)

    def twin_profiles_list(self) -> dict[str, Any]:
        return {"profiles": [tprofile.summary(p) for p in self.twin_profiles.list()]}

    def twin_profile(self, pid: str) -> dict[str, Any]:
        profile = self.twin_profiles.get(pid)
        if profile is None:
            raise err("profile_not_found", profile=pid)
        return tprofile.detail(profile)

    def capture_twin_profile(
        self,
        vid: str,
        name: str | None = None,
        frame: dict[str, Any] | None = None,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        """Capture a profile from a connected aircraft with its whole parameter table read; with
        ``profile_id``, recapture into that profile (identity and parameters; name and frame stay)."""
        existing = None
        if profile_id is not None:
            existing = self.twin_profiles.get(profile_id)
            if existing is None:
                raise err("profile_not_found", profile=profile_id)

        def run() -> dict[str, Any]:
            v = self._vehicle(vid)  # type: ignore[attr-defined]
            if v.kind != "real":
                raise err("real_only")
            info = self._identity(v)
            if not info["params"]["complete"]:
                raise err(
                    "params_incomplete", received=info["params"]["received"], total=info["params"]["total"]
                )
            if info["identity"]["family"] is None:
                raise _reason("pack_unsupported", "the aircraft's firmware is not identified yet")
            st = v.state
            params = {n: float(e["value"]) for n, e in st["params"].items()}
            types = {n: str(e.get("type") or "") for n, e in st["params"].items()}
            home = None
            if st.get("home"):
                h = st["home"]
                home = {"lat": float(h["lat"]), "lon": float(h["lon"]), "alt_m": float(h["alt"])}
            elif st.get("global") and st["global"].get("valid"):
                g = st["global"]
                alt = v.home_amsl()
                home = {"lat": g["lat"], "lon": g["lon"], "alt_m": alt if alt is not None else g["alt"]}
            return {
                "info": info,
                "params": params,
                "types": types,
                "home": home,
                "cells": v.cells(),
                "label": v.label,
                "wall": self.wall(),  # type: ignore[attr-defined]
            }

        got = self.call(run)  # type: ignore[attr-defined]
        identity = got["info"]["identity"]
        if existing is not None and (existing.get("firmware") or {}).get("family") != identity["family"]:
            raise _reason("pack_unsupported", "the pack and the profile are for different firmware")
        matched = ident.match(identity, self.packs.list())
        try:
            fresh = tprofile.capture(
                vehicle_id=vid,
                label=got["label"],
                identity=identity,
                params=got["params"],
                types=got["types"],
                pack=matched["recommended"],
                match_level=matched["match"],
                home=got["home"],
                cells=got["cells"],
                captured_at=got["wall"],
                name=name,
                frame=frame,
            )
        except tprofile.ProfileError as exc:
            raise err("invalid_request", reason=exc.reason) from None
        profile = tprofile.recapture(existing, fresh) if existing is not None else fresh
        self.twin_profiles.save(profile)
        return tprofile.detail(profile)

    def update_twin_profile(self, pid: str, patch: dict[str, Any]) -> dict[str, Any]:
        profile = self.twin_profiles.get(pid)
        if profile is None:
            raise err("profile_not_found", profile=pid)
        if patch.get("pack") is not None:
            pack = self.packs.get(str(patch["pack"]))
            if pack is None:
                raise err("pack_missing", pack=str(patch["pack"]))
            if pack.family != (profile.get("firmware") or {}).get("family"):
                raise _reason("pack_unsupported", "the pack and the profile are for different firmware")
        try:
            updated = tprofile.edit(profile, patch, self.wall())  # type: ignore[attr-defined]
        except tprofile.ProfileError as exc:
            raise err("invalid_request", reason=exc.reason) from None
        if "pack" in patch and patch["pack"] is not None:
            pack = self.packs.get(str(patch["pack"]))
            updated["match"] = ident.level_for(profile.get("firmware") or {}, pack) if pack else "none"
        self.twin_profiles.save(updated)
        return tprofile.detail(updated)

    def delete_twin_profile(self, pid: str) -> dict[str, Any]:
        if not self.twin_profiles.delete(pid):
            raise err("profile_not_found", profile=pid)
        return {"id": pid, "deleted": True}

    # ------------------------------------------------------------------ twin simulators (11 §6)

    def _resolve_twin(
        self,
        flavour: str | None,
        profile_id: str | None,
        pack_id: str | None,
        frame: dict[str, Any] | None,
        home: dict[str, Any] | None,
    ) -> tuple[
        Pack, dict[str, Any] | None, str | None, dict[str, Any] | None, tuple[float, float, float] | None
    ]:
        """(pack, profile, match level, frame spec, home) for new twins; raises the API errors."""
        profile = None
        if profile_id is not None:
            profile = self.twin_profiles.get(profile_id)
            if profile is None:
                raise err("profile_not_found", profile=profile_id)
        family = (profile or {}).get("firmware", {}).get("family") if profile else None
        if pack_id is not None:
            pack = self.packs.get(pack_id)
            if pack is None:
                raise err("pack_missing", pack=pack_id)
        elif profile is not None and profile.get("pack") and self.packs.get(profile["pack"]) is not None:
            pack = self.packs.get(profile["pack"])  # type: ignore[assignment]
        else:
            pack = self._family_pack(family or flavour or "ardupilot")
        assert pack is not None
        if family is not None and pack.family != family:
            raise _reason("pack_unsupported", "the pack and the profile are for different firmware")
        if flavour is not None and pack.family != flavour:
            raise _reason(
                "pack_unsupported", "the pack belongs to a different firmware than the requested autopilot"
            )
        problem = pack.problem()
        if problem is not None:
            raise err("pack_unsupported", reason=problem[0], reason_zh=problem[1])
        if profile is not None:
            frame_issue = ident.frame_problem(pack.family, profile.get("params") or {})
            if frame_issue is not None:
                raise err("frame_unsupported", reason=frame_issue[0], reason_zh=frame_issue[1])
        level = ident.level_for(profile.get("firmware") or {}, pack) if profile is not None else None
        try:
            spec = None
            if frame is not None:
                spec = tprofile.normalise_frame(frame, (profile or {}).get("frame"))
            where = tprofile.normalise_home(home)
        except tprofile.ProfileError as exc:
            raise err("invalid_request", reason=exc.reason) from None
        base = None if where is None else (where["lat"], where["lon"], where["alt_m"])
        return pack, profile, level, spec, base

    def _add_twins(
        self,
        count: int,
        flavour: str | None,
        airframe: dict[str, Any] | None,
        profile_id: str | None,
        pack_id: str | None,
        frame: dict[str, Any] | None,
        home: dict[str, Any] | None,
    ) -> list[str]:
        from .drivers.sim import SimDriver
        from .fleet import SIM_SPAWNS, Vehicle

        pack, profile, level, spec, base_home = self._resolve_twin(flavour, profile_id, pack_id, frame, home)
        if base_home is None and profile is not None and profile.get("home"):
            h = profile["home"]
            base_home = (float(h["lat"]), float(h["lon"]), float(h.get("alt_m", 0.0)))
        base_home = base_home or DEFAULT_HOME

        def reserve() -> tuple[list[str], list[Any]]:
            sims = {vid for vid, v in self.vehicles.items() if v.kind == "sim"}  # type: ignore[attr-defined]
            taken = sims | self._twin_reserved
            if len(taken) + count > SIM_LIMIT:
                raise err("sim_limit", limit=SIM_LIMIT)
            limit = self.settings.twin_limit  # type: ignore[attr-defined]
            if len(self._twins()) + len(self._twin_reserved) + count > limit:
                raise err("twin_limit", limit=limit)
            ids = []
            for _ in range(count):
                n = next(i for i in range(1, SIM_LIMIT + 1) if f"sim-{i}" not in taken)
                taken.add(f"sim-{n}")
                ids.append(f"sim-{n}")
            self._twin_reserved.update(ids)
            fit = self._resolve_airframe(airframe) if airframe else None  # type: ignore[attr-defined]
            frames = [fit or self._resolve_airframe(None) for _ in ids]  # type: ignore[attr-defined]
            return ids, frames

        ids, airframes = self.call(reserve)  # type: ignore[attr-defined]
        added: list[str] = []
        try:
            for vid, af in zip(ids, airframes, strict=True):
                n = int(vid.split("-", 1)[1])
                spawn = SIM_SPAWNS[n - 1]
                try:
                    config = prepare(pack, profile, spec, shifted_home(base_home, spawn[0], spawn[1]), level)
                except ValueError as exc:
                    raise err("invalid_request", reason=f"frame {exc.args[0] if exc.args else ''}") from None
                twin = TwinAutopilot(
                    config,
                    name=vid,
                    turbulence=self.settings.sim_turbulence,  # type: ignore[attr-defined]
                    airframe=af,
                )
                try:
                    twin.start()
                except TwinStartError as exc:
                    raise err(
                        "twin_start_failed",
                        reason=exc.reason,
                        reason_zh=_start_reason_zh(exc.reason),
                        lines=exc.lines,
                        pack=pack.id,
                        added=list(added),
                    ) from None
                except OSError as exc:
                    raise err(
                        "twin_start_failed",
                        reason=str(exc),
                        reason_zh=f"无法打开回环端口或启动进程（{exc}）",
                        lines=[],
                        pack=pack.id,
                        added=list(added),
                    ) from None

                def attach(
                    vid: str = vid, twin: TwinAutopilot = twin, n: int = n, spawn: Any = spawn
                ) -> None:
                    v = Vehicle(vid, "sim", f"SIM-{n}", SimDriver(twin))  # type: ignore[arg-type]
                    self._attach(v)  # type: ignore[attr-defined]
                    v.created = self.clock()  # type: ignore[attr-defined]
                    v.spawn = spawn
                    v.frame_offset = (spawn[0], spawn[1], 0.0)
                    v.safety_origin = (spawn[0], spawn[1], 0.0)
                    twin.notify = lambda kind, data, v=v: self._twin_notice(v, kind, data)
                    self.vehicles[vid] = v  # type: ignore[attr-defined]
                    self._twin_reserved.discard(vid)
                    self.events.emit(  # type: ignore[attr-defined]
                        "sim_added",
                        vid,
                        data={
                            "autopilot": pack.family,
                            "engine": "sitl",
                            "hover_assist": True,
                            "airframe": twin.airframe.ref(),
                            "firmware": twin.firmware,
                        },
                        labels={"vehicle": v.label},
                    )
                    self._twin_notice(v, "twin_started", {"restart": False})

                try:
                    self.call(attach)  # type: ignore[attr-defined]
                except BaseException:
                    twin.close()
                    raise
                added.append(vid)
        finally:

            def release() -> None:
                self._twin_reserved.difference_update(ids)

            try:
                self.call(release)  # type: ignore[attr-defined]
            except RelayError:
                self._twin_reserved.difference_update(ids)
        return added

    def _twin_notice(self, v: Any, kind: str, data: dict[str, Any]) -> None:
        """A twin's lifecycle notification, on the fleet thread: events and the link state."""
        twin = v.driver.sim
        firmware = twin.firmware
        text = f"{FAMILY_TEXT.get(firmware['family'], firmware['family'])} {firmware['version']}"
        if kind == "twin_started":
            self.events.emit(  # type: ignore[attr-defined]
                "twin_started",
                v.id,
                data={
                    **firmware,
                    "restart": bool(data.get("restart")),
                    "pid": twin.pid,
                    "boot_s": twin.boot_s,
                },
                labels={"vehicle": v.label, "firmware": text},
            )
        elif kind == "twin_exited":
            self.events.emit(  # type: ignore[attr-defined]
                "twin_exited",
                v.id,
                data={"code": data.get("code"), "lines": data.get("lines") or [], **firmware},
                labels={"vehicle": v.label},
            )
            now = self.clock()  # type: ignore[attr-defined]
            if v.link_state in ("ok", "delayed"):
                self._set_link(v, "lost", now)  # type: ignore[attr-defined]
        elif kind == "twin_start_failed":
            reason = str(data.get("reason") or "")
            self.events.emit(  # type: ignore[attr-defined]
                "twin_start_failed",
                v.id,
                data={
                    "reason_en": reason,
                    "reason_zh": _start_reason_zh(reason),
                    "lines": data.get("lines") or [],
                },
                labels={"vehicle": v.label},
            )

    def twin_view(self, vid: str) -> dict[str, Any]:
        """``GET /sims/{id}/twin``."""

        def run() -> dict[str, Any]:
            v, twin = self._twin(vid)
            return {"id": vid, "label": v.label, **twin.view()}

        return self.call(run)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ frames (11 §5, §8.1)

    def set_sim_frame(
        self, vid: str, spec: dict[str, Any], actor: Actor, app_control: bool = False
    ) -> dict[str, Any]:
        """``PUT /sims/{id}/frame``: a new frame for a disarmed simulator (twin or fast simulator)."""

        def run() -> dict[str, Any]:
            v = self._airframe_vehicle(vid, actor, app_control)  # type: ignore[attr-defined]
            if v.armed:
                raise err("armed")
            sim = v.driver.sim
            if not sim.quad.s.on_ground:
                raise err("armed")
            current = frame_view(sim.quad.frame)
            try:
                frame = tprofile.normalise_frame(spec, current)
            except tprofile.ProfileError as exc:
                raise err("invalid_request", reason=exc.reason) from None
            if _is_twin(sim):
                cfg = sim.config
                try:
                    model = tprofile.frame_model(frame, cfg.effective, cfg.family)
                except ValueError as exc:
                    raise err("invalid_request", reason=f"frame {exc.args[0] if exc.args else ''}") from None
                from dataclasses import replace

                sim.set_frame(replace(cfg, frame=model))
            else:
                try:
                    model = tprofile.frame_model(frame, {}, "relay")
                except ValueError as exc:
                    raise err("invalid_request", reason=f"frame {exc.args[0] if exc.args else ''}") from None
                sim.quad.set_frame(model)
            return {
                "id": vid,
                "engine": "sitl" if _is_twin(sim) else "relay",
                "frame": frame_view(sim.quad.frame),
            }

        return self.call(run)  # type: ignore[attr-defined]


def _start_reason_zh(reason: str) -> str:
    table = {
        "no heartbeat from the firmware within 15 s": "15 秒内未收到固件心跳",
        "the firmware did not connect its MAVLink link": "固件未连接 MAVLink 链路",
        "the firmware did not connect to the simulator port": "固件未连接仿真端口",
    }
    if reason in table:
        return table[reason]
    if reason.startswith("the firmware exited with code"):
        return "固件进程退出，退出码" + reason.rsplit(" ", 1)[-1]
    return reason


__all__ = ["TwinOps", "frame_view"]
