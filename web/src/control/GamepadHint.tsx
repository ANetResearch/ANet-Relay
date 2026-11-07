// Controller connection cards (02 §7.2) and the input notices on the stage (02 §5.3, §5.4, §7.3). Mounted once at
// the app root. Cards: L3, bottom centre 120 above the window edge, 400 x 88, at most two stacked 8 apart, 6 s
// with the timer paused on hover. Stage notices portal into the stage frame of the Fly workspace.
import * as React from 'react'
import { createPortal } from 'react-dom'
import { useShallow } from 'zustand/react/shallow'
import { t as tr, useT } from '@/i18n'
import { cn } from '@/lib/utils'
import { useRelay } from '@/store/relay'
import { getVehicle } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { useStage } from '@/stage/store'
import { useStageFrame } from './stageFrame'
import { Button } from '@/ui/components/ui/button'
import { Icon } from '@/ui/icons'
import { motionReduced } from '@/ui/motion/tier'
import { flyRingCourse } from './actions'
import { DualSenseGlyph } from './DualSenseGlyph'
import { useInputUi, type HintCard } from './store'
import { useCT } from './strings'
import './control.css'

const SHOW_MS = 6000

function HintCardView({ card, onDone }: { card: HintCard; onDone(): void }) {
  const t = useT()
  const hasSim = useRelay((s) => (s.fleet?.vehicles ?? []).some((v) => v.kind === 'sim'))
  const aircraft = useRelay((s) => (card.vehicle ? (s.fleet?.vehicles.find((v) => v.id === card.vehicle)?.label ?? card.vehicle) : ''))
  const [leaving, setLeaving] = React.useState(false)
  const remaining = React.useRef(SHOW_MS)
  const startedAt = React.useRef(0)
  const timer = React.useRef<ReturnType<typeof setTimeout> | null>(null)

  const leave = React.useCallback(() => {
    if (timer.current) clearTimeout(timer.current)
    if (motionReduced()) onDone()
    else {
      setLeaving(true)
      setTimeout(onDone, 250)
    }
  }, [onDone])
  const arm = React.useCallback(() => {
    startedAt.current = performance.now()
    timer.current = setTimeout(leave, remaining.current)
  }, [leave])
  React.useEffect(() => {
    arm()
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [arm])
  const pause = () => {
    if (!timer.current) return
    clearTimeout(timer.current)
    timer.current = null
    remaining.current = Math.max(800, remaining.current - (performance.now() - startedAt.current))
  }

  const title = card.model === 'dualsense' ? t('pad.dualsense') : card.model === 'standard' ? t('pad.generic') : t('pad.unrecognised')
  const line =
    card.state === 'flying' ? t('pad.flying', { n: card.pad, aircraft }) : card.state === 'holdOptions' ? t('pad.holdOptions', { n: card.pad }) : t('pad.noFree', { n: card.pad })
  return (
    <div
      role="status"
      className="pad-hint l3 pointer-events-auto flex h-[88px] w-[400px] max-w-[calc(100vw-32px)] items-center gap-4 rounded-[calc(var(--radius)*1.4)] px-4"
      data-leaving={leaving ? '' : undefined}
      data-pad-hint={card.pad}
      onPointerEnter={pause}
      onPointerLeave={() => !timer.current && !leaving && arm()}
    >
      <DualSenseGlyph />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <p className="truncate text-hud-title font-[560] text-[var(--g100)]">{title}</p>
        <p className="truncate text-hud-sub text-[var(--g400)]">{line}</p>
        <div className="mt-1.5 flex items-center gap-1.5">
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              void flyRingCourse(card.pad)
              leave()
            }}
          >
            <Icon icon="course" />
            {hasSim ? t('pad.flyCourse') : t('pad.addSimFly')}
          </Button>
          <Button size="sm" variant="ghost" className="text-[var(--g400)]" onClick={leave}>
            {t('action.dismiss')}
          </Button>
        </div>
      </div>
    </div>
  )
}

function Hints() {
  const hints = useInputUi((s) => s.hints)
  const dismiss = useInputUi((s) => s.dismissHint)
  const collapsed = useUi((s) => s.timelineCollapsed)
  const fly = useUi((s) => s.workspace === 'fly')
  if (!hints.length) return null
  // 120 above the window edge (02 §7.2) at the least, but never over the stage HUD or the toast row beneath it
  // (see RelayToaster: toasts clear the stage HUD in Fly, 24 above the timeline elsewhere; one toast is about 44 high)
  const lift = fly ? 'calc(8px + var(--stage-toast-lift, 88px) + 52px)' : '76px'
  const bottom = `max(120px, calc(${collapsed ? 'var(--timeline-h-collapsed)' : 'var(--timeline-h)'} + ${lift}))`
  return (
    <div className="pointer-events-none fixed left-1/2 z-[60] flex -translate-x-1/2 flex-col-reverse gap-2" style={{ bottom }} data-pad-hints="">
      {[...hints].reverse().map((h) => (
        <HintCardView key={h.key} card={h} onDone={() => dismiss(h.key)} />
      ))}
    </div>
  )
}

