// Freeze the Python backend with PyInstaller (backend/relay-backend.spec) into
// pyi-dist/anet-relay-backend, then smoke-test the frozen executable.
//   node scripts/build-backend.mjs [--no-smoke]
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { pickPort, waitForHttp } from "../electron/ports.mjs";
import { ensureVenv, exists, fail, log, ROOT, run, start, WIN } from "./lib.mjs";

const PYINSTALLER = "pyinstaller==6.22.3";
const OUT = path.join(ROOT, "pyi-dist", "anet-relay-backend");
const EXE = path.join(OUT, WIN ? "anet-relay-backend.exe" : "anet-relay-backend");

if (!exists("backend/relay/__main__.py")) fail("backend/relay is missing: the backend has not been written yet.");
if (!exists("backend/pyproject.toml")) fail("backend/pyproject.toml is missing.");

const python = ensureVenv();

// Runtime dependencies come from backend/pyproject.toml ([project].dependencies); the
// package itself is not installed, the spec imports it from backend/.
const deps = path.join(os.tmpdir(), `relay-backend-deps-${process.pid}.txt`);
run(python, [
  "-c",
  [
    "import sys, tomllib",
    "data = tomllib.load(open('backend/pyproject.toml', 'rb'))",
    "open(sys.argv[1], 'w').write('\\n'.join(data['project'].get('dependencies', [])) + '\\n')",
  ].join("; "),
  deps,
]);
try {
  run(python, ["-m", "pip", "install", "--disable-pip-version-check", "-r", deps, PYINSTALLER]);
} finally {
  fs.rmSync(deps, { force: true });
}

run(python, [
  "-m",
  "PyInstaller",
  "backend/relay-backend.spec",
  "--noconfirm",
  "--clean",
  "--distpath",
  "pyi-dist",
  "--workpath",
  "pyi-build",
]);
if (!fs.existsSync(EXE)) fail(`${path.relative(ROOT, EXE)} was not produced.`);
log(`frozen backend: ${path.relative(ROOT, OUT)}`);

if (!process.argv.includes("--no-smoke")) await smoke();

/** Start the frozen backend on a free port and wait for GET /api/v1/health. */
async function smoke() {
  const port = await pickPort();
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "relay-smoke-"));
  const child = start(EXE, [], {
    cwd: OUT,
    shell: false,
    stdio: ["pipe", "inherit", "inherit"],
    env: {
      ...process.env,
      RELAY_HTTP_PORT: String(port),
      RELAY_OPERATOR_TOKEN: crypto.randomBytes(32).toString("hex"),
      RELAY_DATA_DIR: dataDir,
      RELAY_LIFELINE: "stdin",
    },
  });
  const exited = new AbortController();
  child.done.then((code) => exited.abort(new Error(`frozen backend exited with code ${code}`)));
  try {
    const res = await waitForHttp(`http://127.0.0.1:${port}/api/v1/health`, {
      timeoutMs: 30000,
      signal: exited.signal,
      accept: (r) => r.status === 200,
    });
    log(`smoke test passed: ${res.body.trim()}`);
  } catch (error) {
    fail(`smoke test failed: ${error.message}`);
  } finally {
    child.kill("SIGTERM");
    await Promise.race([child.done, new Promise((r) => setTimeout(r, 3000))]);
    if (child.exitCode === null) child.kill("SIGKILL");
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
}
