// Boot status and backend-loss states (02 §15). During boot it renders into #boot-status (inside the index.html
// mask): "Starting services…" after 8 s, the failure line with View log / Retry / Quit after 20 s, the ports
// message. After boot it shows the L3 card "Relay lost its backend." while the stream stays down.
import * as React from 'react'
import { createPortal } from 'react-dom'
import { env, stream } from '@/api'
import { useT } from '@/i18n'
import { useBoot } from '@/app/boot'
import { backendWatch, onBackendChange } from '@/app/connection'
import { useConn } from '@/store/selectors'
import { Button } from '@/ui/components/ui/button'

const LOST_AFTER_MS = 2000

function BootActions({ onRetry }: { onRetry(): void }) {
  const t = useT()
  const bridge = typeof window !== 'undefined' ? window.relay : undefined
  return (
    <div className="flex items-center gap-2 text-[var(--g100)]">
      {bridge ? (
        <Button size="sm" variant="outline" onClick={() => void bridge.revealLogs()}>
          {t('action.viewLog')}
        </Button>
      ) : null}
      <Button size="sm" variant="outline" onClick={onRetry}>
        {t('action.retry')}
      </Button>
      {bridge?.quit ? (
        <Button size="sm" variant="ghost" onClick={() => bridge.quit?.()}>
          {t('action.quit')}
        </Button>
      ) : null}
    </div>
  )
}

function BootStatus() {
  const t = useT()
  const phase = useBoot((s) => s.phase)
  const ports = useBoot((s) => s.ports)
  const [host] = React.useState(() => document.getElementById('boot-status'))
  if (!host || phase === 'booting' || phase === 'ready') return null
  const retry = () => {
    useBoot.getState().retry()
    const bridge = window.relay
    if (bridge?.restartBackend) void bridge.restartBackend()
    stream()?.reconnect()
  }
  let body: React.ReactNode
  if (phase === 'slow') body = <p>{t('boot.starting')}</p>
  else if (phase === 'ports')
    body = (
      <>
        <p className="text-[var(--g200)]">{ports[0] === ports[1] ? t('boot.port', { port: ports[0] }) : t('boot.ports', { from: ports[0], to: ports[1] })}</p>
        <BootActions onRetry={retry} />
      </>
    )
  else
    body = (
      <>
        <p className="text-[var(--g200)]">{env().electron ? t('boot.failed') : t('boot.unreachable', { url: env().apiBase })}</p>
        <BootActions onRetry={retry} />
      </>
    )
  return createPortal(body, host)
}

/** performance.now(), refreshed every `ms` while `on`. */
function useTicker(on: boolean, ms: number): number {
  const [now, setNow] = React.useState(() => performance.now())
  React.useEffect(() => {
    if (!on) return
    const id = setInterval(() => setNow(performance.now()), ms)
    return () => clearInterval(id)
  }, [on, ms])
  return now
}

/** Post-boot: the stream has been down for 2 s, or Electron reported that the backend exited. */
function useBackendLost(): boolean {
  const conn = useConn()
  const revealed = useBoot((s) => s.revealed)
  const [, bump] = React.useReducer((n: number) => n + 1, 0)
  React.useEffect(() => onBackendChange(bump), [])
  const down = revealed && !env().fixture && conn.downSince !== null
  const now = useTicker(down, 250)
  const exited = backendWatch.state?.state === 'crashed' || (backendWatch.state?.state === 'failed' && revealed)
  return revealed && (exited || (down && now - (conn.downSince ?? 0) >= LOST_AFTER_MS))
}

function BackendLostCard() {
  const t = useT()
  const conn = useConn()
  const bridge = typeof window !== 'undefined' ? window.relay : undefined
  const now = useTicker(conn.retryAt !== null, 250)
  const secs = conn.retryAt === null ? null : Math.max(0, Math.ceil((conn.retryAt - now) / 1000))
  return (
    <div className="fixed inset-0 z-(--z-dialog) flex items-start justify-center bg-[var(--overlay)] pt-[22vh]" role="alertdialog" aria-labelledby="backend-lost-title">
      <div className="l3 enter flex w-[420px] max-w-[calc(100vw-32px)] flex-col gap-3 rounded-xl p-5">
        <p id="backend-lost-title" className="text-ed-title font-[560] text-[var(--g50)]">
          {t('boot.lost')}
        </p>
        <p className="text-ed-value text-[var(--g300)]">{t('boot.lostBody')}</p>
        <div className="mt-1 flex items-center gap-2">
          {bridge?.restartBackend ? (
            <Button size="lg" variant="default" onClick={() => void bridge.restartBackend?.().then(() => stream()?.reconnect())}>
              {t('action.restartBackend')}
            </Button>
          ) : (
            <Button size="lg" variant="default" onClick={() => stream()?.reconnect()}>
              {t('action.retry')}
            </Button>
          )}
          {bridge ? (
            <Button size="lg" variant="outline" onClick={() => void bridge.revealLogs()}>
              {t('action.viewLog')}
            </Button>
          ) : null}
          <span className="flex-1" />
          {secs !== null ? <span className="num text-hud-sub text-[var(--g500)]">{t('boot.reconnecting', { s: secs })}</span> : null}
        </div>
      </div>
    </div>
  )
}

export function BootLayer() {
  const lost = useBackendLost()
  return (
    <>
      <BootStatus />
      {lost ? <BackendLostCard /> : null}
    </>
  )
}
