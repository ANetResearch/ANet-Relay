// Package the desktop app for one platform on the host architecture.
//   node scripts/dist.mjs mac|linux|win [--prebuilt] [--arch arm64|x64]
// Without --prebuilt the web interface and the frozen backend are built first. The
// backend is a native PyInstaller build, so each architecture is packaged on its own
// machine (or CI runner); --arch only overrides the label for unusual hosts.
import { bin, fail, run } from "./lib.mjs";

const platform = process.argv[2];
const FLAGS = { mac: "--mac", linux: "--linux", win: "--win" };
if (!FLAGS[platform]) fail("usage: node scripts/dist.mjs mac|linux|win [--prebuilt] [--arch arm64|x64]");

const archIndex = process.argv.indexOf("--arch");
const arch = archIndex > 0 ? process.argv[archIndex + 1] : process.arch;
if (!["arm64", "x64"].includes(arch)) fail(`unsupported architecture: ${arch}`);

if (!process.argv.includes("--prebuilt")) {
  run(process.execPath, ["scripts/build-web.mjs"]);
  run(process.execPath, ["scripts/build-backend.mjs"]);
}

run(bin("electron-builder"), ["--config", "electron-builder.yml", FLAGS[platform], `--${arch}`, "--publish", "never"]);
