// Inspector Log (02 §11, R-13): the aircraft's status texts and Relay events (possession, handback, mode, arming,
// alerts), newest first, with MAV_SEVERITY words; CRIT and above follow the single-red rule.
import * as React from 'react'
import { request, type RelayEvent, type Vehicle } from '@/api'
import { pickText, useLang } from '@/i18n'
import { clock } from '@/lib/format'
import { cn } from '@/lib/utils'
import { criticalTone } from '@/store/red'
import { useRelay } from '@/store/relay'
import { useRedOwner, useVehicleField } from '@/store/selectors'
import { useT8, type AnyKey } from '@/fleet/copy'
import { usePoll } from './usePoll'

export interface LogEntry {
  key: string
  t: number
  severity: number
  source: 'autopilot' | 'relay'
  text: string
}

/** MAV_SEVERITY for Relay events (0 EMERG .. 7 DEBUG). */
const EVENT_SEVERITY: Partial<Record<RelayEvent['kind'], number>> = {
  crashed: 2,
  battery_critical: 2,
  link_lost: 3,
  skill_failed: 3,
  course_aborted: 4,
  battery_warn: 4,
  link_delayed: 4,
  airborne_no_controller: 4,
  stop: 4,
  stop_all: 4,
  command_ack: 7,
}

/** Merges events (window ring and fetched history) with the snapshot's status texts; newest first, deduplicated. */
export function buildLog(events: readonly RelayEvent[], statustext: Vehicle['statustext'], lang: 'en' | 'zh', limit = 400): LogEntry[] {
  const out = new Map<string, LogEntry>()
  for (const e of events) {
    if (e.kind === 'statustext') {
      const d = e.data as { severity?: number; text?: string }
      const text = String(d.text ?? pickText(lang, e.text_en, e.text_zh))
      const key = `s|${Math.round(e.t * 10)}|${text}`
      out.set(key, { key, t: e.t, severity: typeof d.severity === 'number' ? d.severity : 6, source: 'autopilot', text })
    } else {
      const key = `e|${e.seq}`
      out.set(key, { key, t: e.t, severity: EVENT_SEVERITY[e.kind] ?? 6, source: 'relay', text: pickText(lang, e.text_en, e.text_zh) })
    }
  }
  for (const s of statustext) {
    const key = `s|${Math.round(s.t * 10)}|${s.text}`
    if (!out.has(key)) out.set(key, { key, t: s.t, severity: s.severity, source: 'autopilot', text: s.text })
  }
  return [...out.values()].sort((a, b) => b.t - a.t).slice(0, limit)
}

const EMPTY: never[] = []

export function LogTab({ vehicleId }: { vehicleId: string }) {
  const t = useT8()
  const lang = useLang()
  const owner = useRedOwner()
  const statustext = useVehicleField(vehicleId, (v) => v.statustext) ?? EMPTY
  const ring = useRelay((s) => s.events)
  const history = usePoll(
    () => request<{ events: RelayEvent[] }>('GET', `/events?vehicle=${encodeURIComponent(vehicleId)}&tail=true&limit=300`),
    0,
    vehicleId,
  )
  const entries = React.useMemo(() => {
    const own = ring.filter((e) => e.vehicle === vehicleId)
    const past = history.data?.events ?? []
    return buildLog([...past, ...own], statustext, lang)
  }, [ring, history.data, statustext, vehicleId, lang])

  if (!entries.length) return <p className="px-3 py-3 text-hud-sub text-[var(--g500)]">{t('log.empty')}</p>
  return (
    <ol className="flex flex-col" data-inspector-log="">
      {entries.map((e) => {
        const sev = Math.max(0, Math.min(7, e.severity))
        const hot = sev <= 2
        return (
          <li key={e.key} className="grid grid-cols-[56px_44px_1fr] items-start gap-2 border-b border-dotted border-[var(--border)] px-3 py-1.5" data-sev={sev}>
            <span className="num text-[10px] leading-4 text-[var(--g500)]">{clock(e.t)}</span>
            <span
              className={cn(
                'num text-[10px] leading-4 tracking-[0.04em]',
                hot ? (criticalTone(owner) === 'red' ? 'text-[var(--r500)]' : 'font-bold text-[var(--g50)]') : sev <= 4 ? 'text-[var(--g100)]' : sev === 7 ? 'text-[var(--g600)]' : 'text-[var(--g400)]',
              )}
            >
              {t(`sev.${sev}` as AnyKey)}
            </span>
            <span className="min-w-0 text-[11px] leading-4 break-words" data-selectable="">
              <span className={e.source === 'autopilot' ? 'text-[var(--g100)]' : 'text-[var(--g300)]'}>{e.text}</span>
              <span className="ml-1.5 text-[10px] text-[var(--g600)]">{t(e.source === 'autopilot' ? 'log.source.autopilot' : 'log.source.relay')}</span>
            </span>
          </li>
        )
      })}
    </ol>
  )
}
