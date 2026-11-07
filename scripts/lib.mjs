// Shared helpers for the build, dev and test scripts (Node only, no dependencies).
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const WIN = process.platform === "win32";
export const VENV = path.join(ROOT, ".venv");
export const VENV_PYTHON = WIN ? path.join(VENV, "Scripts", "python.exe") : path.join(VENV, "bin", "python");
export const NPM = WIN ? "npm.cmd" : "npm";
export const bin = (name) => path.join(ROOT, "node_modules", ".bin", WIN ? `${name}.cmd` : name);

export function log(message) {
  console.log(`[relay] ${message}`);
}

export function fail(message) {
  console.error(`[relay] ${message}`);
  process.exit(1);
}

/** Run a command, inherit stdio, throw on a non-zero exit. */
export function run(command, args, options = {}) {
  log(`$ ${[command, ...args].join(" ")}`);
  const res = spawnSync(command, args, { stdio: "inherit", cwd: ROOT, shell: WIN, ...options });
  if (res.error) throw res.error;
  if (res.status !== 0) {
    const err = new Error(`${path.basename(command)} exited with code ${res.status}`);
    err.status = res.status;
    throw err;
  }
}

/** Run a command and return its trimmed stdout, or null when it fails. */
export function capture(command, args, options = {}) {
  const res = spawnSync(command, args, { encoding: "utf8", cwd: ROOT, shell: WIN, ...options });
  return res.status === 0 ? res.stdout.trim() : null;
}

/** Start a long-running child; resolves its exit code. */
export function start(command, args, options = {}) {
  log(`$ ${[command, ...args].join(" ")}`);
  const child = spawn(command, args, { stdio: "inherit", cwd: ROOT, shell: WIN, ...options });
  child.done = new Promise((resolve) => child.once("exit", (code, signal) => resolve(code ?? (signal ? 1 : 0))));
  return child;
}

export const exists = (p) => fs.existsSync(path.join(ROOT, p));

/** Ensure the repository virtualenv exists (Python 3.12). */
export function ensureVenv() {
  if (fs.existsSync(VENV_PYTHON)) return VENV_PYTHON;
  const candidates = [process.env.RELAY_PYTHON, "python3.12", "python3", "python"].filter(Boolean);
  for (const py of candidates) {
    const version = capture(py, ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"]);
    if (version === "3.12") {
      run(py, ["-m", "venv", VENV]);
      return VENV_PYTHON;
    }
  }
  fail("Python 3.12 was not found. Install it or set RELAY_PYTHON.");
}
