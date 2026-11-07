// Section and Group (02 §2.4): panel padding 12, group spacing 16, hud-cap headings. A collapsible Group uses the
// shadcn Collapsible with the chevron state icon.
import * as React from 'react'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/ui/components/ui/collapsible'
import { StateIcon } from '@/ui/icons'
import { cn } from '@/lib/utils'

/** Panel-level block with a 13/560 title row (40 high) and an optional right slot. */
export function Section({ title, right, children, className, bodyClassName }: {
  title: React.ReactNode
  right?: React.ReactNode
  children?: React.ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <section className={cn('flex min-h-0 flex-col', className)}>
      <header className="flex h-10 shrink-0 items-center justify-between gap-2 px-3">
        <h2 className="text-hud-title font-[560] text-[var(--g100)]">{title}</h2>
        {right ? <div className="flex items-center gap-1">{right}</div> : null}
      </header>
      <div className={cn('min-h-0 flex-1', bodyClassName)}>{children}</div>
    </section>
  )
}

/** Group inside a panel: hud-cap label, optional count / right slot, optional collapse. */
export function Group({ label, count, right, collapsible = false, defaultOpen = true, children, className }: {
  label: React.ReactNode
  count?: number
  right?: React.ReactNode
  collapsible?: boolean
  defaultOpen?: boolean
  children?: React.ReactNode
  className?: string
}) {
  const [open, setOpen] = React.useState(defaultOpen)
  const head = (
    <>
      <span className="cap">{label}</span>
      {count !== undefined ? <span className="num text-hud-cap text-[var(--g500)]">{count}</span> : null}
      <span className="flex-1" />
      {right}
    </>
  )
  if (!collapsible) {
    return (
      <div className={cn('flex flex-col gap-2', className)}>
        <div className="flex h-5 items-center gap-2 px-3">{head}</div>
        {children}
      </div>
    )
  }
  return (
    <Collapsible open={open} onOpenChange={setOpen} className={cn('flex flex-col gap-2', className)}>
      <CollapsibleTrigger className="flex h-6 items-center gap-2 rounded-sm px-3 text-left outline-none hover:bg-[var(--g850)] focus-visible:ring-1 focus-visible:ring-ring">
        {head}
        <StateIcon icon="chevron.down" alt={open} className="size-3.5 text-[var(--g400)]" />
      </CollapsibleTrigger>
      <CollapsibleContent>{children}</CollapsibleContent>
    </Collapsible>
  )
}
