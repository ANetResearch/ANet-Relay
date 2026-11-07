// Alert centre (02 §14): popover list opened from the fleet summary. Each alert can be acknowledged; acknowledging
// only removes the visual interruption, never the state.
import type { Alert } from '@/api'
import { pickText, useLang, useT } from '@/i18n'
import { cn } from '@/lib/utils'
import { criticalTone } from '@/store/red'
import { useAlerts, useRedOwner } from '@/store/selectors'
import { useRelay } from '@/store/relay'
import { Button } from '@/ui/components/ui/button'
import { ScrollArea } from '@/ui/components/ui/scroll-area'
import { useAgo } from '@/ui/relay'
import { ackAlert } from './actions'

const RANK: Record<Alert['level'], number> = { critical: 0, warn: 1, info: 2 }

function AlertRow({ a }: { a: Alert }) {
  const t = useT()
  const lang = useLang()
  const owner = useRedOwner()
  const localAck = useRelay((s) => s.localAcks.has(a.id))
  const label = useRelay((s) => (a.vehicle ? (s.fleet?.vehicles.find((v) => v.id === a.vehicle)?.label ?? a.vehicle) : t('alerts.station')))
  const ago = useAgo(a.since)
  const acked = a.acked || localAck
  const bar =
    a.level === 'critical' && !acked ? (criticalTone(owner) === 'red' ? 'bg-[var(--r500)]' : 'bg-[var(--g50)]') : a.level === 'warn' ? 'bg-[var(--g300)]' : 'bg-[var(--g600)]'
  return (
    <li className={cn('relative flex gap-3 py-2.5 pr-3 pl-4', acked && 'opacity-60')} data-alert={a.level}>
      <i className={cn('absolute top-2.5 bottom-2.5 left-0 w-0.5 rounded-full', bar)} aria-hidden="true" />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex items-center gap-2">
          <span className="cap">{t(`alerts.level.${a.level}`)}</span>
          <span className="num truncate text-[11px] text-[var(--g300)]">{label}</span>
          <span className="flex-1" />
          <span className="num text-[11px] text-[var(--g500)]">{ago}</span>
        </div>
        <p className="text-ed-value text-[var(--g100)]">{pickText(lang, a.text_en, a.text_zh)}</p>
      </div>
      <div className="flex shrink-0 items-start pt-3">
        {acked ? (
          <span className="text-hud-sub text-[var(--g500)]">{t('alerts.acknowledged')}</span>
        ) : (
          <Button size="sm" variant="outline" onClick={() => ackAlert(a.id)}>
            {t('action.acknowledge')}
          </Button>
        )}
      </div>
    </li>
  )
}

export function AlertCenter() {
  const t = useT()
  const alerts = useAlerts()
  const sorted = [...alerts].sort((x, y) => Number(x.acked) - Number(y.acked) || RANK[x.level] - RANK[y.level] || y.since - x.since)
  const open = alerts.filter((a) => !a.acked).length
  return (
    <div className="flex max-h-[min(520px,70vh)] flex-col">
      <header className="flex h-10 shrink-0 items-center gap-2 border-b border-[var(--border)] px-4">
        <span className="text-hud-title font-[560] text-[var(--g100)]">{t('alerts.title')}</span>
        <span className="num text-hud-sub text-[var(--g500)]">{open}</span>
      </header>
      {sorted.length === 0 ? (
        <div className="flex flex-col gap-1 px-4 py-6">
          <p className="text-ed-value text-[var(--g200)]">{t('alerts.empty')}</p>
          <p className="text-hud-sub text-[var(--g500)]">{t('alerts.emptyBody')}</p>
        </div>
      ) : (
        <ScrollArea className="min-h-0 flex-1">
          <ul className="divide-y divide-[var(--border)]">
            {sorted.map((a) => (
              <AlertRow key={a.id} a={a} />
            ))}
          </ul>
        </ScrollArea>
      )}
      <footer className="border-t border-[var(--border)] px-4 py-2 text-hud-sub text-[var(--g500)]">{t('alerts.ackNote')}</footer>
    </div>
  )
}
