// Multi-aircraft trace replay (06 §3.4, §7): the Lab opens an evaluation trace, the window switches to Fly and the
// stage replays every aircraft of the trace with its rounds. This store holds the open trace and the cursor; the
// TraceReplayBar (mounted on the Fly stage) advances it and publishes the aircraft into the shared replay store of
// records/replayState.ts (its `crafts` list), which the stage already reads in its frame loop. TraceLayer draws
// the rounds and the arena boundary from here.
import { create } from 'zustand'
import { RelayApiError } from '@/api'
import { useLangStore } from '@/i18n'
import { exitReplay, setReplay, useReplay, type ReplayCraft } from '@/records/replayState'
import { notify } from '@/shell/notify'
import { useUi } from '@/store/ui'
import { labApi } from './api'
import { tL } from './copy'
import { parseTrace, sampleAt, traceStageId, type Trace } from './trace'

export const REPLAY_SPEEDS = [1, 4, 16] as const
export type ReplaySpeed = (typeof REPLAY_SPEEDS)[number]

export interface TraceReplayState {
  trace: Trace | null
  /** seconds from the start of the trace */
  t: number
  playing: boolean
  speed: ReplaySpeed
  loading: string | null
}

export const useTraceReplay = create<TraceReplayState>()(() => ({ trace: null, t: 0, playing: false, speed: 1, loading: null }))

/** The trace's aircraft at time t as replay crafts (stage ids prefixed, angles in radians, throttle 0..100). */
export function craftsAt(trace: Trace, t: number): ReplayCraft[] {
  return trace.aircraft.map((c) => {
    const s = sampleAt(c, t)
    return {
      id: traceStageId(c.id),
      label: c.name,
      sim: true,
      program: c.program ? c.name : null,
      state: s.state,
      hp: s.hp,
      frame: {
        t,
        armed: s.armed,
        mode: null,
        roll: s.roll,
        pitch: s.pitch,
        yaw: s.yaw,
        x: s.x,
        y: s.y,
        z: s.z,
        alt: s.z,
        throttle: s.throttle * 100,
        controller: 'program',
      },
    }
  })
}

/** Writes the cursor into the stage's replay store. */
export function publishReplay(trace: Trace, t: number, playing: boolean): void {
  setReplay({
    active: true,
    recordId: null,
    vehicleId: null,
    label: tL('lab.replay.title', { title: trace.title || trace.id, n: trace.aircraft.length }),
    kind: 'sim',
    t,
    frame: null,
    playing,
    crafts: craftsAt(trace, t),
  })
}

export function openTrace(trace: Trace): void {
  useTraceReplay.setState({ trace, t: 0, playing: true, loading: null })
  publishReplay(trace, 0, true)
  useUi.getState().setWorkspace('fly')
}

export async function openTraceById(id: string): Promise<void> {
  useTraceReplay.setState({ loading: id })
  try {
    const trace = parseTrace(await labApi.trace(id))
    if (useTraceReplay.getState().loading !== id) return
    if (!trace) {
      useTraceReplay.setState({ loading: null })
      notify(tL('lab.replay.invalid'), { level: 'warn' })
      return
    }
    openTrace(trace)
  } catch (e) {
    useTraceReplay.setState({ loading: null })
    const lang = useLangStore.getState().lang
    notify(tL('lab.replay.failed', { reason: e instanceof RelayApiError ? e.text(lang) : String(e) }), { level: 'warn' })
  }
}

export function closeTrace(): void {
  useTraceReplay.setState({ trace: null, t: 0, playing: false, loading: null })
  if (useReplay.getState().crafts) exitReplay()
}

let mountedBars = 0

/** The replay bar is mounted; returns its unmount. While one is mounted, leaving the Fly workspace closes the
 * replay. The last unmount closes it a microtask later, unless a bar mounted again meanwhile: StrictMode's
 * mount, cleanup, mount in development must not close a trace that was just opened. */
export function attachReplayBar(): () => void {
  mountedBars += 1
  const off = useUi.subscribe((s) => {
    if (s.workspace !== 'fly' && useTraceReplay.getState().trace) closeTrace()
  })
  return () => {
    mountedBars -= 1
    off()
    queueMicrotask(() => {
      if (mountedBars === 0 && useTraceReplay.getState().trace) closeTrace()
    })
  }
}

export function seekTrace(t: number): void {
  const s = useTraceReplay.getState()
  if (!s.trace) return
  const tt = Math.min(Math.max(0, t), s.trace.duration_s)
  useTraceReplay.setState({ t: tt })
  publishReplay(s.trace, tt, s.playing)
}

export function setTracePlaying(playing: boolean): void {
  const s = useTraceReplay.getState()
  if (!s.trace) return
  // play from the start again once the end was reached
  const t = playing && s.t >= s.trace.duration_s ? 0 : s.t
  useTraceReplay.setState({ playing, t })
  publishReplay(s.trace, t, playing)
}

/** One animation step: advance by dt real seconds x speed; stops at the end. */
export function stepTrace(dtS: number): void {
  const s = useTraceReplay.getState()
  if (!s.trace || !s.playing) return
  const t = Math.min(s.trace.duration_s, s.t + dtS * s.speed)
  const playing = t < s.trace.duration_s
  useTraceReplay.setState({ t, playing })
  publishReplay(s.trace, t, playing)
}
