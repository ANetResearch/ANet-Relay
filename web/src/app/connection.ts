// Connection orchestration: health polling and the stream drive the boot stages; snapshots and events go into the
// relay store; after a reconnect the event gap is filled from GET /events?after=. In fixture mode the dev fixture
// replaces both transports (src/dev/fixtures.ts).
import { api, env, installStream, RelayStream, type BackendState, type RelayEvent } from '@/api'
import { useRelay } from '@/store/relay'
import { useBoot } from './boot'

export interface BackendWatch {
  /** last lifecycle state from Electron, if the shell reports it */
  state: BackendState | null
}
export const backendWatch: BackendWatch = { state: null }
const backendListeners = new Set<() => void>()
export function onBackendChange(l: () => void): () => void {
  backendListeners.add(l)
  return () => backendListeners.delete(l)
}

async function pollHealth(stop: { stopped: boolean }): Promise<void> {
  while (!stop.stopped && useBoot.getState().stage < 2) {
    const ctl = new AbortController()
    const tm = setTimeout(() => ctl.abort(), 1500)
    try {
      const h = await api.health(ctl.signal)
      if (h && (h as { ok?: boolean }).ok !== false) {
        useBoot.getState().setStage(2)
        return
      }
    } catch {
      /* not up yet */
    } finally {
      clearTimeout(tm)
    }
    await new Promise((r) => setTimeout(r, 400))
  }
}

async function fillGap(): Promise<void> {
  const after = useRelay.getState().lastSeq
  try {
    const res = await api.events(after, 500)
    const list: RelayEvent[] = Array.isArray(res) ? res : (res?.events ?? [])
    useRelay.getState().applyEvents(list)
  } catch {
    /* the stream continues; the timeline reconciles from snapshots */
  }
}

export function startConnection(): () => void {
  const e = env()
  const stop = { stopped: false }
  const bridge = typeof window !== 'undefined' ? window.relay : undefined

  // Electron reports the backend lifecycle (03 §12); a plain browser goes straight to health polling
  let offBackend: (() => void) | undefined
  if (bridge?.onBackendState) {
    const apply = (s: BackendState) => {
      backendWatch.state = s
      for (const l of backendListeners) l()
      if (s.state === 'starting' || s.state === 'ready') useBoot.getState().setStage(1)
      if (s.state === 'ready') useBoot.getState().setStage(2)
      if (s.state === 'failed') useBoot.getState().fail('timeout')
      if (s.state === 'ports_busy') useBoot.getState().fail('ports', s.ports ?? [8765, 8775])
    }
    offBackend = bridge.onBackendState(apply)
    // states reported before this page subscribed (a failure during the first load)
    void bridge.getBackendState?.().then((s) => {
      if (s && !backendWatch.state) apply(s)
    })
  } else {
    useBoot.getState().setStage(1)
  }
  void pollHealth(stop)

  let opened = false
  const s = new RelayStream(
    () => (e.operatorToken ? `${e.streamUrl}?operator=${encodeURIComponent(e.operatorToken)}` : e.streamUrl),
    {
      onHello: (h) => useRelay.getState().setHello(h),
      onFleet: (f) => {
        useRelay.getState().applyFleet(f)
        const b = useBoot.getState()
        if (b.stage < 3) {
          b.setStage(1)
          b.setStage(2)
          b.setStage(3)
        }
      },
      onEvent: (ev) => useRelay.getState().applyEvents([ev]),
      onState: (state, info) => {
        useRelay.getState().setConn(state, info)
        if (state === 'open') {
          if (opened) void fillGap()
          opened = true
        }
      },
    },
  )
  installStream(s)
  s.start()
  return () => {
    stop.stopped = true
    offBackend?.()
    s.stop()
    installStream(null)
  }
}
