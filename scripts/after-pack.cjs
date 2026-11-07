// electron-builder afterPack hook: ad-hoc signature for macOS builds (03 §13).
//
// The app is packaged with `identity: null`, so electron-builder does not sign it.
// Packaging changes Info.plist and adds app.asar and the backend, which invalidates
// the linker signature Electron ships with; Apple Silicon then reports a downloaded
// copy as "damaged". An ad-hoc signature (no certificate) keeps the bundle valid.
// First launch of a downloaded copy still needs Gatekeeper's quarantine removed:
//   xattr -dr com.apple.quarantine "/Applications/ANet Relay.app"
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const MACHO_MAGIC = new Set([0xfeedface, 0xfeedfacf, 0xcefaedfe, 0xcffaedfe, 0xcafebabe, 0xbebafeca]);

function isMachO(file) {
  let fd;
  try {
    fd = fs.openSync(file, "r");
    const buf = Buffer.alloc(4);
    if (fs.readSync(fd, buf, 0, 4, 0) !== 4) return false;
    return MACHO_MAGIC.has(buf.readUInt32BE(0));
  } catch {
    return false;
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isSymbolicLink()) continue;
    if (entry.isDirectory()) yield* walk(full);
    else if (entry.isFile()) yield full;
  }
}

function codesign(args) {
  execFileSync("codesign", args, { stdio: "inherit" });
}

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== "darwin") return;
  if (process.platform !== "darwin") {
    throw new Error("macOS packages must be built on macOS: the ad-hoc signature needs codesign.");
  }
  const appPath = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`);

  // codesign --deep does not reach loose Mach-O files under Contents/Resources, so the
  // PyInstaller backend's executable and libraries are signed first, one by one.
  const backendDir = path.join(appPath, "Contents", "Resources", "backend");
  if (fs.existsSync(backendDir)) {
    let count = 0;
    for (const file of walk(backendDir)) {
      if (!isMachO(file)) continue;
      codesign(["--force", "--sign", "-", "--timestamp=none", file]);
      count += 1;
    }
    console.log(`  • ad-hoc signed ${count} backend binaries`);
  }

  codesign(["--force", "--deep", "--sign", "-", "--timestamp=none", appPath]);
  codesign(["--verify", "--deep", "--strict", "--verbose=2", appPath]);
  console.log(`  • ad-hoc signed ${path.basename(appPath)}`);
};
