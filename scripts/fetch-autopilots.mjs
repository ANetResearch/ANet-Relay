// Download this platform's autopilot runtime packs (11 §3.2; docs/autopilot-packs.md) from the GitHub Release
// autopilots-v1, verify each tarball against index-<platform>.json and unpack it.
//   node scripts/fetch-autopilots.mjs [--dir var/autopilots] [--platform linux-x64] [--repo owner/name]
//                                     [--tag autopilots-v1] [--optional] [--force] [--api https://api.github.com]
// --optional: a missing release, or a release without this platform's index, is a warning, not an error
// (release.yml before the first autopilots run). Packs whose sha256 matches the installed index.json are
// kept unless --force. GITHUB_TOKEN or GH_TOKEN, when set, authenticates the API calls (private repository,
// CI rate limits); it is sent to api.github.com only and never written anywhere.
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { fail, log, ROOT } from "./lib.mjs";

const PLATFORMS = ["linux-x64", "mac-arm64", "mac-x64", "win-x64"];

function option(name, fallback) {
  const i = process.argv.indexOf(name);
  return i > 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback;
}

function hostPlatform() {
  const os = { darwin: "mac", linux: "linux", win32: "win" }[process.platform];
  const arch = { x64: "x64", arm64: "arm64" }[process.arch];
  return os && arch ? `${os}-${arch}` : null;
}

function defaultRepo() {
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));
  const url = typeof pkg.repository === "string" ? pkg.repository : pkg.repository?.url || "";
  const m = /github\.com[/:]([^/]+\/[^/.]+)/.exec(url);
  return m ? m[1] : null;
}

const token = process.env.GITHUB_TOKEN || process.env.GH_TOKEN || "";

async function request(url, { binary = false, api = false } = {}) {
  const headers = { "User-Agent": "anet-relay-fetch-autopilots" };
  if (api) headers.Accept = binary ? "application/octet-stream" : "application/vnd.github+json";
  // The token only goes to the API host; asset downloads redirect elsewhere and fetch drops it there.
  if (token && new URL(url).host === "api.github.com") headers.Authorization = `Bearer ${token}`;
  for (let attempt = 1; ; attempt += 1) {
    let res;
    try {
      res = await fetch(url, { headers, redirect: "follow" });
    } catch (error) {
      if (attempt >= 3) throw error;
      await new Promise((resolve) => setTimeout(resolve, 1000 * attempt));
      continue;
    }
    if (res.ok) return binary ? Buffer.from(await res.arrayBuffer()) : res.json();
    if (res.status === 404) return null;
    if (attempt >= 3 || res.status < 500) throw new Error(`GET ${url}: HTTP ${res.status}`);
    await new Promise((resolve) => setTimeout(resolve, 1000 * attempt));
  }
}

async function download(asset) {
  // With a token the API asset URL also works for a private repository; without one the public URL does.
  return token
    ? request(asset.url, { binary: true, api: true })
    : request(asset.browser_download_url, { binary: true });
}

const sha256 = (buf) => crypto.createHash("sha256").update(buf).digest("hex");

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

function tar(args, cwd) {
  // Relative archive names keep GNU tar (Git for Windows) from reading "C:" as a remote host.
  const res = spawnSync("tar", args, { cwd, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  if (res.error) throw res.error;
  if (res.status !== 0) throw new Error(`tar ${args.join(" ")}: ${res.stderr.trim()}`);
  return res.stdout;
}

function checkMembers(listing, id) {
  for (const raw of listing.split(/\r?\n/)) {
    const name = raw.trim().replace(/\/$/, "");
    if (!name) continue;
    const parts = name.split("/");
    if (name.startsWith("/") || /^[A-Za-z]:/.test(name) || parts.includes("..") || parts[0] !== id) {
      throw new Error(`${id}: unexpected archive member ${name}`);
    }
  }
}

function dirSize(dir) {
  let total = 0;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) total += dirSize(full);
    else if (entry.isFile()) total += fs.statSync(full).size;
  }
  return total;
}

const mb = (n) => `${(n / 1048576).toFixed(1)} MB`;

async function main() {
  const dir = path.resolve(option("--dir", path.join(ROOT, "var", "autopilots")));
  const platform = option("--platform", hostPlatform());
  const repo = option("--repo", defaultRepo());
  const tag = option("--tag", "autopilots-v1");
  const api = option("--api", "https://api.github.com").replace(/\/$/, "");
  const optional = process.argv.includes("--optional");
  const force = process.argv.includes("--force");
  if (!PLATFORMS.includes(platform)) fail(`unsupported platform: ${platform} (one of ${PLATFORMS.join(", ")})`);
  if (!repo) fail("no repository: pass --repo owner/name");

  const skip = (message) => {
    if (!optional) fail(message);
    console.log(`::warning::${message}; continuing without autopilot packs`);
    log(`warning: ${message}; continuing without autopilot packs`);
  };

  const release = await request(`${api}/repos/${repo}/releases/tags/${tag}`, { api: true });
  if (!release) return skip(`release ${tag} not found in ${repo}`);
  const assets = new Map((release.assets || []).map((a) => [a.name, a]));
  const indexAsset = assets.get(`index-${platform}.json`);
  if (!indexAsset) return skip(`release ${tag} has no index-${platform}.json`);
  const indexData = await download(indexAsset);
  if (!indexData) fail(`index-${platform}.json: download failed`);
  const index = JSON.parse(indexData.toString("utf8"));
  if (index.format !== 1 || index.platform !== platform || !Array.isArray(index.packs)) {
    fail(`index-${platform}.json: unexpected format`);
  }

  fs.mkdirSync(dir, { recursive: true });
  const installed = readJson(path.join(dir, "index.json"));
  const installedSha = new Map((installed?.packs || []).map((p) => [p.id, p.sha256]));
  for (const pack of index.packs) {
    const id = String(pack.id || "");
    if (!/^(ardupilot-copter|px4)-\d+\.\d+\.\d+$/.test(id)) fail(`index-${platform}.json: bad pack id ${id}`);
    const target = path.join(dir, id);
    if (!force && installedSha.get(id) === pack.sha256 && fs.existsSync(path.join(target, "manifest.json"))) {
      log(`${id}: up to date`);
      continue;
    }
    const name = `${id}-${platform}.tar.gz`;
    const asset = assets.get(name);
    if (!asset) fail(`release ${tag} lists ${id} but has no ${name}`);
    const data = await download(asset);
    if (!data) fail(`${name}: download failed`);
    const digest = sha256(data);
    if (digest !== pack.sha256) fail(`${name}: sha256 ${digest} does not match the index (${pack.sha256})`);
    const part = `${name}.part`;
    fs.writeFileSync(path.join(dir, part), data);
    try {
      checkMembers(tar(["-tzf", part], dir), id);
      fs.rmSync(target, { recursive: true, force: true });
      tar(["-xzf", part], dir);
    } finally {
      fs.rmSync(path.join(dir, part), { force: true });
    }
    const manifest = readJson(path.join(target, "manifest.json"));
    if (manifest?.id !== id || manifest?.platform !== platform) fail(`${name}: manifest does not match ${id}`);
    log(`${id}: ${mb(data.length)} downloaded, ${mb(dirSize(target))} unpacked`);
  }
  fs.writeFileSync(path.join(dir, "index.json"), `${JSON.stringify(index, null, 2)}\n`);
  log(`${index.packs.length} packs for ${platform} in ${dir}, ${mb(dirSize(dir))} in total`);
}

main().catch((error) => fail(error.message));
