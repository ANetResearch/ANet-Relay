#!/usr/bin/env python3
"""Assemble ANet Relay autopilot runtime packs and their index (11 §3; docs/autopilot-packs.md).

Standard library only: it runs on the CI runners (Linux, macOS, Windows) before any Relay dependency exists.

  build-pack.py ardupilot --version 4.7.2 --platform linux-x64 --binary arducopter \\
      --git-version-file git-version.txt --copter-parm copter.parm --built official --out dist
  build-pack.py px4 --version 1.16.2 --platform linux-x64 --tree PX4-Autopilot/build/px4_sitl_default \\
      --git 54f0455f --built ci --out dist
  build-pack.py index --platform linux-x64 dist [--out dist/index-linux-x64.json]

A pack command writes <out>/<id>/ (manifest, binaries, defaults), <out>/<id>-<platform>.tar.gz and
<out>/<id>-<platform>.tar.gz.sha256. The index command reads the tarballs (or unpacked pack directories
with their .sha256 files) in a directory and writes index.json.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

FORMAT = 1
REPO = Path(__file__).resolve().parent.parent
AUTOPILOTS = REPO / "autopilots"
PLATFORMS = ("linux-x64", "mac-arm64", "mac-x64", "win-x64")
FAMILIES = {
    "ardupilot": {
        "license": "GPL-3.0-only",
        "source": "https://github.com/ArduPilot/ardupilot/tree/Copter-{version}",
    },
    "px4": {
        "license": "BSD-3-Clause",
        "source": "https://github.com/PX4/PX4-Autopilot/tree/v{version}",
    },
}
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
GIT_RE = re.compile(r"^[0-9a-f]{8,40}$")
# Executable formats worth stripping: ELF, Mach-O (thin and fat) and PE.
BINARY_MAGIC = (
    b"\x7fELF",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"MZ",
)
MANIFEST_KEYS = (
    "format",
    "id",
    "family",
    "vehicle",
    "version",
    "git",
    "platform",
    "entry",
    "romfs",
    "defaults",
    "size_bytes",
    "license",
    "source",
    "built",
)


class PackError(Exception):
    pass


def pack_id(family: str, version: str) -> str:
    return f"ardupilot-copter-{version}" if family == "ardupilot" else f"px4-{version}"


def tarball_name(pid: str, platform: str) -> str:
    return f"{pid}-{platform}.tar.gz"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_from_version_file(path: Path) -> str:
    """The commit hash from ArduPilot's git-version.txt ('commit <hash>' on the first line)."""
    for line in path.read_text("utf-8", errors="replace").splitlines():
        match = re.match(r"^commit\s+([0-9a-f]{8,40})\b", line.strip())
        if match:
            return match.group(1)
    raise PackError(f"no commit hash in {path}")


def normalize_git(value: str) -> str:
    value = value.strip().lower()
    if not GIT_RE.match(value):
        raise PackError(f"git must be 8 to 40 hex digits: {value!r}")
    return value


def is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            head = handle.read(4)
    except OSError:
        return False
    return any(head.startswith(magic) for magic in BINARY_MAGIC)


def strip_binary(path: Path, platform: str, tool: str | None) -> bool:
    """Strip symbols from an executable in place. Returns False when no strip tool is available."""
    tool = tool or shutil.which("strip")
    if not tool or not is_binary(path):
        return False
    # macOS: debug and local symbols only (-S -x), which keeps the executable valid for codesign.
    args = [tool, "-S", "-x", str(path)] if platform.startswith("mac") else [tool, str(path)]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"warning: {' '.join(args)} failed: {result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def tree_size(root: Path) -> int:
    """Total bytes of the regular files in a pack directory, manifest.json excluded."""
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if path.relative_to(root).as_posix() == "manifest.json":
            continue
        total += path.stat().st_size
    return total


def copy_file(src: Path, dst: Path, mode: int | None = None) -> None:
    if not src.is_file():
        raise PackError(f"missing file: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    if mode is not None:
        dst.chmod(mode)


def copy_tree(src: Path, dst: Path) -> None:
    """Copy a directory keeping symbolic links as links (PX4's px4-* client links)."""
    if not src.is_dir():
        raise PackError(f"missing directory: {src}")
    shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)


def write_manifest(root: Path, manifest: dict) -> dict:
    manifest["size_bytes"] = tree_size(root)
    ordered = {key: manifest[key] for key in MANIFEST_KEYS if key in manifest}
    (root / "manifest.json").write_text(json.dumps(ordered, indent=2) + "\n", "utf-8")
    return ordered


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    # Reproducible ownership; everything under bin/ is executable even when staged on a filesystem
    # without permission bits (Windows), other files keep the staged execute bit.
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    if info.isdir():
        info.mode = 0o755
    elif info.isfile():
        in_bin = info.name.split("/")[1:2] == ["bin"]
        info.mode = 0o755 if in_bin or info.mode & stat.S_IXUSR else 0o644
    return info


