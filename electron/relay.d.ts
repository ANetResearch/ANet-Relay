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
  /** settings only: the section to open, e.g. "copilot" */
  section?: string;
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

export type CopilotApi = "openai-completions" | "anthropic-messages" | "openai-responses";

export interface CopilotConfig {
  api: CopilotApi;
  /** e.g. https://api.openai.com/v1, https://api.anthropic.com, http://127.0.0.1:11434/v1 */
  baseUrl: string;
  model: string;
  hasKey: boolean;
  /** last four characters of the stored key, for recognition only */
  keyHint: string | null;
  /** false when the OS offers no keychain: the key is stored in a 0600 file instead */
  secureStorage: boolean;
  configured: boolean;
  /** which tool sets the agent gets (04 §6.2) */
  toolScope: CopilotToolScope;
  /** floating window opacity in percent, 60..100 (04 §6.1) */
  opacity: number;
}

/** apiKey: a string sets it, null or "" clears it, absent keeps the stored key. */
export type CopilotConfigInput = Partial<Pick<CopilotConfig, "api" | "baseUrl" | "model" | "toolScope" | "opacity">> & { apiKey?: string | null };

export type CopilotEvent =
  | { type: "busy"; busy: boolean }
  | { type: "assistant_start" }
  | { type: "text"; delta: string }
  | { type: "assistant_end"; error: string | null; aborted: boolean }
  | { type: "tool_start"; id: string; tool: string; args: Record<string, unknown> }
  | { type: "tool_update"; id: string; text: string }
  | { type: "tool_end"; id: string; tool: string; error: boolean; text: string }
  | { type: "approval"; id: string; toolCallId: string; tool: string; vehicle: string; label: string; request: Record<string, unknown> }
  | { type: "approval_closed"; id: string; approved: boolean; reason?: string }
  | { type: "error"; error: string };

export type CopilotToolScope = "flight" | "flight+device" | "all";
export type CopilotWindowAction = "show" | "hide" | "toggle";

export interface RelayCopilot {
  getConfig(): Promise<CopilotConfig>;
  saveConfig(cfg: CopilotConfigInput): Promise<CopilotConfig>;
  test(): Promise<{ ok: boolean; ms?: number; reply?: string; error?: string }>;
  /** Starts a turn; progress arrives through onEvent. error: not_configured | backend_unavailable | busy | empty */
  prompt(text: string, vehicle?: string | null): Promise<{ ok: boolean; error?: string }>;
  abort(): Promise<boolean>;
  reset(): Promise<boolean>;
  approve(id: string, ok: boolean): Promise<boolean>;
  getState(): Promise<{ busy: boolean }>;
  onEvent(callback: (event: CopilotEvent) => void): () => void;
  /** Show, hide or toggle the floating Copilot window (04 §6.1). */
  window(action: CopilotWindowAction): Promise<{ visible: boolean }>;
  getWindowState(): Promise<{ visible: boolean }>;
  onWindowState(callback: (state: { visible: boolean }) => void): () => void;
  /** Ask the main window to open Settings > Copilot. */
  openSettings(): Promise<boolean>;
}

export type AppWindowAction = "focus" | "minimize" | "maximize" | "restore" | "fullscreen";

export interface AppWindowState {
  focused: boolean;
  minimized: boolean;
  maximized: boolean;
  fullscreen: boolean;
}

/** Application control through the main process (04 §3.2); served to the main window's page only. */
export interface RelayApp {
  window(action: AppWindowAction): Promise<AppWindowState>;
  getWindowState(): Promise<AppWindowState>;
  onWindowState(callback: (state: AppWindowState) => void): () => void;
  /** Main window capture, at most maxWidth pixels wide; null when it cannot be captured. */
  screenshot(maxWidth: number): Promise<{ png_base64: string; width: number; height: number } | null>;
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
  copilot: RelayCopilot;
  app: RelayApp;
}

declare global {
  interface Window {
    /** Present only inside the ANet Relay desktop shell. */
    relay?: RelayShell;
  }
}
