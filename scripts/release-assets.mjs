// Release helpers for .github/workflows/release.yml (03 §13).
//   node scripts/release-assets.mjs collect <outDir>
//       Copy this machine's packages from release/ to <outDir> under the canonical names
//       ANet-Relay-<ver>-<os>-<arch>.<ext> (os: mac|linux|win, arch: arm64|x64).
//   node scripts/release-assets.mjs checksums <dir>
//       Write <dir>/SHA256SUMS.txt for every other file in <dir> (sha256sum format).
//   node scripts/release-assets.mjs notes <version> <outFile>
//       Extract the CHANGELOG.md section of <version> (without the heading) to <outFile>.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { fail, log, ROOT } from "./lib.mjs";

const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));
const EXTENSIONS = ["tar.gz", "AppImage", "zip", "dmg", "exe"];
const ARCH = { x64: "x64", x86_64: "x64", amd64: "x64", arm64: "arm64", aarch64: "arm64" };
const OS = { mac: "mac", darwin: "mac", linux: "linux", win: "win", windows: "win" };

function collect(outDir) {
  const src = path.join(ROOT, "release");
  if (!fs.existsSync(src)) fail("release/ does not exist: run a dist script first.");
  fs.mkdirSync(outDir, { recursive: true });
  const prefix = `ANet-Relay-${pkg.version}-`;
  const pattern = new RegExp(`^(${Object.keys(OS).join("|")})-(${Object.keys(ARCH).join("|")})\\.(${EXTENSIONS.join("|").replace(".", "\\.")})$`);
  let count = 0;
  for (const name of fs.readdirSync(src)) {
    if (!name.startsWith(prefix)) continue;
    const m = pattern.exec(name.slice(prefix.length));
    if (!m) continue;
    const canonical = `${prefix}${OS[m[1]]}-${ARCH[m[2]]}.${m[3]}`;
    fs.copyFileSync(path.join(src, name), path.join(outDir, canonical));
    log(`${name} -> ${canonical}`);
    count += 1;
  }
  if (count === 0) fail(`no packages named ${prefix}* in release/`);
}

function checksums(dir) {
  const lines = fs
    .readdirSync(dir)
    .filter((name) => name !== "SHA256SUMS.txt" && fs.statSync(path.join(dir, name)).isFile())
    .sort()
    .map((name) => {
      const hash = crypto.createHash("sha256").update(fs.readFileSync(path.join(dir, name))).digest("hex");
      return `${hash}  ${name}`;
    });
  if (lines.length === 0) fail(`no files in ${dir}`);
  fs.writeFileSync(path.join(dir, "SHA256SUMS.txt"), `${lines.join("\n")}\n`);
  log(`SHA256SUMS.txt: ${lines.length} files`);
}

function notes(version, outFile) {
  const v = version.replace(/^v/, "");
  const text = fs.readFileSync(path.join(ROOT, "CHANGELOG.md"), "utf8");
  const lines = text.split(/\r?\n/);
  const start = lines.findIndex((l) => new RegExp(`^## \\[?v?${v.replace(/\./g, "\\.")}\\]?(\\s|$)`).test(l));
  if (start < 0) fail(`CHANGELOG.md has no section for ${v}`);
  let end = lines.findIndex((l, i) => i > start && /^## /.test(l));
  if (end < 0) end = lines.length;
  const body = lines.slice(start + 1, end).join("\n").trim();
  if (!body) fail(`CHANGELOG.md section ${v} is empty`);
  fs.writeFileSync(outFile, `${body}\n`);
  log(`release notes for ${v}: ${body.split("\n").length} lines`);
}

const [command, a, b] = process.argv.slice(2);
if (command === "collect" && a) collect(path.resolve(a));
else if (command === "checksums" && a) checksums(path.resolve(a));
else if (command === "notes" && a && b) notes(a, path.resolve(b));
else fail("usage: release-assets.mjs collect <outDir> | checksums <dir> | notes <version> <outFile>");
