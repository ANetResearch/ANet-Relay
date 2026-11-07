// Relay timeline model (02 §4.5): per-aircraft lanes of controller segments, armed intervals, link-lost intervals
// and event ticks. Fed by events (history, exact times) and reconciled against every snapshot (the backend's state
// is the truth when an event was missed). Pure and framework-free; RelayTimeline.tsx renders it on a canvas.
import type { Fleet, RelayEvent, Vehicle } from '@/api'

export type SegKind = 'agent' | 'human' | 'none'

/** Human input as a language-neutral token: "keyboard", "screen", "pad:1". */
export type InputToken = string

export interface Segment {
  start: number
  end: number | null
  kind: SegKind
  /** agent: session name; human: input token; none: null */
  label: string | null
  /** agent name whose claim is suspended while a human flies */
  waiting: string | null
}

export interface Interval {
  start: number
  end: number | null
}

export type TickKind = 'handback' | 'stop' | 'crash' | 'lost'
export interface Tick {
  t: number
  kind: TickKind
  label: string | null
}

export interface Lane {
  id: string
  label: string
  sim: boolean
  segs: Segment[]
  armed: Interval[]
  lost: Interval[]
  ticks: Tick[]
  /** last known claim name, kept so possession can show who is waiting */
  claim: string | null
}

interface Target {
  kind: SegKind
  label: string | null
  waiting: string | null
}

export function inputToken(p: { input: string; pad?: number | null } | null | undefined): InputToken {
  if (!p) return 'keyboard'
  return p.input === 'gamepad' ? `pad:${p.pad ?? 1}` : p.input
}

export function targetOf(v: Pick<Vehicle, 'controller' | 'claim' | 'possession'>): Target {
  if (v.controller === 'human' && v.possession) return { kind: 'human', label: inputToken(v.possession), waiting: v.claim?.name ?? null }
  if (v.controller === 'agent' && v.claim) return { kind: 'agent', label: v.claim.name, waiting: null }
  return { kind: 'none', label: null, waiting: null }
}

const same = (s: Segment | undefined, t: Target) => !!s && s.kind === t.kind && s.label === t.label && s.waiting === t.waiting

function nameOf(e: RelayEvent): string | null {
  const d = e.data ?? {}
  const n = (d.name ?? d.session_name ?? d.agent ?? d.session ?? e.session) as unknown
  return typeof n === 'string' && n ? n : null
}

export class TimelineModel {
  readonly lanes = new Map<string, Lane>()
  /** bumps on every change; the renderer redraws when it moves */
  version = 0
  private lastSeq = 0

  lane(id: string, label = id, sim = false): Lane {
    let l = this.lanes.get(id)
    if (!l) {
      l = { id, label, sim, segs: [], armed: [], lost: [], ticks: [], claim: null }
      this.lanes.set(id, l)
    }
    return l
  }

  /** Moves the lane to `target` at time t (closing the open segment). Late events move the boundary back. */
  private transition(l: Lane, t: number, target: Target): void {
    const cur = l.segs[l.segs.length - 1]
    if (same(cur, target)) {
      if (cur && t < cur.start) {
        const prev = l.segs[l.segs.length - 2]
        const bound = prev ? Math.max(prev.start, t) : t
        if (prev) prev.end = bound
        cur.start = bound
        this.version++
      }
      return
    }
    if (cur && cur.end === null) {
      if (t < cur.start) t = cur.start
      cur.end = t
    }
    if (cur && cur.end === cur.start && l.segs.length > 0) l.segs.pop()
    l.segs.push({ start: t, end: null, kind: target.kind, label: target.label, waiting: target.waiting })
    this.version++
  }

  private setInterval(list: Interval[], t: number, on: boolean): void {
    const open = list.length > 0 && list[list.length - 1].end === null
    if (on && !open) {
      list.push({ start: t, end: null })
      this.version++
    } else if (!on && open) {
      list[list.length - 1].end = Math.max(t, list[list.length - 1].start)
      this.version++
    }
  }

  private tick(l: Lane, t: number, kind: TickKind, label: string | null = null): void {
    l.ticks.push({ t, kind, label })
    this.version++
  }

  applyEvents(events: readonly RelayEvent[]): void {
    for (const e of events) {
      if (e.seq <= this.lastSeq) continue
      this.lastSeq = e.seq
      this.applyEvent(e)
    }
  }

