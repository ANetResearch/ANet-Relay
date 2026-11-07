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

const validMenu = (p) => p !== null && typeof p === "object" && MENU_ACTIONS.has(p.action);
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
    }),
  );
}
