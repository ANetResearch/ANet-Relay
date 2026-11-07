// Controller pill (02 §2.3, §5.4). YOU · PAD 1 (g50 on g950) / AGENT · harness-a (g800, dashed g500) /
// harness-a · waiting / PAD 1 · disconnected / "Idle" text. A change of controller morphs the width (fast 250 ms)
// and cross-fades the label; reduced motion replaces it directly.
import * as React from 'react'
import type { Vehicle } from '@/api'
import { useT } from '@/i18n'
import { cn } from '@/lib/utils'
import { Icon } from '@/ui/icons'
import { motionReduced } from '@/ui/motion/tier'

export type PillVariant = 'human' | 'agent' | 'waiting' | 'disconnected' | 'idle'

export interface PillSpec {
  variant: PillVariant
  label: string
}

type Ctl = Pick<Vehicle, 'controller' | 'claim' | 'possession'>

/** Input source label: KEYBOARD, SCREEN, PAD 1. */
export function useInputLabel(): (p: NonNullable<Vehicle['possession']>) => string {
  const t = useT()
  return React.useCallback(
    (p) => (p.input === 'keyboard' ? t('input.keyboard') : p.input === 'screen' ? t('input.screen') : t('input.pad', { n: p.pad ?? 1 })),
    [t],
  )
}

/** The pill for the vehicle's current controller. `padDisconnected` comes from the InputLayer (02 §7.3). */
export function usePillSpec(v: Ctl, padDisconnected = false): PillSpec {
  const t = useT()
  const inputLabel = useInputLabel()
  if (v.controller === 'human' && v.possession) {
    if (padDisconnected && v.possession.input === 'gamepad') {
      return { variant: 'disconnected', label: `${inputLabel(v.possession)} · ${t('ctrl.disconnected')}` }
    }
    return { variant: 'human', label: `${t('ctrl.you')} · ${inputLabel(v.possession)}` }
  }
  if (v.controller === 'agent' && v.claim) return { variant: 'agent', label: `${t('ctrl.agent')} · ${v.claim.name}` }
  return { variant: 'idle', label: t('status.idle') }
}

/** Secondary pill shown next to a human pill while an agent's claim is suspended. */
export function useWaitingSpec(v: Ctl): PillSpec | null {
  const t = useT()
  if (v.claim && (v.claim.suspended || v.controller === 'human')) return { variant: 'waiting', label: `${v.claim.name} · ${t('ctrl.waiting')}` }
  return null
}

interface Layer {
  key: string
  spec: PillSpec
  leaving: boolean
  entering: boolean
}

/** Low-level morphing pill: give it a spec, it animates between specs. */
export const MorphPill = React.memo(function MorphPill({ spec, className }: { spec: PillSpec; className?: string }) {
  const key = `${spec.variant}|${spec.label}`
  const [layers, setLayers] = React.useState<Layer[]>(() => [{ key, spec, leaving: false, entering: false }])
  const [shownKey, setShownKey] = React.useState(key)
  const root = React.useRef<HTMLSpanElement>(null)
  const measure = React.useRef<HTMLSpanElement>(null)

  // adjust state during render when the controller changes (no effect round trip)
  if (key !== shownKey) {
    setShownKey(key)
    setLayers((ls) =>
      motionReduced()
        ? [{ key, spec, leaving: false, entering: false }]
        : [...ls.filter((l) => !l.leaving).map((l) => ({ ...l, leaving: true, entering: false })), { key, spec, leaving: false, entering: true }],
    )
  }

  React.useEffect(() => {
    if (!layers.some((l) => l.leaving)) return
    const tm = setTimeout(() => setLayers((ls) => ls.filter((l) => !l.leaving)), 260)
    return () => clearTimeout(tm)
  }, [layers])

  // width follows the new label; the CSS transition on .pill animates it
  React.useLayoutEffect(() => {
    const el = measure.current
    const r = root.current
    if (el && r) r.style.width = `${el.offsetWidth + 2}px`
  }, [key])

  if (spec.variant === 'idle') {
    // keyed apart from the pill: a reused node would keep the pill's measured inline width
    return (
      <span key="idle" className={cn('text-hud-sub text-[var(--g500)]', className)}>
        {spec.label}
      </span>
    )
  }
  return (
    <span key="pill" ref={root} className={cn('pill', className)} data-variant={spec.variant}>
      {layers.map((l) => (
        <span key={l.key} data-leaving={l.leaving ? '' : undefined} data-entering={l.entering ? '' : undefined} aria-hidden={l.leaving || undefined}>
          {l.spec.variant === 'waiting' ? <Icon icon="hold" /> : null}
          {l.spec.label}
        </span>
      ))}
      <span ref={measure} aria-hidden="true" className="invisible absolute">
        {spec.variant === 'waiting' ? <Icon icon="hold" /> : null}
        {spec.label}
      </span>
    </span>
  )
})

/** Controller pill for a vehicle, optionally followed by the waiting pill of a suspended claim. */
export function ControllerPill({ vehicle, showWaiting = false, compactWaiting = false, padDisconnected = false, className }: {
  vehicle: Ctl
  showWaiting?: boolean
  /** Narrow places (fleet card, inspector header): the waiting claim becomes an icon-only pill with a tooltip. */
  compactWaiting?: boolean
  padDisconnected?: boolean
  className?: string
}) {
  const spec = usePillSpec(vehicle, padDisconnected)
  const waiting = useWaitingSpec(vehicle)
  const shown = showWaiting && waiting && spec.variant !== 'idle'
  return (
    <span className={cn('inline-flex min-w-0 items-center gap-1.5', className)}>
      <MorphPill spec={spec} />
      {shown && compactWaiting ? (
        <span className="pill shrink-0" data-variant="waiting" title={waiting.label} aria-label={waiting.label} role="img" data-waiting-compact="">
          <span className="!px-[4px]">
            <Icon icon="hold" />
          </span>
        </span>
      ) : shown ? (
        <MorphPill spec={waiting} />
      ) : null}
    </span>
  )
}
