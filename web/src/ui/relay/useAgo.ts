// "12 s ago" against the backend clock, re-evaluated once per second.
import * as React from 'react'
import { ageParts } from '@/lib/format'
import { useT } from '@/i18n'
import { backendNow } from '@/store/relay'

export function useAgo(t0: number | null | undefined): string {
  const t = useT()
  const [, tick] = React.useReducer((n: number) => n + 1, 0)
  React.useEffect(() => {
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])
  if (t0 === null || t0 === undefined) return '—'
  const p = ageParts(backendNow(), t0)
  if (p.unit === 'now') return t('time.justNow')
  if (p.unit === 's') return t('time.secondsAgo', { n: p.n })
  if (p.unit === 'min') return t('time.minutesAgo', { n: p.n })
  return t('time.hoursAgo', { n: p.n })
}
