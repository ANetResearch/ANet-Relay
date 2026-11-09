"""Autopilot runtime packs (11 §3, docs/autopilot-packs.md): list, verify, import and delete.

Two places hold packs:

* the bundled directory ``relay.config.autopilots_dir()`` (the installer's ``resources/autopilots``, or
  ``var/autopilots`` in development): ``index.json`` and one ``<id>/`` directory per pack, read only;
* the imported directory ``<data dir>/autopilots``: packs the operator imported from a tarball, in the same
  layout, with their own ``index.json``. Only these can be deleted.

An imported pack never replaces a bundled one with the same id. A tarball is verified before anything is
written: its sha256 against an explicit digest, the ``<file>.sha256`` beside it or an ``index*.json`` beside
it that lists it; its members against the rules of the format (one ``<id>/`` directory, no absolute paths,
no ``..``, links stay inside the pack, regular files, directories and symbolic links only); its manifest
(format 1, this platform, a known firmware, the entry executable present).
"""

from __future__ import annotations

import hashlib
import json
import os
import platform as _platform
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import autopilots_dir

FORMAT = 1
FAMILIES = ("ardupilot", "px4")
VEHICLES = ("copter",)
PLATFORMS = ("linux-x64", "mac-arm64", "mac-x64", "win-x64")
ID_RE = re.compile(r"^(ardupilot-copter|px4)-\d+\.\d+\.\d+$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
GIT_RE = re.compile(r"^[0-9a-f]{8,40}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_UNPACKED_BYTES = 512 * 1024 * 1024  # a pack is a few MB; refuse an archive that would unpack far beyond
MAX_MEMBERS = 4096


class PackError(ValueError):
    """A pack or a tarball that cannot be used: ``reason`` (English) and ``reason_zh``."""

    def __init__(self, reason: str, reason_zh: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.reason_zh = reason_zh


def current_platform() -> str | None:
    """This computer's pack platform, or None when no pack can run here."""
    machine = _platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    x64 = machine in ("x86_64", "amd64")
    if sys.platform.startswith("linux") and x64:
        return "linux-x64"
    if sys.platform == "darwin":
        return "mac-arm64" if arm else "mac-x64" if x64 else None
    if sys.platform in ("win32", "cygwin") and x64:
        return "win-x64"
    return None


def version_tuple(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(p) for p in version.split("."))
    return (major, minor, patch)


@dataclass(frozen=True)
class Pack:
    id: str
    family: str
    vehicle: str
    version: str
    git: str
    platform: str
    entry: str
    defaults: tuple[str, ...]
    romfs: str | None
    size_bytes: int
    license: str
    source: str
    built: str
    sha256: str | None
    origin: str  # "bundled" | "imported"
    path: Path
    # Built from source patched so that the firmware binds 127.0.0.1 only (manifest "loopback": true);
    # False for the official ArduPilot binary, which binds two UDP sockets on 0.0.0.0 (11 §2).
    loopback: bool = False

    @property
    def git8(self) -> str:
        return self.git[:8]

    @property
    def version_key(self) -> tuple[int, int, int]:
        return version_tuple(self.version)

    @property
    def executable(self) -> Path:
        return self.path / self.entry

    def default_files(self) -> list[Path]:
        return [self.path / name for name in self.defaults]

    def problem(self) -> tuple[str, str] | None:
        """Why this pack cannot run on this computer (English, Chinese), or None."""
        here = current_platform()
        if self.platform != here:
            where = here or "an unsupported platform"
            return (
                f"built for {self.platform}, this computer is {where}",
                f"适用于 {self.platform}，本机为 {here or '不支持的平台'}",
            )
        exe = self.executable
        if not exe.is_file():
            return f"{self.entry} is missing", f"缺少 {self.entry}"
        if os.name != "nt" and not os.access(exe, os.X_OK):
            return f"{self.entry} is not executable", f"{self.entry} 不可执行"
        for path in self.default_files():
            if not path.is_file():
                rel = path.relative_to(self.path)
                return f"{rel} is missing", f"缺少 {rel}"
        if self.family == "px4" and not (self.path / (self.romfs or "etc")).is_dir():
            return "the ROMFS directory is missing", "缺少 ROMFS 目录"
        return None

    def view(self) -> dict[str, Any]:
        problem = self.problem()
        return {
            "id": self.id,
            "family": self.family,
            "vehicle": self.vehicle,
            "version": self.version,
            "git": self.git,
            "platform": self.platform,
            "size_bytes": self.size_bytes,
            "license": self.license,
            "source": self.source,
            "built": self.built,
            "origin": self.origin,
            "sha256": self.sha256,
            "usable": problem is None,
            "problem": None if problem is None else problem[0],
            "problem_zh": None if problem is None else problem[1],
        }


def parse_manifest(data: Any) -> dict[str, Any]:
    """Validate a manifest (docs/autopilot-packs.md); unknown fields are ignored. Raises PackError."""
    if not isinstance(data, dict):
        raise PackError("manifest.json is not an object", "manifest.json 不是对象")
    if data.get("format") != FORMAT:
        raise PackError(
            f"manifest format {data.get('format')!r} is not supported",
            f"不支持清单格式 {data.get('format')!r}",
        )
    out: dict[str, Any] = {}
    for key in ("id", "family", "vehicle", "version", "git", "platform", "entry"):
        value = data.get(key)
        if not isinstance(value, str) or not value:
            raise PackError(f"manifest field {key} is missing", f"清单缺少字段 {key}")
        out[key] = value
    if out["family"] not in FAMILIES:
        raise PackError(f"firmware {out['family']} is not supported", f"不支持固件 {out['family']}")
    if out["vehicle"] not in VEHICLES:
        raise PackError(f"vehicle {out['vehicle']} is not supported", f"不支持机型 {out['vehicle']}")
    if not VERSION_RE.match(out["version"]):
        raise PackError(f"version {out['version']} is not x.y.z", f"版本号 {out['version']} 不是 x.y.z 形式")
    if not GIT_RE.match(out["git"]):
        raise PackError("git must be 8 to 40 lowercase hex digits", "git 须为 8 到 40 位小写十六进制数")
    if out["platform"] not in PLATFORMS:
        raise PackError(f"platform {out['platform']} is not known", f"未知平台 {out['platform']}")
    if not ID_RE.match(out["id"]):
        raise PackError(f"pack id {out['id']} is not valid", f"运行包编号 {out['id']} 无效")
    expected = (
        f"ardupilot-copter-{out['version']}" if out["family"] == "ardupilot" else f"px4-{out['version']}"
    )
    if out["id"] != expected:
        raise PackError(
            f"pack id {out['id']} does not match {expected}", f"运行包编号 {out['id']} 应为 {expected}"
        )
    defaults = data.get("defaults") or []
    if not isinstance(defaults, list) or not all(isinstance(d, str) and d for d in defaults):
        raise PackError("defaults must be a list of file names", "defaults 须为文件名列表")
    for rel in [out["entry"], *defaults, *([data["romfs"]] if data.get("romfs") else [])]:
        _check_relative(rel)
    out["defaults"] = tuple(defaults)
    romfs = data.get("romfs")
    out["romfs"] = romfs if isinstance(romfs, str) and romfs else ("etc" if out["family"] == "px4" else None)
    size = data.get("size_bytes")
    out["size_bytes"] = int(size) if isinstance(size, (int, float)) and not isinstance(size, bool) else 0
    for key, default in (("license", ""), ("source", ""), ("built", "")):
        value = data.get(key)
        out[key] = value if isinstance(value, str) else default
    out["loopback"] = data.get("loopback") is True
    return out


def _check_relative(rel: str) -> None:
    path = PurePosixPath(rel)
    if path.is_absolute() or ".." in path.parts or "\\" in rel:
        raise PackError(f"{rel} must be a path inside the pack", f"{rel} 须为运行包内的路径")


def _pack(manifest: dict[str, Any], path: Path, sha256: str | None, origin: str) -> Pack:
    return Pack(
        id=manifest["id"],
        family=manifest["family"],
        vehicle=manifest["vehicle"],
        version=manifest["version"],
        git=manifest["git"],
        platform=manifest["platform"],
        entry=manifest["entry"],
        defaults=manifest["defaults"],
        romfs=manifest["romfs"],
        size_bytes=manifest["size_bytes"],
        license=manifest["license"],
        source=manifest["source"],
        built=manifest["built"],
        sha256=sha256,
        origin=origin,
        path=path,
        loopback=manifest["loopback"],
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def _scan(root: Path | None, origin: str) -> list[Pack]:
    """The packs of one directory: those its index lists, else every ``<id>/manifest.json``."""
    if root is None or not root.is_dir():
        return []
    index = _read_json(root / "index.json")
    digests: dict[str, str | None] = {}
    if isinstance(index, dict) and isinstance(index.get("packs"), list):
        for entry in index["packs"]:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                sha = entry.get("sha256")
                digests[entry["id"]] = sha if isinstance(sha, str) and SHA_RE.match(sha) else None
        names = list(digests)
    else:
        names = sorted(p.name for p in root.iterdir() if (p / "manifest.json").is_file())
    packs = []
    for name in names:
        directory = root / name
        if not ID_RE.match(name) or not directory.is_dir():
            continue
        try:
            manifest = parse_manifest(_read_json(directory / "manifest.json"))
        except PackError:
            continue
        if manifest["id"] != name:
            continue
        packs.append(_pack(manifest, directory, digests.get(name), origin))
    return packs


def sort_key(pack: Pack) -> tuple[int, tuple[int, int, int]]:
    """ArduPilot first, newest version first within a family (the index order)."""
    family = FAMILIES.index(pack.family) if pack.family in FAMILIES else len(FAMILIES)
    major, minor, patch = pack.version_key
    return (family, (-major, -minor, -patch))


class PackStore:
    """The bundled and imported packs of this installation."""

    def __init__(self, bundled: Path | None = None, imported: Path | None = None) -> None:
        self._bundled = bundled
        self.imported_dir = imported
        self._lock = threading.Lock()

    @property
    def bundled_dir(self) -> Path:
        return self._bundled if self._bundled is not None else autopilots_dir()

    def list(self) -> list[Pack]:
        bundled = _scan(self.bundled_dir, "bundled")
        ids = {p.id for p in bundled}
        imported = [p for p in _scan(self.imported_dir, "imported") if p.id not in ids]
        here = current_platform()
        packs = [p for p in bundled + imported if p.platform == here or p.origin == "imported"]
        return sorted(packs, key=sort_key)

    def get(self, pack_id: str) -> Pack | None:
        for pack in self.list():
            if pack.id == pack_id:
                return pack
        return None

    # ------------------------------------------------------------------ import and delete

    def import_tarball(
        self,
        archive: Path,
        sha256: str | None = None,
        in_use: Callable[[str], bool] | None = None,
    ) -> Pack:
        """Verify ``archive`` and install it into the imported directory; returns the new pack. ``in_use``
        says whether a twin runs or boots from a pack id: such a pack is not replaced under it."""
        if self.imported_dir is None:
            raise PackError(
                "this Relay has no data directory to import packs into", "此 Relay 没有可导入运行包的数据目录"
            )
        archive = Path(archive).expanduser()
        if not archive.is_file():
            raise PackError(f"{archive} is not a file", f"{archive} 不是文件")
        digest = _sha256(archive)
        expected = (sha256 or "").strip().lower() or _expected_digest(archive)
        if expected is None:
            raise PackError(
                "no sha256 to verify the file against: give the digest or keep the .sha256 file beside it",
                "没有可用于校验的 sha256：请提供摘要，或将 .sha256 文件放在同一目录",
            )
        if not SHA_RE.match(expected):
            raise PackError("sha256 must be 64 hex digits", "sha256 须为 64 位十六进制数")
        if digest != expected:
            raise PackError("the file's sha256 does not match", "文件的 sha256 不一致")
        with self._lock:
            root = self.imported_dir
            root.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".import-", dir=root))
            try:
                pack_id = _extract(archive, staging)
                manifest = parse_manifest(_read_json(staging / pack_id / "manifest.json"))
                if manifest["id"] != pack_id:
                    raise PackError(
                        f"the archive directory {pack_id} does not match the manifest id",
                        f"压缩包目录 {pack_id} 与清单编号不一致",
                    )
                here = current_platform()
                if manifest["platform"] != here:
                    raise PackError(
                        f"the pack is built for {manifest['platform']}, this computer is {here}",
                        f"运行包适用于 {manifest['platform']}，本机为 {here}",
                    )
                if any(p.id == pack_id for p in _scan(self.bundled_dir, "bundled")):
                    raise PackError(
                        f"{pack_id} is already installed with Relay", f"{pack_id} 已随 Relay 安装"
                    )
                pack = _pack(manifest, staging / pack_id, digest, "imported")
                problem = pack.problem()
                if problem is not None:
                    raise PackError(*problem)
                target = root / pack_id
                if target.exists() and in_use is not None and in_use(pack_id):
                    raise PackError(IN_USE[0], IN_USE[1])
                _replace(staging / pack_id, target, staging / ".replaced")
                self._write_index(root, pack_id, digest)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        installed = self.get(pack_id)
        assert installed is not None
        return installed

    def delete(self, pack_id: str) -> None:
        """Remove an imported pack. Raises KeyError when there is none, PackError for a bundled one."""
        with self._lock:
            imported = {p.id: p for p in _scan(self.imported_dir, "imported")}
            if pack_id not in imported:
                if any(p.id == pack_id for p in _scan(self.bundled_dir, "bundled")):
                    raise PackError(
                        "packs installed with Relay cannot be deleted", "随 Relay 安装的运行包不可删除"
                    )
                raise KeyError(pack_id)
            assert self.imported_dir is not None
            path = imported[pack_id].path
            trash = Path(tempfile.mkdtemp(prefix=".delete-", dir=self.imported_dir))
            try:
                _move(path, trash / pack_id)
                self._write_index(self.imported_dir, pack_id, None)
            finally:
                shutil.rmtree(trash, ignore_errors=True)

    @staticmethod
    def _write_index(root: Path, pack_id: str, digest: str | None) -> None:
        index = _read_json(root / "index.json")
        entries = index.get("packs") if isinstance(index, dict) else None
        entries = [e for e in (entries or []) if isinstance(e, dict) and e.get("id") != pack_id]
        if digest is not None:
            manifest = _read_json(root / pack_id / "manifest.json") or {}
            entries.append({**manifest, "sha256": digest, "imported_at": time.time()})
        body = {"format": FORMAT, "platform": current_platform(), "packs": entries}
        tmp = root / "index.json.tmp"
        tmp.write_text(json.dumps(body, indent=2) + "\n", "utf-8")
        os.replace(tmp, root / "index.json")


IN_USE = ("the pack runs a firmware twin", "运行包正被固件孪生使用")


def _move(source: Path, target: Path) -> None:
    """Rename a pack directory; a pack whose files are open (Windows: a running firmware) stays whole."""
    try:
        source.rename(target)
    except OSError as exc:
        raise PackError(f"the pack directory cannot be moved ({exc})", "无法移动运行包目录") from None


def _replace(new: Path, target: Path, aside: Path) -> None:
    """Install ``new`` at ``target``; an existing pack is moved ``aside`` first and put back on failure."""
    if target.exists():
        _move(target, aside)
        try:
            _move(new, target)
        except PackError:
            aside.rename(target)
            raise
    else:
        _move(new, target)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _expected_digest(archive: Path) -> str | None:
    """The digest published beside ``archive``: ``<archive>.sha256`` or an index that names it."""
    side = archive.with_name(archive.name + ".sha256")
    if side.is_file():
        try:
            text = side.read_text("utf-8").split()
        except OSError:
            text = []
        if text:
            return text[0].strip().lower()
    match = re.match(r"^(.+)-(linux-x64|mac-arm64|mac-x64|win-x64)\.tar\.gz$", archive.name)
    if match is None:
        return None
    pack_id = match.group(1)
    for index in sorted(archive.parent.glob("index*.json")):
        data = _read_json(index)
        for entry in (data or {}).get("packs", []) if isinstance(data, dict) else []:
            if (
                isinstance(entry, dict)
                and entry.get("id") == pack_id
                and isinstance(entry.get("sha256"), str)
            ):
                return entry["sha256"].strip().lower()
    return None


def _extract(archive: Path, dest: Path) -> str:
    """Check every member of ``archive`` and unpack it into ``dest``; returns the pack id (its directory)."""
    try:
        tar = tarfile.open(archive, "r:gz")  # noqa: SIM115 - closed by the with block below
    except (tarfile.TarError, OSError) as exc:
        raise PackError(f"not a gzip tar archive ({exc})", "不是 gzip tar 压缩包") from None
    with tar:
        members = tar.getmembers()
        if not members or len(members) > MAX_MEMBERS:
            raise PackError("the archive is empty or has too many entries", "压缩包为空或条目过多")
        top = PurePosixPath(members[0].name).parts[0] if PurePosixPath(members[0].name).parts else ""
        if not ID_RE.match(top):
            raise PackError("the archive must hold one <id>/ directory", "压缩包须只含一个 <id>/ 目录")
        total = 0
        for m in members:
            name = PurePosixPath(m.name)
            if name.is_absolute() or ".." in name.parts or not name.parts or name.parts[0] != top:
                raise PackError(f"entry {m.name} is outside {top}/", f"条目 {m.name} 不在 {top}/ 内")
            if not (m.isfile() or m.isdir() or m.issym()):
                raise PackError(
                    f"entry {m.name} is not a file, directory or symbolic link",
                    f"条目 {m.name} 不是文件、目录或符号链接",
                )
            if m.issym():
                target = PurePosixPath(m.linkname)
                if target.is_absolute():
                    raise PackError(f"link {m.name} points outside the pack", f"链接 {m.name} 指向运行包之外")
                depth = len(name.parts) - 2  # directories below <id>/ that the link sits in
                for part in target.parts:
                    depth = depth - 1 if part == ".." else depth + (0 if part == "." else 1)
                    if depth < 0:
                        raise PackError(
                            f"link {m.name} points outside the pack", f"链接 {m.name} 指向运行包之外"
                        )
            total += max(0, m.size)
            if total > MAX_UNPACKED_BYTES:
                raise PackError("the archive unpacks to more than 512 MB", "压缩包解压后超过 512 MB")
        # The checks above are lexical; links made earlier in the archive can still chain out of the pack
        # (a -> ., b -> a/.., ...). So every path is resolved on disk before anything is written through
        # it, and every link once all of them exist.
        root = os.path.join(os.path.realpath(dest), top)

        def inside(path: Path, name: str) -> None:
            real = os.path.realpath(path)
            if real != root and not real.startswith(root + os.sep):
                raise PackError(f"entry {name} resolves outside the pack", f"条目 {name} 解析到运行包之外")

        try:
            for m in members:
                target = dest / m.name
                inside(target, m.name)
                if m.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif m.issym():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(m.linkname, target)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    inside(target, m.name)
                    source = tar.extractfile(m)
                    if source is None:
                        raise PackError(f"entry {m.name} cannot be read", f"无法读取条目 {m.name}")
                    with source, target.open("wb") as out:
                        shutil.copyfileobj(source, out)
                    mode = stat.S_IMODE(m.mode) & 0o755
                    os.chmod(target, mode | stat.S_IRUSR | stat.S_IWUSR)
            for m in members:
                if m.issym():
                    inside(dest / m.name, m.name)
        except OSError as exc:
            raise PackError(f"the archive cannot be unpacked ({exc})", "压缩包无法解压") from None
    return top


__all__ = [
    "FAMILIES",
    "Pack",
    "PackError",
    "PackStore",
    "current_platform",
    "parse_manifest",
    "version_tuple",
]
