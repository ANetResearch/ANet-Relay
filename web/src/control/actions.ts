// Side effects of the input engine and of the WP7 surfaces: REST calls with error toasts, the "fly the ring
// course" flow of the controller hint (02 §7.2), and the window events other work packages listen to.
import { api, RelayApiError } from '@/api'
import { t } from '@/i18n'
import { moveSelection, stopVehicle } from '@/shell/actions'
import { notify, notifyError } from '@/shell/notify'
import { getVehicle } from '@/store/selectors'
import { useRelay } from '@/store/relay'
import { useUi } from '@/store/ui'
import { setControlFeel } from '@/settings/prefs'
import { autoBindTarget, possessedBy, type SourceKey } from './bindings'
import type { EngineActions, EngineNotice } from './engine'
import type { ControlFeel } from './shaping'
import { ct, type ControlKey } from './strings'

/** Window events for the stage (WP6): touchpad view cycle and the selection-ring pulse of a newly bound pad. */
export const VIEW_CYCLE_EVENT = 'relay:view-cycle'
export const PAD_BOUND_EVENT = 'relay:pad-bound'
export const DEFAULT_COURSE = 'cage-loop'

function report<T>(p: Promise<T>): Promise<T> {
  return p.catch((e: unknown) => {
    notifyError(e)
    throw e
  })
}

export function sourceLabel(s: SourceKey): string {
  if (s === 'keyboard') return t('input.keyboard')
  if (s === 'screen') return t('input.screen')
  return t('input.pad', { n: Number(s.slice(4)) })
}

const label = (id: string) => getVehicle(id)?.label ?? id

/** Last course started per aircraft in this window, so Square repeats it. */
const lastCourse = new Map<string, string>()
export function rememberCourse(vehicle: string, courseId: string): void {
  lastCourse.set(vehicle, courseId)
}

export function toggleCourse(vehicle: string): void {
  const v = getVehicle(vehicle)
  if (!v) return
  const active = v.course && (v.course.state === 'waiting_takeoff' || v.course.state === 'running')
  if (active) void report(api.courseStop(vehicle)).catch(() => undefined)
  else {
    const id = lastCourse.get(vehicle) ?? v.course?.course_id ?? DEFAULT_COURSE
    void report(api.courseStart(vehicle, id)).catch(() => undefined)
  }
}

export function possessWith(vehicle: string, pad: number | null): Promise<unknown> {
  return report(api.possess(vehicle, pad === null ? { input: 'keyboard' } : { input: 'gamepad', pad }))
}

export function releaseVehicle(vehicle: string): Promise<unknown> {
  return report(api.release(vehicle))
}

function notice(n: EngineNotice): void {
  if (n.kind === 'realArm') notify(t('pad.realArm'), { level: 'warn' })
  else if (n.kind === 'maxPads') notify(t('pad.max'), { level: 'warn' })
  else if (n.kind === 'noSelection') notify(ct('ctl.selectFirst'))
  else notify(ct('ctl.alreadyFlown', { aircraft: label(n.vehicle), input: sourceLabel(n.by) }), { level: 'warn' })
}

// ------------------------------------------------------------------------------- WP-C: fire hook and feel

/** Transport of the hold-to-fire input (05 §7.4); the arena package installs it. */
export type FireHandler = (vehicle: string, on: boolean) => void
let fireHandler: FireHandler = () => undefined

/**
 * Installs the fire transport: the input engine calls it with `on` at the press, every 100 ms while held and `off`
 * at the release, for possessed simulated aircraft only. Returns a function that restores the no-op default.
 */
export function setFireHandler(handler: FireHandler): () => void {
  fireHandler = handler
  return () => {
    if (fireHandler === handler) fireHandler = () => undefined
  }
}

function setFeel(feel: ControlFeel): void {
  setControlFeel(feel)
  notify(ct('feel.changed', { feel: ct(`feel.${feel}` as ControlKey) }), { timeoutMs: 1500 })
}

export const engineActions: EngineActions = {
  possess: possessWith,
  release: releaseVehicle,
  arm: (vehicle, on) => report(on ? api.arm(vehicle) : api.disarm(vehicle)),
  stop: stopVehicle,
  mode: (vehicle, name) => report(api.mode(vehicle, name)),
  course: toggleCourse,
  resetSim: (vehicle) => report(api.resetSim(vehicle)),
  moveSelection,
  viewCycle: () => window.dispatchEvent(new CustomEvent(VIEW_CYCLE_EVENT)),
  notice,
  simAssist: (vehicle, on) => report(api.simAssist(vehicle, on)),
  fire: (vehicle, on) => fireHandler(vehicle, on),
  setFeel,
}

async function waitForVehicle(id: string, timeoutMs = 4000): Promise<boolean> {
  const t0 = performance.now()
  while (performance.now() - t0 < timeoutMs) {
    if (getVehicle(id)) return true
    await new Promise((r) => setTimeout(r, 100))
  }
  return false
}

/**
 * "Fly the ring course" (02 §7.2, S5): fly a simulated aircraft with this controller, adding one when none is
 * free, select it and start the course. The course waits for take-off (03 §8).
 */
export async function flyRingCourse(pad: number, courseId: string = DEFAULT_COURSE): Promise<void> {
  try {
    const vs = useRelay.getState().fleet?.vehicles ?? []
    let id = possessedBy(vs, `pad:${pad}`)?.kind === 'sim' ? possessedBy(vs, `pad:${pad}`)!.id : null
    if (!id) {
      id = autoBindTarget(vs)?.id ?? null
      if (!id) {
        const res = (await api.addSim()) as { added?: string[] }
        id = res.added?.[0] ?? null
        if (!id || !(await waitForVehicle(id))) return
      }
      await api.possess(id, { input: 'gamepad', pad })
    }
    useUi.getState().select(id)
    rememberCourse(id, courseId)
    await api.courseStart(id, courseId)
  } catch (e) {
    if (e instanceof RelayApiError || e instanceof Error) notifyError(e)
  }
}
