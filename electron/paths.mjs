// Filesystem locations for the main process.
import { app } from "electron";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const BACKEND_EXE = process.platform === "win32" ? "anet-relay-backend.exe" : "anet-relay-backend";

/** Repository root in development; the asar root when packaged. */
export const appRoot = () => app.getAppPath();

export const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export const userDataDir = () => app.getPath("userData");
/** Written by the backend itself (rotating, 03 §11). */
export const backendLogPath = () => path.join(userDataDir(), "backend.log");
/** Backend stdout/stderr captured by the shell: start-up failures and tracebacks land here. */
export const stdioLogPath = () => path.join(userDataDir(), "backend-stdio.log");
export const portFilePath = () => path.join(userDataDir(), "http-port");

export const iconPath = () => path.join(appRoot(), "brand", "icon.png");

/** Built renderer. RELAY_WEB_DIR overrides it in development (tests, stubs). */
export const webDistDir = () =>
  !app.isPackaged && process.env.RELAY_WEB_DIR
    ? path.resolve(process.env.RELAY_WEB_DIR)
    : path.join(appRoot(), "web", "dist");

/**
 * How to launch the backend.
 * - Packaged: the PyInstaller onedir executable under resources/backend.
 * - Development: the repository virtualenv running scripts/relay_entry.py, which is
 *   `python -m relay` plus a stdin lifeline; cwd is backend/.
 * - RELAY_BACKEND_CMD (development only): JSON array [command, ...args], used to
 *   exercise the shell against a stub backend.
 */
export function backendLaunch() {
  if (app.isPackaged) {
    const exe = path.join(process.resourcesPath, "backend", BACKEND_EXE);
    return { command: exe, args: [], cwd: path.dirname(exe) };
  }
  const override = process.env.RELAY_BACKEND_CMD;
  if (override) {
    const parsed = JSON.parse(override);
    if (!Array.isArray(parsed) || parsed.length === 0 || !parsed.every((s) => typeof s === "string")) {
      throw new Error("RELAY_BACKEND_CMD must be a JSON array of strings");
    }
    return { command: parsed[0], args: parsed.slice(1), cwd: repoRoot };
  }
  const python =
    process.platform === "win32"
      ? path.join(repoRoot, ".venv", "Scripts", "python.exe")
      : path.join(repoRoot, ".venv", "bin", "python");
  return {
    command: python,
    args: [path.join(repoRoot, "scripts", "relay_entry.py")],
    cwd: path.join(repoRoot, "backend"),
  };
}

/** Autopilot runtime packs (11 §3.2): resources/autopilots when packaged; undefined in development. */
export function autopilotsDir() {
  return app.isPackaged ? path.join(process.resourcesPath, "autopilots") : undefined;
}

export function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}
