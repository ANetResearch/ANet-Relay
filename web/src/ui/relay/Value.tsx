// Tabular value with an honest blank (02 §1.5): a formatted string, or "—" whose tooltip says why it is missing.
import * as React from 'react'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/ui/components/ui/tooltip'
import { DASH } from '@/lib/format'
import { cn } from '@/lib/utils'

export interface ValueProps {
  /** formatted value; null or undefined renders "—" */
  value: string | null | undefined
  /** why the value is missing; required so no blank is unexplained (02 §20 item 7) */
  reason: string
  className?: string
}

export const Value = React.memo(function Value({ value, reason, className }: ValueProps) {
  if (value !== null && value !== undefined) return <span className={cn('num', className)}>{value}</span>
  return (
    <Tooltip>
      <TooltipTrigger render={<span className={cn('num cursor-help text-[var(--g500)]', className)} data-blank="" aria-label={reason} />}>
        {DASH}
      </TooltipTrigger>
      <TooltipContent>{reason}</TooltipContent>
    </Tooltip>
  )
})

/** Label + value cell used in HUD rows and key-value grids (02 §2.2 roles). */
export function Readout({ label, children, size = 'value', className }: { label: string; children: React.ReactNode; size?: 'value' | 'kpi'; className?: string }) {
  return (
    <div className={cn('flex min-w-0 flex-col gap-0.5', className)}>
      <span className="cap">{label}</span>
      <span className={cn(size === 'kpi' ? 'text-hud-kpi font-medium text-[var(--g50)]' : 'text-ed-value text-[var(--g100)]')}>{children}</span>
    </div>
  )
}
