// Small shared pieces of the twin surfaces: the engine badge (11 §8.5), the failure block with the firmware's last
// output lines (11 §6.3) and a monospace output pane.
import * as React from 'react'
import type { Vehicle } from '@/api'
import { cn } from '@/lib/utils'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/ui/components/ui/tooltip'
import { Icon } from '@/ui/icons'
import { engineBadge, engineBadgeText, type TwinErrorView } from './model'
import { useTT } from './strings'

/** "固件孪生 ArduCopter 4.6.3 · 精确匹配" / "硬件在环 PX4 1.16.2"; nothing for the fast simulator and plain aircraft. */
export function EngineBadge({ vehicle, className }: { vehicle: Vehicle; className?: string }) {
  const t = useTT()
  const b = engineBadge(vehicle)
  if (!b) return null
  const text = engineBadgeText(b, t)
  const weak = b.kind === 'twin' && b.match !== null && b.match !== 'exact' && b.match !== 'version'
  const badge = (
    <span
      className={cn('inline-flex h-5 max-w-full min-w-0 items-center gap-1 rounded-sm border border-[var(--g600)] px-1.5 font-mono text-[10px] leading-4 text-[var(--g100)]', className)}
      data-engine-badge={b.kind}
      data-match={b.match ?? undefined}
    >
      <Icon icon={b.kind === 'twin' ? 'chip' : 'usb'} className="size-3 shrink-0 text-[var(--g400)]" />
      <span className="truncate">{text}</span>
      {weak ? <Icon icon="alert" className="size-3 shrink-0 text-[var(--g200)]" /> : null}
    </span>
  )
  if (!weak) return badge
  return (
    <Tooltip>
      <TooltipTrigger render={<span className="inline-flex min-w-0" />}>{badge}</TooltipTrigger>
      <TooltipContent className="max-w-[260px]">{t('match.warning')}</TooltipContent>
    </Tooltip>
  )
}

/** The firmware's output, newest line last, scrolled to the end. */
export function OutputLines({ lines, className }: { lines: readonly string[]; className?: string }) {
  const ref = React.useRef<HTMLPreElement>(null)
  React.useLayoutEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [lines])
  return (
    <pre ref={ref} className={cn('max-h-40 overflow-auto rounded-sm border border-[var(--border)] bg-[var(--g950)] px-2 py-1.5 font-mono text-[10px] leading-[14px] whitespace-pre-wrap text-[var(--g300)]', className)} data-selectable="" data-output-lines="">
      {lines.join('\n')}
    </pre>
  )
}

/** A refused twin request: title, the backend's message in the window language, the output lines and partial results. */
export function TwinFailure({ failure }: { failure: TwinErrorView }) {
  const t = useTT()
  return (
    <div className="flex flex-col gap-1.5 rounded-md border border-[var(--g600)] px-3 py-2.5" role="alert" data-twin-error={failure.code}>
      <span className="flex items-center gap-1.5 text-[12px] font-[560] text-[var(--g50)]">
        <Icon icon="alert" className="size-3.5" />
        {t(failure.title)}
      </span>
      <span className="text-ed-value text-[var(--g100)]">{failure.message}</span>
      {failure.added.length ? <span className="text-hud-sub text-[var(--g400)]">{t('sim.partial', { sims: failure.added.join(', ') })}</span> : null}
      {failure.lines.length ? (
        <>
          <span className="cap pt-1">{t('twin.exit')}</span>
          <OutputLines lines={failure.lines} />
        </>
      ) : null}
    </div>
  )
}
