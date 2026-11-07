// Status words and the link dot (02 §2.3). Every state carries a shape or a word, never colour alone.
import * as React from 'react'
import type { Vehicle } from '@/api'
import { useT } from '@/i18n'
import { cn } from '@/lib/utils'
import { wholeSeconds } from '@/lib/format'
import { armedTone } from '@/store/red'
import { useRedOwner } from '@/store/selectors'

export type LinkState = Vehicle['link']['state']

/** 8 x 8 glyph: solid (live), ring (delayed), slashed ring (lost), dim solid (offline), dashed ring (waiting). */
export const LinkDot = React.memo(function LinkDot({ state, className }: { state: LinkState; className?: string }) {
  return (
    <svg viewBox="0 0 8 8" className={cn('link-dot', className)} aria-hidden="true" data-link={state}>
      {state === 'ok' ? <circle cx="4" cy="4" r="3" fill="var(--g300)" /> : null}
      {state === 'delayed' ? <circle cx="4" cy="4" r="2.5" fill="none" stroke="var(--g300)" strokeWidth="1" /> : null}
      {state === 'lost' ? (
        <>
          <circle cx="4" cy="4" r="2.5" fill="none" stroke="var(--g50)" strokeWidth="1" />
          <path d="M1.4 6.6 6.6 1.4" stroke="var(--g50)" strokeWidth="1" strokeLinecap="round" />
        </>
      ) : null}
      {state === 'offline' ? <circle cx="4" cy="4" r="3" fill="var(--g600)" /> : null}
      {state === 'waiting' ? <circle cx="4" cy="4" r="2.5" fill="none" stroke="var(--g500)" strokeWidth="1" strokeDasharray="1.6 1.4" /> : null}
    </svg>
  )
})

/** "Live" · "Delayed 3.4 s" · "Lost 7 s" · "Offline" · "Waiting". `critical` paints a lost link red when red is free. */
export const LinkStatus = React.memo(function LinkStatus({
  link,
  critical = false,
  className,
}: {
  link: Pick<Vehicle['link'], 'state' | 'heartbeat_age_s'>
  critical?: boolean
  className?: string
}) {
  const t = useT()
  const owner = useRedOwner()
  const age = link.heartbeat_age_s
  let word: string
  switch (link.state) {
    case 'ok':
      word = t('status.live')
      break
    case 'delayed':
      word = age === null ? t('status.delayed') : t('status.delayedFor', { s: age.toFixed(1) })
      break
    case 'lost': {
      const s = wholeSeconds(age)
      word = s === null ? t('status.lost') : t('status.lostFor', { s })
      break
    }
    case 'offline':
      word = t('status.offline')
      break
    default:
      word = t('status.waiting')
  }
  const tone =
    link.state === 'lost'
      ? critical && owner === 'critical'
        ? 'text-[var(--r500)]'
        : 'text-[var(--g50)]'
      : link.state === 'ok' || link.state === 'delayed'
        ? 'text-[var(--g300)]'
        : 'text-[var(--g500)]'
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-hud-sub whitespace-nowrap', tone, className)} data-link={link.state}>
      <LinkDot state={link.state} />
      <span className="num">{word}</span>
    </span>
  )
})

/** "ARMED" in r500 while red belongs to armed; bold g50 while a critical alert owns red; "Disarmed" in g400. */
export const ArmedWord = React.memo(function ArmedWord({ armed, className }: { armed: boolean; className?: string }) {
  const t = useT()
  const owner = useRedOwner()
  if (!armed) return <span className={cn('text-hud-sub text-[var(--g400)]', className)}>{t('status.disarmed')}</span>
  const red = armedTone(owner) === 'red'
  return (
    <span
      data-armed=""
      className={cn('text-hud-sub tracking-[0.04em] whitespace-nowrap', red ? 'font-[560] text-[var(--r500)]' : 'font-bold text-[var(--g50)]', className)}
    >
      {t('status.armed')}
    </span>
  )
})

/** Mono "SIM" tag in a 1 px g600 frame (02 §2.3). */
export function SimTag({ className }: { className?: string }) {
  const t = useT()
  return <span className={cn('sim-tag', className)}>{t('kind.sim')}</span>
}

/** Card / lane status bar colour for one vehicle (02 §4.3 left 2 px bar). */
export function useStatusBar(v: Pick<Vehicle, 'armed' | 'link'> | null, hasCritical: boolean): 'armed-red' | 'armed-strong' | 'critical-red' | 'lost' | 'idle' {
  const owner = useRedOwner()
  if (!v) return 'idle'
  if (hasCritical && owner === 'critical') return 'critical-red'
  if (v.link.state === 'lost') return 'lost'
  if (v.armed) return owner === 'armed' ? 'armed-red' : 'armed-strong'
  return 'idle'
}