def write_tarball(out: Path, root: Path, platform: str) -> tuple[Path, str]:
    """<out>/<id>-<platform>.tar.gz holding the <id>/ directory, plus its .sha256 file."""
    pid = root.name
    tar_path = out / tarball_name(pid, platform)
    with tarfile.open(tar_path, "w:gz", compresslevel=9) as tar:
        tar.add(root, arcname=pid, filter=_tar_filter, recursive=False)
        for path in sorted(root.rglob("*")):
            tar.add(
                path,
                arcname=f"{pid}/{path.relative_to(root).as_posix()}",
                filter=_tar_filter,
                recursive=False,
            )
    digest = sha256_file(tar_path)
    Path(f"{tar_path}.sha256").write_text(f"{digest}  {tar_path.name}\n", "utf-8")
    return tar_path, digest


def base_manifest(family: str, version: str, git: str, platform: str, built: str) -> dict:
    if not VERSION_RE.match(version):
        raise PackError(f"version must be x.y.z: {version!r}")
    if platform not in PLATFORMS:
        raise PackError(f"platform must be one of {', '.join(PLATFORMS)}")
    if family == "px4" and platform == "win-x64":
        raise PackError("PX4 SITL has no native Windows build (11 §3.1)")
    info = FAMILIES[family]
    return {
        "format": FORMAT,
        "id": pack_id(family, version),
        "family": family,
        "vehicle": "copter",
        "version": version,
        "git": normalize_git(git),
        "platform": platform,
        "license": info["license"],
        "source": info["source"].format(version=version),
        "built": built,
    }


def fresh_dir(out: Path, pid: str) -> Path:
    root = out / pid
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    return root


def build_ardupilot(args: argparse.Namespace) -> dict:
    git = args.git or (git_from_version_file(Path(args.git_version_file)) if args.git_version_file else "")
    if not git:
        raise PackError("--git or --git-version-file is required")
    manifest = base_manifest("ardupilot", args.version, git, args.platform, args.built)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    root = fresh_dir(out, manifest["id"])
    exe = "arducopter.exe" if args.platform == "win-x64" else "arducopter"
    entry = root / "bin" / exe
    copy_file(Path(args.binary), entry, 0o755)
    # Cygwin builds run with their DLLs next to the executable.
    for dll in args.lib or []:
        copy_file(Path(dll), root / "bin" / Path(dll).name, 0o755)
    copy_file(Path(args.copter_parm), root / "defaults" / "copter.parm", 0o644)
    copy_file(
        AUTOPILOTS / "defaults" / "ardupilot" / "relay-quadx.parm",
        root / "defaults" / "relay-quadx.parm",
        0o644,
    )
    if not args.no_strip:
        strip_binary(entry, args.platform, args.strip)
    manifest["entry"] = f"bin/{exe}"
    manifest["defaults"] = ["defaults/copter.parm", "defaults/relay-quadx.parm"]
    return finish(out, root, manifest)


def build_px4(args: argparse.Namespace) -> dict:
    manifest = base_manifest("px4", args.version, args.git, args.platform, args.built)
    tree = Path(args.tree)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    root = fresh_dir(out, manifest["id"])
    binary = tree / "bin" / "px4"
    if not binary.is_file():
        raise PackError(f"missing PX4 executable: {binary}")
    # bin/: px4, its px4-<module> client links and px4-alias.sh, which the startup scripts call.
    (root / "bin").mkdir()
    for path in sorted((tree / "bin").iterdir()):
        target = root / "bin" / path.name
        if path.is_symlink():
            link = os.readlink(path)
            if "/" in link or "\\" in link:
                raise PackError(f"{path} links outside bin/: {link}")
            target.symlink_to(link)
        elif path.is_file():
            copy_file(path, target, 0o755 if os.access(path, os.X_OK) else 0o644)
    copy_tree(tree / "etc", root / "etc")
    overlay = AUTOPILOTS / "px4" / "px4-rc.mavlink"
    copy_file(overlay, root / "etc" / "init.d-posix" / "px4-rc.mavlink", 0o755)
    copy_file(
        AUTOPILOTS / "defaults" / "px4" / "relay-quadx.params",
        root / "defaults" / "relay-quadx.params",
        0o644,
    )
    if not args.no_strip:
        strip_binary(root / "bin" / "px4", args.platform, args.strip)
    manifest["entry"] = "bin/px4"
    manifest["romfs"] = "etc"
    manifest["defaults"] = ["defaults/relay-quadx.params"]
    return finish(out, root, manifest)


def finish(out: Path, root: Path, manifest: dict) -> dict:
    manifest = write_manifest(root, manifest)
    tar_path, digest = write_tarball(out, root, manifest["platform"])
    print(
        f"{manifest['id']} {manifest['platform']}: {manifest['size_bytes']} bytes unpacked, "
        f"{tar_path.stat().st_size} bytes packed, sha256 {digest}"
    )
    return {**manifest, "sha256": digest}


