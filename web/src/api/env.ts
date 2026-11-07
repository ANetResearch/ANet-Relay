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
  /** stop-all only: the shell already sent POST /stop-all */
  shellIssued?: boolean
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
}

declare global {
  interface Window {
    relay?: RelayBridge
  }
}

export type FixtureName = 'busy' | 'empty' | 'alerts'

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
  const fixture: FixtureName | null = fx === 'busy' || fx === 'empty' || fx === 'alerts' ? fx : null
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
