// Small polling hook for detail routes that are not in the snapshot (messages, params, sessions, records).
// Requests never overlap; a failure keeps the last good data and reports the error.
import * as React from 'react'

export interface Poll<T> {
  data: T | null
  error: unknown
  loading: boolean
  reload(): void
}

/** Calls `load` on mount, whenever `key` changes and every `everyMs` (0 = once). `everyMs` may change between renders. */
export function usePoll<T>(load: () => Promise<T>, everyMs: number, key: unknown, enabled = true): Poll<T> {
  const [state, setState] = React.useState<{ data: T | null; error: unknown; loading: boolean; key: unknown }>({ data: null, error: null, loading: enabled, key })
  const loadRef = React.useRef(load)
  React.useLayoutEffect(() => {
    loadRef.current = load
  })
  const [nonce, setNonce] = React.useState(0)
  if (state.key !== key) setState({ data: null, error: null, loading: enabled, key })

  React.useEffect(() => {
    if (!enabled) return
    let alive = true
    let busy = false
    let timer: ReturnType<typeof setTimeout> | null = null
    const run = () => {
      if (busy) return
      busy = true
      loadRef.current().then(
        (data) => alive && setState((s) => ({ ...s, data, error: null, loading: false })),
        (error) => alive && setState((s) => ({ ...s, error, loading: false })),
      ).finally(() => {
        busy = false
        if (alive && everyMs > 0) timer = setTimeout(run, everyMs)
      })
    }
    run()
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [key, everyMs, enabled, nonce])

  const reload = React.useCallback(() => setNonce((n) => n + 1), [])
  return { data: state.key === key ? state.data : null, error: state.error, loading: state.loading, reload }
}
