# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 6.22.3 spec for the ANet Relay backend (03 §13): onedir build at
# pyi-dist/anet-relay-backend, launched by the Electron shell from resources/backend.
#
#   .venv/bin/python -m PyInstaller backend/relay-backend.spec --noconfirm --clean \
#       --distpath pyi-dist --workpath pyi-build        (npm run build:backend)
#
# Entry: scripts/relay_entry.py, i.e. `python -m relay` plus the stdin lifeline. The same executable with
# `--program-host` runs the onboard-program host (WP-1, 06 §2.4; relay/programs/host.py).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

BACKEND = Path(SPECPATH).resolve()
ROOT = BACKEND.parent
# Make `relay` importable for collect_submodules() and the analysis.
sys.path.insert(0, str(BACKEND))

hiddenimports = [
    # Includes relay.programs.host and relay.judge.worker, which the frozen executable runs when it
    # re-executes itself with --program-host (WP-1) or --judge-worker (WP-2b).
    *collect_submodules("relay"),
    # uvicorn picks its loop, protocol and lifespan implementations at run time.
    *collect_submodules("uvicorn"),
    # pymavlink imports dialects by name (mavutil.set_dialect).
    "pymavlink.dialects.v20.ardupilotmega",
    "pymavlink.dialects.v20.common",
    "pymavlink.dialects.v10.ardupilotmega",
    "pymavlink.dialects.v10.common",
    "pymavlink.dialects.v20.all",  # mavutil's default when MAVLINK_DIALECT is unset
    # pyserial (11 §7.1): relay.mav.serial_link imports it lazily, and pyserial picks its port backend and
    # its port lister by platform at run time; serial_for_url() imports URL handlers by name.
    "serial",
    "serial.serialutil",
    "serial.tools",
    "serial.tools.list_ports",
    "serial.tools.list_ports_common",
    *(
        ["serial.serialwin32", "serial.win32", "serial.tools.list_ports_windows"]
        if sys.platform == "win32"
        else ["serial.serialposix", "serial.tools.list_ports_posix"]
        + (["serial.tools.list_ports_osx"] if sys.platform == "darwin" else ["serial.tools.list_ports_linux"])
    ),
    "serial.urlhandler.protocol_loop",
]

a = Analysis(
    [str(ROOT / "scripts" / "relay_entry.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    # WP-1: the built-in example programs are read as source text (collect_data_files skips .py files)
    datas=[
        *collect_data_files("relay"),
        (str(BACKEND / "relay" / "programs" / "examples" / "*.py"), "relay/programs/examples"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "_tkinter", "turtle", "idlelib", "pytest", "ruff"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="anet-relay-backend",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="anet-relay-backend",
)
