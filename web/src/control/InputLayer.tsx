// The input engine host (02 §5, §7; 03 §6.1 H1-H4). Mounted once at the app root; renders no DOM. It owns the
// keyboard flight keys (registerHotkey, Fly workspace), the first-interaction gate, focus tracking, the 50 Hz
// engine tick, publishing to the stores, and the possession toasts of 02 §5.2 (You are flying, Mode set) and
// §5.4 (airborne with no controller). Handback and release toasts belong to the shell (shell/Toasts.tsx).
import * as React from 'react'
import { cancelSticks, sendSticks, type RelayEvent } from '@/api'
import { t } from '@/i18n'
import { menuHandlers, registerHotkey } from '@/shell/keyboard'
import { notify } from '@/shell/notify'
import { useRelay, type PadInfo } from '@/store/relay'
import { getVehicle } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { setHold } from '@/fleet/holds'
import { usePrefs } from '@/settings/prefs'
import { isWindowSource, sourceOf } from './bindings'
import { engineActions, PAD_BOUND_EVENT, possessWith, releaseVehicle, setFireHandler, sourceLabel } from './actions'
import { InputEngine, type EngineOutput } from './engine'
import { FLIGHT_KEYS, type FlightKey, type GamepadLike } from './mapping'
import { liveWriter, publishDevices, useInputUi, type HintCard } from './store'
import { ct } from './strings'
import { sendFire } from '@/arena/store'

const TICK_MS = 20
const MAX_HINTS = 2

let active: InputEngine | null = null

/** On-screen sticks (Control tab) feed the window source of the running engine. */
export function screenStick(side: 'left' | 'right', x: number, y: number, pressed: boolean): void {
  active?.setScreen(side, x, y, pressed)
}

/** Current window throttle stick (y, -1..1) so the on-screen left stick can start from it. */
export function windowThrottleY(): number {
  return active?.windowThrottleY ?? 0
}

/** The mapping wizard (05 §6.3) takes a controller out of flight while it runs. */
export function setPadLearning(pad: number, on: boolean): void {
  active?.setLearning(pad, on)
}

function windowDriven(): string | null {
  const vs = useRelay.getState().fleet?.vehicles ?? []
  return vs.find((v) => v.possession && isWindowSource(sourceOf(v.possession)))?.id ?? null
}

/** P (02 §3): possess / release the selected aircraft with the keyboard source. */
export function toggleKeyboardPossession(): void {
  const id = useUi.getState().selectedId
  const v = getVehicle(id)
  if (!v) {
    notify(ct('ctl.selectFirst'))
    return
  }
  if (v.possession && isWindowSource(sourceOf(v.possession))) void releaseVehicle(v.id).catch(() => undefined)
  else if (v.possession) notify(ct('ctl.alreadyFlown', { aircraft: v.label, input: sourceLabel(sourceOf(v.possession)) }), { level: 'warn' })
  else void possessWith(v.id, null).catch(() => undefined)
}

function readGamepads(): readonly (GamepadLike | null)[] {
  if (typeof navigator === 'undefined' || typeof navigator.getGamepads !== 'function') return []
  return navigator.getGamepads() as unknown as readonly (GamepadLike | null)[]
}

function samePads(a: readonly PadInfo[], b: readonly PadInfo[]): boolean {
  if (a.length !== b.length) return false
  return a.every((p, i) => p.index === b[i].index && p.id === b[i].id && p.connected === b[i].connected && p.vehicle === b[i].vehicle && p.model === b[i].model)
}

const keyOf = (o: object) => Object.keys(o).sort().join('|')

function publish(out: EngineOutput): void {
  const relay = useRelay.getState()
  const gate = out.gateOpen ? 'open' : 'pending'
  const pads: PadInfo[] = out.pads.map((p) => ({ index: p.index, id: p.id, model: p.model, connected: p.connected, vehicle: p.vehicle, battery: null }))
  if (relay.inputs.gate !== gate || !samePads(relay.inputs.pads, pads)) relay.setInputs({ gate, pads })
  publishDevices(out)

  const ui = useInputUi.getState()
  const patch: Partial<ReturnType<typeof useInputUi.getState>> = {}
  const holdKey = (h: EngineOutput['holds']) => Object.entries(h).map(([v, s]) => `${v}:${s.kind}:${s.pad}`).sort().join('|')
  if (holdKey(ui.holds) !== holdKey(out.holds)) {
    patch.holds = out.holds
    // mirror to the shared hold store read by fleet cards and stage labels (CONTRACTS.md §5, fleet/holds.ts)
    for (const id of Object.keys(ui.holds)) if (!(id in out.holds)) setHold(id, null)
    for (const [id, h] of Object.entries(out.holds)) {
      const prev = ui.holds[id]
      if (!prev || prev.kind !== h.kind || prev.pad !== h.pad) setHold(id, { kind: h.kind, durationMs: h.durationMs })
    }
  }
  if (keyOf(ui.padLost) !== keyOf(out.padLost)) patch.padLost = out.padLost
  if (ui.paused !== out.paused) patch.paused = out.paused
  const awKey = (a: Record<string, string>) => Object.entries(a).map((e) => e.join(':')).sort().join('|')
  if (awKey(ui.awaiting) !== awKey(out.awaiting)) patch.awaiting = out.awaiting
  if (out.hints.length) patch.hints = [...ui.hints, ...out.hints].slice(-MAX_HINTS) as HintCard[]
  if (Object.keys(patch).length) useInputUi.setState(patch)

  for (const h of out.hints) if (h.vehicle) window.dispatchEvent(new CustomEvent(PAD_BOUND_EVENT, { detail: { vehicle: h.vehicle, pad: h.pad } }))

  const now = performance.now()
  for (const [id, f] of out.frames) liveWriter.set(id, { ...f, at: now })
  liveWriter.retain(new Set(out.frames.keys()))
  for (const p of out.pads) liveWriter.pad(p.index, p.connected ? p.raw : null)
}

