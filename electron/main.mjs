// ANet Relay main process (03 §12): single instance, operator token, backend
// supervision, window, protocol, permissions, menus and the preload IPC surface.
import { app, BrowserWindow, dialog, ipcMain, nativeImage, session, shell } from "electron";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { Backend, describeExit } from "./backend.mjs";
import { buildMenu, MENU_ACTIONS } from "./menu.mjs";
import {
  backendLaunch,
  ensureDir,
  iconPath,
  portFilePath,
  stdioLogPath,
  userDataDir,
  webDistDir,
} from "./paths.mjs";
import { DEFAULT_PORT, PORT_RANGE_END, PortError } from "./ports.mjs";
import { APP_INDEX_URL, APP_ORIGIN, handleScheme, registerScheme } from "./protocol.mjs";
import { setLanguage, strings } from "./strings.mjs";

const DEV = !app.isPackaged;
const DEV_URL = DEV ? process.env.RELAY_WEB_URL || "" : "";
const STOP_ALL_TIMEOUT_MS = 1500;

// Development and tests may isolate their data directory; a packaged build never does.
if (DEV && process.env.RELAY_USER_DATA_DIR) {
  app.setPath("userData", path.resolve(process.env.RELAY_USER_DATA_DIR));
}

// The dev script injects the token so Vite and Electron share it; a packaged build
// always generates its own (32 random bytes, hex) and ignores the environment.
const OPERATOR_TOKEN =
  DEV && /^[0-9a-f]{64}$/.test(process.env.RELAY_OPERATOR_TOKEN || "")
    ? process.env.RELAY_OPERATOR_TOKEN
    : crypto.randomBytes(32).toString("hex");
// Children inherit process.env; the token reaches the backend only explicitly.
delete process.env.RELAY_OPERATOR_TOKEN;

let mainWindow = null;
let backend = null;
let quitting = false;
let starting = false;
let dialogAbort = null;
const bridgeWindows = new Map();

/** Backend supervision state mirrored to the renderer (02 §15). */
let backendState = { state: "starting", apiBase: null, reason: null, logPath: null, ports: null };

registerScheme();

// ---------------------------------------------------------------------------
// Origins and senders

function devOrigin() {
  try {
    return new URL(DEV_URL).origin;
  } catch {
    return null;
  }
}

/** True for URLs that belong to the app's own renderer. */
function isAppUrl(raw) {
  try {
    const url = new URL(raw);
    // Node's URL gives custom schemes an opaque origin ("null"); compare the parts.
    if (`${url.protocol}//${url.host}` === APP_ORIGIN) return true;
    return DEV_URL !== "" && url.origin === devOrigin();
  } catch {
    return false;
  }
}

function isTrustedSender(event) {
  const frame = event.senderFrame;
  return Boolean(frame) && frame === mainWindow?.webContents.mainFrame && isAppUrl(frame.url);
}

function isWebUrl(raw) {
  try {
    const { protocol } = new URL(raw);
    return protocol === "http:" || protocol === "https:";
  } catch {
    return false;
  }
}

function openExternal(url) {
  if (isWebUrl(url)) shell.openExternal(url).catch(() => {});
}

// ---------------------------------------------------------------------------
// Renderer notifications

function sendToRenderer(channel, payload) {
  const wc = mainWindow?.webContents;
  if (wc && !wc.isDestroyed()) wc.send(channel, payload);
}

function setBackendState(state, reason = null, ports = null) {
  backendState = {
    state,
    apiBase: backend?.origin ?? null,
    reason,
    ports,
    logPath: state === "ready" || state === "starting" ? null : stdioLogPath(),
  };
  sendToRenderer("relay:backend", backendState);
}

function sendMenuAction(action, detail = {}) {
  if (!MENU_ACTIONS.includes(action)) return;
  const win = mainWindow;
  if (!win) return;
  if (!win.isVisible()) win.show();
  sendToRenderer("relay:menu", { ...detail, action });
}

function revealLogs() {
  const file = stdioLogPath();
  if (fs.existsSync(file)) shell.showItemInFolder(file);
  else shell.openPath(userDataDir()).catch(() => {});
  return true;
}

// ---------------------------------------------------------------------------
// Telemetry radio setup page (03 §9 fallback "open the setup page in Relay")

const BRIDGE_ADDR = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?::(\d{1,5}))?$/;

