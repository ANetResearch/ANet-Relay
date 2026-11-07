// REST client for /api/v1 (03 §10.1). Every operator call carries X-Relay-Operator; error bodies
// {"error": {code, message_en, message_zh, detail}} become RelayApiError. The transport is swappable so the dev
// fixture (src/dev/fixtures.ts) can serve the same calls without a backend.
import { env } from './env'
import type { Alert, ErrorBody, Fleet, Health, Vehicle } from './types'
import type { RelayEvent } from './events'

export class RelayApiError extends Error {
  readonly code: string
  readonly status: number
  readonly messageEn: string
  readonly messageZh: string
  readonly detail: unknown
  constructor(status: number, code: string, messageEn: string, messageZh: string, detail?: unknown) {
    super(`${code}: ${messageEn}`)
    this.name = 'RelayApiError'
    this.status = status
    this.code = code
    this.messageEn = messageEn
    this.messageZh = messageZh
    this.detail = detail
  }
  /** message in the UI language */
  text(lang: 'en' | 'zh'): string {
    return lang === 'zh' ? this.messageZh || this.messageEn : this.messageEn
  }
}

/** Builds a RelayApiError from a response status and parsed body; tolerant of non-conforming bodies. */
export function toApiError(status: number, body: unknown, statusText = ''): RelayApiError {
  const e = (body as Partial<ErrorBody> | null)?.error
  if (e && typeof e === 'object' && typeof e.code === 'string') {
    return new RelayApiError(status, e.code, e.message_en ?? e.code, e.message_zh ?? e.message_en ?? e.code, e.detail)
  }
  // FastAPI validation errors: {"detail": [...]}
  const detail = (body as { detail?: unknown } | null)?.detail
  const code = status === 0 ? 'network_error' : status === 422 ? 'validation_error' : `http_${status}`
  const msg = typeof detail === 'string' ? detail : statusText || (status === 0 ? 'The backend is not reachable.' : `Request failed (${status}).`)
  const zh = status === 0 ? '无法连接后端。' : `请求失败（${status}）。`
  return new RelayApiError(status, code, msg, typeof detail === 'string' ? detail : zh, detail)
}

export type Method = 'GET' | 'POST' | 'PUT' | 'DELETE'
export type Auth = 'none' | 'operator'

export interface RestTransport {
  request(method: Method, path: string, body: unknown, auth: Auth, signal?: AbortSignal): Promise<unknown>
}

