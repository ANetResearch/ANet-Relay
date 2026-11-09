// Shell-level actions shared by the top bar, the keyboard layer and the command palette. Stop paths never wait on
// UI state: they fire the request immediately and report the outcome afterwards (02 §1.6).
import { api } from '@/api'
import { t } from '@/i18n'
import { useRelay } from '@/store/relay'
import { getVehicle } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { notify, notifyError } from './notify'
import { openSimDialog } from '@/twin/actions'

export function stopAll(): void {
  api.stopAll().then(() => notify(t('toast.stopAllSent')), notifyError)
}

export function stopVehicle(id: string): void {
  const label = getVehicle(id)?.label ?? id
  api.stop(id).then(() => notify(t('toast.stopSent', { aircraft: label })), notifyError)
}

/** Esc with no overlay (02 §3): the keyboard-possessed aircraft, else the selected one. */
export function stopFocused(): void {
  const vs = useRelay.getState().fleet?.vehicles ?? []
  const possessed = vs.find((v) => v.possession?.input === 'keyboard')
  const id = possessed?.id ?? useUi.getState().selectedId
  if (id) stopVehicle(id)
  else notify(t('toast.stopNoTarget'), { level: 'info' })
}

export function ackAlert(id: string): void {
  useRelay.getState().ackLocal(id)
  api.ackAlert(id).catch(notifyError)
}

export function possess(id: string): void {
  api.possess(id, { input: 'keyboard' }).catch(notifyError)
}

export function release(id: string): void {
  api.release(id).catch(notifyError)
}

export function scanNetwork(): void {
  api.discover().catch(notifyError)
}

/** Opens the add-simulator dialog (11 §11): the engine, a twin's profile or pack, the frame. */
export function addSim(): void {
  openSimDialog()
}

/** Moves the selection through the fleet in display order (REAL then SIMULATED). */
export function moveSelection(delta: 1 | -1): void {
  const vs = useRelay.getState().fleet?.vehicles ?? []
  const order = [...vs.filter((v) => v.kind === 'real'), ...vs.filter((v) => v.kind === 'sim')].map((v) => v.id)
  if (!order.length) return
  const cur = useUi.getState().selectedId
  const i = cur ? order.indexOf(cur) : -1
  const next = i < 0 ? (delta > 0 ? 0 : order.length - 1) : Math.min(order.length - 1, Math.max(0, i + delta))
  useUi.getState().select(order[next])
}
