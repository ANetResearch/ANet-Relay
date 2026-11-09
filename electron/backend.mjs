// Backend process lifecycle: spawn, stdio capture, readiness, shutdown.
import { spawn } from "node:child_process";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";

import { HOST, pickPort, waitForHttp } from "./ports.mjs";

const HEALTH_TIMEOUT_MS = 20000;
const KILL_GRACE_MS = 1500;
const LOG_ROTATE_BYTES = 5 * 1024 * 1024;

/**
 * Supervises one backend process at a time.
 *
 * Events:
 *   "exit" ({code, signal, uptimeMs}) - a ready process ended without stop() being called.
 */
export class Backend extends EventEmitter {
  /**
   * @param {object} opts
   * @param {() => {command: string, args: string[], cwd: string}} opts.launch
   * @param {string} opts.token     operator token, passed via environment only
   * @param {string} opts.dataDir   RELAY_DATA_DIR (Electron userData)
   * @param {string} opts.logPath   stdout/stderr destination
   * @param {string} opts.portFile  where the chosen port is written
   * @param {string|undefined} opts.explicitPort  RELAY_HTTP_PORT from the environment
   * @param {string|undefined} opts.autopilotsDir RELAY_AUTOPILOTS_DIR (packaged runtime packs, 11 §3.2)
   */
  constructor({ launch, token, dataDir, logPath, portFile, explicitPort, autopilotsDir }) {
    super();
    this.launch = launch;
    this.token = token;
    this.dataDir = dataDir;
    this.logPath = logPath;
    this.portFile = portFile;
    this.explicitPort = explicitPort;
    this.autopilotsDir = autopilotsDir;
    this.port = null;
    this.child = null;
    this.stopping = false;
    this.ready = false;
  }

  get origin() {
    return this.port ? `http://${HOST}:${this.port}` : null;
  }

  get running() {
    return this.child !== null;
  }

  /** Choose a port (keeping the previous one when possible) and record it. */
  async reservePort() {
    this.port = await pickPort({ explicit: this.explicitPort, prefer: this.port ?? undefined });
    try {
      fs.writeFileSync(this.portFile, `${this.port}\n`);
    } catch {
      // informational only (agents may read it); never fatal
    }
    return this.port;
  }

  /** Spawn the process and resolve once GET /api/v1/health reports ok. */
  async start() {
    if (this.child) throw new Error("backend already running");
    if (!this.port) await this.reservePort();
    const { command, args, cwd } = this.launch();
    if (path.isAbsolute(command) && !fs.existsSync(command)) {
      const err = new Error(`Backend executable not found: ${command}`);
      err.code = "ENOENT";
      throw err;
    }

    const log = openLog(this.logPath);
    log.write(`\n=== ${new Date().toISOString()} start ${command} ${args.join(" ")} port=${this.port} ===\n`);

    this.stopping = false;
    this.ready = false;
    const startedAt = Date.now();
    const child = spawn(command, args, {
      cwd,
      env: backendEnv(process.env, {
        port: this.port,
        token: this.token,
        dataDir: this.dataDir,
        autopilotsDir: this.autopilotsDir,
      }),
      // stdin stays open as a lifeline: it reaches EOF when this process dies.
      stdio: ["pipe", "pipe", "pipe"],
      // Own process group, so a terminal Ctrl-C reaches main first and main
      // decides how the backend stops.
      detached: process.platform !== "win32",
      windowsHide: true,
    });
    this.child = child;
    child.stdout.pipe(log, { end: false });
    child.stderr.pipe(log, { end: false });

    let logOpen = true;
    const closeLog = (line) => {
      if (logOpen) {
        logOpen = false;
        log.end(line);
      }
    };
    log.on("error", () => {}); // a full disk must not take main down

    const exited = new AbortController();
    child.once("error", (error) => {
      // Spawn failure: no "exit" follows.
      closeLog(`=== spawn error: ${error.message} ===\n`);
      if (this.child === child) this.child = null;
      exited.abort(error);
    });
    child.once("exit", (code, signal) => {
      if (this.child === child) this.child = null;
      const info = { code, signal, uptimeMs: Date.now() - startedAt };
      exited.abort(Object.assign(new Error(describeExit(info)), { code: "EXITED", ...info }));
      // Failures before readiness surface as a start() rejection instead.
      if (!this.stopping && this.ready) this.emit("exit", info);
    });
    // "close" follows once stdout and stderr are drained.
    child.once("close", (code, signal) => {
      closeLog(`=== exit code=${code} signal=${signal} ===\n`);
    });

    try {
      await waitForHttp(`${this.origin}/api/v1/health`, {
        timeoutMs: HEALTH_TIMEOUT_MS,
        signal: exited.signal,
        accept: (res) => res.status === 200 && healthOk(res.body),
      });
      this.ready = true;
    } catch (error) {
      // Do not report the exit of a process that never became ready twice.
      this.stopping = true;
      await this.stop();
      throw error;
    }
  }

  /** SIGTERM, then SIGKILL after the grace period. Resolves when the process is gone. */
  stop() {
    const child = this.child;
    this.stopping = true;
    if (!child || child.pid === undefined || child.exitCode !== null || child.signalCode !== null) {
      this.child = null;
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      const timer = setTimeout(() => signalTree(child, "SIGKILL"), KILL_GRACE_MS);
      child.once("exit", () => {
        clearTimeout(timer);
        resolve();
      });
      signalTree(child, "SIGTERM");
    });
  }

  /** Synchronous last resort for crash and process-exit paths. */
  killNow() {
    const child = this.child;
    this.stopping = true;
    if (child?.pid !== undefined && child.exitCode === null && child.signalCode === null) {
      signalTree(child, "SIGKILL");
    }
  }

  removePortFile() {
    try {
      fs.rmSync(this.portFile, { force: true });
    } catch {
      // best effort
    }
  }
}

/**
 * Environment of the backend process. RELAY_AUTOPILOTS_DIR is set only when the shell knows where the runtime
 * packs are (a packaged build); otherwise an inherited value or the backend's own fallback applies.
 */
export function backendEnv(base, { port, token, dataDir, autopilotsDir }) {
  return {
    ...base,
    RELAY_HTTP_PORT: String(port),
    RELAY_OPERATOR_TOKEN: token,
    RELAY_DATA_DIR: dataDir,
    ...(autopilotsDir ? { RELAY_AUTOPILOTS_DIR: autopilotsDir } : {}),
    PYTHONUNBUFFERED: "1",
    RELAY_LIFELINE: "stdin",
  };
}

function healthOk(body) {
  try {
    return JSON.parse(body)?.ok === true;
  } catch {
    return false;
  }
}

function signalTree(child, signal) {
  try {
    // Negative pid addresses the whole process group (POSIX, detached spawn).
    if (process.platform !== "win32") process.kill(-child.pid, signal);
    else child.kill(signal);
  } catch {
    try {
      child.kill(signal);
    } catch {
      // already gone
    }
  }
}

function openLog(logPath) {
  try {
    if (fs.statSync(logPath).size > LOG_ROTATE_BYTES) fs.renameSync(logPath, `${logPath}.1`);
  } catch {
    // no previous log
  }
  return fs.createWriteStream(logPath, { flags: "a" });
}

export function describeExit({ code, signal }) {
  return signal ? `terminated by ${signal}` : `exited with code ${code}`;
}