  applyEvent(e: RelayEvent): void {
    if (e.kind === 'stop_all' && !e.vehicle) {
      for (const l of this.lanes.values()) this.tick(l, e.t, 'stop')
      return
    }
    if (!e.vehicle) return
    const l = this.lane(e.vehicle)
    const cur = l.segs[l.segs.length - 1]
    const d = e.data ?? {}
    switch (e.kind) {
      case 'claim_started': {
        l.claim = nameOf(e)
        if (!cur || cur.kind === 'none') this.transition(l, e.t, { kind: 'agent', label: l.claim, waiting: null })
        else if (cur.kind === 'human') this.transition(l, e.t, { kind: 'human', label: cur.label, waiting: l.claim })
        break
      }
      case 'claim_released':
      case 'agent_session_expired':
      case 'agent_session_closed': {
        l.claim = null
        if (cur?.kind === 'agent') this.transition(l, e.t, { kind: 'none', label: null, waiting: null })
        else if (cur?.kind === 'human' && cur.waiting) this.transition(l, e.t, { kind: 'human', label: cur.label, waiting: null })
        break
      }
      case 'possession_started': {
        const tok = inputToken({ input: String(d.input ?? 'keyboard'), pad: typeof d.pad === 'number' ? d.pad : null })
        const suspended = (d.suspended_claim ?? d.claim) as { name?: string } | string | null | undefined
        const waiting = typeof suspended === 'string' ? suspended : suspended?.name ?? (cur?.kind === 'agent' ? cur.label : l.claim)
        if (waiting) l.claim = waiting
        this.transition(l, e.t, { kind: 'human', label: tok, waiting: waiting ?? null })
        break
      }
      case 'possession_released':
        this.transition(l, e.t, { kind: 'none', label: null, waiting: null })
        break
      case 'relay_handback': {
        const name = nameOf(e) ?? l.claim
        l.claim = name
        this.transition(l, e.t, { kind: 'agent', label: name, waiting: null })
        this.tick(l, e.t, 'handback', name)
        break
      }
      case 'armed':
        this.setInterval(l.armed, e.t, true)
        break
      case 'disarmed':
        this.setInterval(l.armed, e.t, false)
        break
      case 'link_lost':
        this.setInterval(l.lost, e.t, true)
        this.tick(l, e.t, 'lost')
        break
      case 'link_restored':
        this.setInterval(l.lost, e.t, false)
        break
      case 'stop':
      case 'stop_all':
        this.tick(l, e.t, 'stop')
        break
      case 'crashed':
        this.tick(l, e.t, 'crash')
        break
      default:
        break
    }
  }

  /** Reconciles every lane with the snapshot (03 §4.1 controller is authoritative). */
  applySnapshot(f: Pick<Fleet, 't' | 'vehicles'>): void {
    const seen = new Set<string>()
    for (const v of f.vehicles) {
      seen.add(v.id)
      const l = this.lane(v.id, v.label, v.kind === 'sim')
      if (l.label !== v.label || l.sim !== (v.kind === 'sim')) {
        l.label = v.label
        l.sim = v.kind === 'sim'
        this.version++
      }
      if (v.claim) l.claim = v.claim.name
      this.transition(l, f.t, targetOf(v))
      this.setInterval(l.armed, f.t, v.armed)
      this.setInterval(l.lost, f.t, v.link.state === 'lost')
    }
    for (const id of [...this.lanes.keys()]) {
      if (!seen.has(id)) {
        this.lanes.delete(id)
        this.version++
      }
    }
  }

  /** Drops everything that ended before `before` (seconds). */
  prune(before: number): void {
    for (const l of this.lanes.values()) {
      const n = l.segs.length + l.armed.length + l.lost.length + l.ticks.length
      l.segs = l.segs.filter((s, i) => s.end === null || s.end >= before || i === l.segs.length - 1)
      l.armed = l.armed.filter((s) => s.end === null || s.end >= before)
      l.lost = l.lost.filter((s) => s.end === null || s.end >= before)
      l.ticks = l.ticks.filter((k) => k.t >= before)
      if (n !== l.segs.length + l.armed.length + l.lost.length + l.ticks.length) this.version++
    }
  }

  /** Segment covering time t on a lane, if any. */
  segmentAt(id: string, t: number): Segment | null {
    const l = this.lanes.get(id)
    if (!l) return null
    for (let i = l.segs.length - 1; i >= 0; i--) {
      const s = l.segs[i]
      if (s.start <= t && (s.end === null || t < s.end)) return s
    }
    return null
  }
}
