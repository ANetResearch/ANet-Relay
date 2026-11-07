// Critical alert bar (02 §14): under the top bar, 32 high, g900, a 2 px left bar in r500 (single-red rule), g50
// text, Acknowledge on the right. Only the newest unacknowledged critical alert is shown, with "+n" for the rest.
import { pickText, useLang, useT } from '@/i18n'
import { useOpenAlerts, useRedOwner } from '@/store/selectors'
import { useRelay } from '@/store/relay'
import { criticalTone } from '@/store/red'
import { useUi } from '@/store/ui'
import { Button } from '@/ui/components/ui/button'
import { Icon } from '@/ui/icons'
import { useAgo } from '@/ui/relay'
import { ackAlert } from './actions'

export function AlertBar() {
  const t = useT()
  const lang = useLang()
  const crit = useOpenAlerts('critical')
  const owner = useRedOwner()
  const a = crit[0]
  const label = useRelay((s) => (a?.vehicle ? (s.fleet?.vehicles.find((v) => v.id === a.vehicle)?.label ?? a.vehicle) : null))
  const ago = useAgo(a?.since)
  const openCenter = useUi((s) => s.setAlertCenterOpen)
  if (!a) return null
  const text = pickText(lang, a.text_en, a.text_zh)
  return (
    <div className="alert-bar enter" role="alert" data-tone={criticalTone(owner)} data-alert-bar="">
      <Icon icon="alert.critical" className="size-4 shrink-0 text-[var(--g50)]" />
      {label && !text.includes(label) ? <span className="num shrink-0 text-[11px] text-[var(--g300)]">{label}</span> : null}
      <span className="min-w-0 flex-1 truncate text-ed-value font-[500] text-[var(--g50)]">{text}</span>
      <span className="num shrink-0 text-[11px] text-[var(--g500)]">{ago}</span>
      {crit.length > 1 ? (
        <button
          type="button"
          onClick={() => openCenter(true)}
          className="num shrink-0 rounded-sm px-1.5 text-[11px] text-[var(--g300)] ring-1 ring-[var(--g700)] hover:text-[var(--g50)]"
        >
          {t('alerts.more', { n: crit.length - 1 })}
        </button>
      ) : null}
      <Button size="sm" variant="outline" className="shrink-0" onClick={() => ackAlert(a.id)}>
        {t('action.acknowledge')}
      </Button>
    </div>
  )
}
