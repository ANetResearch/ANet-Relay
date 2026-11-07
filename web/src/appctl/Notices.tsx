import { cn } from '@/lib/utils'
import { Icon } from '@/ui/icons'
import { useAppNotices } from './notices'

/** Bottom-right stack of agent-action notices; never takes pointer events away from the stage beneath. */
export function AppNotices() {
  const items = useAppNotices((s) => s.items)
  if (!items.length) return null
  return (
    <div className="pointer-events-none fixed right-4 bottom-4 z-(--z-toast) flex w-[min(360px,calc(100vw-32px))] flex-col items-end gap-1.5" aria-live="polite" data-app-notices="">
      {items.map((n) => (
        <div key={n.id} className="l3 pointer-events-auto flex max-w-full items-center gap-2 rounded-lg px-3 py-2 text-hud-sub text-[var(--g200)]" data-app-notice={n.level}>
          <Icon icon={n.level === 'warn' ? 'alert' : 'copilot'} className={cn('size-3.5 flex-none', n.level === 'warn' ? 'text-[var(--g50)]' : 'text-[var(--g400)]')} />
          <span className="min-w-0 break-words">{n.text}</span>
        </div>
      ))}
    </div>
  )
}
