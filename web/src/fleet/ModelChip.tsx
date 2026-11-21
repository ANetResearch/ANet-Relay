// Model tag of a fleet card (12 §2.2): says at a glance whether an aircraft is the large ANetDU7 or the small
// quad, and lets the operator choose it for a real aircraft right where it was found. A real aircraft nobody has
// chosen a model for (vehicle.model_confirmed false) shows a bright "Model?" tag; choosing either entry confirms it.
import * as React from 'react'
import { api, type Vehicle } from '@/api'
import { cn } from '@/lib/utils'
import { notifyError } from '@/shell/notify'
import { useHello } from '@/store/selectors'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/ui/components/ui/dropdown-menu'
import { modelOf } from '@/stage/models'
import { useT8 } from './copy'

type Model = Vehicle['model']
const SHORT: Record<Model, string> = { 'anet-du7': 'DU7', micro: 'Micro' }
const TITLE: Record<Model, string> = { 'anet-du7': 'ANetDU7', micro: 'Micro 120' }
const HINT = { 'anet-du7': 'fleet.model.du7', micro: 'fleet.model.micro' } as const
const CHIP = 'shrink-0 rounded-sm border px-1 text-[10px] leading-[14px] font-[560] tracking-wide'

export function ModelChip({ v }: { v: Vehicle }) {
  const t = useT8()
  const operator = Boolean(useHello()?.operator)
  const [busy, setBusy] = React.useState(false)
  const model = modelOf(v)
  const confirmed = v.model_confirmed !== false
  if (v.kind !== 'real')
    return (
      <span className={cn(CHIP, 'border-transparent text-[var(--g500)]')} title={`${TITLE[model]} · ${t('fleet.model.simHint')}`} data-model-chip={model}>
        {SHORT[model]}
      </span>
    )
  const choose = (m: Model) => {
    if (m === model && confirmed) return
    setBusy(true)
    api
      .setModel(v.id, m)
      .catch(notifyError)
      .finally(() => setBusy(false))
  }
  const title = !operator
    ? t('fleet.model.operatorOnly')
    : confirmed
      ? `${TITLE[model]} · ${t(HINT[model])}`
      : t('fleet.model.unconfirmed', { model: TITLE[model] })
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <button
            type="button"
            disabled={busy || !operator}
            title={title}
            aria-label={t('fleet.model.title')}
            data-model-chip={model}
            data-model-confirmed={confirmed ? '' : undefined}
            onClick={(e) => e.stopPropagation()}
            onDoubleClick={(e) => e.stopPropagation()}
            className={cn(
              CHIP,
              'cursor-pointer enabled:hover:border-[var(--g300)] enabled:hover:text-[var(--g50)]',
              confirmed ? 'border-[var(--g700)] text-[var(--g300)]' : 'border-[var(--g200)] bg-[var(--g100)] text-[var(--g950)] enabled:hover:text-[var(--g950)]',
            )}
          />
        }
      >
        {confirmed ? SHORT[model] : t('fleet.model.pick')}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="l3 w-64" onClick={(e) => e.stopPropagation()}>
        <DropdownMenuGroup>
          <DropdownMenuLabel>{t('fleet.model.title')}</DropdownMenuLabel>
          {(['anet-du7', 'micro'] as const).map((m) => (
            <DropdownMenuItem key={m} onClick={() => choose(m)} data-model-option={m} className="flex-col items-start gap-0">
              <span className="text-[12px] text-[var(--g50)]">
                {TITLE[m]}
                {confirmed && m === model ? ' ✓' : ''}
              </span>
              <span className="text-[11px] text-[var(--g400)]">{t(HINT[m])}</span>
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
