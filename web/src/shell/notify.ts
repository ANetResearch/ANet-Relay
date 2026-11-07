// Toasts (02 §14, §18): info 3 s, warn 5 s, bottom centre above the timeline. Critical never toasts; it lives in
// the alert bar. Feature modules call notify(); they never mount their own toast viewport.
import { toast } from '@/ui/components/ui/toast'
import { RelayApiError } from '@/api'
import { t, useLangStore } from '@/i18n'

export type NotifyLevel = 'info' | 'warn' | 'success'

export function notify(title: string, opts: { level?: NotifyLevel; description?: string; timeoutMs?: number } = {}): void {
  const level = opts.level ?? 'info'
  toast.add({
    title,
    description: opts.description,
    type: level === 'warn' ? 'warning' : level,
    timeout: opts.timeoutMs ?? (level === 'warn' ? 5000 : 3000),
  })
}

/** Toast for a failed request, in the UI language. */
export function notifyError(err: unknown): void {
  const lang = useLangStore.getState().lang
  const msg = err instanceof RelayApiError ? err.text(lang) : err instanceof Error ? err.message : String(err)
  notify(t('toast.requestFailed'), { level: 'warn', description: msg })
}
