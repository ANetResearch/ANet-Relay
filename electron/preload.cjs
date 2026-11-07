// Sandboxed preload. Exposes window.relay and nothing else (03 §12); the contract is
// typed in electron/relay.d.ts.
const { contextBridge, ipcRenderer } = require("electron");

// Same list as MENU_ACTIONS in menu.mjs (a sandboxed preload cannot import ESM).
const MENU_ACTIONS = new Set(["stop-all", "toggle-fleet", "toggle-inspector", "toggle-timeline", "possess", "settings"]);
const BACKEND_STATES = new Set(["starting", "ready", "failed", "ports_busy", "crashed"]);

const boot = ipcRenderer.sendSync("relay:bootstrap");

function subscribe(channel, accept, callback) {
  if (typeof callback !== "function") throw new TypeError("callback must be a function");
  const listener = (_event, payload) => {
    if (accept(payload)) callback(payload);
  };
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}

const COPILOT_EVENTS = new Set([
  "busy",
  "assistant_start",
  "text",
  "assistant_end",
  "tool_start",
  "tool_update",
  "tool_end",
  "approval",
  "approval_closed",
  "error",
]);
const validCopilot = (p) => p !== null && typeof p === "object" && COPILOT_EVENTS.has(p.type);
const validMenu = (p) => p !== null && typeof p === "object" && MENU_ACTIONS.has(p.action);
const validWindowState = (p) => p !== null && typeof p === "object" && typeof p.visible === "boolean";
const validAppWindow = (p) => p !== null && typeof p === "object" && typeof p.focused === "boolean";
const WINDOW_ACTIONS = new Set(["show", "hide", "toggle"]);
const APP_WINDOW_ACTIONS = new Set(["focus", "minimize", "maximize", "restore", "fullscreen"]);
const validBackend = (p) => p !== null && typeof p === "object" && BACKEND_STATES.has(p.state);

if (boot) {
  contextBridge.exposeInMainWorld(
    "relay",
    Object.freeze({
      apiBase: boot.apiBase,
      operatorToken: boot.operatorToken,
      platform: boot.platform,
      version: boot.version,
      /** Open an http(s) URL in the system browser. Resolves true when opened. */
      openExternal: (url) => ipcRenderer.invoke("relay:open-external", String(url)),
      /** Reveal the backend log in the system file manager. */
      revealLogs: () => ipcRenderer.invoke("relay:reveal-logs"),
      /** Open a telemetry radio's own setup page (LAN or loopback IPv4, optional port) in a Relay window. */
      openBridgePage: (ip) => ipcRenderer.invoke("relay:open-bridge-page", String(ip)),
      /** Tell the shell the interface language ("en" | "zh") so menus and dialogs follow it. */
      setLanguage: (lang) => ipcRenderer.invoke("relay:set-language", String(lang)),
      /** Current backend supervision state. */
      getBackendState: () => ipcRenderer.invoke("relay:backend-state"),
      /** Restart the backend (02 §15 Retry / Restart backend). */
      restartBackend: () => ipcRenderer.invoke("relay:restart-backend"),
      /** Quit the application (02 §15 Quit). */
      quit: () => ipcRenderer.invoke("relay:quit"),
      /** Subscribe to backend state changes. Returns an unsubscribe function. */
      onBackendState: (callback) => subscribe("relay:backend", validBackend, callback),
      /** Subscribe to menu actions ({action, ...}). Returns an unsubscribe function. */
      onMenu: (callback) => subscribe("relay:menu", validMenu, callback),
      /** Built-in agent (03 §16). The API key stays in the main process. */
      copilot: Object.freeze({
        getConfig: () => ipcRenderer.invoke("relay:copilot-config"),
        saveConfig: (cfg) => ipcRenderer.invoke("relay:copilot-save", cfg),
        test: () => ipcRenderer.invoke("relay:copilot-test"),
        prompt: (text, vehicle) => ipcRenderer.invoke("relay:copilot-prompt", { text: String(text), vehicle: vehicle ?? null }),
        abort: () => ipcRenderer.invoke("relay:copilot-abort"),
        reset: () => ipcRenderer.invoke("relay:copilot-reset"),
        approve: (id, ok) => ipcRenderer.invoke("relay:copilot-approve", { id: String(id), ok: Boolean(ok) }),
        getState: () => ipcRenderer.invoke("relay:copilot-state"),
        onEvent: (callback) => subscribe("relay:copilot", validCopilot, callback),
        /** Floating Copilot window (04 §6.1): show | hide | toggle; resolves with {visible}. */
        window: (action) => {
          if (!WINDOW_ACTIONS.has(action)) return Promise.reject(new TypeError("action must be show, hide or toggle"));
          return ipcRenderer.invoke("relay:copilot-window", action);
        },
        getWindowState: () => ipcRenderer.invoke("relay:copilot-window-state"),
        onWindowState: (callback) => subscribe("relay:copilot-window-state-changed", validWindowState, callback),
        /** Ask the main window to open Settings > Copilot. */
        openSettings: () => ipcRenderer.invoke("relay:copilot-open-settings"),
      }),
      /** Application control through the main process (04 §3.2). Only the main window's page is served. */
      app: Object.freeze({
        window: (action) => {
          if (!APP_WINDOW_ACTIONS.has(action)) return Promise.reject(new TypeError("unknown window action"));
          return ipcRenderer.invoke("relay:window-action", action);
        },
        getWindowState: () => ipcRenderer.invoke("relay:window-state"),
        onWindowState: (callback) => subscribe("relay:window-state-changed", validAppWindow, callback),
        screenshot: (maxWidth) => ipcRenderer.invoke("relay:screenshot", Number(maxWidth) || 1280),
      }),
    }),
  );
}
