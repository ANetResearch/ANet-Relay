// Runtime environment of the renderer: the Electron preload bridge (03 §12) or, in a plain browser during
// development, query parameters / Vite env. Nothing here is persisted; the operator token lives in memory only.

// window.relay, exposed by electron/preload.cjs (03 §12). Mirrors electron/relay.d.ts, the shell's source of truth;
// keep the two in step.

/** Backend lifecycle reported by the Electron main process (02 §15). */
export type BackendStateName =
  | 'starting' // process launched, waiting for GET /api/v1/health (up to 20 s)
  | 'ready' // health check passed
  | 'failed' // did not start (spawn error, early exit or health timeout)
  | 'ports_busy' // 127.0.0.1:8765-8775 all in use, or an explicit RELAY_HTTP_PORT is taken
  | 'crashed' // exited after it was ready: sticks are no longer sent

export interface BackendState {
  state: BackendStateName
  apiBase: string | null
  /** technical reason (English), for a detail line only */
  reason: string | null
  logPath: string | null
  /** ports_busy only: the taken range ([p, p] for an explicit port) */
  ports?: [number, number] | null
}

export type MenuAction = 'stop-all' | 'toggle-fleet' | 'toggle-inspector' | 'toggle-timeline' | 'possess' | 'settings'

export interface MenuEvent {
  action: MenuAction
  /** settings only: the section to open (the Copilot window's settings button asks for 'copilot') */
  section?: string
  /** stop-all only: the shell already sent POST /stop-all */
  shellIssued?: boolean
}

/** Built-in agent (03 §16); the API key never leaves the main process. */
export type CopilotApi = 'openai-completions' | 'anthropic-messages' | 'openai-responses'

export interface CopilotConfig {
  api: CopilotApi
  baseUrl: string
  model: string
  hasKey: boolean
  keyHint: string | null
  /** 06 §9: 'file' (default, AES-256-GCM in the app data folder) or the opt-in system 'keychain' */
  keyStorage: CopilotKeyStorage
  /** 'keychain': a key saved by an earlier version waits in the keychain and is not decrypted automatically */
  keyMigration: 'keychain' | null
  configured: boolean
  /** which tool sets the agent gets (04 §6.2) */
  toolScope: CopilotToolScope
  /** floating window opacity, 60..100 (04 §6.1) */
  opacity: number
}

export type CopilotToolScope = 'flight' | 'flight+device' | 'all'

export type CopilotKeyStorage = 'file' | 'keychain'

export type CopilotConfigInput = Partial<Pick<CopilotConfig, 'api' | 'baseUrl' | 'model' | 'toolScope' | 'opacity' | 'keyStorage'>> & { apiKey?: string | null }

export type CopilotEvent =
  | { type: 'busy'; busy: boolean }
  | { type: 'assistant_start' }
  | { type: 'text'; delta: string }
  | { type: 'assistant_end'; error: string | null; aborted: boolean }
  | { type: 'tool_start'; id: string; tool: string; args: Record<string, unknown> }
  | { type: 'tool_update'; id: string; text: string }
  | { type: 'tool_end'; id: string; tool: string; error: boolean; text: string }
  | { type: 'approval'; id: string; toolCallId: string; tool: string; vehicle: string; label: string; request: Record<string, unknown> }
  | { type: 'approval_closed'; id: string; approved: boolean; reason?: string }
  | { type: 'error'; error: string }
  | { type: 'config' }

export type CopilotWindowAction = 'show' | 'hide' | 'toggle'

/** State of the floating Copilot window (04 §6.1). */
export interface CopilotWindowState {
  visible: boolean
}

export interface RelayCopilotBridge {
  getConfig(): Promise<CopilotConfig>
  saveConfig(cfg: CopilotConfigInput): Promise<CopilotConfig>
  test(): Promise<{ ok: boolean; ms?: number; reply?: string; error?: string }>
  prompt(text: string, vehicle?: string | null): Promise<{ ok: boolean; error?: string }>
  abort(): Promise<boolean>
  reset(): Promise<boolean>
  approve(id: string, ok: boolean): Promise<boolean>
  getState(): Promise<{ busy: boolean }>
  onEvent(cb: (e: CopilotEvent) => void): () => void
  /** Show, hide or toggle the floating window; resolves with its new state. */
  window(action: CopilotWindowAction): Promise<CopilotWindowState>
  getWindowState(): Promise<CopilotWindowState>
  onWindowState(cb: (s: CopilotWindowState) => void): () => void
  /** Ask the main window to open Settings > Copilot (the floating window has no settings of its own). */
  openSettings(): Promise<boolean>
}

