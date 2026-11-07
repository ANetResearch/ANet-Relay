// Address tab (02 §8, R-03, R-06): IPv4 plus port (default 14555), inline validation, recent addresses.
import * as React from 'react'
import { api } from '@/api'
import { notify, notifyError } from '@/shell/notify'
import { useRelay } from '@/store/relay'
import { useUi } from '@/store/ui'
import { Button } from '@/ui/components/ui/button'
import { Input } from '@/ui/components/ui/input'
import { Label } from '@/ui/components/ui/label'
import { Icon } from '@/ui/icons'
import { t8, useT8 } from '@/fleet/copy'
import { isIPv4, isPort } from './address'

const RECENT_KEY = 'relay.recentAddresses'
const RECENT_MAX = 5
const DEFAULT_PORT = 14555

export function loadRecent(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]') as unknown
    return Array.isArray(raw) ? raw.filter((s): s is string => typeof s === 'string').slice(0, RECENT_MAX) : []
  } catch {
    return []
  }
}

export function rememberAddress(addr: string): void {
  try {
    const list = [addr, ...loadRecent().filter((a) => a !== addr)].slice(0, RECENT_MAX)
    localStorage.setItem(RECENT_KEY, JSON.stringify(list))
  } catch {
    /* storage unavailable */
  }
}

/** Waits up to `ms` for an aircraft with this host to show up in the snapshot. */
function waitForHost(host: string, ms: number): Promise<string | null> {
  const find = () => useRelay.getState().fleet?.vehicles.find((v) => v.host === host)?.id ?? null
  return new Promise((resolve) => {
    const hit = find()
    if (hit) return resolve(hit)
    const unsub = useRelay.subscribe(() => {
      const id = find()
      if (id) {
        unsub()
        clearTimeout(tm)
        resolve(id)
      }
    })
    const tm = setTimeout(() => {
      unsub()
      resolve(null)
    }, ms)
  })
}

export function AddressTab({ onDone }: { onDone(): void }) {
  const t = useT8()
  const [host, setHost] = React.useState('')
  const [port, setPort] = React.useState(String(DEFAULT_PORT))
  const [touched, setTouched] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [recent] = React.useState(loadRecent)
  const hostOk = isIPv4(host)
  const portNum = Number(port)
  const portOk = isPort(portNum)
  const hostId = React.useId()
  const portId = React.useId()

  const submit = async () => {
    setTouched(true)
    if (!hostOk || !portOk || busy) return
    const h = host.trim()
    const existing = useRelay.getState().fleet?.vehicles.find((v) => v.host === h)
    if (existing) {
      notify(t8('conn.alreadyInFleet', { ip: h }))
      useUi.getState().select(existing.id)
      onDone()
      return
    }
    setBusy(true)
    try {
      await api.connect(h, portNum === DEFAULT_PORT ? undefined : portNum)
      rememberAddress(portNum === DEFAULT_PORT ? h : `${h}:${portNum}`)
      notify(t8('conn.addedToFleet', { ip: h }))
      const id = await waitForHost(h, 3000)
      if (id) {
        useUi.getState().setWorkspace('fly')
        useUi.getState().select(id)
      }
      onDone()
    } catch (err) {
      notifyError(err)
    } finally {
      setBusy(false)
    }
  }

  const pick = (addr: string) => {
    const [h, p] = addr.split(':')
    setHost(h)
    setPort(p ?? String(DEFAULT_PORT))
  }

  return (
    <form
      className="flex flex-col gap-4"
      data-connect-address=""
      onSubmit={(e) => {
        e.preventDefault()
        void submit()
      }}
    >
      <p className="text-hud-sub text-[var(--g400)]">{t('conn.addressHint')}</p>
      <div className="grid grid-cols-[1fr_112px] gap-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={hostId} className="cap">
            {t('conn.address')}
          </Label>
          <Input
            id={hostId}
            autoFocus
            value={host}
            inputMode="decimal"
            spellCheck={false}
            autoComplete="off"
            placeholder="10.7.7.36"
            aria-invalid={touched && !hostOk ? true : undefined}
            onChange={(e) => setHost(e.target.value)}
            onBlur={() => host && setTouched(true)}
            className="h-8 font-mono text-[12px]"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={portId} className="cap">
            {t('conn.port')}
          </Label>
          <Input
            id={portId}
            value={port}
            inputMode="numeric"
            aria-invalid={touched && !portOk ? true : undefined}
            onChange={(e) => setPort(e.target.value.replace(/[^\d]/g, ''))}
            className="h-8 font-mono text-[12px]"
          />
        </div>
      </div>
      <div className="-mt-2 min-h-4 text-hud-sub" aria-live="polite">
        {touched && !hostOk ? (
          <span className="text-[var(--g50)]">{t('conn.invalidIp')}</span>
        ) : touched && !portOk ? (
          <span className="text-[var(--g50)]">{t('conn.invalidPort')}</span>
        ) : (
          <span className="text-[var(--g500)]">{t('conn.portHint')}</span>
        )}
      </div>
      {recent.length ? (
        <div className="flex flex-col gap-1.5">
          <span className="cap">{t('conn.recent')}</span>
          <div className="flex flex-wrap gap-1.5">
            {recent.map((a) => (
              <button
                key={a}
                type="button"
                onClick={() => pick(a)}
                className="addr h-6 rounded-full border border-[var(--border)] px-2.5 hover:border-[var(--g500)] hover:text-[var(--g100)]"
              >
                {a}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      <div className="flex justify-end">
        <Button type="submit" disabled={busy} className="h-8 min-w-24 gap-1.5 text-[12px]">
          <Icon icon="connect" className="size-4" />
          {busy ? t('conn.connecting') : t('action.connect')}
        </Button>
      </div>
    </form>
  )
}
