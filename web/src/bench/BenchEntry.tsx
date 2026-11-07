// Control tab entry (05 §4.5): one group under the preflight checklist with "Run bench check", which opens the Bench
// tab. While a run is active it shows the procedure and the step; otherwise the last verdict from the snapshot brief.
import { useInspectorTab } from '@/inspector/tabs'
import { cn } from '@/lib/utils'
import { Button } from '@/ui/components/ui/button'
import { Icon } from '@/ui/icons'
import { Group } from '@/ui/relay'
import { useCatalogue, useBrief } from './useProcedure'
import { isActive, stateTone } from './model'
import { useTB, type KeyB } from './copy'
import { pickText, useLang } from '@/i18n'
import './bench.css'

export function BenchEntry({ vehicleId }: { vehicleId: string }) {
  const t = useTB()
  const lang = useLang()
  const brief = useBrief(vehicleId)
  const cat = useCatalogue()
  const setTab = useInspectorTab((s) => s.setTab)
  const entry = brief ? cat?.procedures.find((p) => p.id === brief.procedure) : undefined
  const title = entry ? pickText(lang, entry.title_en, entry.title_zh) : (brief?.procedure ?? '')
  const active = isActive(brief?.state)
  const status = !brief
    ? null
    : active
      ? t('bench.entry.active', { procedure: title, index: Math.min(brief.index + 1, brief.total), total: brief.total })
      : t('bench.entry.last', { procedure: title, verdict: t(`bench.state.${brief.state}` as KeyB) })
  return (
    <Group label={t('bench.entry.title')}>
      <div className="flex flex-col gap-2 px-3" data-bench-entry={vehicleId}>
        <p className="text-hud-sub text-[var(--g400)]">{t('bench.entry.note')}</p>
        {status ? <p className={cn('text-hud-sub', brief ? `bench-tone-${stateTone(brief.state)}` : '')}>{status}</p> : null}
        <Button variant="outline" className="self-start" onClick={() => setTab('bench')} data-bench-action="open">
          <Icon icon="activity" />
          {active ? t('bench.entry.open') : t('bench.entry.run')}
        </Button>
      </div>
    </Group>
  )
}