/** LAN (RFC 1918, link-local) or loopback IPv4, optional port; returns "host[:port]" or null. */
function parseBridgeAddress(raw) {
  const m = BRIDGE_ADDR.exec(String(raw).trim());
  if (!m) return null;
  const o = m.slice(1, 5).map(Number);
  if (o.some((n) => n > 255)) return null;
  const port = m[5] === undefined ? null : Number(m[5]);
  if (port !== null && (port < 1 || port > 65535)) return null;
  const lan =
    o[0] === 10 ||
    o[0] === 127 ||
    (o[0] === 172 && o[1] >= 16 && o[1] <= 31) ||
    (o[0] === 192 && o[1] === 168) ||
    (o[0] === 169 && o[1] === 254);
  if (!lan) return null;
  return port === null ? o.join(".") : `${o.join(".")}:${port}`;
}

function openBridgePage(raw) {
  const host = parseBridgeAddress(raw);
  if (!host) return false;
  const existing = bridgeWindows.get(host);
  if (existing && !existing.isDestroyed()) {
    existing.focus();
    return true;
  }
  const origin = `http://${host}`;
  const win = new BrowserWindow({
    width: 960,
    height: 760,
    minWidth: 640,
    minHeight: 480,
    title: strings().bridgeTitle(host),
    backgroundColor: "#0A0B0D",
    autoHideMenuBar: true,
    webPreferences: {
      partition: "bridge",
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: false,
      spellcheck: false,
    },
  });
  bridgeWindows.set(host, win);
  win.on("closed", () => bridgeWindows.delete(host));
  win.on("page-title-updated", (event) => event.preventDefault());
  const sameOrigin = (url) => {
    try {
      return new URL(url).origin === origin;
    } catch {
      return false;
    }
  };
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  win.webContents.on("will-navigate", (event, url) => {
    if (!sameOrigin(url)) event.preventDefault();
  });
  win.webContents.on("will-redirect", (event, url) => {
    if (!sameOrigin(url)) event.preventDefault();
  });
  win.loadURL(`${origin}/`).catch(() => {});
  return true;
}

// ---------------------------------------------------------------------------
// IPC (preload surface, electron/relay.d.ts)

function registerIpc() {
  ipcMain.on("relay:bootstrap", (event) => {
    if (!isTrustedSender(event)) {
      event.returnValue = null;
      return;
    }
    event.returnValue = {
      apiBase: backend?.origin ?? null,
      operatorToken: OPERATOR_TOKEN,
      platform: process.platform,
      version: app.getVersion(),
    };
  });

  const handle = (channel, fn) =>
    ipcMain.handle(channel, (event, ...args) => (isTrustedSender(event) ? fn(...args) : false));

  handle("relay:open-external", (url) => {
    if (typeof url !== "string" || url.length > 2048 || !isWebUrl(url)) return false;
    openExternal(url);
    return true;
  });
  handle("relay:reveal-logs", () => revealLogs());
  handle("relay:open-bridge-page", (ip) => openBridgePage(ip));
  handle("relay:set-language", (lang) => {
    if (setLanguage(lang)) installMenu();
    return true;
  });
  handle("relay:backend-state", () => backendState);
  handle("relay:restart-backend", () => restartBackend());
  handle("relay:quit", () => {
    app.quit();
    return true;
  });
}

// ---------------------------------------------------------------------------
// Session hardening: only video capture (UVC capture cards, 02 §9) and sanitized
// clipboard writes (copy buttons) are granted, and only to the app's own pages.

function allowPermission(permission, details = {}) {
  if (permission === "media") {
    const types = details.mediaTypes ?? (details.mediaType ? [details.mediaType] : []);
    return types.length > 0 && types.every((t) => t === "video");
  }
  return permission === "clipboard-sanitized-write";
}

function hardenSessions() {
  const ses = session.defaultSession;
  ses.setPermissionRequestHandler((wc, permission, callback, details) => {
    callback(isAppUrl(details.requestingUrl ?? wc.getURL()) && allowPermission(permission, details));
  });
  ses.setPermissionCheckHandler((wc, permission, origin, details) => {
    if (!isAppUrl(origin) && !isAppUrl(details?.requestingUrl ?? "")) return false;
    if (permission === "media") return details?.mediaType === "video" || details?.mediaType === undefined;
    return permission === "clipboard-sanitized-write";
  });
  ses.setDevicePermissionHandler(() => false);
  for (const ev of ["select-hid-device", "select-serial-port", "select-usb-device", "select-bluetooth-device"]) {
    ses.on(ev, (event, ...args) => {
      event.preventDefault();
      const callback = args.find((a) => typeof a === "function");
      callback?.("");
    });
  }

  // Third-party telemetry radio pages get nothing.
  const bridge = session.fromPartition("bridge");
  bridge.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  bridge.setPermissionCheckHandler(() => false);
  bridge.setDevicePermissionHandler(() => false);
}

// ---------------------------------------------------------------------------
// Main window

