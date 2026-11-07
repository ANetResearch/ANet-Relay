// Contract of window.relay, exposed by electron/preload.cjs (03 §12).
// The renderer may reference this file from web/src (e.g. a triple-slash reference
// or a copy in web/src/types); it is the single source of truth for the shell API.

export type RelayMenuAction =
  | "stop-all" // Fly > Stop all (Cmd/Ctrl+.). shellIssued: the shell already POSTed /stop-all.
  | "toggle-fleet" // View > Toggle fleet (Alt+Cmd/Ctrl+B)
  | "toggle-inspector" // View > Toggle inspector (Alt+Cmd/Ctrl+I)
  | "toggle-timeline" // View > Toggle timeline (Alt+Cmd/Ctrl+T)
  | "possess" // Fly > Possess (menu click only; the renderer owns the P key)
  | "settings"; // ANet Relay > Settings (Cmd/Ctrl+,)

export interface RelayMenuEvent {
  action: RelayMenuAction;
  /** stop-all only: true when the shell already sent POST /api/v1/stop-all. */
  shellIssued?: boolean;
}

export type RelayBackendStateName =
  | "starting" // process launched, waiting for GET /api/v1/health (up to 20 s)
  | "ready" // health check passed; open the WebSocket next
  | "failed" // did not start (spawn error, early exit or health timeout)
  | "ports_busy" // 127.0.0.1:8765-8775 all in use, or an explicit RELAY_HTTP_PORT is taken
  | "crashed"; // exited after it was ready: sticks are no longer sent

export interface RelayBackendState {
  state: RelayBackendStateName;
  /** http://127.0.0.1:<port>; may be null before a port is reserved. */
  apiBase: string | null;
  /** Technical reason (English), for the detail line only. */
  reason: string | null;
  /** Captured backend stdout/stderr, set for failure states. */
  logPath: string | null;
  /** ports_busy only: the port range that is taken ([p, p] for an explicit port). */
  ports?: [number, number] | null;
}

export interface RelayShell {
  /** http://127.0.0.1:<port>, fixed for the lifetime of the page (the shell reloads the page if it changes). */
  readonly apiBase: string | null;
  /** Send as X-Relay-Operator (REST) or ?operator= (WebSocket). */
  readonly operatorToken: string;
  readonly platform: "darwin" | "linux" | "win32" | string;
  readonly version: string;
  openExternal(url: string): Promise<boolean>;
  revealLogs(): Promise<boolean>;
  /** LAN or loopback IPv4 with optional port, e.g. "192.168.4.1" or "127.0.0.2:8080". */
  openBridgePage(ip: string): Promise<boolean>;
  setLanguage(lang: "en" | "zh"): Promise<boolean>;
  getBackendState(): Promise<RelayBackendState>;
  /** Resolves true once the restarted backend is healthy. */
  restartBackend(): Promise<boolean>;
  quit(): Promise<boolean>;
  onBackendState(callback: (state: RelayBackendState) => void): () => void;
  onMenu(callback: (event: RelayMenuEvent) => void): () => void;
}

declare global {
  interface Window {
    /** Present only inside the ANet Relay desktop shell. */
    relay?: RelayShell;
  }
}
