// Type-to-confirm (02 §11; 03 §6.1 H4, H12): ARM for a real aircraft, the parameter name for a write, MOTOR for a
// motor test. The action is enabled only when the input equals the word exactly (case-sensitive). Inline block,
// 74 px high by default, so it can expand under the button that opened it.
import * as React from 'react'
import { Button } from '@/ui/components/ui/button'
import { Input } from '@/ui/components/ui/input'
import { useT } from '@/i18n'
import { cn } from '@/lib/utils'

export interface TypeToConfirmProps {
  /** the exact word to type, e.g. "ARM", "ANGLE_MAX", "MOTOR" */
  word: string
  /** sentence above the input, e.g. t('arm.typePrompt', { aircraft }) */
  prompt?: string
  actionLabel: string
  onConfirm(): void | Promise<void>
  onCancel?(): void
  busy?: boolean
  error?: string | null
  autoFocus?: boolean
  className?: string
}

export function matchesWord(input: string, word: string): boolean {
  return input === word
}

export function TypeToConfirm({ word, prompt, actionLabel, onConfirm, onCancel, busy = false, error, autoFocus = true, className }: TypeToConfirmProps) {
  const t = useT()
  const [text, setText] = React.useState('')
  const ok = matchesWord(text, word)
  const id = React.useId()
  const submit = () => {
    if (ok && !busy) void onConfirm()
  }
  return (
    <div className={cn('flex min-h-[74px] flex-col justify-center gap-2 rounded-md l2 px-3 py-2.5', className)} data-type-confirm="">
      <label htmlFor={id} className="text-hud-sub text-[var(--g300)]">
        {prompt ?? t('confirm.typeWord', { word })}
      </label>
      <div className="flex items-center gap-2">
        <Input
          id={id}
          value={text}
          autoFocus={autoFocus}
          autoComplete="off"
          spellCheck={false}
          placeholder={word}
          aria-invalid={text.length > 0 && !word.startsWith(text) ? true : undefined}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit()
            if (e.key === 'Escape' && onCancel) {
              e.stopPropagation()
              onCancel()
            }
          }}
          className="h-7 flex-1 font-mono text-[12px] tracking-[0.04em]"
        />
        <Button size="default" variant={ok ? 'default' : 'outline'} disabled={!ok || busy} onClick={submit} className="min-w-16">
          {actionLabel}
        </Button>
        {onCancel ? (
          <Button size="default" variant="ghost" onClick={onCancel}>
            {t('action.cancel')}
          </Button>
        ) : null}
      </div>
      {error ? <p className="text-hud-sub text-[var(--g50)]">{error}</p> : null}
    </div>
  )
}
