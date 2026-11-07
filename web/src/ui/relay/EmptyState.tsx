// Empty state (02 §4.3): a left-aligned text block, 220 wide, placed above centre; title 13/560, body hud-sub,
// then actions stacked as full-width outline buttons.
import * as React from 'react'
import { cn } from '@/lib/utils'

export function EmptyState({ title, body, actions, width = 220, className, children }: {
  title: React.ReactNode
  body?: React.ReactNode
  actions?: React.ReactNode
  width?: number
  className?: string
  children?: React.ReactNode
}) {
  return (
    <div className={cn('flex h-full w-full justify-center pt-[18%]', className)}>
      <div className="flex flex-col items-stretch gap-2 text-left" style={{ width }}>
        <p className="text-hud-title font-[560] text-[var(--g100)]">{title}</p>
        {body ? <p className="text-hud-sub text-[var(--g400)]">{body}</p> : null}
        {children}
        {actions ? <div className="mt-2 flex flex-col gap-1.5">{actions}</div> : null}
      </div>
    </div>
  )
}

/** Labelled empty box for a module another work package delivers (CONTRACTS.md). */
export function Placeholder({ name, note, className, children, style }: {
  name: string
  note?: string
  className?: string
  children?: React.ReactNode
  style?: React.CSSProperties
}) {
  return (
    <div className={cn('placeholder', className)} data-placeholder={name} style={style}>
      <span className="placeholder-name">{name}</span>
      {note ? <span className="text-hud-sub text-[var(--g500)]">{note}</span> : null}
      {children}
    </div>
  )
}
