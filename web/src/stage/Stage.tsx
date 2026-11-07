// The three-dimensional stage (02 §4.4, §6). Scene in ./scene, HUD overlays in ./Overlays. Owns the view hotkey C,
// the view commands, the stage reaction to the input layer's window events (touchpad view cycle, pad-bound ripple),
// the start view and chase-on-course preferences, and the replay presentation of records/replayState.ts.
// Contract: web/CONTRACTS.md#stage.
import * as React from 'react'
import type { ReactNode } from 'react'
import { clock } from '@/lib/format'
import { useReplay } from '@/records/replayState'
import { usePrefs } from '@/settings/prefs'
import { registerCommands } from '@/shell/commands'
import { registerHotkey } from '@/shell/keyboard'
import { notify } from '@/shell/notify'
import { useRelay } from '@/store/relay'
import { useUi } from '@/store/ui'
import { Icon } from '@/ui/icons'
import { isActive } from '@/course/geometry'
import { t6, useT6 } from './copy'
import { live, startLive } from './live'
import { framingExtras } from './scene/CameraRig'
import { rig } from './scene/rigState'
import { HudStrip, IdentityBlock, StickPanel, useStickPanelShown, ViewTools } from './Overlays'
import { DRIVING_HUD_H, DrivingHud } from './DrivingHud'
import { chaseTargetId } from './scene/CameraRig'
import { StageCanvas } from './scene/StageCanvas'
import { stagePulse, useStage, type ViewMode } from './store'
import './stage.css'

export interface StageProps {
  /** 3D layers rendered inside the stage scene (CourseLayer) */
  children?: ReactNode
}

/** Window events of the input layer (control/actions.ts). */
const VIEW_CYCLE_EVENT = 'relay:view-cycle'
const PAD_BOUND_EVENT = 'relay:pad-bound'

const COURSE_HUD_H = 56
/** Stage stick panel: 72 boxes, key row and padding. */
const STICK_PANEL_H = 116

function useStageSize(ref: React.RefObject<HTMLDivElement | null>): { w: number; h: number } {
  const [size, setSize] = React.useState({ w: 800, h: 600 })
  React.useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () => {
      const r = el.getBoundingClientRect()
      const w = Math.round(r.width / 4) * 4
      const h = Math.round(r.height / 4) * 4
      setSize((s) => (s.w === w && s.h === h ? s : { w, h }))
    }
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref])
  return size
}

/** Height the bottom HUD strip must clear when the video picture-in-picture sits under it (0 when it does not). */
function usePipClearance(ref: React.RefObject<HTMLDivElement | null>, stripWidth: number): number {
  const [clear, setClear] = React.useState(0)
  React.useEffect(() => {
    const el = ref.current
    if (!el) return
    const check = () => {
      const frame = el.parentElement
      const pip = frame?.querySelector<HTMLElement>('[data-video-pip]')
      let next = 0
      if (pip && frame) {
        const f = frame.getBoundingClientRect()
        const p = pip.getBoundingClientRect()
        const stripLeft = f.left + (f.width - stripWidth) / 2
        const stripRight = stripLeft + stripWidth
        const overlapsX = p.width > 0 && p.left < stripRight && p.right > stripLeft
        const nearBottom = f.bottom - p.top < f.height / 2
        if (overlapsX && nearBottom) next = Math.round(f.bottom - p.top) + 8
      }
      setClear((c) => (c === next ? c : next))
    }
    check()
    const timer = setInterval(check, 500)
    return () => clearInterval(timer)
  }, [ref, stripWidth])
  return clear
}

function ReplayBadge() {
  const t = useT6()
  const ts = useReplay((r) => (r.active ? (r.frame?.t ?? r.t) : null))
  if (ts === null) return null
  return (
    <div className="stage-hud stage-replay" data-stage-replay="">
      <Icon icon="tl.play" className="size-3.5 text-[var(--g400)]" />
      <span className="num text-[12px] text-[var(--g100)]">{t('stage.replay', { t: clock(ts) ?? '—' })}</span>
    </div>
  )
}