// ----------------------------------------------------------------------------------------------- stage notices

interface Notices {
  paused: boolean
  /** possessed aircraft with a lost link */
  lost: string[]
  /** aircraft waiting for a stick movement, with the reason */
  awaiting: [string, 'focus' | 'link'][]
  /** aircraft whose controller is disconnected, with the pad number */
  padLost: [string, number][]
  /** airborne with no controller (backend warn alert) */
  airborne: string[]
}

function useNotices(): Notices {
  const input = useInputUi(useShallow((s) => ({ paused: s.paused, awaiting: s.awaiting, padLost: s.padLost })))
  const lost = useRelay(
    useShallow((s) => (s.fleet?.vehicles ?? []).filter((v) => v.controller === 'human' && (v.link.state === 'lost' || v.link.state === 'offline')).map((v) => v.id)),
  )
  const airborne = useRelay(
    useShallow((s) =>
      (s.fleet?.alerts ?? []).filter((a) => a.code === 'airborne_no_controller' && a.vehicle && !a.acked && !s.localAcks.has(a.id)).map((a) => a.vehicle as string),
    ),
  )
  return {
    paused: input.paused,
    lost,
    awaiting: Object.entries(input.awaiting).filter(([id]) => !lost.includes(id)),
    padLost: Object.entries(input.padLost).map(([id, p]) => [id, p.pad] as [string, number]),
    airborne,
  }
}

const name = (id: string) => getVehicle(id)?.label ?? id

function Chip({ children, tone = 'info' }: { children: React.ReactNode; tone?: 'info' | 'warn' }) {
  return (
    <div className="hud-surface flex h-8 items-center gap-2 rounded-md px-3 text-ed-value text-[var(--g100)]" role="status">
      <Icon icon={tone === 'warn' ? 'alert' : 'info'} className={cn('size-3.5', tone === 'warn' ? 'text-[var(--g50)]' : 'text-[var(--g400)]')} />
      <span className="whitespace-nowrap">{children}</span>
    </div>
  )
}

function StageNotices() {
  const frame = useStageFrame()
  const ws = useUi((s) => s.workspace)
  // the course HUD (top centre, 56 high) is shown while running and briefly after it ends
  const courseActive = useStage((s) => s.courseHudShown)
  const n = useNotices()
  const t = useT()
  const ct = useCT()
  if (!frame || ws !== 'fly') return null
  const top: React.ReactNode[] = []
  if (n.paused) top.push(<Chip key="paused">{tr('stage.inputPaused')}</Chip>)
  for (const [id, why] of n.awaiting) {
    if (n.paused) break
    top.push(<Chip key={`aw-${id}`}>{why === 'link' ? t('stage.linkRestored') : `${name(id)} · ${ct('ctl.moveToResume')}`}</Chip>)
  }
  for (const id of n.airborne) top.push(<Chip key={`air-${id}`} tone="warn">{t('possess.airborneNoController', { aircraft: name(id) })}</Chip>)
  const cards: React.ReactNode[] = []
  for (const id of n.lost) cards.push(<p key={`lost-${id}`}>{t('stage.linkLost', { aircraft: name(id) })}</p>)
  for (const [id, pad] of n.padLost) cards.push(<p key={`pad-${id}`}>{t('stage.padDisconnected', { n: pad, aircraft: name(id) })}</p>)
  return createPortal(
    <>
      {top.length ? (
        <div className="pointer-events-none absolute left-1/2 z-[4] flex -translate-x-1/2 flex-col items-center gap-2" style={{ top: courseActive ? 16 + 56 + 8 : 16 }} data-input-notices="">
          {top}
        </div>
      ) : null}
      {cards.length ? (
        <div className="pointer-events-none absolute top-1/2 left-1/2 z-[4] -translate-1/2" data-input-card="">
          <div className="l3 flex min-w-[320px] flex-col gap-1 rounded-[calc(var(--radius)*1.4)] px-4 py-3 text-ed-value text-[var(--g100)]" role="alert">
            <span className="cap">{ct('ctl.sticks')}</span>
            {cards}
          </div>
        </div>
      ) : null}
    </>,
    frame,
  )
}

export function GamepadHints() {
  return (
    <>
      <Hints />
      <StageNotices />
    </>
  )
}
