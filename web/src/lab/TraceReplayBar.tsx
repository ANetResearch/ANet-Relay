// Replay controls of an evaluation trace on the Fly stage (06 §7), in the Records replay idiom: play / pause, 1x 4x
// 16x, a scrubber, the cursor time and Exit, with a roster of the aircraft (program state, hit points) and the
// latest hit or shoot-down. Mounted inside the Fly stage frame; it drives the cursor while it is mounted and closes
// the replay when the Fly workspace goes away or an aircraft starts being flown by hand (the stage must show the
// live fleet then).
import * as React from 'react'
import { useShallow } from 'zustand/react/shallow'
import { useRelay } from '@/store/relay'
import { notify } from '@/shell/notify'
import { Button } from '@/ui/components/ui/button'
import { Slider } from '@/ui/components/ui/slider'
import { ToggleGroup, ToggleGroupItem } from '@/ui/components/ui/toggle-group'
import { Icon, StateIcon } from '@/ui/icons'
import { tL, useTL } from './copy'
import { attachReplayBar, closeTrace, REPLAY_SPEEDS, seekTrace, setTracePlaying, stepTrace, useTraceReplay, type ReplaySpeed } from './traceReplay'
import { recentEvent, sampleAt, type Trace } from './trace'
import './lab.css'

function useDriver(active: boolean): void {
  React.useEffect(() => {
    if (!active) return
    let raf = 0
    let last = performance.now()
    const tick = (now: number) => {
      stepTrace(Math.min(0.1, (now - last) / 1000))
      last = now
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [active])
}

/** Closes the replay when any aircraft is newly flown by hand (the stage then has to show the live fleet). */
function useCloseOnPossession(active: boolean): void {
  React.useEffect(() => {
    if (!active) return
    const held = (s: ReturnType<typeof useRelay.getState>) => new Set((s.fleet?.vehicles ?? []).filter((v) => v.possession).map((v) => v.id))
    let before = held(useRelay.getState())
    return useRelay.subscribe((s) => {
      const now = held(s)
      const fresh = [...now].some((id) => !before.has(id))
      before = now
      if (fresh && useTraceReplay.getState().trace) {
        closeTrace()
        notify(tL('lab.replay.closedForFlight'))
      }
    })
  }, [active])
}

/** Closes the replay when the Fly workspace goes away or the bar is really unmounted (`attachReplayBar`). */
function useCloseOnLeave(): void {
  React.useEffect(() => attachReplayBar(), [])
}

function eventText(trace: Trace, t: number, tl: ReturnType<typeof useTL>): string | null {
  const e = recentEvent(trace.events, t)
  if (!e) return null
  const name = (id: string | null) => trace.aircraft.find((c) => c.id === id)?.name ?? id ?? ''
  if (e.kind === 'hit') return tl('lab.replay.hit', { by: name(e.by), vehicle: name(e.vehicle) })
  if (e.kind === 'down') return tl('lab.replay.down', { vehicle: name(e.vehicle) })
  if (e.kind === 'finish') return tl('lab.replay.finish')
  // v0.5 (07 §5)
  if (e.kind === 'collision') {
    const ring = e.with?.startsWith('ring:') ? Number(e.with.slice(5)) : null
    if (ring !== null && Number.isInteger(ring)) return tl('lab.replay.collisionRing', { vehicle: name(e.vehicle), n: ring + 1 })
    return tl('lab.replay.collision', { vehicle: name(e.vehicle), other: name(e.with ?? null) })
  }
  if (e.kind === 'crash') return tl('lab.replay.crash', { vehicle: name(e.vehicle) })
  return null
}

export function TraceReplayBar() {
  const tl = useTL()
  const trace = useTraceReplay((s) => s.trace)
  const { t, playing, speed } = useTraceReplay(useShallow((s) => ({ t: s.t, playing: s.playing, speed: s.speed })))
  useDriver(Boolean(trace) && playing)
  useCloseOnPossession(Boolean(trace))
  useCloseOnLeave()
  if (!trace) return null
  const total = trace.duration_s
  const caption = eventText(trace, t, tl)
  return (
    <div className="lab-replay l3 pointer-events-auto flex w-[min(720px,calc(100%-32px))] flex-col gap-1.5 rounded-[var(--radius)] px-3 py-2" data-trace-replay={trace.id}>
      <div className="flex items-center gap-2">
        <Button variant="outline" size="icon" aria-label={playing ? tl('lab.replay.pause') : tl('lab.replay.play')} onClick={() => setTracePlaying(!playing)}>
          <StateIcon icon="tl.play" alt={playing} />
        </Button>
        <ToggleGroup
          value={[String(speed)]}
          onValueChange={(v: unknown[]) => v[0] && useTraceReplay.setState({ speed: Number(v[0]) as ReplaySpeed })}
          spacing={0}
          className="rounded-md bg-[var(--g950)] p-0.5 ring-1 ring-[var(--border)]"
        >
          {REPLAY_SPEEDS.map((x) => (
            <ToggleGroupItem key={x} value={String(x)} className="num h-6 min-w-9 rounded-[5px] px-1.5 text-[11px] text-[var(--g400)] hover:bg-transparent data-[pressed]:text-[var(--g50)]">
              {tl('lab.replay.speed', { n: x })}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <div className="min-w-0 flex-1 px-1">
          <Slider value={[t]} min={0} max={Math.max(0.1, total)} step={0.1} onValueChange={(v) => seekTrace(Array.isArray(v) ? v[0] : (v as number))} aria-label={tl('lab.replay.scrub')} />
        </div>
        <span className="num shrink-0 text-[11px] text-[var(--g200)]">
          {t.toFixed(1)} / {total.toFixed(1)} s
        </span>
        <Button variant="ghost" size="sm" className="gap-1 text-[var(--g300)]" onClick={closeTrace}>
          <Icon icon="close" />
          {tl('lab.replay.exit')}
        </Button>
      </div>
      <div className="flex min-w-0 items-center gap-3 text-[11px]" aria-label={tl('lab.replay.aircraft')}>
        {trace.aircraft.map((c) => {
          const s = sampleAt(c, t)
          return (
            <span key={c.id} className="min-w-0 truncate text-[var(--g300)]" data-trace-craft={c.id}>
              <span className="text-[var(--g100)]">{c.name}</span>
              {s.state ? ` · ${s.state}` : ''}
              {s.hp !== null ? ` · ${tl('lab.replay.hp', { n: s.hp })}` : ''}
            </span>
          )
        })}
        <span className="flex-1" />
        {caption ? <span className="shrink-0 font-[560] text-[var(--g50)]">{caption}</span> : null}
      </div>
    </div>
  )
}