function useStageBindings(): void {
  // C cycles Overview -> Chase -> Top; the touchpad press of a pad does the same through a window event
  React.useEffect(() => {
    const cycle = () => useStage.getState().cycleView(chaseTargetId() !== null)
    const offKey = registerHotkey({ combo: 'c', workspace: 'fly', handler: cycle })
    const onPad = (e: Event) => {
      const d = (e as CustomEvent<{ vehicle?: string }>).detail
      if (d?.vehicle) stagePulse(d.vehicle)
    }
    window.addEventListener(VIEW_CYCLE_EVENT, cycle)
    window.addEventListener(PAD_BOUND_EVENT, onPad)
    const offCmd = registerCommands(() => {
      const views: { id: ViewMode; label: string }[] = [
        { id: 'overview', label: t6('stage.view.overview') },
        { id: 'chase', label: t6('stage.view.chase') },
        { id: 'top', label: t6('stage.view.top') },
      ]
      const chaseOk = chaseTargetId() !== null
      return [
        ...views
          .filter((v) => v.id !== 'chase' || chaseOk)
          .map((v) => ({
            id: `stage.view.${v.id}`,
            group: 'view' as const,
            label: t6('stage.cmdView', { view: v.label }),
            icon: v.id === 'chase' ? ('cam.follow' as const) : v.id === 'top' ? ('grid' as const) : ('scan.frame' as const),
            shortcut: 'C',
            run: () => {
              useUi.getState().setWorkspace('fly')
              useStage.getState().setView(v.id)
            },
          })),
        { id: 'stage.reframe', group: 'view' as const, label: t6('stage.cmdReframe'), icon: 'scan.frame' as const, run: () => useStage.getState().setView('overview') },
      ]
    })
    return () => {
      offKey()
      offCmd()
      window.removeEventListener(VIEW_CYCLE_EVENT, cycle)
      window.removeEventListener(PAD_BOUND_EVENT, onPad)
    }
  }, [])

  // start view preference, once per mount (chase falls back to overview until there is something to chase)
  React.useEffect(() => {
    const v = usePrefs.getState().startView
    if (v && v !== useStage.getState().view) useStage.getState().setView(v)
  }, [])

  // chase on course (02 §6.5): a human-flown aircraft starting a course switches to Chase unless turned off
  React.useEffect(() => {
    const active = new Map<string, boolean>()
    let last = useRelay.getState().fleet
    return useRelay.subscribe((s) => {
      if (s.fleet === last) return
      last = s.fleet
      for (const v of s.fleet?.vehicles ?? []) {
        const now = isActive(v.course)
        const was = active.get(v.id) ?? now
        active.set(v.id, now)
        if (!now || was || v.controller !== 'human') continue
        const sel = useUi.getState().selectedId
        if (sel && sel !== v.id) continue
        if (!usePrefs.getState().chaseOnCourse) continue
        if (!v.capabilities.position) continue
        useStage.getState().setView('chase')
      }
    })
  }, [])
}

export function Stage({ children }: StageProps) {
  const root = React.useRef<HTMLDivElement>(null)
  const { w } = useStageSize(root)
  const replaying = useReplay((r) => r.active)
  const courseHud = useStage((s) => s.courseHudShown)
  const [modelFailed, setModelFailed] = React.useState(false)
  const t = useT6()

  React.useEffect(() => startLive(), [])
  React.useEffect(() => {
    // development probe for screenshot and e2e scripts (never in production builds)
    if (import.meta.env.DEV) Object.assign(window, { __relayStage: { live, rig, framingExtras, stage: useStage } })
  }, [])
  useStageBindings()

  React.useEffect(() => {
    if (modelFailed) notify(t6('stage.modelFallback'), { level: 'warn' })
  }, [modelFailed])

  // on narrow stages the 480-wide course HUD (top centre) would collide with the corner blocks: move them below it
  const topShift = courseHud && !replaying && w < 480 + 2 * 260 ? 16 + COURSE_HUD_H + 8 : 16
  const stripWidth = Math.min(640, w - 32)
  const pipClear = usePipClearance(root, stripWidth)
  const stripBottom = Math.max(16, pipClear)
  // the stick panel sits bottom left; lift it over the strip when the two would overlap
  const sticksLifted = (w - stripWidth) / 2 < 200 + 16
  const sticksBottom = sticksLifted ? stripBottom + 64 + 8 : 16
  // toasts and hint cards (window bottom centre) clear the strip, and the stick panel when it is lifted over it
  const sticksShown = useStickPanelShown()
  // WP-C: the driving HUD (05 §6.4) sits on top of the stick panel and lifts with it
  const hudBottom = sticksBottom + (sticksShown ? STICK_PANEL_H + 8 : 0)
  const toastLift = !replaying && sticksShown && sticksLifted ? hudBottom + DRIVING_HUD_H + 8 : stripBottom + 64 + 8
  React.useEffect(() => {
    if (replaying) return
    const root = document.documentElement
    root.style.setProperty('--stage-toast-lift', `${toastLift}px`)
    return () => {
      root.style.removeProperty('--stage-toast-lift')
    }
  }, [toastLift, replaying])

  return (
    <div
      ref={root}
      className="absolute inset-0"
      data-stage=""
      aria-label={t('stage.viewSwitch')}
      // pointing at the stage hands the keyboard back to flight keys (the canvas itself takes no focus)
      onPointerDown={() => {
        const el = document.activeElement
        if (el instanceof HTMLElement && el !== document.body && !el.closest('[data-stage]')) el.blur()
      }}
    >
      <StageCanvas onModelError={() => setModelFailed(true)}>{children}</StageCanvas>
      <div className="stage-overlay">
        {replaying ? (
          <ReplayBadge />
        ) : (
          <>
            <IdentityBlock top={topShift} />
            <ViewTools top={topShift} />
            <HudStrip width={w} bottom={stripBottom} />
            <StickPanel bottom={sticksBottom} />
            <DrivingHud bottom={hudBottom} />
          </>
        )}
      </div>
    </div>
  )
}
