"""Autopilot runtime packs (11 §3, docs/autopilot-packs.md): pack directory and scripts/build-pack.py."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tarfile
from pathlib import Path

import pytest

from relay import config

REPO = Path(__file__).resolve().parents[2]


def _load_build_pack():
    spec = importlib.util.spec_from_file_location("relay_build_pack", REPO / "scripts" / "build-pack.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bp = _load_build_pack()


# --- autopilots_dir ---------------------------------------------------------------------------------------


def test_autopilots_dir_prefers_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("RELAY_AUTOPILOTS_DIR", str(tmp_path / "packs"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert config.autopilots_dir() == tmp_path / "packs"


def test_autopilots_dir_frozen_uses_the_packaged_resources(monkeypatch, tmp_path):
    monkeypatch.delenv("RELAY_AUTOPILOTS_DIR", raising=False)
    exe = tmp_path / "Resources" / "backend" / "anet-relay-backend"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert config.autopilots_dir() == (tmp_path / "Resources" / "autopilots").resolve()


def test_autopilots_dir_development_is_var_in_the_repository(monkeypatch):
    monkeypatch.setenv("RELAY_AUTOPILOTS_DIR", "  ")
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert config.autopilots_dir() == REPO / "var" / "autopilots"


# --- build-pack.py ----------------------------------------------------------------------------------------


@pytest.fixture
def ardupilot_inputs(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "arducopter").write_bytes(b"#!/bin/sh\necho arducopter\n")
    (src / "git-version.txt").write_text("commit fb773567896014da007bca58358704d4a0d8516f\nAuthor: x\n")
    (src / "copter.parm").write_text("FRAME_CLASS 1\n")
    (src / "cygwin1.dll").write_bytes(b"MZ-dll")
    return src


def _pack_ardupilot(src: Path, out: Path, *extra: str, platform: str = "linux-x64", version: str = "4.7.2"):
    args = [
        "ardupilot",
        "--version",
        version,
        "--platform",
        platform,
        "--built",
        "official",
        "--binary",
        str(src / "arducopter"),
        "--git-version-file",
        str(src / "git-version.txt"),
        "--copter-parm",
        str(src / "copter.parm"),
        "--out",
        str(out),
        "--no-strip",
        *extra,
    ]
    assert bp.main(args) == 0


def _px4_tree(tmp_path: Path) -> Path:
    tree = tmp_path / "px4_sitl_default"
    (tree / "bin").mkdir(parents=True)
    (tree / "bin" / "px4").write_bytes(b"\x7fELF-px4")
    (tree / "bin" / "px4").chmod(0o755)
    (tree / "bin" / "px4-alias.sh").write_text("alias mavlink='px4-mavlink'\n")
    (tree / "bin" / "px4-mavlink").symlink_to("px4")
    rc = tree / "etc" / "init.d-posix"
    rc.mkdir(parents=True)
    (rc / "rcS").write_text("#!/bin/sh\n")
    (rc / "px4-rc.mavlink").write_text("mavlink start -x -u 18570 -r 4000000 -f\n")
    return tree


def test_ardupilot_pack_manifest_and_tarball(ardupilot_inputs, tmp_path):
    out = tmp_path / "dist"
    _pack_ardupilot(ardupilot_inputs, out)
    root = out / "ardupilot-copter-4.7.2"
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest == {
        "format": 1,
        "id": "ardupilot-copter-4.7.2",
        "family": "ardupilot",
        "vehicle": "copter",
        "version": "4.7.2",
        "git": "fb773567896014da007bca58358704d4a0d8516f",
        "platform": "linux-x64",
        "entry": "bin/arducopter",
        "defaults": ["defaults/copter.parm", "defaults/relay-quadx.parm"],
        "size_bytes": manifest["size_bytes"],
        "license": "GPL-3.0-only",
        "source": "https://github.com/ArduPilot/ardupilot/tree/Copter-4.7.2",
        "built": "official",
    }
    files = [p for p in root.rglob("*") if p.is_file() and p.name != "manifest.json"]
    assert manifest["size_bytes"] == sum(p.stat().st_size for p in files)
    baseline = (root / "defaults" / "relay-quadx.parm").read_text()
    assert "FRAME_TYPE 1" in baseline and "SIM_RATE_HZ 1000" in baseline
    assert (root / "defaults" / "copter.parm").read_text() == "FRAME_CLASS 1\n"

    tar_path = out / "ardupilot-copter-4.7.2-linux-x64.tar.gz"
    digest = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    assert (out / f"{tar_path.name}.sha256").read_text().split() == [digest, tar_path.name]
    with tarfile.open(tar_path) as tar:
        members = {m.name: m for m in tar.getmembers()}
    pid = "ardupilot-copter-4.7.2"
    assert all(name == pid or name.startswith(f"{pid}/") for name in members)
    entry = members["ardupilot-copter-4.7.2/bin/arducopter"]
    assert entry.mode == 0o755 and entry.uid == 0 and entry.uname == ""
    assert members["ardupilot-copter-4.7.2/defaults/copter.parm"].mode == 0o644


def test_windows_pack_ships_the_libraries_next_to_the_executable(ardupilot_inputs, tmp_path):
    out = tmp_path / "dist"
    _pack_ardupilot(ardupilot_inputs, out, "--lib", str(ardupilot_inputs / "cygwin1.dll"), platform="win-x64")
    root = out / "ardupilot-copter-4.7.2"
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["entry"] == "bin/arducopter.exe" and manifest["platform"] == "win-x64"
    assert (root / "bin" / "arducopter.exe").is_file() and (root / "bin" / "cygwin1.dll").is_file()
    assert (out / "ardupilot-copter-4.7.2-win-x64.tar.gz").is_file()


def test_px4_pack_overlays_the_gcs_link_and_keeps_client_links(tmp_path):
    out = tmp_path / "dist"
    tree = _px4_tree(tmp_path)
    args = ["px4", "--version", "1.16.2", "--platform", "linux-x64", "--tree", str(tree)]
    assert bp.main([*args, "--git", "54F0455FFCD7", "--out", str(out), "--no-strip"]) == 0
    root = out / "px4-1.16.2"
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["git"] == "54f0455ffcd7"
    assert manifest["entry"] == "bin/px4" and manifest["romfs"] == "etc"
    assert manifest["defaults"] == ["defaults/relay-quadx.params"]
    assert manifest["license"] == "BSD-3-Clause" and manifest["built"] == "ci"
    assert manifest["source"] == "https://github.com/PX4/PX4-Autopilot/tree/v1.16.2"
    assert list(manifest) == [k for k in bp.MANIFEST_KEYS if k in manifest]
    rc = (root / "etc" / "init.d-posix" / "px4-rc.mavlink").read_text()
    assert "RELAY_GCS_PORT_LOCAL" in rc and "RELAY_GCS_PORT_REMOTE" in rc and "RELAY_PX4_PARAMS" in rc
    for port in ("14550", "14540", "13280"):
        assert not any(port in line for line in rc.splitlines() if line.startswith("mavlink start"))
    assert "param set-default MPC_THR_HOVER 0.42" in (root / "defaults" / "relay-quadx.params").read_text()
    link = root / "bin" / "px4-mavlink"
    assert link.is_symlink() and link.readlink() == Path("px4")
    with tarfile.open(out / "px4-1.16.2-linux-x64.tar.gz") as tar:
        member = tar.getmember("px4-1.16.2/bin/px4-mavlink")
    assert member.issym() and member.linkname == "px4"


def test_index_lists_tarballs_and_unpacked_packs(ardupilot_inputs, tmp_path):
    out = tmp_path / "dist"
    _pack_ardupilot(ardupilot_inputs, out, version="4.6.3")
    _pack_ardupilot(ardupilot_inputs, out, version="4.7.2")
    tree = _px4_tree(tmp_path)
    args = ["px4", "--version", "1.16.2", "--platform", "linux-x64", "--tree", str(tree), "--git", "54f0455f"]
    assert bp.main([*args, "--out", str(out), "--no-strip"]) == 0
    # An installed tree keeps the unpacked directory and the .sha256 only.
    px4_tar = out / "px4-1.16.2-linux-x64.tar.gz"
    px4_sha = hashlib.sha256(px4_tar.read_bytes()).hexdigest()
    px4_tar.unlink()
    assert bp.main(["index", str(out), "--platform", "linux-x64"]) == 0
    index = json.loads((out / "index.json").read_text())
    assert index["format"] == 1 and index["platform"] == "linux-x64"
    ids = [p["id"] for p in index["packs"]]
    assert ids == ["ardupilot-copter-4.7.2", "ardupilot-copter-4.6.3", "px4-1.16.2"]
    by_id = {p["id"]: p for p in index["packs"]}
    tar_472 = out / "ardupilot-copter-4.7.2-linux-x64.tar.gz"
    assert by_id["ardupilot-copter-4.7.2"]["sha256"] == hashlib.sha256(tar_472.read_bytes()).hexdigest()
    assert by_id["px4-1.16.2"]["sha256"] == px4_sha
    manifest = json.loads((out / "ardupilot-copter-4.7.2" / "manifest.json").read_text())
    assert {k: v for k, v in by_id["ardupilot-copter-4.7.2"].items() if k != "sha256"} == manifest
    # Other platforms get an empty index rather than an error.
    assert bp.build_index(out, "mac-arm64") == {"format": 1, "platform": "mac-arm64", "packs": []}


def test_index_needs_a_digest_for_unpacked_packs(ardupilot_inputs, tmp_path):
    out = tmp_path / "dist"
    _pack_ardupilot(ardupilot_inputs, out)
    (out / "ardupilot-copter-4.7.2-linux-x64.tar.gz").unlink()
    (out / "ardupilot-copter-4.7.2-linux-x64.tar.gz.sha256").unlink()
    with pytest.raises(bp.PackError):
        bp.build_index(out, "linux-x64")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("--version", "4.7"), "x.y.z"),
        (("--platform", "win-x64", "--family", "px4"), "Windows"),
        (("--git", "not-hex"), "hex"),
    ],
)
def test_bad_inputs_are_refused(change, message):
    family = "px4" if "--family" in change else "ardupilot"
    values = dict(zip(change[::2], change[1::2], strict=True))
    with pytest.raises(bp.PackError, match=message):
        bp.base_manifest(
            family,
            values.get("--version", "4.7.2"),
            values.get("--git", "fb773567"),
            values.get("--platform", "linux-x64"),
            "ci",
        )


def test_git_version_file_without_a_commit_is_refused(tmp_path):
    path = tmp_path / "git-version.txt"
    path.write_text("APMVERSION: ArduCopter V4.7.2\n")
    with pytest.raises(bp.PackError):
        bp.git_from_version_file(path)


def test_bundled_versions_are_listed_with_their_tags():
    data = json.loads((REPO / "autopilots" / "versions.json").read_text())
    assert [v["version"] for v in data["ardupilot"]] == ["4.7.2", "4.6.3", "4.5.7", "4.4.4", "4.3.8"]
    assert [v["version"] for v in data["px4"]] == ["1.17.0", "1.16.2", "1.15.4", "1.14.4"]
    assert all(v["tag"] == f"Copter-{v['version']}" for v in data["ardupilot"])
    assert all(v["tag"] == f"v{v['version']}" for v in data["px4"])