/** Possession toasts from live events (older than 5 s = history replayed on connect, ignored). */
function useEventToasts(): void {
  React.useEffect(() => {
    let last = useRelay.getState().lastSeq
    const modeWatch = new Map<string, number>()
    return useRelay.subscribe((s) => {
      if (s.lastSeq === last) return
      const fresh = s.events.filter((e) => e.seq > last)
      last = s.lastSeq
      const now = s.fleet?.t ?? Date.now() / 1000
      for (const e of fresh) {
        if (now - e.t > 5 || !e.vehicle) continue
        const aircraft = getVehicle(e.vehicle)?.label ?? (e.data?.vehicle as string | undefined) ?? e.vehicle
        handleEvent(e, aircraft, modeWatch)
      }
    })
  }, [])
}

function handleEvent(e: RelayEvent, aircraft: string, modeWatch: Map<string, number>): void {
  const id = e.vehicle as string
  if (e.kind === 'possession_started') {
    const input = e.data?.input === 'gamepad' ? t('input.pad', { n: Number(e.data?.pad ?? 1) }) : e.data?.input === 'screen' ? t('input.screen') : t('input.keyboard')
    notify(t('possess.youAreFlying', { aircraft, input }), { timeoutMs: 2000 })
    modeWatch.set(id, e.t)
  } else if (e.kind === 'mode_changed') {
    const since = modeWatch.get(id)
    if (since !== undefined && e.t - since <= 3 && getVehicle(id)?.controller === 'human') {
      modeWatch.delete(id)
      notify(t('possess.modeSet', { mode: String(e.data?.mode ?? '') }))
    }
  } else if (e.kind === 'airborne_no_controller') {
    notify(t('possess.airborneNoController', { aircraft }), { level: 'warn' })
  } else if (e.kind === 'possession_released' || e.kind === 'relay_handback') {
    modeWatch.delete(id)
  }
}

export function InputLayer() {
  useEventToasts()
  // hold-to-fire goes to the arena over the stream (05 §7.4; WP-C hook, WP-D transport)
  React.useEffect(() => setFireHandler((vehicle, on) => void sendFire(vehicle, on)), [])
  React.useEffect(() => {
    // sticks flow only while the window has focus (H2); an interaction proves focus even if no focus event came
    let focused = typeof document === 'undefined' ? true : document.hasFocus()
    const engine = new InputEngine({
      now: () => performance.now(),
      gamepads: readGamepads,
      vehicles: () => useRelay.getState().fleet?.vehicles ?? [],
      selected: () => useUi.getState().selectedId,
      focused: () => focused,
      send: (vehicle, f) => void sendSticks(vehicle, f),
      cancel: (vehicle) => cancelSticks(vehicle),
      prefs: () => {
        const p = usePrefs.getState()
        return {
          deadzone: p.padDeadzone,
          expo: p.padExpo,
          feel: p.controlFeel,
          stickMode: p.stickMode,
          invert: p.invert,
          throttleDetent: p.throttleDetent,
        }
      },
      profiles: () => usePrefs.getState().padProfiles,
      act: engineActions,
    })
    active = engine

    const gate = () => {
      focused = true
      engine.openGate()
    }
    const onBlur = () => {
      focused = false
      engine.blur()
    }
    const onFocus = () => {
      focused = true
    }
    const onVisibility = () => (document.visibilityState === 'hidden' ? onBlur() : onFocus())
    const onKeyUp = (e: KeyboardEvent) => {
      if (/^Key[A-Z]$/.test(e.code)) engine.keyUp(e.code.slice(3).toLowerCase())
    }
    menuHandlers.possess = toggleKeyboardPossession
    window.addEventListener('pointerdown', gate, { capture: true })
    window.addEventListener('keydown', gate, { capture: true })
    window.addEventListener('gamepadconnected', gate)
    window.addEventListener('blur', onBlur)
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('keyup', onKeyUp)

    const offs = [
      ...FLIGHT_KEYS.map((k: FlightKey) =>
        registerHotkey({
          combo: k,
          workspace: 'fly',
          repeat: true,
          handler: () => {
            if (!windowDriven()) return false
            engine.keyDown(k)
          },
        }),
      ),
      registerHotkey({
        combo: 'space',
        workspace: 'fly',
        handler: () => {
          if (!windowDriven()) return false
          engine.centre()
        },
      }),
      registerHotkey({ combo: 'p', workspace: 'fly', handler: toggleKeyboardPossession }),
      // WP-C (05 §6.1, §6.3): Z throttle to the hover point, H hover assist
      registerHotkey({
        combo: 'z',
        workspace: 'fly',
        handler: () => {
          if (!windowDriven()) return false
          engine.throttleToHover()
        },
      }),
      registerHotkey({
        combo: 'h',
        workspace: 'fly',
        handler: () => {
          if (!windowDriven()) return false
          engine.toggleAssist()
        },
      }),
    ]

    const timer = setInterval(() => publish(engine.tick()), TICK_MS)
    return () => {
      clearInterval(timer)
      offs.forEach((f) => f())
      window.removeEventListener('pointerdown', gate, { capture: true })
      window.removeEventListener('keydown', gate, { capture: true })
      window.removeEventListener('gamepadconnected', gate)
      if (menuHandlers.possess === toggleKeyboardPossession) delete menuHandlers.possess
      window.removeEventListener('blur', onBlur)
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('keyup', onKeyUp)
      engine.dispose()
      liveWriter.clear()
      if (active === engine) active = null
    }
  }, [])
  return null
}
