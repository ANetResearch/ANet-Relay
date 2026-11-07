// Inspector building blocks: key-value rows (label g400, value Mono g100) and a hud-cap block heading.
import * as React from 'react'
import { cn } from '@/lib/utils'
import { Value } from '@/ui/relay'

/** hud-cap heading inside a tab, 12 padding, optional right slot. */
export function Block({ title, right, children, className }: { title: React.ReactNode; right?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn('flex flex-col gap-1.5 px-3', className)}>
      <header className="flex h-5 items-center gap-2">
        <span className="cap">{title}</span>
        <span className="flex-1" />
        {right}
      </header>
      {children}
    </section>
  )
}

/** Label / value list with dotted separators (lieflat table.log skin, compact). */
export function KvList({ children, className }: { children: React.ReactNode; className?: string }) {
  return <dl className={cn('kv-list', className)}>{children}</dl>
}

export function Kv({ label, value, reason, children }: { label: string; value?: string | null; reason?: string; children?: React.ReactNode }) {
  return (
    <div className="kv-row">
      <dt>{label}</dt>
      <dd>{children ?? <Value value={value} reason={reason ?? ''} />}</dd>
    </div>
  )
}
