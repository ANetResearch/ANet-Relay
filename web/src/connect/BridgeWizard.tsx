// Bridge set-up wizard (02 §8, R-04, 03 §9, H13): 1 Connect to the bridge · 2 Read · 3 Configure · 4 Rejoin.
// Every value that will be written is shown before writing; the hotspot is kept as the fallback; the password goes
// to the bridge only (the backend never stores it) and is dropped from memory when the wizard closes.
import * as React from 'react'
import { api, RelayApiError } from '@/api'
import { useLang } from '@/i18n'
import { cn } from '@/lib/utils'
import { useRelay } from '@/store/relay'
import { useUi } from '@/store/ui'
import { Button } from '@/ui/components/ui/button'
import { Input } from '@/ui/components/ui/input'
import { Label } from '@/ui/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/ui/components/ui/native-select'
import { Switch } from '@/ui/components/ui/switch'
import { ToggleGroup, ToggleGroupItem } from '@/ui/components/ui/toggle-group'
import { Icon, StateIcon } from '@/ui/icons'
import { useT8, type AnyKey } from '@/fleet/copy'
import { isIPv4, isPort, parseHostPort } from './address'
import { net, useNetwork, type BridgeConfig, type BridgeRecord } from './net'

type Step = 1 | 2 | 3 | 4
const STEPS: { n: Step; key: AnyKey }[] = [
  { n: 1, key: 'conn.step.connect' },
  { n: 2, key: 'conn.step.read' },
  { n: 3, key: 'conn.step.configure' },
  { n: 4, key: 'conn.step.rejoin' },
]
const HOTSPOT_IP = '192.168.4.1'
const BAUDS = [57600, 115200, 230400, 460800, 921600]
const REJOIN_TIMEOUT_S = 60
const RESCAN_EVERY_S = 20

function errText(err: unknown, lang: 'en' | 'zh'): string {
  if (err instanceof RelayApiError) {
    const reason = (err.detail as { reason?: unknown } | null)?.reason
    return typeof reason === 'string' && reason ? reason : err.text(lang)
  }
  return err instanceof Error ? err.message : String(err)
}

function Stepper({ step }: { step: Step }) {
  const t = useT8()
  return (
    <ol className="flex items-center gap-2" aria-label={t('conn.stepOf', { n: step })}>
      {STEPS.map((s, i) => {
        const state = s.n < step ? 'done' : s.n === step ? 'current' : 'next'
        return (
          <React.Fragment key={s.n}>
            {i > 0 ? <li aria-hidden="true" className={cn('h-px min-w-3 flex-1', s.n <= step ? 'bg-[var(--g500)]' : 'bg-[var(--g800)]')} /> : null}
            <li className="flex shrink-0 items-center gap-1.5" data-step={state} aria-current={state === 'current' ? 'step' : undefined}>
              <span className="wiz-dot num">{state === 'done' ? <Icon icon="check" className="size-3" /> : s.n}</span>
              <span className={cn('text-[11px] whitespace-nowrap', state === 'current' ? 'font-[560] text-[var(--g50)]' : state === 'done' ? 'text-[var(--g300)]' : 'text-[var(--g500)]')}>
                {t(s.key)}
              </span>
            </li>
          </React.Fragment>
        )
      })}
    </ol>
  )
}

function KV({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(120px,auto)_1fr] rounded-md border border-[var(--border)] bg-[var(--g900)] text-[12px]">
      {rows.map(([k, v], i) => (
        <React.Fragment key={k}>
          <dt className={cn('px-3 py-1.5 text-[var(--g400)]', i > 0 && 'border-t border-dotted border-[var(--border)]')}>{k}</dt>
          <dd className={cn('num px-3 py-1.5 text-[var(--g100)]', i > 0 && 'border-t border-dotted border-[var(--border)]')}>{v}</dd>
        </React.Fragment>
      ))}
    </dl>
  )
}

