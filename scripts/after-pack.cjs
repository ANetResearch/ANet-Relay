// electron-builder afterPack hook: macOS signing (03 §13).
//
// The app is packaged with `identity: null`, so electron-builder never signs on its own; this hook decides.
//
// Developer ID (MAC_SIGN_IDENTITY set: a certificate name or SHA-1 hash, optionally in MAC_SIGN_KEYCHAIN):
//   every Mach-O file of the PyInstaller backend is signed first with the Hardened Runtime, then
//   @electron/osx-sign signs the Electron frameworks, helpers and the app with brand/entitlements.mac.plist.
//   With APPLE_API_KEY_PATH, APPLE_API_KEY_ID and APPLE_API_ISSUER (an App Store Connect API key) the app is
//   then notarized and the ticket stapled, so a downloaded copy opens without any Gatekeeper step.
//   MAC_NOTARIZE=required turns missing notarization credentials into an error.
//
// Ad hoc (no identity): packaging changes Info.plist and adds app.asar and the backend, which invalidates the
//   linker signature Electron ships with; Apple Silicon then reports a downloaded copy as "damaged". An ad-hoc
//   signature keeps the bundle valid. First launch still needs the quarantine attribute removed:
//     xattr -dr com.apple.quarantine "/Applications/ANet Relay.app"
//
// zip and dmg are made after this hook, from the signed (and stapled) app.
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const MACHO_MAGIC = new Set([0xfeedface, 0xfeedfacf, 0xcefaedfe, 0xcffaedfe, 0xcafebabe, 0xbebafeca]);
const ENTITLEMENTS = path.join(__dirname, "..", "brand", "entitlements.mac.plist");

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

function run(cmd, args, options = {}) {
  return execFileSync(cmd, args, { stdio: "inherit", ...options });
}

function backendBinaries(appPath) {
  const dir = path.join(appPath, "Contents", "Resources", "backend");
  return fs.existsSync(dir) ? [...walk(dir)].filter(isMachO) : [];
}

function adHoc(appPath) {
  // codesign --deep does not reach loose Mach-O files under Contents/Resources, so the
  // PyInstaller backend's executable and libraries are signed first, one by one.
  const files = backendBinaries(appPath);
  for (const file of files) run("codesign", ["--force", "--sign", "-", "--timestamp=none", file]);
  console.log(`  • ad-hoc signed ${files.length} backend binaries`);
  run("codesign", ["--force", "--deep", "--sign", "-", "--timestamp=none", appPath]);
  run("codesign", ["--verify", "--deep", "--strict", "--verbose=2", appPath]);
  console.log(`  • ad-hoc signed ${path.basename(appPath)}`);
}

async function developerId(appPath, identity, keychain) {
  const keychainArgs = keychain ? ["--keychain", keychain] : [];
  const backend = path.join(appPath, "Contents", "Resources", "backend") + path.sep;
  const files = backendBinaries(appPath);
  for (const file of files) {
    run("codesign", ["--force", "--sign", identity, ...keychainArgs, "--options", "runtime", "--timestamp", "--entitlements", ENTITLEMENTS, file]);
  }
  console.log(`  • signed ${files.length} backend binaries with the Developer ID`);

  const { signAsync } = require("@electron/osx-sign");
  await signAsync({
    app: appPath,
    identity,
    ...(keychain ? { keychain } : {}),
    platform: "darwin",
    type: "distribution",
    preAutoEntitlements: false,
    optionsForFile: () => ({ hardenedRuntime: true, entitlements: ENTITLEMENTS }),
    // Only Mach-O code and bundles: the backend is already signed, and data files (app.asar, images, the
    // backend's archives) are sealed as resources by the app's signature instead of carrying their own.
    ignore: (file) => {
      if (file.startsWith(backend)) return true;
      if (/\.(app|framework)$/.test(file)) return false;
      return !isMachO(file);
    },
  });
  run("codesign", ["--verify", "--deep", "--strict", "--verbose=2", appPath]);
  console.log(`  • signed ${path.basename(appPath)} with the Developer ID (Hardened Runtime)`);
}

function notarize(appPath) {
  const key = process.env.APPLE_API_KEY_PATH;
  const keyId = process.env.APPLE_API_KEY_ID;
  const issuer = process.env.APPLE_API_ISSUER;
  if (!key || !keyId || !issuer) {
    if (process.env.MAC_NOTARIZE === "required") {
      throw new Error("MAC_NOTARIZE=required but APPLE_API_KEY_PATH, APPLE_API_KEY_ID or APPLE_API_ISSUER is missing.");
    }
    console.log("  • not notarized: no App Store Connect API key (APPLE_API_KEY_PATH, APPLE_API_KEY_ID, APPLE_API_ISSUER)");
    return;
  }
  const auth = ["--key", key, "--key-id", keyId, "--issuer", issuer];
  const zip = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "relay-notarize-")), "app.zip");
  run("ditto", ["-c", "-k", "--sequesterRsrc", "--keepParent", appPath, zip]);
  console.log("  • submitted for notarization, waiting for Apple");
  let out;
  try {
    out = execFileSync("xcrun", ["notarytool", "submit", zip, ...auth, "--wait", "--timeout", "45m", "--output-format", "json"], { encoding: "utf8" });
  } catch (err) {
    out = String(err.stdout || "");
    if (!out.trim()) throw err;
  } finally {
    fs.rmSync(path.dirname(zip), { recursive: true, force: true });
  }
  const result = JSON.parse(out);
  if (result.status !== "Accepted") {
    if (result.id) {
      try {
        run("xcrun", ["notarytool", "log", result.id, ...auth]);
      } catch {
        // the log is a diagnostic; the status below is the error
      }
    }
    throw new Error(`notarization ${result.status || "failed"} (${result.id || "no submission id"}): ${result.message || ""}`);
  }
  run("xcrun", ["stapler", "staple", appPath]);
  run("spctl", ["--assess", "--type", "execute", "--verbose=2", appPath]);
  console.log(`  • notarized and stapled (${result.id})`);
}

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== "darwin") return;
  if (process.platform !== "darwin") {
    throw new Error("macOS packages must be built on macOS: signing needs codesign.");
  }
  const appPath = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`);
  const identity = (process.env.MAC_SIGN_IDENTITY || "").trim();
  if (!identity) {
    adHoc(appPath);
    return;
  }
  await developerId(appPath, identity, (process.env.MAC_SIGN_KEYCHAIN || "").trim() || undefined);
  notarize(appPath);
};
