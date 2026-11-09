// Autopilot runtime packs (11 §3.2): the backend learns the packaged pack directory from the shell, and
// scripts/fetch-autopilots.mjs installs a release's packs only when their sha256 matches the index.
//   node --test electron/test/autopilots.test.mjs
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, test } from "node:test";

import { backendEnv } from "../backend.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const FETCH = path.join(ROOT, "scripts", "fetch-autopilots.mjs");
const dirs = [];

afterEach(() => {
  for (const d of dirs.splice(0)) fs.rmSync(d, { recursive: true, force: true });
});

function tempDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "relay-autopilots-"));
  dirs.push(dir);
  return dir;
}

test("backend environment carries RELAY_AUTOPILOTS_DIR only when the shell knows it", () => {
  const base = { PATH: "/usr/bin", RELAY_AUTOPILOTS_DIR: "/inherited" };
  const packaged = backendEnv(base, { port: 8765, token: "t", dataDir: "/data", autopilotsDir: "/res/autopilots" });
  assert.equal(packaged.RELAY_AUTOPILOTS_DIR, "/res/autopilots");
  assert.equal(packaged.RELAY_HTTP_PORT, "8765");
  assert.equal(packaged.RELAY_DATA_DIR, "/data");
  assert.equal(packaged.RELAY_LIFELINE, "stdin");
  assert.equal(packaged.PATH, "/usr/bin");
  const dev = backendEnv(base, { port: 8765, token: "t", dataDir: "/data", autopilotsDir: undefined });
  assert.equal(dev.RELAY_AUTOPILOTS_DIR, "/inherited");
  assert.equal("RELAY_AUTOPILOTS_DIR" in backendEnv({}, { port: 1, token: "t", dataDir: "/d" }), false);
});

/** A pack tarball <id>-linux-x64.tar.gz made with the system tar, as build-pack.py lays it out. */
function makeTarball(dir, id) {
  const stage = path.join(dir, "stage");
  fs.mkdirSync(path.join(stage, id, "bin"), { recursive: true });
  fs.writeFileSync(path.join(stage, id, "bin", "arducopter"), "#!/bin/sh\n", { mode: 0o755 });
  const manifest = { format: 1, id, family: "ardupilot", vehicle: "copter", version: id.split("-").pop(), platform: "linux-x64" };
  fs.writeFileSync(path.join(stage, id, "manifest.json"), JSON.stringify(manifest));
  const name = `${id}-linux-x64.tar.gz`;
  const res = spawnSync("tar", ["-czf", path.join("..", name), id], { cwd: stage });
  assert.equal(res.status, 0, String(res.stderr));
  const data = fs.readFileSync(path.join(dir, name));
  return { name, data, manifest: { ...manifest, sha256: crypto.createHash("sha256").update(data).digest("hex") } };
}

/** Serves /repos/<repo>/releases/tags/<tag> and its assets the way the GitHub API does. */
async function fakeGitHub(files, { release = true } = {}) {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    if (url.pathname === "/repos/acme/relay/releases/tags/autopilots-v1" && release) {
      const base = `http://127.0.0.1:${server.address().port}`;
      const assets = Object.keys(files).map((name) => ({ name, url: `${base}/assets/${name}`, browser_download_url: `${base}/dl/${name}` }));
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ tag_name: "autopilots-v1", assets }));
      return;
    }
    const m = /^\/dl\/(.+)$/.exec(url.pathname);
    if (m && files[m[1]]) {
      res.writeHead(200);
      res.end(files[m[1]]);
      return;
    }
    res.writeHead(404);
    res.end("{}");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, api: `http://127.0.0.1:${server.address().port}` };
}

function runFetch(args) {
  return new Promise((resolve) => {
    const env = { ...process.env };
    delete env.GITHUB_TOKEN;
    delete env.GH_TOKEN;
    const child = spawn(process.execPath, [FETCH, "--repo", "acme/relay", "--platform", "linux-x64", ...args], { env });
    let out = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (out += d));
    child.on("exit", (code) => resolve({ code, out }));
  });
}

test("fetch-autopilots verifies, unpacks and indexes the release packs", async () => {
  const work = tempDir();
  const pack = makeTarball(work, "ardupilot-copter-4.7.2");
  const index = { format: 1, platform: "linux-x64", packs: [pack.manifest] };
  const { server, api } = await fakeGitHub({ [pack.name]: pack.data, "index-linux-x64.json": JSON.stringify(index) });
  try {
    const dest = path.join(work, "autopilots");
    const first = await runFetch(["--api", api, "--dir", dest]);
    assert.equal(first.code, 0, first.out);
    assert.ok(fs.existsSync(path.join(dest, "ardupilot-copter-4.7.2", "bin", "arducopter")));
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dest, "index.json"), "utf8")), index);
    assert.deepEqual(fs.readdirSync(dest).sort(), ["ardupilot-copter-4.7.2", "index.json"]);
    const again = await runFetch(["--api", api, "--dir", dest]);
    assert.equal(again.code, 0, again.out);
    assert.match(again.out, /up to date/);
  } finally {
    server.close();
  }
});

test("fetch-autopilots refuses a tarball whose sha256 differs from the index", async () => {
  const work = tempDir();
  const pack = makeTarball(work, "ardupilot-copter-4.7.2");
  const index = { format: 1, platform: "linux-x64", packs: [{ ...pack.manifest, sha256: "0".repeat(64) }] };
  const { server, api } = await fakeGitHub({ [pack.name]: pack.data, "index-linux-x64.json": JSON.stringify(index) });
  try {
    const dest = path.join(work, "autopilots");
    const res = await runFetch(["--api", api, "--dir", dest]);
    assert.notEqual(res.code, 0);
    assert.match(res.out, /sha256/);
    assert.equal(fs.existsSync(path.join(dest, "ardupilot-copter-4.7.2")), false);
  } finally {
    server.close();
  }
});

test("fetch-autopilots --optional tolerates a missing release", async () => {
  const work = tempDir();
  const { server, api } = await fakeGitHub({}, { release: false });
  try {
    const dest = path.join(work, "autopilots");
    const optional = await runFetch(["--api", api, "--dir", dest, "--optional"]);
    assert.equal(optional.code, 0, optional.out);
    assert.match(optional.out, /not found/);
    const required = await runFetch(["--api", api, "--dir", dest]);
    assert.notEqual(required.code, 0);
  } finally {
    server.close();
  }
});
