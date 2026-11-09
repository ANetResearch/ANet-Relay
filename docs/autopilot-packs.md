# Autopilot runtime packs

An autopilot runtime pack is the desktop (SITL) build of one flight-controller firmware, one version, for
one platform, together with the defaults Relay starts it with. Relay ships the packs inside the installer
and runs them as firmware twins (design contract `docs/design/11-v054.zh-CN.md`, §3). This document is the
pack format; the twin runtime (`backend/relay/twin/`) reads exactly what is described here.

## Bundled versions

`autopilots/versions.json` lists the bundled versions in priority order with their source tags.

| Firmware | Versions | linux-x64 | mac-arm64, mac-x64 | win-x64 |
|---|---|---|---|---|
| ArduCopter | 4.7.2, 4.6.3, 4.5.7, 4.4.4, 4.3.8 | official SITL binary (firmware.ardupilot.org) | built by CI with waf | built by CI with Cygwin |
| PX4 | 1.17.0, 1.16.2, 1.15.4, 1.14.4 | built by CI | built by CI | not provided (PX4 SITL has no native Windows build) |

A version that fails to build on a platform is left out of that platform's index; `index.json` is the
authoritative list of what a given installer contains.

## Layout

```
<root>/
  index.json
  ardupilot-copter-4.7.2/
    manifest.json
    bin/arducopter            (bin/arducopter.exe and the Cygwin DLLs on win-x64)
    defaults/copter.parm      (Tools/autotest/default_params/copter.parm of the tag)
    defaults/relay-quadx.parm (autopilots/defaults/ardupilot/relay-quadx.parm)
  px4-1.16.2/
    manifest.json
    bin/px4                   (plus the px4-<module> links to px4 and px4-alias.sh)
    etc/                      (ROMFS: init.d-posix, init.d, airframes; px4-rc.mavlink replaced, see below)
    defaults/relay-quadx.params
```

`<root>` is resolved by `relay.config.autopilots_dir()`:

1. `RELAY_AUTOPILOTS_DIR` when set (the Electron shell sets it to `<resources>/autopilots` in a packaged
   build);
2. `<resources>/autopilots` next to a frozen backend (`<resources>/backend/anet-relay-backend`);
3. `<repo>/var/autopilots` in development (`node scripts/fetch-autopilots.mjs`, not committed).

## manifest.json

```json
{
  "format": 1,
  "id": "ardupilot-copter-4.7.2",
  "family": "ardupilot",
  "vehicle": "copter",
  "version": "4.7.2",
  "git": "fb773567896014da007bca58358704d4a0d8516f",
  "platform": "linux-x64",
  "entry": "bin/arducopter",
  "defaults": ["defaults/copter.parm", "defaults/relay-quadx.parm"],
  "size_bytes": 5908019,
  "license": "GPL-3.0-only",
  "source": "https://github.com/ArduPilot/ardupilot/tree/Copter-4.7.2",
  "built": "official"
}
```

| Field | Value |
|---|---|
| `format` | `1` |
| `id` | `ardupilot-copter-<version>` or `px4-<version>`; equal to the directory name |
| `family` | `ardupilot` or `px4` |
| `vehicle` | `copter` |
| `version` | `x.y.z` without a `v` prefix |
| `git` | source commit, 8 to 40 lowercase hex digits; identity matching (§4) compares the first 8 with `AUTOPILOT_VERSION.flight_custom_version` |
| `platform` | `linux-x64`, `mac-arm64`, `mac-x64` or `win-x64` |
| `entry` | executable relative to the pack: `bin/arducopter`, `bin/arducopter.exe` or `bin/px4` |
| `romfs` | PX4 only: `etc`, the ROMFS directory passed to `px4 -d` |
| `defaults` | parameter files relative to the pack, in load order (see below) |
| `size_bytes` | total size of the regular files in the pack directory, `manifest.json` excluded |
| `license` | `GPL-3.0-only` (ArduPilot) or `BSD-3-Clause` (PX4) |
| `source` | URL of the source tag |
| `built` | `official` (an upstream binary) or `ci` (built by `.github/workflows/autopilots.yml`) |

Readers must ignore unknown fields and reject a manifest whose `format` is not 1.

## index.json

```json
{
  "format": 1,
  "platform": "linux-x64",
  "packs": [{ "...manifest fields...": "", "sha256": "<sha256 of <id>-<platform>.tar.gz>" }]
}
```

`packs` holds one entry per pack of that platform, ArduPilot first, newest version first within a
family. `sha256` is the digest of the pack's tarball; it is verified when a tarball is downloaded
(`fetch-autopilots.mjs`, the release build) or imported. An installed pack directory is not re-hashed.

## Tarball