export type AppWindowAction = 'focus' | 'minimize' | 'maximize' | 'restore' | 'fullscreen'

/** State of the main window as the shell sees it (04 §3.1 UiState.window). */
export interface AppWindowState {
  focused: boolean
  minimized: boolean
  maximized: boolean
  fullscreen: boolean
}

export interface AppScreenshot {
  png_base64: string
  width: number
  height: number
}

/** Application control through the main process (04 §3.2 window and screenshot). */
export interface RelayAppBridge {
  window(action: AppWindowAction): Promise<AppWindowState>
  getWindowState(): Promise<AppWindowState>
  onWindowState(cb: (s: AppWindowState) => void): () => void
  /** Main window capture, downscaled to at most maxWidth pixels wide. */
  screenshot(maxWidth: number): Promise<AppScreenshot | null>
}

export interface RelayBridge {
  readonly apiBase: string | null
  readonly operatorToken: string
  readonly platform: string
  readonly version: string
  openExternal(url: string): Promise<boolean>
  revealLogs(): Promise<boolean>
  openBridgePage(ip: string): Promise<boolean>
  setLanguage(lang: 'en' | 'zh'): Promise<boolean>
  getBackendState(): Promise<BackendState>
  /** resolves true once the restarted backend is healthy */
  restartBackend(): Promise<boolean>
  quit(): Promise<boolean>
  onBackendState(cb: (s: BackendState) => void): () => void
  onMenu(cb: (e: MenuEvent) => void): () => void
  /** absent in shells older than 0.2 */
  copilot?: RelayCopilotBridge
  /** absent in shells older than 0.2.1 */
  app?: RelayAppBridge
}

declare global {
  interface Window {
    relay?: RelayBridge
  }
}

export type FixtureName = 'busy' | 'empty' | 'alerts' | 'dogfight' // WP-C v0.5: dogfight

export interface RelayEnv {
  /** REST base ending in /api/v1 */
  apiBase: string
  /** ws(s) URL of /api/v1/stream without credentials */
  streamUrl: string
  operatorToken: string | null
  platform: string
  electron: boolean
  fixture: FixtureName | null
}

/** Accepts "http://127.0.0.1:8765", "http://127.0.0.1:8765/" or ".../api/v1" and returns the /api/v1 base. */
export function normalizeApiBase(raw: string): string {
  const s = raw.replace(/\/+$/, '')
  return s.endsWith('/api/v1') ? s : `${s}/api/v1`
}

export function streamUrlFor(apiBase: string): string {
  return `${apiBase.replace(/^http/, 'ws')}/stream`
}

function detectPlatform(): string {
  const p = (typeof navigator !== 'undefined' && (navigator.platform || navigator.userAgent)) || ''
  if (/mac/i.test(p)) return 'darwin'
  if (/win/i.test(p)) return 'win32'
  return 'linux'
}

export function readEnv(search: string = typeof location !== 'undefined' ? location.search : ''): RelayEnv {
  const q = new URLSearchParams(search)
  const fx = q.get('fixture')
  const fixture: FixtureName | null = fx === 'busy' || fx === 'empty' || fx === 'alerts' || fx === 'dogfight' ? fx : null
  const bridge = typeof window !== 'undefined' ? window.relay : undefined
  if (bridge) {
    // null when the shell could not reserve a port (ports_busy); the boot layer explains that state
    const apiBase = normalizeApiBase(bridge.apiBase ?? 'http://127.0.0.1:8765')
    return { apiBase, streamUrl: streamUrlFor(apiBase), operatorToken: bridge.operatorToken || null, platform: bridge.platform, electron: true, fixture }
  }
  const viteEnv = (import.meta as { env?: Record<string, string | undefined> }).env ?? {}
  const apiBase = normalizeApiBase(q.get('api') ?? viteEnv.VITE_RELAY_API ?? 'http://127.0.0.1:8765')
  return {
    apiBase,
    streamUrl: streamUrlFor(apiBase),
    operatorToken: q.get('operator') ?? viteEnv.VITE_RELAY_OPERATOR ?? null,
    platform: q.get('platform') ?? detectPlatform(),
    electron: false,
    fixture,
  }
}

let current: RelayEnv | null = null
export function env(): RelayEnv {
  if (!current) current = readEnv()
  return current
}
/** tests and fixtures only */
export function setEnv(e: RelayEnv): void {
  current = e
}