function Field({ label, htmlFor, hint, error, children, className }: { label: string; htmlFor?: string; hint?: string; error?: string | null; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <Label htmlFor={htmlFor} className="cap">
        {label}
      </Label>
      {children}
      {error ? <span className="text-hud-sub text-[var(--g50)]">{error}</span> : hint ? <span className="text-hud-sub text-[var(--g500)]">{hint}</span> : null}
    </div>
  )
}

// ------------------------------------------------------------------------------------------------ step 1

function ConnectStep({ address, setAddress, onNext }: { address: string; setAddress(a: string): void; onNext(): void }) {
  const t = useT8()
  const { info } = useNetwork(2000)
  const on = !!info?.on_bridge_ap
  const auto = React.useRef(false)
  const parsed = parseHostPort(address, 80)
  const id = React.useId()
  React.useEffect(() => {
    if (on && !auto.current && parsed?.host === HOTSPOT_IP) {
      auto.current = true
      const tm = setTimeout(onNext, 700)
      return () => clearTimeout(tm)
    }
  }, [on, parsed?.host, onNext])
  return (
    <div className="flex flex-col gap-4">
      <p className="text-ed-value text-[var(--g200)]">{t('conn.joinHotspot')}</p>
      <div className="flex items-center gap-2.5 rounded-md border border-[var(--border)] bg-[var(--g900)] px-3 py-2.5" role="status" data-hotspot={on ? 'on' : 'off'}>
        <StateIcon icon="wifi" alt={!on} className={cn('size-4', on ? 'text-[var(--g50)]' : 'text-[var(--g500)]')} />
        <span className={cn('text-ed-value', on ? 'text-[var(--g50)]' : 'text-[var(--g400)]')}>
          {on ? t('conn.connectedTo', { ip: parsed?.host ?? HOTSPOT_IP }) : t('conn.waitingHotspot')}
        </span>
        {!on ? <span className="wiz-wait ml-auto" aria-hidden="true" /> : null}
      </div>
      <Field label={t('conn.bridgeAddress')} htmlFor={id} hint={t('conn.bridgeAddressHint')} error={address && !parsed ? t('conn.invalidIp') : null}>
        <Input id={id} value={address} spellCheck={false} onChange={(e) => setAddress(e.target.value)} className="h-8 w-56 font-mono text-[12px]" />
      </Field>
      <div className="flex justify-end gap-2">
        <Button onClick={onNext} disabled={!parsed} className="h-8 gap-1.5 text-[12px]">
          {on ? t('conn.next') : t('conn.joined')}
          <Icon icon="chevron.right" />
        </Button>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------ step 2

function ReadStep({ host, port, record, setRecord, onBack, onNext }: {
  host: string
  port: number
  record: BridgeRecord | null
  setRecord(r: BridgeRecord | null): void
  onBack(): void
  onNext(): void
}) {
  const t = useT8()
  const lang = useLang()
  const [state, setState] = React.useState<{ busy: boolean; error: string | null }>({ busy: !record, error: null })
  const load = React.useCallback(() => {
    net.readBridge(host, port).then(
      (r) => {
        setRecord(r)
        setState({ busy: false, error: null })
      },
      (err) => {
        setRecord(null)
        setState({ busy: false, error: errText(err, lang) })
      },
    )
  }, [host, port, lang, setRecord])
  const read = () => {
    setState({ busy: true, error: null })
    load()
  }
  // read once on entry when nothing is cached; "Read again" re-reads explicitly
  const needsRead = React.useRef(!record)
  React.useEffect(() => {
    if (!needsRead.current) return
    needsRead.current = false
    load()
  }, [load])
  const s = record?.settings
  const openPage = () => {
    const target = port === 80 ? host : `${host}:${port}`
    if (window.relay?.openBridgePage) void window.relay.openBridgePage(target)
    else window.open(`http://${target}/`, '_blank', 'noopener')
  }
  const modeWord = (m: string | undefined) => (m === 'station' ? t('conn.mode.station') : m === 'ap' ? t('conn.mode.ap') : (m ?? '—'))
  return (
    <div className="flex flex-col gap-4">
      {state.busy ? (
        <div className="flex items-center gap-2.5 text-ed-value text-[var(--g300)]" role="status">
          <span className="wiz-wait" aria-hidden="true" />
          {t('conn.reading')}
        </div>
      ) : state.error ? (
        <div className="flex flex-col gap-2 rounded-md border border-[var(--g600)] bg-[var(--g900)] px-3 py-2.5">
          <span className="text-ed-value text-[var(--g50)]">{t('conn.readFailed', { reason: state.error })}</span>
        </div>
      ) : record && (record.kind !== 'mavesp8266' || !s) ? (
        <div className="flex flex-col gap-2 rounded-md border border-[var(--g600)] bg-[var(--g900)] px-3 py-3" data-bridge-unknown="">
          <span className="text-ed-value font-[560] text-[var(--g50)]">{t('conn.unknownFirmware')}</span>
          <span className="text-hud-sub text-[var(--g400)]">{t('conn.unknownBody')}</span>
          <div>
            <Button variant="outline" onClick={openPage} className="h-7 gap-1.5 text-[12px]">
              <Icon icon="external" />
              {t('conn.openSetup')}
            </Button>
          </div>
        </div>
      ) : s ? (
        <div className="flex flex-col gap-1.5" data-bridge-read="">
          <span className="cap">{t('conn.current')}</span>
          <KV
            rows={[
              [t('conn.field.firmware'), `mavesp8266 ${s.version ?? record?.version ?? ''}`.trim()],
              [t('conn.field.mode'), modeWord(s.mode)],
              [t('conn.field.apSsid'), s.ap_ssid ?? '—'],
              [t('conn.field.channel'), s.channel ?? '—'],
              [t('conn.field.staSsid'), s.sta_ssid || '—'],
              [t('conn.field.hostPort'), s.host_port ?? '—'],
              [t('conn.field.clientPort'), s.client_port ?? '—'],
              [t('conn.field.baud'), s.baud ?? '—'],
            ]}
          />
        </div>
      ) : null}
      <div className="flex justify-between gap-2">
        <Button variant="ghost" onClick={onBack} className="h-8 text-[12px]">
          {t('conn.back')}
        </Button>
        <div className="flex gap-2">
          {!state.busy ? (
            <Button variant="outline" onClick={read} className="h-8 gap-1.5 text-[12px]">
              <Icon icon="refresh" />
              {t('conn.readAgain')}
            </Button>
          ) : null}
          <Button onClick={onNext} disabled={!s || state.busy} className="h-8 gap-1.5 text-[12px]">
            {t('conn.next')}
            <Icon icon="chevron.right" />
          </Button>
        </div>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------ step 3

interface Form {
  mode: 'station' | 'ap'
  ssid: string
  password: string
  staticIp: boolean
  ip: string
  gateway: string
  subnet: string
  hostPort: string
  clientPort: string
  baud: number
}

export function validateForm(f: Form): Partial<Record<'ssid' | 'password' | 'ip' | 'port', AnyKey>> {
  const e: Partial<Record<'ssid' | 'password' | 'ip' | 'port', AnyKey>> = {}
  if (f.mode === 'station') {
    const ssid = f.ssid.trim()
    if (!ssid || ssid.length > 16) e.ssid = 'conn.err.ssid'
    if (f.password.length < 8 || f.password.length > 16) e.password = 'conn.err.password'
    if (f.staticIp && ![f.ip, f.gateway, f.subnet].every(isIPv4)) e.ip = 'conn.err.ip'
  }
  if (!isPort(Number(f.hostPort)) || !isPort(Number(f.clientPort))) e.port = 'conn.err.port'
  return e
}

export function toConfig(f: Form): BridgeConfig {
  const base: BridgeConfig = { mode: f.mode, host_port: Number(f.hostPort), client_port: Number(f.clientPort), baud: f.baud }
  if (f.mode === 'ap') return base
  return {
    ...base,
    ssid: f.ssid.trim(),
    password: f.password,
    ip: f.staticIp ? f.ip : '0.0.0.0',
    gateway: f.staticIp ? f.gateway : '0.0.0.0',
    subnet: f.staticIp ? f.subnet : '0.0.0.0',
  }
}

function ConfigureStep({ record, host, port, onBack, onWritten }: {
  record: BridgeRecord
  host: string
  port: number
  onBack(): void
  onWritten(network: string): void
}) {
  const t = useT8()
  const lang = useLang()
  const s = record.settings!
  const [form, setForm] = React.useState<Form>(() => ({
    mode: 'station',
    ssid: record.last_network_ssid || s.sta_ssid || '',
    password: '',
    staticIp: !s.dhcp && !!s.sta_ip && s.sta_ip !== '0.0.0.0',
    ip: s.sta_ip && s.sta_ip !== '0.0.0.0' ? s.sta_ip : '',
    gateway: s.sta_gateway && s.sta_gateway !== '0.0.0.0' ? s.sta_gateway : '',
    subnet: s.sta_subnet && s.sta_subnet !== '0.0.0.0' ? s.sta_subnet : '255.255.255.0',
    hostPort: String(s.host_port ?? 14550),
    clientPort: String(s.client_port ?? 14555),
    baud: s.baud ?? 921600,
  }))
  const [show, setShow] = React.useState(false)
  const [touched, setTouched] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const errors = validateForm(form)
  const ok = Object.keys(errors).length === 0
  const set = (patch: Partial<Form>) => setForm((f) => ({ ...f, ...patch }))
  const ids = { ssid: React.useId(), pwd: React.useId(), hp: React.useId(), cp: React.useId(), baud: React.useId(), ip: React.useId(), gw: React.useId(), sn: React.useId() }
  const station = form.mode === 'station'

  const write = async () => {
    setTouched(true)
    if (!ok || busy) return
    setBusy(true)
    setError(null)
    try {
      await net.configureBridge(host, port, toConfig(form))
      setForm((f) => ({ ...f, password: '' }))
      onWritten(station ? form.ssid.trim() : (s.ap_ssid ?? ''))
    } catch (err) {
      setError(t('conn.writeFailed', { reason: errText(err, lang) }))
    } finally {
      setBusy(false)
    }
  }

  const cfg = toConfig(form)
  const review: [string, React.ReactNode][] = [
    [t('conn.field.mode'), station ? t('conn.mode.station') : t('conn.mode.ap')],
    ...(station
      ? ([
          [t('conn.field.staSsid'), cfg.ssid || '—'],
          [t('conn.field.password'), form.password ? `${'•'.repeat(Math.min(form.password.length, 16))} · ${t('conn.hidden')}` : '—'],
          [t('conn.field.ip'), form.staticIp ? `${cfg.ip} · ${cfg.gateway} · ${cfg.subnet}` : `${t('conn.field.dhcp')} · 0.0.0.0`],
        ] as [string, React.ReactNode][])
      : []),
    [t('conn.field.hostPort'), cfg.host_port],
    [t('conn.field.clientPort'), cfg.client_port],
    [t('conn.field.baud'), cfg.baud],
    [t('conn.fallbackHotspot'), `${s.ap_ssid ?? '—'} · ${t('conn.kept')}`],
  ]

  return (
    <form
      className="flex flex-col gap-4"
      data-bridge-configure=""
      onSubmit={(e) => {
        e.preventDefault()
        void write()
      }}
    >
      <Field label={t('conn.field.mode')}>
        <ToggleGroup value={[form.mode]} onValueChange={(v: unknown[]) => v[0] && set({ mode: v[0] as Form['mode'] })} spacing={0} className="w-64 rounded-md bg-[var(--g900)] p-0.5 ring-1 ring-[var(--border)]">
          <ToggleGroupItem value="station" className="h-7 flex-1 rounded-[5px] text-[12px] text-[var(--g400)] hover:bg-transparent data-[pressed]:text-[var(--g50)]">
            {t('conn.mode.station')}
          </ToggleGroupItem>
          <ToggleGroupItem value="ap" className="h-7 flex-1 rounded-[5px] text-[12px] text-[var(--g400)] hover:bg-transparent data-[pressed]:text-[var(--g50)]">
            {t('conn.mode.ap')}
          </ToggleGroupItem>
        </ToggleGroup>
      </Field>
      {station ? (
        <>
          <div className="grid grid-cols-2 gap-3">
            <Field label={t('conn.field.staSsid')} htmlFor={ids.ssid} hint={t('conn.ssidHint')} error={touched && errors.ssid ? t(errors.ssid) : null}>
              <Input id={ids.ssid} value={form.ssid} maxLength={32} spellCheck={false} autoComplete="off" onChange={(e) => set({ ssid: e.target.value })} className="h-8 text-[12px]" aria-invalid={touched && !!errors.ssid ? true : undefined} />
            </Field>
            <Field label={t('conn.field.password')} htmlFor={ids.pwd} hint={t('conn.passwordHint')} error={touched && errors.password ? t(errors.password) : null}>
              <div className="relative">
                <Input
                  id={ids.pwd}
                  type={show ? 'text' : 'password'}
                  value={form.password}
                  maxLength={32}
                  autoComplete="new-password"
                  spellCheck={false}
                  onChange={(e) => set({ password: e.target.value })}
                  className="h-8 pr-8 font-mono text-[12px]"
                  aria-invalid={touched && !!errors.password ? true : undefined}
                />
                <Button type="button" variant="ghost" size="icon-sm" className="absolute top-1 right-1 text-[var(--g400)]" onClick={() => setShow((v) => !v)} aria-label={show ? t('conn.hidePassword') : t('conn.showPassword')}>
                  <StateIcon icon="layer.visible" alt={!show} />
                </Button>
              </div>
            </Field>
          </div>
          <div className="flex flex-col gap-2">
            <label className="flex items-center gap-2 text-ed-value text-[var(--g200)]">
              <Switch checked={form.staticIp} onCheckedChange={(v) => set({ staticIp: v })} size="sm" />
              {t('conn.useStatic')}
            </label>
            {form.staticIp ? (
              <div className="grid grid-cols-3 gap-3">
                <Field label={t('conn.field.ip')} htmlFor={ids.ip}>
                  <Input id={ids.ip} value={form.ip} onChange={(e) => set({ ip: e.target.value })} className="h-8 font-mono text-[12px]" placeholder="10.7.7.36" />
                </Field>
                <Field label={t('conn.field.gateway')} htmlFor={ids.gw}>
                  <Input id={ids.gw} value={form.gateway} onChange={(e) => set({ gateway: e.target.value })} className="h-8 font-mono text-[12px]" placeholder="10.7.7.1" />
                </Field>
                <Field label={t('conn.field.subnet')} htmlFor={ids.sn}>
                  <Input id={ids.sn} value={form.subnet} onChange={(e) => set({ subnet: e.target.value })} className="h-8 font-mono text-[12px]" />
                </Field>
                {touched && errors.ip ? <span className="col-span-3 text-hud-sub text-[var(--g50)]">{t(errors.ip)}</span> : null}
              </div>
            ) : (
              <span className="text-hud-sub text-[var(--g500)]">{t('conn.dhcpNote')}</span>
            )}
          </div>
        </>
      ) : (
        <p className="text-hud-sub text-[var(--g400)]">{t('conn.apNote')}</p>
      )}
      <div className="grid grid-cols-3 gap-3">
        <Field label={t('conn.field.hostPort')} htmlFor={ids.hp}>
          <Input id={ids.hp} value={form.hostPort} inputMode="numeric" onChange={(e) => set({ hostPort: e.target.value.replace(/[^\d]/g, '') })} className="h-8 font-mono text-[12px]" />
        </Field>
        <Field label={t('conn.field.clientPort')} htmlFor={ids.cp}>
          <Input id={ids.cp} value={form.clientPort} inputMode="numeric" onChange={(e) => set({ clientPort: e.target.value.replace(/[^\d]/g, '') })} className="h-8 font-mono text-[12px]" />
        </Field>
        <Field label={t('conn.field.baud')} htmlFor={ids.baud}>
          <NativeSelect id={ids.baud} value={String(form.baud)} onChange={(e) => set({ baud: Number(e.target.value) })} className="w-full [&_select]:h-8 [&_select]:font-mono [&_select]:text-[12px]">
            {BAUDS.map((b) => (
              <NativeSelectOption key={b} value={b}>
                {b}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </Field>
        {touched && errors.port ? <span className="col-span-3 text-hud-sub text-[var(--g50)]">{t(errors.port)}</span> : null}
      </div>
      <div className="flex flex-col gap-1.5">
        <span className="cap">{t('conn.review')}</span>
        <KV rows={review} />
        <p className="text-hud-sub text-[var(--g400)]">{t('conn.fallback')}</p>
      </div>
      {error ? <p className="text-ed-value text-[var(--g50)]" role="alert">{error}</p> : null}
      {/* the form is taller than the dialog on short screens; keep the actions in view */}
      <div className="sticky bottom-0 -mx-5 -mb-5 flex justify-between gap-2 border-t border-[var(--border)] bg-[var(--popover)] px-5 py-3">
        <Button type="button" variant="ghost" onClick={onBack} className="h-8 text-[12px]">
          {t('conn.back')}
        </Button>
        <Button type="submit" disabled={busy || (touched && !ok)} className="h-8 gap-1.5 text-[12px]">
          <Icon icon="refresh" />
          {busy ? t('conn.writing') : t('conn.writeRestart')}
        </Button>
      </div>
    </form>
  )
}

// ------------------------------------------------------------------------------------------------ step 4

function RejoinStep({ network, known, onDone }: { network: string; known: ReadonlySet<string>; onDone(): void }) {
  const t = useT8()
  const [round, setRound] = React.useState(0)
  const [elapsed, setElapsed] = React.useState(0)
  const found = useRelay((s) => s.fleet?.vehicles.find((v) => v.kind === 'real' && v.host && !known.has(v.host) && v.link.state === 'ok') ?? null)
  const foundId = found?.id ?? null
  const foundHost = found?.host ?? null

  React.useEffect(() => {
    if (foundId) return
    const t0 = performance.now()
    const tick = setInterval(() => {
      const s = Math.floor((performance.now() - t0) / 1000)
      setElapsed(s)
      if (s > 0 && s % RESCAN_EVERY_S === 0 && s < REJOIN_TIMEOUT_S && !useRelay.getState().fleet?.scan.running) api.discover().catch(() => {})
      if (s >= REJOIN_TIMEOUT_S) clearInterval(tick)
    }, 1000)
    // the first scan starts a few seconds in, once the bridge has had time to restart and join
    const first = setTimeout(() => {
      if (!useRelay.getState().fleet?.scan.running) api.discover().catch(() => {})
    }, 4000)
    return () => {
      clearInterval(tick)
      clearTimeout(first)
    }
  }, [round, foundId])

  const timedOut = !foundId && elapsed >= REJOIN_TIMEOUT_S
  const show = () => {
    if (foundId) {
      useUi.getState().setWorkspace('fly')
      useUi.getState().select(foundId)
    }
    onDone()
  }
  return (
    <div className="flex flex-col gap-4" data-bridge-rejoin={foundId ? 'found' : timedOut ? 'timeout' : 'looking'}>
      <p className="text-hud-sub text-[var(--g300)]">{t('conn.written')}</p>
      <p className="text-ed-value text-[var(--g100)]">{t('conn.rejoin', { network: network || '—' })}</p>
      {foundId ? (
        <div className="flex items-center gap-2.5 rounded-md border border-[var(--g500)] bg-[var(--g900)] px-3 py-2.5" role="status">
          <Icon icon="check.circle" className="size-4 text-[var(--g50)]" />
          <div className="flex flex-col">
            <span className="text-ed-value font-[560] text-[var(--g50)]">{t('conn.found', { ip: foundHost ?? '' })}</span>
            <span className="text-hud-sub text-[var(--g400)]">{t('conn.foundNew', { network: network || '—' })}</span>
          </div>
        </div>
      ) : timedOut ? (
        <div className="flex flex-col gap-2 rounded-md border border-[var(--g600)] bg-[var(--g900)] px-3 py-2.5" role="status">
          <span className="text-ed-value text-[var(--g50)]">{t('conn.notFound')}</span>
          <ul className="flex flex-col gap-1 pl-1">
            {(['conn.check.band', 'conn.check.password', 'conn.check.isolation', 'conn.check.dhcp'] as const).map((k) => (
              <li key={k} className="flex items-center gap-2 text-hud-sub text-[var(--g300)]">
                <span className="h-px w-2 bg-[var(--g500)]" aria-hidden="true" />
                {t(k)}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="flex flex-col gap-2" role="status">
          <span className="num text-[11px] text-[var(--g300)]">{t('conn.looking', { s: elapsed })}</span>
          <span className="scan-line" aria-hidden="true">
            <i style={{ transform: `scaleX(${Math.min(1, elapsed / REJOIN_TIMEOUT_S)})` }} />
          </span>
        </div>
      )}
      <div className="flex justify-end gap-2">
        {timedOut ? (
          <Button variant="outline" onClick={() => {
            setElapsed(0)
            setRound((r) => r + 1)
          }} className="h-8 gap-1.5 text-[12px]">
            <Icon icon="refresh" />
            {t('conn.lookAgain')}
          </Button>
        ) : null}
        <Button onClick={show} variant={foundId ? 'default' : 'outline'} className="h-8 text-[12px]">
          {t('action.done')}
        </Button>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------ wizard

export function BridgeWizard({ onDone }: { onDone(): void }) {
  const t = useT8()
  const [step, setStep] = React.useState<Step>(1)
  const [address, setAddress] = React.useState(HOTSPOT_IP)
  const [record, setRecord] = React.useState<BridgeRecord | null>(null)
  const [network, setNetwork] = React.useState('')
  const [known, setKnown] = React.useState<ReadonlySet<string>>(new Set())
  const target = parseHostPort(address, 80)
  const toRead = React.useCallback(() => {
    setRecord(null)
    setStep(2)
  }, [])

  return (
    <div className="flex flex-col gap-5" data-bridge-wizard={step}>
      <Stepper step={step} />
      {step === 1 ? <ConnectStep address={address} setAddress={setAddress} onNext={toRead} /> : null}
      {step === 2 && target ? <ReadStep host={target.host} port={target.port} record={record} setRecord={setRecord} onBack={() => setStep(1)} onNext={() => setStep(3)} /> : null}
      {step === 3 && target && record?.settings ? (
        <ConfigureStep
          record={record}
          host={target.host}
          port={target.port}
          onBack={() => setStep(2)}
          onWritten={(n) => {
            setNetwork(n)
            setKnown(new Set((useRelay.getState().fleet?.vehicles ?? []).filter((v) => v.link.state === 'ok').map((v) => v.host).filter((h): h is string => !!h)))
            setStep(4)
          }}
        />
      ) : null}
      {step === 4 ? <RejoinStep network={network} known={known} onDone={onDone} /> : null}
      <span className="sr-only" aria-live="polite">
        {t('conn.stepOf', { n: step })}
      </span>
    </div>
  )
}
