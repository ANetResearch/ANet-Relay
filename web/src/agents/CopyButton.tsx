// Copy-to-clipboard button with the copy -> check morph (02 §2.5); reverts after 1.6 s.
import * as React from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/ui/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/ui/components/ui/tooltip'
import { StateIcon } from '@/ui/icons'
import { useT8 } from '@/fleet/copy'

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // clipboard API unavailable (insecure context): fall back to a hidden textarea
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.setAttribute('readonly', '')
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      ta.remove()
      return ok
    } catch {
      return false
    }
  }
}

export function CopyButton({ text, label, className, withText = false }: { text: string; label?: string; className?: string; withText?: boolean }) {
  const t = useT8()
  const [done, setDone] = React.useState(false)
  React.useEffect(() => {
    if (!done) return
    const tm = setTimeout(() => setDone(false), 1600)
    return () => clearTimeout(tm)
  }, [done])
  const name = label ?? t('action.copy')
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            size={withText ? 'sm' : 'icon-sm'}
            aria-label={name}
            className={cn('shrink-0 text-[var(--g400)] hover:text-[var(--g100)]', done && 'text-[var(--g50)]', className)}
            onClick={() => void copyText(text).then((ok) => ok && setDone(true))}
          />
        }
      >
        <StateIcon icon="copy" alt={done} />
        {withText ? <span>{done ? t('action.copied') : name}</span> : null}
      </TooltipTrigger>
      <TooltipContent>{done ? t('action.copied') : name}</TooltipContent>
    </Tooltip>
  )
}

/** Opens an http(s) URL in the system browser (Electron) or a new tab. */
export function openExternal(url: string): void {
  if (window.relay?.openExternal) void window.relay.openExternal(url)
  else window.open(url, '_blank', 'noopener')
}