export const httpTransport: RestTransport = {
  async request(method, path, body, auth, signal) {
    const e = env()
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    if (auth === 'operator' && e.operatorToken) headers['X-Relay-Operator'] = e.operatorToken
    let res: Response
    try {
      res = await fetch(`${e.apiBase}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal })
    } catch (err) {
      if ((err as { name?: string }).name === 'AbortError') throw err
      throw toApiError(0, null)
    }
    const text = await res.text()
    let parsed: unknown = null
    if (text) {
      try {
        parsed = JSON.parse(text)
      } catch {
        parsed = { detail: text }
      }
    }
    if (!res.ok) throw toApiError(res.status, parsed, res.statusText)
    return parsed
  },
}

let transport: RestTransport = httpTransport
export function setTransport(t: RestTransport): void {
  transport = t
}

export function request<T>(method: Method, path: string, body?: unknown, auth: Auth = 'none', signal?: AbortSignal): Promise<T> {
  return transport.request(method, path, body, auth, signal) as Promise<T>
}

const op = <T>(method: Method, path: string, body?: unknown) => request<T>(method, path, body, 'operator')
const enc = encodeURIComponent

export type StickFrame = { roll: number; pitch: number; yaw: number; throttle: number }
export type PossessInput = { input: 'keyboard' | 'screen' | 'gamepad'; pad?: number }
export type Ok = { ok?: boolean } & Record<string, unknown>
export type SkillName = 'takeoff' | 'land' | 'hold' | 'rtl' | 'goto' | 'move' | 'yaw'
export type PlanAction = 'takeoff' | 'hover' | 'hold' | 'move' | 'yaw' | 'land' | 'goto' | 'rtl'
/** One plan step (03 §5.8): takeoff {alt}, hover {seconds}, move {forward, right, up}, yaw {deg}, land. */
export type PlanStep = { action: PlanAction; alt?: number; seconds?: number; forward?: number; right?: number; up?: number; deg?: number }

/** Typed helpers for every 03 §10.1 route the window uses. Shapes the doc leaves open are typed as `unknown`. */
export const api = {
  health: (signal?: AbortSignal) => request<Health>('GET', '/health', undefined, 'none', signal),
  capabilities: () => request<unknown>('GET', '/capabilities'),
  fleet: () => request<Fleet>('GET', '/fleet'),
  vehicle: (id: string) => request<Vehicle>('GET', `/vehicles/${enc(id)}`),
  messages: (id: string) => request<unknown>('GET', `/vehicles/${enc(id)}/messages`),
  params: (id: string) => request<unknown>('GET', `/vehicles/${enc(id)}/params`),
  preflight: (id: string) => request<unknown>('GET', `/vehicles/${enc(id)}/preflight`),
  topology: () => request<unknown>('GET', '/topology'),
  events: (after?: number, limit?: number) => {
    const q = new URLSearchParams()
    if (after !== undefined) q.set('after', String(after))
    if (limit !== undefined) q.set('limit', String(limit))
    const s = q.toString()
    return request<RelayEvent[] | { events: RelayEvent[] }>('GET', `/events${s ? `?${s}` : ''}`)
  },
  network: () => request<unknown>('GET', '/network'),
  courses: () => request<unknown>('GET', '/courses'),
  putCourse: (id: string, course: unknown) => op<Ok>('PUT', `/courses/${enc(id)}`, course),
  discover: () => op<Ok>('POST', '/discover'),
  connect: (host: string, port?: number) => op<Ok>('POST', '/vehicles', port === undefined ? { host } : { host, port }),
  remove: (id: string) => op<Ok>('DELETE', `/vehicles/${enc(id)}`),
  label: (id: string, label: string) => op<Ok>('POST', `/vehicles/${enc(id)}/label`, { label }),
  calibration: (id: string, c: { signs?: Vehicle['calibration']['signs']; hover?: number }) => op<Ok>('POST', `/vehicles/${enc(id)}/calibration`, c),
  confirm: (id: string, item: 'signs' | 'props') => op<Ok>('POST', `/vehicles/${enc(id)}/confirm`, { item }),
  possess: (id: string, input: PossessInput) => op<Ok>('POST', `/vehicles/${enc(id)}/possess`, input),
  release: (id: string) => op<Ok>('POST', `/vehicles/${enc(id)}/release`),
  sticks: (id: string, s: StickFrame) => op<Ok>('POST', `/vehicles/${enc(id)}/sticks`, s),
  arm: (id: string) => op<Ok>('POST', `/vehicles/${enc(id)}/arm`, { arm: true, confirm: 'ARM' }),
  disarm: (id: string) => op<Ok>('POST', `/vehicles/${enc(id)}/arm`, { arm: false }),
  mode: (id: string, name: string) => op<Ok>('POST', `/vehicles/${enc(id)}/mode`, { name }),
  skill: (id: string, name: SkillName, args?: Record<string, number | string>) =>
    op<Ok>('POST', `/vehicles/${enc(id)}/skills/${name}`, args ?? {}),
  plan: (id: string, steps: PlanStep[], opts?: { name?: string; confirm?: string }) =>
    op<Ok>('POST', `/vehicles/${enc(id)}/plan`, { steps, ...opts }),
  cancelPlan: (id: string) => op<Ok>('POST', `/vehicles/${enc(id)}/plan/cancel`),
  courseStart: (id: string, courseId: string) => op<Ok>('POST', `/vehicles/${enc(id)}/course/start`, { course_id: courseId }),
  courseStop: (id: string) => op<Ok>('POST', `/vehicles/${enc(id)}/course/stop`),
  stop: (id: string) => op<Ok>('POST', `/vehicles/${enc(id)}/stop`),
  stopAll: () => op<Ok>('POST', '/stop-all'),
  refreshParams: (id: string) => op<Ok>('POST', `/vehicles/${enc(id)}/params/refresh`),
  setParam: (id: string, name: string, value: number) => op<Ok>('POST', `/vehicles/${enc(id)}/params`, { name, value, confirm: name }),
  motorTest: (id: string, motor: number, throttlePct: number, seconds: number) =>
    op<Ok>('POST', `/vehicles/${enc(id)}/motor-test`, { motor, throttle_pct: throttlePct, seconds, confirm: 'MOTOR' }),
  addSim: (body?: Record<string, unknown>) => op<Ok>('POST', '/sims', body ?? {}),
  removeSims: () => op<Ok>('DELETE', '/sims'),
  resetSim: (id: string) => op<Ok>('POST', `/sims/${enc(id)}/reset`),
  sessions: () => request<unknown>('GET', '/sessions'),
  bridge: (ip: string) => op<unknown>('GET', `/bridges/${enc(ip)}`),
  configureBridge: (ip: string, body: Record<string, unknown>) => op<Ok>('POST', `/bridges/${enc(ip)}/configure`, body),
  historySessions: () => request<unknown>('GET', '/history/sessions'),
  historySession: (sid: number | string) => request<unknown>('GET', `/history/sessions/${enc(String(sid))}`),
  historyExportUrl: (sid: number | string, fmt: 'csv' | 'json') => `${env().apiBase}/history/sessions/${enc(String(sid))}/export?fmt=${fmt}`,
  alerts: () => request<Alert[]>('GET', '/alerts'),
  ackAlert: (id: string) => op<Ok>('POST', `/alerts/${enc(id)}/ack`),
  settings: () => request<Record<string, unknown>>('GET', '/settings'),
  putSettings: (s: Record<string, unknown>) => op<Ok>('PUT', '/settings', s),
} as const
