#!/usr/bin/env python3
"""Bind ArduPilot SITL's UDP sockets to loopback (11 §2; docs/autopilot-packs.md).

  loopback.py <ardupilot source tree>

Stock SITL binds two UDP sockets on every interface, and no command-line option moves them: the RC input
port ``5501 + 10n`` and the client socket of the JSON physics link (unbound, so the first ``sendto`` binds
it to 0.0.0.0). This script changes both to 127.0.0.1 in the SITL HAL and the JSON simulator backend; flight
code is untouched. Each change is an exact string replacement that must match exactly once in the files it
names (the RC input moved from ``AP_HAL_SITL/SITL_State.cpp`` to ``AP_RCProtocol_UDP.cpp`` in 4.6), so a
source layout this script does not know fails the build instead of producing an unpatched pack. Running it
on a tree it already patched is a no-op. Covers Copter-4.3 to 4.7 (autopilots/versions.json).

Standard library only.
"""

from __future__ import annotations

import sys
from pathlib import Path

JSON_ANCHOR = (
    "    control_port = port_out;\n"
    "\n"
    '    printf("JSON control interface set to %s:%u\\n", target_ip, control_port);'
)
JSON_PATCHED = (
    "    control_port = port_out;\n"
    "\n"
    "    // ANet Relay pack: with the physics on this computer, bind the client socket to loopback so\n"
    "    // that the simulator listens on no other interface (stock SITL leaves it to sendto, which\n"
    "    // binds 0.0.0.0).\n"
    '    if (strcmp(target_ip, "127.0.0.1") == 0 && !sock.bind("127.0.0.1", 0)) {\n'
    '        printf("JSON: bind to 127.0.0.1 failed - %s\\n", strerror(errno));\n'
    "    }\n"
    "\n"
    '    printf("JSON control interface set to %s:%u\\n", target_ip, control_port);'
)

# (what, [(file, old, new), ...]): exactly one of the alternatives matches, exactly once.
PATCHES: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "RC input 5501 + 10n",
        [
            (  # 4.6 and later
                "libraries/AP_RCProtocol/AP_RCProtocol_UDP.cpp",
                'rc_in.bind("0.0.0.0", sitl->rcin_port)',
                'rc_in.bind("127.0.0.1", sitl->rcin_port)',
            ),
            (  # 4.3 to 4.5
                "libraries/AP_HAL_SITL/SITL_State.cpp",
                '_sitl_rc_in.bind("0.0.0.0", _rcin_port)',
                '_sitl_rc_in.bind("127.0.0.1", _rcin_port)',
            ),
        ],
    ),
    (
        "JSON physics client socket",
        [("libraries/SITL/SIM_JSON.cpp", JSON_ANCHOR, JSON_PATCHED)],
    ),
]


class PatchError(Exception):
    pass


def apply(root: Path) -> list[str]:
    """Patch the tree in place; returns one line per change. Raises PatchError and writes nothing when any
    change does not match exactly once."""
    writes: dict[Path, str] = {}
    report = []
    for what, sites in PATCHES:
        found = []
        for rel, old, new in sites:
            path = root / rel
            if not path.is_file():
                continue
            text = writes.get(path)
            if text is None:
                text = path.read_text("utf-8")
            found.append((path, rel, old, new, text, text.count(old), text.count(new)))
        todo = [f for f in found if f[5]]
        done = [f for f in found if f[6] and not f[5]]
        if len(todo) == 1 and todo[0][5] == 1 and not done:
            path, rel, old, new, text, _, _ = todo[0]
            writes[path] = text.replace(old, new)
            report.append(f"{what}: {rel} patched")
        elif not todo and len(done) == 1 and done[0][6] == 1:
            report.append(f"{what}: {done[0][1]} already patched")
        else:
            counts = ", ".join(f"{f[1]}: {f[5]} unpatched, {f[6]} patched" for f in found) or "no file found"
            raise PatchError(f"{what}: expected exactly one bind site ({counts})")
    for path, text in writes.items():
        path.write_text(text, "utf-8")
    return report


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.split("\n\n")[1].strip(), file=sys.stderr)
        return 2
    root = Path(argv[0])
    if not (root / "libraries").is_dir():
        print(f"loopback patch: {root} is not an ArduPilot source tree", file=sys.stderr)
        return 1
    try:
        for line in apply(root):
            print(line)
    except (PatchError, OSError, UnicodeDecodeError) as exc:
        print(f"loopback patch: {exc}", file=sys.stderr)
        return 1
    print("SITL UDP sockets bind 127.0.0.1")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
