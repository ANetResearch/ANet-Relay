// Battery bar (02 §4.3): five 6 x 8 cells; warn blinks the last lit cell; critical follows the single-red rule.
import * as React from 'react'
import type { Vehicle } from '@/api'
import { batteryCells, percent, volts } from '@/lib/format'
import { cn } from '@/lib/utils'
import { criticalTone } from '@/store/red'
import { useRedOwner } from '@/store/selectors'
import { useT } from '@/i18n'
import { Value } from './Value'

type Battery = Vehicle['battery']

export const BatteryBar = React.memo(function BatteryBar({ battery, className }: { battery: Battery; className?: string }) {
  const owner = useRedOwner()
  const lit = batteryCells(battery)
  const cells = Array.from({ length: 5 }, (_, i) => i)
  return (
    <span
      className={cn('battery', className)}
      data-level={battery.level}
      data-tone={criticalTone(owner)}
      role="img"
      aria-label={percent(battery.remaining_pct) ?? volts(battery.voltage) ?? '—'}
    >
      {cells.map((i) => (
        <i key={i} data-on={lit !== null && i < lit ? '' : undefined} data-last={lit !== null && lit > 0 && i === lit - 1 ? '' : undefined} />
      ))}
    </span>
  )
})

/** Bar + "11.6 V 62 %" (02 §4.3 card row 3). */
export const BatteryReadout = React.memo(function BatteryReadout({ battery, className }: { battery: Battery; className?: string }) {
  const t = useT()
  return (
    <span className={cn('inline-flex items-center gap-2 text-[11px] leading-4', className)}>
      <BatteryBar battery={battery} />
      <Value value={volts(battery.voltage)} reason={t('value.reason.notSent', { msg: 'SYS_STATUS' })} className="text-[var(--g200)]" />
      <Value value={percent(battery.remaining_pct)} reason={t('value.reason.notSent', { msg: 'BATTERY_STATUS' })} className="text-[var(--g400)]" />
    </span>
  )
})
