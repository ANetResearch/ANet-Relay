// Connect → USB serial (11 §7.1): the serial ports of this computer (flight controllers first, refresh), the baud
// rate (automatic or fixed) and POST /vehicles {serial}. A refusal (serial_unavailable: busy, not found, no
// heartbeat, pyserial missing) stays in the tab in the window language.
import * as React from 'react'
import { api, serialOf, type SerialBaud, type SerialPorts } from '@/api'
import { useLang } from '@/i18n'
import { notify } from '@/shell/notify'
import { useRelay } from '@/store/relay'
import { useUi } from '@/store/ui'
import { Button } from '@/ui/components/ui/button'
import { NativeSelect, NativeSelectOption } from '@/ui/components/ui/native-select'
import { Spinner } from '@/ui/components/ui/spinner'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/ui/components/ui/table'
import { Icon } from '@/ui/icons'
import { BAUDS, portTitle, sortPorts, twinErrorView, vidPid, type TwinErrorView } from './model'
import { TwinFailure } from './parts'
import { useTT } from './strings'

/** Waits up to `ms` for an aircraft on this serial device in the snapshot. */
function waitForDevice(device: string, ms: number): Promise<string | null> {
  const find = () => useRelay.getState().fleet?.vehicles.find((v) => serialOf(v)?.device === device)?.id ?? null
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

export function SerialTab({ onDone }: { onDone(): void }) {
  const t = useTT()
  const lang = useLang()
  const [data, setData] = React.useState<SerialPorts | null>(null)
  const [error, setError] = React.useState<TwinErrorView | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [device, setDevice] = React.useState<string | null>(null)
  const [baud, setBaud] = React.useState<SerialBaud>('auto')
  const [busy, setBusy] = React.useState(false)
  const [failure, setFailure] = React.useState<TwinErrorView | null>(null)
  const inFleet = useRelay((s) => (s.fleet?.vehicles ?? []).map((v) => serialOf(v)?.device).filter(Boolean).join('\n'))
  const used = React.useMemo(() => new Set(inFleet.split('\n')), [inFleet])

  const [nonce, setNonce] = React.useState(0)
  const load = () => {
    setLoading(true)
    setNonce((n) => n + 1)
  }
  React.useEffect(() => {
    let alive = true
    api
      .serialPorts()
      .then((d) => {
        if (!alive) return
        setData(d)
        setError(null)
        // the first flight controller not yet in the fleet, else the first free port
        const ports = sortPorts(d.ports)
        const taken = new Set((useRelay.getState().fleet?.vehicles ?? []).map((v) => serialOf(v)?.device))
        const free = ports.filter((p) => p.vehicle === null && !taken.has(p.device))
        setDevice((cur) => (cur && ports.some((p) => p.device === cur) ? cur : (free.find((p) => p.autopilot)?.device ?? free[0]?.device ?? ports[0]?.device ?? null)))
      })
      .catch((e: unknown) => alive && setError(twinErrorView(e, lang)))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [nonce, lang])

  const ports = data ? sortPorts(data.ports) : []
  const connect = async () => {
    if (!device || busy) return
    setBusy(true)
    setFailure(null)
    try {
      await api.connectSerial(device, baud)
      notify(t('serial.connected', { device }))
      const id = await waitForDevice(device, 3000)
      if (id) {
        useUi.getState().setWorkspace('fly')
        useUi.getState().select(id)
      }
      onDone()
    } catch (e) {
      setFailure(twinErrorView(e, lang))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-4" data-connect-serial="">
      <p className="text-hud-sub text-[var(--g400)]">{t('serial.hint')}</p>
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <span className="cap">{t('serial.ports')}</span>
          {data ? <span className="num text-hud-cap text-[var(--g500)]">{ports.length}</span> : null}
          <span className="flex-1" />
          <Button variant="ghost" size="sm" className="gap-1.5 text-[var(--g300)]" onClick={load} disabled={loading} data-serial-refresh="">
            {loading ? <Spinner /> : <Icon icon="refresh" className="size-3.5" />}
            {t('serial.refresh')}
          </Button>
        </div>
        {error ? (
          <TwinFailure failure={error} />
        ) : !data ? (
          <p className="text-hud-sub text-[var(--g500)]">{t('serial.loading')}</p>
        ) : !data.available ? (
          <p className="rounded-md border border-[var(--g600)] px-3 py-3 text-hud-sub text-[var(--g200)]" data-serial-unavailable="">
            {t('serial.unavailable')}
            {data.reason ? `${lang === 'zh' ? '：' : ': '}${lang === 'zh' ? (data.reason_zh ?? data.reason) : data.reason}` : ''}
          </p>
        ) : ports.length === 0 ? (
          <p className="rounded-md border border-dashed border-[var(--g700)] px-3 py-3 text-hud-sub text-[var(--g400)]">{t('serial.none')}</p>
        ) : (
          <Table data-lf-table="" className="text-[12px]" role="radiogroup" aria-label={t('serial.ports')}>
            <TableHeader>
              <TableRow>
                <TableHead className="h-7 text-hud-cap">{t('serial.col.device')}</TableHead>
                <TableHead className="h-7 text-hud-cap">{t('serial.col.description')}</TableHead>
                <TableHead className="h-7 text-right text-hud-cap">{t('serial.col.id')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ports.map((p) => {
                const selected = p.device === device
                const taken = p.vehicle !== null || used.has(p.device)
                return (
                  <TableRow
                    key={p.device}
                    role="radio"
                    aria-checked={selected}
                    tabIndex={0}
                    className="h-10 cursor-pointer"
                    data-selected={selected ? '' : undefined}
                    data-serial-port={p.device}
                    onClick={() => setDevice(p.device)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        setDevice(p.device)
                      }
                    }}
                  >
                    <TableCell className="py-1">
                      <div className="flex flex-col">
                        <span className="addr">{p.device}</span>
                        <span className={p.autopilot ? 'text-[10px] font-[560] text-[var(--g100)]' : 'text-[10px] text-[var(--g500)]'}>
                          {p.autopilot ? t('serial.autopilot') : p.kind === 'usb-serial' ? [t('serial.usbSerial'), p.adapter].filter(Boolean).join(' ') : t('serial.adapter')}
                          {taken ? ` · ${t('serial.inFleet')}` : ''}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell className="max-w-[220px] py-1">
                      <div className="flex min-w-0 flex-col">
                        <span className="truncate text-[var(--g200)]">{portTitle(p)}</span>
                        {p.manufacturer ? <span className="truncate text-[10px] text-[var(--g500)]">{p.manufacturer}</span> : null}
                      </div>
                    </TableCell>
                    <TableCell className="num py-1 text-right text-[11px] text-[var(--g400)]">{vidPid(p) ?? '—'}</TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor="serial-baud" className="cap">
          {t('serial.baud')}
        </label>
        <NativeSelect id="serial-baud" value={String(baud)} onChange={(e) => setBaud(e.target.value === 'auto' ? 'auto' : Number(e.target.value))} className="w-40" data-serial-baud="">
          {BAUDS.map((b) => (
            <NativeSelectOption key={String(b)} value={String(b)}>
              {b === 'auto' ? t('serial.baud.auto') : String(b)}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        <span className="text-hud-sub text-[var(--g500)]">{t('serial.baudHint')}</span>
      </div>
      {failure ? <TwinFailure failure={failure} /> : null}
      <div className="flex items-center justify-end gap-3">
        {busy ? <span className="text-hud-sub text-[var(--g400)]">{t('serial.connecting')}</span> : null}
        <Button onClick={() => void connect()} disabled={!device || busy || data?.available === false || Boolean(device && (used.has(device) || ports.some((p) => p.device === device && p.vehicle !== null)))} className="h-8 min-w-24 gap-1.5 text-[12px]" data-serial-connect="">
          {busy ? <Spinner /> : <Icon icon="usb" className="size-4" />}
          {device ? t('serial.connect') : t('serial.select')}
        </Button>
      </div>
    </div>
  )
}