`<id>-<platform>.tar.gz`, a gzip tar holding exactly the `<id>/` directory. Owner and group are 0,
directories are 0755, everything under `bin/` is 0755 and other files keep the execute bit they were
staged with. Symbolic links (PX4's `bin/px4-<module>` links) stay links and point inside `bin/`. An
importer must refuse members outside `<id>/`, absolute paths, `..` components and links that leave the
pack.

Release assets (GitHub Release `autopilots-v1`): every `<id>-<platform>.tar.gz` and one
`index-<platform>.json` per platform, which is installed as `index.json`.

## Defaults

### ArduPilot

`defaults` lists `copter.parm` (the upstream SITL defaults of that tag) and `relay-quadx.parm` (the Relay
baseline: `FRAME_TYPE 1`, rate gains and hover thrust for Relay's default 120 g frame, `SIM_RATE_HZ 1000`).
Both are ArduPilot `--defaults` files (`NAME VALUE` per line, `#` comments). The twin appends a profile file
and passes the comma-separated list in this order:

```
arducopter --model JSON:127.0.0.1 --speedup 100 -I <n> \
  --defaults <pack>/defaults/copter.parm,<pack>/defaults/relay-quadx.parm,<profile.parm> --home <lat,lon,alt,hdg>
```

ArduPilot SITL's default `SERIAL0` server listens on every interface (`0.0.0.0:5760 + 10n`). To keep the
twin on the loopback interface (11 §2), let Relay listen on 127.0.0.1 and have the firmware connect out, for
example `--serial0 tcpclient:127.0.0.1:<port>` or `--serial0 udpclient:127.0.0.1:<port>`.

### PX4

`defaults` lists `relay-quadx.params`, PX4 shell lines (`param set-default NAME VALUE`, `#` comments): hover
thrust, rate gains and rotor geometry for the default frame. The pack's `px4-rc.mavlink` sources the file
named by `RELAY_PX4_PARAMS` before it starts the GCS link; the twin writes one file that sources
`relay-quadx.params` and then sets the profile parameters with `param set`, and points
`RELAY_PX4_PARAMS` at it. This works on every bundled PX4 version (the `PX4_PARAM_<NAME>` environment
override of `rcS` only exists from 1.16).

## PX4 GCS link

`autopilots/px4/px4-rc.mavlink` replaces `etc/init.d-posix/px4-rc.mavlink` in every PX4 pack:

- GCS link `mavlink start -x -u <local> -o <remote> -r 4000000 -f` with local `18570 + n` and remote
  `24550 + n` (`n` = `px4 -i`), overridden by `RELAY_GCS_PORT_LOCAL` and `RELAY_GCS_PORT_REMOTE`; the
  stock script sends to 14550, where Relay listens for real aircraft.
- No API/Offboard link (14540 + n) and no gimbal link (13280 + n).
- The camera payload link and the SIH display link are kept as upstream has them.

Launch:

```
PX4_SIM_MODEL=none_iris RELAY_PX4_PARAMS=<file> \
  <pack>/bin/px4 -i <n> -d <pack>/etc -w <workdir> -s etc/init.d-posix/rcS
```

PX4 then connects to the HIL simulator on TCP `4560 + n` and blocks until Relay accepts. The pack path may
contain spaces (`/Applications/ANet Relay.app/...`), the working directory must not: `rcS` expands it
unquoted. Stock PX4 binds its MAVLink UDP sockets on every interface and has no bind-address option, so the
packs Relay builds carry one source change (`autopilots.yml`, step "Bind MAVLink to loopback"):
`Mavlink::init_udp` binds `INADDR_LOOPBACK`. Flight code is untouched. The baseline also sets
`UXRCE_DDS_CFG 0`, so no micro-ROS client sends to `127.0.0.1:8888`.

## Building

`scripts/build-pack.py` (Python standard library only) assembles a pack, strips the executable when a
`strip` tool is on `PATH`, and writes the tarball and its `.sha256`:

```
python3 scripts/build-pack.py ardupilot --version 4.7.2 --platform linux-x64 --built official \
  --binary arducopter --git-version-file git-version.txt --copter-parm copter.parm --out dist
python3 scripts/build-pack.py px4 --version 1.16.2 --platform linux-x64 --built ci \
  --tree PX4-Autopilot/build/px4_sitl_default --git <commit> --out dist
python3 scripts/build-pack.py index dist --platform linux-x64 --out dist/index-linux-x64.json
```

`--lib <file>` (repeatable) adds runtime libraries next to the ArduPilot executable (the Cygwin DLLs);
`--no-strip` keeps symbols. `index` reads every `*-<platform>.tar.gz` in the directory, and unpacked
`<id>/manifest.json` directories that have a `<id>-<platform>.tar.gz.sha256` beside them.

`.github/workflows/autopilots.yml` (manual) builds every version on every platform and publishes the
release `autopilots-v1`; one failing version does not stop the others. `release.yml` downloads the
platform's packs into `build/autopilots/` before electron-builder, which places them at
`resources/autopilots`; on macOS `scripts/after-pack.cjs` signs their Mach-O files with the app.

Development: `node scripts/fetch-autopilots.mjs [--dir var/autopilots] [--platform linux-x64]` downloads and
verifies this platform's packs.

## Licenses

ArduPilot is GPL-3.0, PX4 is BSD-3-Clause. Each pack is a separate program distributed with Relay;
`license` and `source` in its manifest name the license and the exact source tag. Relay's own license is
unchanged.