def check_manifest(manifest: object, where: str) -> dict:
    if not isinstance(manifest, dict):
        raise PackError(f"{where}: manifest is not an object")
    missing = [key for key in MANIFEST_KEYS if key not in manifest and key != "romfs"]
    if missing:
        raise PackError(f"{where}: manifest lacks {', '.join(missing)}")
    if manifest["format"] != FORMAT:
        raise PackError(f"{where}: unsupported manifest format {manifest['format']}")
    if manifest["id"] != pack_id(manifest["family"], manifest["version"]):
        raise PackError(f"{where}: id {manifest['id']} does not match family and version")
    return manifest


def manifest_from_tarball(path: Path) -> dict:
    with tarfile.open(path, "r:gz") as tar:
        for member in tar:
            parts = member.name.split("/")
            if len(parts) == 2 and parts[1] == "manifest.json" and member.isfile():
                handle = tar.extractfile(member)
                if handle is None:
                    break
                return check_manifest(json.load(io.TextIOWrapper(handle, "utf-8")), path.name)
    raise PackError(f"{path.name}: no <id>/manifest.json")


def read_sha256(path: Path) -> str | None:
    try:
        text = path.read_text("utf-8").split()
    except OSError:
        return None
    return text[0].lower() if text and re.match(r"^[0-9a-f]{64}$", text[0].lower()) else None


def build_index(directory: Path, platform: str) -> dict:
    """index.json of the packs in a directory: tarballs first, then unpacked packs with a .sha256 file."""
    packs: dict[str, dict] = {}
    for tar_path in sorted(directory.glob(f"*-{platform}.tar.gz")):
        manifest = manifest_from_tarball(tar_path)
        if manifest["platform"] != platform or tar_path.name != tarball_name(manifest["id"], platform):
            raise PackError(f"{tar_path.name}: manifest is for {manifest['id']} {manifest['platform']}")
        packs[manifest["id"]] = {**manifest, "sha256": sha256_file(tar_path)}
    for manifest_path in sorted(directory.glob("*/manifest.json")):
        manifest = check_manifest(json.loads(manifest_path.read_text("utf-8")), str(manifest_path))
        if manifest["platform"] != platform or manifest["id"] in packs:
            continue
        if manifest_path.parent.name != manifest["id"]:
            raise PackError(f"{manifest_path}: directory name differs from id {manifest['id']}")
        digest = read_sha256(directory / f"{tarball_name(manifest['id'], platform)}.sha256")
        if digest is None:
            raise PackError(
                f"{manifest['id']}: no tarball and no {tarball_name(manifest['id'], platform)}.sha256"
            )
        packs[manifest["id"]] = {**manifest, "sha256": digest}
    ordered = sorted(packs.values(), key=lambda m: (m["family"], [-int(x) for x in m["version"].split(".")]))
    return {"format": FORMAT, "platform": platform, "packs": ordered}


def cmd_index(args: argparse.Namespace) -> None:
    directory = Path(args.dir)
    index = build_index(directory, args.platform)
    out = Path(args.out) if args.out else directory / "index.json"
    out.write_text(json.dumps(index, indent=2) + "\n", "utf-8")
    print(f"{out}: {len(index['packs'])} packs for {args.platform}")


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = top.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--version", required=True, help="x.y.z")
        p.add_argument("--platform", required=True, choices=PLATFORMS)
        p.add_argument("--built", choices=("official", "ci"), default="ci")
        p.add_argument("--out", required=True, help="output directory")
        p.add_argument("--strip", help="strip tool (default: strip on PATH)")
        p.add_argument("--no-strip", action="store_true")

    ap = sub.add_parser("ardupilot", help="ArduCopter SITL pack from a built or official binary")
    common(ap)
    ap.add_argument("--binary", required=True, help="arducopter or arducopter.exe")
    ap.add_argument("--git", help="source commit hash")
    ap.add_argument("--git-version-file", help="ArduPilot git-version.txt (alternative to --git)")
    ap.add_argument(
        "--copter-parm", required=True, help="Tools/autotest/default_params/copter.parm of the tag"
    )
    ap.add_argument("--lib", action="append", help="runtime library shipped next to the binary (repeatable)")

    px = sub.add_parser("px4", help="PX4 SITL pack from a build/px4_sitl_default tree")
    common(px)
    px.add_argument("--tree", required=True, help="build/px4_sitl_default")
    px.add_argument("--git", required=True, help="source commit hash")

    ix = sub.add_parser("index", help="index.json from the packs in a directory")
    ix.add_argument("dir")
    ix.add_argument("--platform", required=True, choices=PLATFORMS)
    ix.add_argument("--out", help="output file (default: <dir>/index.json)")
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "ardupilot":
            build_ardupilot(args)
        elif args.command == "px4":
            build_px4(args)
        else:
            cmd_index(args)
    except (PackError, OSError, tarfile.TarError, ValueError) as exc:
        print(f"build-pack: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
