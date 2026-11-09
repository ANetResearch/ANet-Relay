// "已坠毁 · 复位" next to a crashed simulator (v0.5.4 operator request): on the stage card and the aircraft label.
// The gamepad Circle hold, the fleet card menu, the inspector and the command palette reset the same way.
import * as React from 'react'
import { cn } from '@/lib/utils'
import { useVehicle } from '@/store/selectors'
import { Icon } from '@/ui/icons'
import { resetSim } from './actions'
import { canResetSim } from './model'
import { useTT } from './strings'

export function CrashedReset({ id, compact = false, className }: { id: string; compact?: boolean; className?: string }) {
  const t = useTT()
  const v = useVehicle(id)
  const [sent, setSent] = React.useState(false)
  if (!v || v.kind !== 'sim' || !v.crashed) return null
  const ok = canResetSim(v) && !sent
  return (
    <button
      type="button"
      disabled={!ok}
      onClick={(e) => {
        e.stopPropagation()
        setSent(true)
        void resetSim(id).finally(() => setSent(false))
      }}
      className={cn(
        'pointer-events-auto inline-flex items-center gap-1 rounded-sm border border-[var(--g500)] font-[560] text-[var(--g50)] outline-none hover:border-[var(--g200)] focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50',
        compact ? 'h-4 px-1 text-[10px] leading-3' : 'h-6 self-start px-2 text-[11px]',
        className,
      )}
      data-crashed-reset={id}
    >
      <Icon icon="reset" className={compact ? 'size-2.5' : 'size-3'} />
      {t('reset.crashed')}
    </button>
  )
}