function createWindow() {
  const mac = process.platform === "darwin";
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 720,
    show: false,
    title: app.name,
    backgroundColor: "#0A0B0D",
    icon: mac ? undefined : nativeImage.createFromPath(iconPath()),
    ...(mac ? { titleBarStyle: "hiddenInset" } : {}),
    webPreferences: {
      preload: path.join(import.meta.dirname, "preload.cjs"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webviewTag: false,
      spellcheck: false,
      // Sticks must not stall when the window is behind another one (02 §5.3 pauses on blur itself).
      backgroundThrottling: false,
    },
  });
  mainWindow = win;

  win.once("ready-to-show", () => win.show());
  win.on("closed", () => {
    if (mainWindow === win) mainWindow = null;
  });

  const wc = win.webContents;
  wc.setWindowOpenHandler(({ url }) => {
    openExternal(url);
    return { action: "deny" };
  });
  wc.on("will-navigate", (event, url) => {
    if (isAppUrl(url)) return;
    event.preventDefault();
    openExternal(url);
  });
  wc.on("will-redirect", (event, url) => {
    if (!isAppUrl(url)) event.preventDefault();
  });
  wc.on("render-process-gone", (_event, details) => {
    if (quitting || details.reason === "clean-exit") return;
    console.error(`[relay] renderer gone: ${details.reason}; reloading`);
    loadRenderer(win);
  });

  loadRenderer(win);
  return win;
}

function loadRenderer(win) {
  win.loadURL(DEV_URL || APP_INDEX_URL).catch((error) => {
    console.error(`[relay] renderer load failed: ${error?.message ?? error}`);
  });
}

function focusWindow() {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function installMenu() {
  buildMenu({
    send: (action) => sendMenuAction(action),
    stopAll: () => sendMenuAction("stop-all", { shellIssued: shellStopAll() }),
    openExternal,
    apiOrigin: () => (backendState.state === "ready" ? backend?.origin ?? null : null),
    revealLogs,
    dev: DEV,
  });
}

/**
 * Stop all from the menu or its accelerator goes to the backend directly, so it works
 * even when the renderer is busy. The renderer is told as well (menu action
 * "stop-all", shellIssued: true) and must not issue a second request for it.
 */
function shellStopAll() {
  if (backendState.state !== "ready" || !backend?.origin) return false;
  fetch(`${backend.origin}/api/v1/stop-all`, {
    method: "POST",
    headers: { "X-Relay-Operator": OPERATOR_TOKEN, "Content-Type": "application/json" },
    body: "{}",
    signal: AbortSignal.timeout(STOP_ALL_TIMEOUT_MS),
  })
    .then((res) => {
      if (!res.ok) console.error(`[relay] stop-all returned HTTP ${res.status}`);
    })
    .catch((error) => console.error(`[relay] stop-all failed: ${error?.message ?? error}`));
  return true;
}

// ---------------------------------------------------------------------------
// Backend supervision

async function startBackend() {
  if (starting || quitting) return false;
  starting = true;
  try {
    const portBefore = backend.port;
    try {
      await backend.reservePort();
    } catch (error) {
      if (error instanceof PortError && error.reason === "exhausted") {
        setBackendState("ports_busy", error.message, [DEFAULT_PORT, PORT_RANGE_END]);
        void backendFailed(strings().portsBusy, error.message);
      } else if (error instanceof PortError && error.reason === "busy") {
        // an explicit RELAY_HTTP_PORT that something else holds
        setBackendState("ports_busy", error.message, [error.port, error.port]);
        void backendFailed(strings().portBusy(error.port), error.message);
      } else {
        const reason = String(error?.message ?? error);
        setBackendState("failed", reason);
        void backendFailed(strings().backendStartFailed, reason);
      }
      return false;
    }
    setBackendState("starting");
    // The renderer reads apiBase once per load; reload it if the port changed.
    if (portBefore !== null && portBefore !== backend.port && mainWindow) loadRenderer(mainWindow);
    try {
      await backend.start();
    } catch (error) {
      if (quitting) return false;
      const reason = String(error?.message ?? error);
      setBackendState("failed", reason);
      void backendFailed(strings().backendStartFailed, reason);
      return false;
    }
    console.log(`[relay] backend ready on ${backend.origin}`);
    setBackendState("ready");
    return true;
  } finally {
    starting = false;
  }
}

async function restartBackend() {
  if (starting || quitting) return false;
  dialogAbort?.abort();
  await backend.stop();
  return startBackend();
}

/** Native dialog: Restart backend / View log / Quit. Closed early when the renderer restarts. */
async function backendFailed(message, reason, detailExtra = null) {
  if (quitting || dialogAbort) return;
  const s = strings();
  console.error(`[relay] ${message} ${reason}`);
  const abort = new AbortController();
  dialogAbort = abort;
  const options = {
    type: "error",
    title: s.dialogTitle,
    message,
    detail: s.detail(detailExtra ? `${detailExtra}\n\n${reason}` : reason, stdioLogPath()),
    buttons: [s.restartBackend, s.viewLog, s.quitButton],
    defaultId: 0,
    // Esc is the operator's stop key (02 §3); it must never quit the app from here.
    cancelId: 0,
    noLink: true,
    signal: abort.signal,
  };
  let choice;
  try {
    do {
      const target = mainWindow && !mainWindow.isDestroyed() ? mainWindow : undefined;
      ({ response: choice } = target ? await dialog.showMessageBox(target, options) : await dialog.showMessageBox(options));
      if (abort.signal.aborted) return;
      if (choice === 1) revealLogs();
    } while (choice === 1 && !quitting);
  } finally {
    if (dialogAbort === abort) dialogAbort = null;
  }
  if (quitting) return;
  if (choice === 0) {
    await backend.stop();
    await startBackend();
  } else {
    app.quit();
  }
}

function onBackendExit(info) {
  const s = strings();
  setBackendState("crashed", describeExit(info));
  void backendFailed(s.backendStopped, describeExit(info), s.backendStoppedDetail);
}

// ---------------------------------------------------------------------------
// Shutdown: SIGTERM, 1.5 s grace, SIGKILL (backend.mjs)

let shutdownDone = false;
function onBeforeQuit(event) {
  quitting = true;
  dialogAbort?.abort();
  if (shutdownDone || !backend) return;
  event.preventDefault();
  backend
    .stop()
    .catch(() => backend.killNow())
    .finally(() => {
      backend.removePortFile();
      shutdownDone = true;
      app.quit();
    });
}

function onFatal(error) {
  console.error("[relay] fatal:", error);
  try {
    backend?.killNow();
    backend?.removePortFile();
  } catch {
    // the process is about to exit
  }
  if (!quitting && app.isReady()) dialog.showErrorBox(app.name, `${strings().fatal}\n\n${error?.stack ?? error}`);
  app.exit(1);
}

// ---------------------------------------------------------------------------
// Startup

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", focusWindow);
  process.on("uncaughtException", onFatal);
  process.on("unhandledRejection", (reason) => console.error("[relay] unhandled rejection:", reason));
  process.on("exit", () => backend?.killNow());
  for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) process.on(sig, () => app.quit());

  app.setAboutPanelOptions({
    applicationName: app.name,
    applicationVersion: app.getVersion(),
    version: `Electron ${process.versions.electron}`,
    copyright: "Copyright © 2026 Agent Network",
    website: "https://github.com/ANetResearch/ANet-Relay",
    iconPath: iconPath(),
  });

  app.on("before-quit", onBeforeQuit);
  app.on("window-all-closed", () => app.quit());
  app.on("web-contents-created", (_e, contents) => {
    contents.on("will-attach-webview", (event) => event.preventDefault());
  });

  app.whenReady().then(async () => {
    ensureDir(userDataDir());
    if (process.platform === "darwin" && DEV) {
      const icon = nativeImage.createFromPath(iconPath());
      if (!icon.isEmpty()) app.dock?.setIcon(icon);
    }
    hardenSessions();
    registerIpc();
    installMenu();

    if (DEV_URL && !isWebUrl(DEV_URL)) {
      dialog.showErrorBox(app.name, "RELAY_WEB_URL must be an http(s) URL.");
      app.exit(1);
      return;
    }
    if (!DEV_URL) {
      if (!fs.existsSync(path.join(webDistDir(), "index.html"))) {
        dialog.showErrorBox(app.name, strings().webMissing);
        app.exit(1);
        return;
      }
      handleScheme(webDistDir());
    }

    backend = new Backend({
      launch: backendLaunch,
      token: OPERATOR_TOKEN,
      dataDir: userDataDir(),
      logPath: stdioLogPath(),
      portFile: portFilePath(),
      explicitPort: DEV ? process.env.RELAY_HTTP_PORT : undefined,
    });
    backend.on("exit", onBackendExit);

    // Fix the port before the window loads so the preload can hand apiBase over;
    // the renderer shows its start-up sequence while the backend comes up. A failure
    // here is reported by startBackend(), which reserves again.
    await backend.reservePort().catch(() => {});
    createWindow();
    await startBackend();

    app.on("activate", () => {
      if (!mainWindow) createWindow();
      else focusWindow();
    });
  });
}
