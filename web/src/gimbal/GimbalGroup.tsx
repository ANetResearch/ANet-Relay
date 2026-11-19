// Inspector Overview "Gimbal" block (12 §5.2, §5.5). For an aircraft with a pod: a rate pad (rate while held, stop on
// release, Center in the middle), the lens switch, zoom (slider on a log scale and a preset stepper with the current
// multiple and range), Photo, Record, Follow / Lock, the laser with its range, the infrared palette while an
// infrared lens is on, the tracking state with Stop, the link of a real pod with Detach, and the recent captures
// (thumbnail when the stage rendered one, time, lens and zoom, laser range). For a real aircraft without a pod: the
// attach form (TCP host and port 2000, or serial device and baud). Commands are the operator's (the window is the
// operator when the backend says so); refusals come back as toasts; every disabled control says why.
import * as React from 'react'
import { api, type GimbalCapture, type GimbalView, type Vehicle } from '@/api'
import { useT, type MessageKey } from '@/i18n'
import { clock } from '@/lib/format'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/shell/notify'
import { useHello, useVehicle } from '@/store/selectors'
import { Button } from '@/ui/components/ui/button'
import { Input } from '@/ui/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/ui/components/ui/native-select'
import { Slider } from '@/ui/components/ui/slider'
import { Icon, type IconKey } from '@/ui/icons'
import { LinkDot } from '@/ui/relay'
import { Segmented } from '@/control/ControllerSettings'
import { Block } from '@/inspector/parts'
import { useVideo } from '@/video/devices'
import {
  attachBody, attachErrors, DEFAULT_ATTACH, isFixed, padSpeed, sliderToZoom, zoomToSlider, isInfrared, LENS_SHORT, LENS_TABLE, LENSES, lensBody, onBody, rangeText, rateBody, signedDeg, stepZoom, zoomBody, zoomText,
  type AttachForm, type Lens, type Palette,
} from './model'
import { useGimbalUi, watchCaptures, type Thumb } from './store'
import './gimbal.css'

function useSend(id: string) {
  const [busy, setBusy] = React.useState<string | null>(null)
  const send = React.useCallback(
    async (key: string, body: { action: string } & Record<string, unknown>) => {
      setBusy(key)
      try {
        await api.gimbalDo(id, body)
        return true
      } catch (e) {
        notifyError(e)
        return false
      } finally {
        setBusy(null)
      }
    },
    [id],
  )
  return { busy, send }
}

/** Why the pod cannot be commanded now, or null. */
export function gimbalBlock(g: Pick<GimbalView, 'link' | 'motors'>, operator: boolean): MessageKey | null {
  if (!operator) return 'gimbal.block.operator'
  if (g.link === 'lost') return 'gimbal.block.lost'
  if (g.link === 'connecting') return 'gimbal.block.connecting'
  return null
}

const PAD: { key: string; icon: IconKey; dx: number; dy: number; label: MessageKey }[] = [
  { key: 'up', icon: 'chevron.up', dx: 0, dy: 1, label: 'gimbal.pad.up' },
  { key: 'left', icon: 'chevron.left', dx: -1, dy: 0, label: 'gimbal.pad.left' },
  { key: 'right', icon: 'chevron.right', dx: 1, dy: 0, label: 'gimbal.pad.right' },
  { key: 'down', icon: 'chevron.down', dx: 0, dy: -1, label: 'gimbal.pad.down' },
]

/** Four-way rate pad: rate while held, stop on release (and on blur); Center in the middle. */
function RatePad({ id, g, disabled, title }: { id: string; g: GimbalView; disabled: boolean; title?: string }) {
  const t = useT()
  const [held, setHeld] = React.useState<string | null>(null)
  const heldRef = React.useRef<string | null>(null)
  const stop = React.useCallback(() => {
    if (!heldRef.current) return
    heldRef.current = null
    setHeld(null)
    api.gimbalDo(id, { action: 'stop' }).catch(notifyError)
  }, [id])
  React.useEffect(() => {
    window.addEventListener('blur', stop)
    return () => {
      window.removeEventListener('blur', stop)
      stop()
    }
  }, [stop])
  const press = (p: (typeof PAD)[number]) => (e: React.PointerEvent<HTMLButtonElement>) => {
    if (e.button !== 0 || disabled) return
    e.currentTarget.setPointerCapture(e.pointerId)
    heldRef.current = p.key
    setHeld(p.key)
    api.gimbalDo(id, rateBody(p.dx, p.dy, padSpeed(g.zoom))).catch((err: unknown) => {
      heldRef.current = null
      setHeld(null)
      notifyError(err)
    })
  }
  const cell = (p: (typeof PAD)[number]) => (
    <Button
      key={p.key}
      variant="outline"
      size="icon"
      disabled={disabled}
      title={title ?? t(p.label)}
      aria-label={t(p.label)}
      data-gimbal-pad={p.key}
      data-held={held === p.key ? '' : undefined}
      onPointerDown={press(p)}
      onPointerUp={stop}
      onPointerCancel={stop}
      onLostPointerCapture={stop}
      onKeyDown={(e) => {
        if ((e.key === ' ' || e.key === 'Enter') && !heldRef.current && !disabled) {
          e.preventDefault()
          heldRef.current = p.key
          setHeld(p.key)
          api.gimbalDo(id, rateBody(p.dx, p.dy, padSpeed(g.zoom))).catch(notifyError)
        }
      }}
      onKeyUp={(e) => {
        if (e.key === ' ' || e.key === 'Enter') stop()
      }}
    >
      <Icon icon={p.icon} />
    </Button>
  )
  return (
    <div className="gimbal-pad" data-gimbal-padset="">
      <span />
      {cell(PAD[0])}
      <span />
      {cell(PAD[1])}
      <Button variant="outline" size="icon" disabled={disabled} title={title ?? t('gimbal.center')} aria-label={t('gimbal.center')} onClick={() => api.gimbalDo(id, { action: 'center' }).catch(notifyError)} data-gimbal-center="">
        <Icon icon="gimbal.center" />
      </Button>
      {cell(PAD[2])}
      <span />
      {cell(PAD[3])}
      <span />
    </div>
  )
}

/** Zoom: a stepper through the presets, the multiple and the range, and a log-scale slider. */
function ZoomControl({ id, g, disabled, title }: { id: string; g: GimbalView; disabled: boolean; title?: string }) {
  const t = useT()
  const fixed = isFixed(g.lens)
  const [lo, hi] = LENS_TABLE[g.lens].zoom
  const [drag, setDrag] = React.useState<number | null>(null)
  const shown = drag ?? g.zoom
  const set = (z: number) => api.gimbalDo(id, zoomBody(g.lens, z)).catch(notifyError)
  const why = fixed ? t('gimbal.zoom.fixed', { lens: LENS_SHORT[g.lens] }) : title
  return (
    <div className="flex flex-col gap-1.5" data-gimbal-zoom="">
      <div className="flex items-center gap-1.5">
        <Button variant="outline" size="icon-sm" disabled={disabled || fixed || g.zoom <= lo + 0.05} onClick={() => void set(stepZoom(g.lens, g.zoom, -1))} title={why ?? t('gimbal.zoom.out')} aria-label={t('gimbal.zoom.out')} data-gimbal-zoom-out="">
          <Icon icon="minus" />
        </Button>
        <span className="num w-12 text-center text-[13px] font-[560] text-[var(--g50)]" data-gimbal-zoom-value="">{zoomText(shown)}</span>
        <Button variant="outline" size="icon-sm" disabled={disabled || fixed || g.zoom >= hi - 0.05} onClick={() => void set(stepZoom(g.lens, g.zoom, 1))} title={why ?? t('gimbal.zoom.in')} aria-label={t('gimbal.zoom.in')} data-gimbal-zoom-in="">
          <Icon icon="plus" />
        </Button>
        <span className="flex-1" />
        <span className="num text-[11px] text-[var(--g500)]">{fixed ? t('gimbal.zoom.fixedShort') : `${zoomText(lo)}–${zoomText(hi)}`}</span>
      </div>
      <Slider
        value={[zoomToSlider(g.lens, shown)]}
        min={0}
        max={1000}
        step={1}
        disabled={disabled || fixed}
        onValueChange={(x) => setDrag(sliderToZoom(g.lens, Array.isArray(x) ? x[0] : (x as number)))}
        onValueCommitted={(x) => {
          setDrag(null)
          void set(sliderToZoom(g.lens, Array.isArray(x) ? x[0] : (x as number)))
        }}
        aria-label={t('gimbal.zoom')}
        title={why}
      />
    </div>
  )
}

function Toggle({ on, icon, label, onClick, disabled, title, data, children }: { on: boolean; icon: IconKey; label: string; onClick(): void; disabled: boolean; title?: string; data: string; children?: React.ReactNode }) {
  return (
    <Button
      variant="outline"
      size="sm"
      disabled={disabled}
      onClick={onClick}
      title={title ?? label}
      aria-pressed={on}
      className={cn(on && 'border-[var(--g500)] bg-[var(--g800)] text-[var(--g50)]')}
      {...{ [`data-gimbal-${data}`]: on ? 'on' : 'off' }}
    >
      <Icon icon={icon} />
      {children ?? label}
    </Button>
  )
}

/** The newest captures: GET /gimbal/captures joined with the thumbnails taken in this window. */
function Captures({ id, g }: { id: string; g: GimbalView }) {
  const t = useT()
  const thumbs = useGimbalUi((s) => s.thumbs[id])
  const [list, setList] = React.useState<GimbalCapture[]>([])
  const photos = g.photos
  const recording = g.recording
  React.useEffect(() => {
    let alive = true
    api.gimbalCaptures(id, 12).then(
      (r) => alive && setList(Array.isArray(r?.captures) ? r.captures : []),
      () => alive && setList([]),
    )
    return () => {
      alive = false
    }
  }, [id, photos, recording])
  const bySeq = new Map((thumbs ?? []).filter((x) => x.seq !== null).map((x) => [x.seq, x]))
  const rows: { key: string; thumb: Thumb | null; c: GimbalCapture | null }[] = list.length
    ? list.filter((c) => c.kind === 'photo').slice(-4).reverse().map((c) => ({ key: `c${c.seq}`, c, thumb: bySeq.get(c.seq) ?? null }))
    : [...(thumbs ?? [])].slice(-4).reverse().map((th, i) => ({ key: `t${th.seq ?? i}`, c: null, thumb: th }))
  return (
    <div className="flex flex-col gap-1.5" data-gimbal-captures={rows.length}>
      <div className="flex h-5 items-center gap-2">
        <span className="text-hud-sub text-[var(--g400)]">{t('gimbal.captures')}</span>
        <span className="num text-hud-cap text-[var(--g500)]">{photos}</span>
      </div>
      {rows.length === 0 ? (
        <p className="text-hud-sub text-[var(--g500)]">{t('gimbal.captures.none')}</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {rows.map(({ key, c, thumb }) => {
            const lens = (c?.lens ?? thumb?.lens) as Lens | null
            const zoom = c?.zoom ?? thumb?.zoom ?? null
            const range = rangeText(c?.laser_range_m)
            return (
              <li key={key} className="flex items-center gap-2.5" data-gimbal-capture={c?.seq ?? thumb?.seq ?? ''}>
                <span className="gimbal-thumb">
                  {thumb?.url ? <img src={thumb.url} alt="" /> : <Icon icon="gimbal.photo" className="absolute inset-0 m-auto size-3.5 text-[var(--g600)]" />}
                </span>
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="num text-[11px] text-[var(--g100)]">{clock(c?.t ?? thumb?.t) ?? '—'}</span>
                  <span className="num truncate text-[11px] text-[var(--g500)]">
                    {[lens ? (isFixed(lens) ? LENS_SHORT[lens] : `${LENS_SHORT[lens]} ${zoomText(zoom)}`) : null, range].filter(Boolean).join(' · ')}
                  </span>
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

/** Attach a DYT pod to a real aircraft: TCP (host, port 2000) or serial (device, baud). */
function AttachForm({ v, operator }: { v: Vehicle; operator: boolean }) {
  const t = useT()
  const [f, setF] = React.useState<AttachForm>(DEFAULT_ATTACH)
  const [tried, setTried] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [ports, setPorts] = React.useState<string[]>([])
  React.useEffect(() => {
    if (f.transport !== 'serial') return
    let alive = true
    api.serialPorts().then(
      (r) => alive && setPorts((r?.ports ?? []).map((p) => p.device).filter((d): d is string => typeof d === 'string')),
      () => alive && setPorts([]),
    )
    return () => {
      alive = false
    }
  }, [f.transport])
  const errors = attachErrors(f)
  const err = (k: keyof typeof errors) => (tried && errors[k] ? t(`gimbal.attach.err.${errors[k]}` as MessageKey) : null)
  const submit = () => {
    setTried(true)
    if (Object.keys(attachErrors(f)).length || busy || !operator) return
    setBusy(true)
    api
      .gimbalAttach(v.id, attachBody(f))
      .then(() => notify(t('gimbal.attached', { aircraft: v.label })))
      .catch(notifyError)
      .finally(() => setBusy(false))
  }
  const stop = (e: React.KeyboardEvent) => {
    e.stopPropagation()
    if (e.key === 'Enter') submit()
  }
  return (
    <div className="flex flex-col gap-2" data-gimbal-attach="">
      <p className="text-hud-sub text-[var(--g400)]">{t('gimbal.attach.none')}</p>
      <div className="flex items-center gap-2">
        <Segmented<'tcp' | 'serial'>
          value={f.transport}
          onChange={(transport) => setF((x) => ({ ...x, transport }))}
          label={t('gimbal.attach.transport')}
          options={[
            { value: 'tcp', label: t('gimbal.attach.tcp') },
            { value: 'serial', label: t('gimbal.attach.serial') },
          ]}
        />
      </div>
      {f.transport === 'tcp' ? (
        <div className="grid grid-cols-[minmax(0,1fr)_72px] gap-2">
          <label className="flex min-w-0 flex-col gap-1">
            <span className="text-[11px] leading-4 text-[var(--g300)]">{t('gimbal.attach.host')}</span>
            <Input value={f.host} placeholder="10.0.0.5" onChange={(e) => setF((x) => ({ ...x, host: e.target.value }))} onKeyDown={stop} aria-invalid={Boolean(err('host')) || undefined} className="num h-7 text-[12px]" data-gimbal-host="" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] leading-4 text-[var(--g300)]">{t('gimbal.attach.port')}</span>
            <Input value={f.port} inputMode="numeric" onChange={(e) => setF((x) => ({ ...x, port: e.target.value }))} onKeyDown={stop} aria-invalid={Boolean(err('port')) || undefined} className="num h-7 text-right text-[12px]" />
          </label>
        </div>
      ) : (
        <div className="grid grid-cols-[minmax(0,1fr)_96px] gap-2">
          <label className="flex min-w-0 flex-col gap-1">
            <span className="text-[11px] leading-4 text-[var(--g300)]">{t('gimbal.attach.device')}</span>
            {ports.length ? (
              <NativeSelect value={f.device} onChange={(e) => setF((x) => ({ ...x, device: e.target.value }))} className="w-full [&_select]:h-7 [&_select]:text-[12px]">
                <NativeSelectOption value="">—</NativeSelectOption>
                {ports.map((p) => (
                  <NativeSelectOption key={p} value={p}>
                    {p}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            ) : (
              <Input value={f.device} placeholder="/dev/ttyUSB0" onChange={(e) => setF((x) => ({ ...x, device: e.target.value }))} onKeyDown={stop} aria-invalid={Boolean(err('device')) || undefined} className="num h-7 text-[12px]" />
            )}
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] leading-4 text-[var(--g300)]">{t('gimbal.attach.baud')}</span>
            <Input value={f.baud} inputMode="numeric" onChange={(e) => setF((x) => ({ ...x, baud: e.target.value }))} onKeyDown={stop} aria-invalid={Boolean(err('baud')) || undefined} className="num h-7 text-right text-[12px]" />
          </label>
        </div>
      )}
      {(['host', 'port', 'device', 'baud'] as const).map((k) => (err(k) ? <p key={k} className="text-hud-sub text-[var(--g50)]" role="alert">{err(k)}</p> : null))}
      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" disabled={busy || !operator} onClick={submit} title={operator ? undefined : t('gimbal.block.operatorAttach')} data-gimbal-attach-go="">
          <Icon icon="gimbal.attach" />
          {t('gimbal.attach.go')}
        </Button>
        <span className="text-hud-sub text-[var(--g500)]">{operator ? t('gimbal.attach.hint') : t('gimbal.block.operatorAttach')}</span>
      </div>
    </div>
  )
}

function Pod({ v, g, operator }: { v: Vehicle; g: GimbalView; operator: boolean }) {
  const t = useT()
  const { busy, send } = useSend(v.id)
  const block = gimbalBlock(g, operator)
  const off = Boolean(block) || busy !== null
  const why = block ? t(block) : undefined
  const motionOff = !g.motors
  const motionWhy = why ?? (motionOff ? t('gimbal.block.motors') : undefined)
  const range = rangeText(g.laser.range_m)
  const ir = isInfrared(g.lens)
  const tracking = g.tracking.state !== 'off'
  return (
    <div className="flex flex-col gap-3" data-gimbal={g.kind}>
      <div className="num flex items-center gap-2 text-[12px] text-[var(--g200)]" data-gimbal-state="">
        <span className="font-[560] text-[var(--g50)]">{isFixed(g.lens) ? LENS_SHORT[g.lens] : `${LENS_SHORT[g.lens]} ${zoomText(g.zoom)}`}</span>
        <span className="text-[var(--g500)]">·</span>
        <span>{t(`gimbal.mode.${g.mode}` as MessageKey)}</span>
        <span className="flex-1" />
        <span className="text-[var(--g400)]">{t('gimbal.yawShort')}</span>
        <span>{signedDeg(g.yaw_deg)}</span>
        <span className="text-[var(--g400)]">{t('gimbal.pitchShort')}</span>
        <span>{signedDeg(g.pitch_deg)}</span>
      </div>
      <div className="grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3">
        <RatePad id={v.id} g={g} disabled={Boolean(block) || motionOff} title={motionWhy} />
        <div className="flex min-w-0 flex-col gap-2.5">
          <div className="flex" data-gimbal-lens-switch={g.lens}>
            <Segmented<Lens>
              value={g.lens}
              onChange={(lens) => void send('lens', lensBody(lens))}
              label={t('gimbal.lens')}
              options={LENSES.map((l) => ({ value: l, label: LENS_SHORT[l] }))}
            />
          </div>
          <ZoomControl id={v.id} g={g} disabled={off} title={why} />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <Button variant="outline" size="sm" disabled={off} onClick={() => void send('photo', { action: 'photo' })} title={why ?? t('gimbal.photo')} data-gimbal-photo="">
          <Icon icon="gimbal.photo" />
          {t('gimbal.photo')}
        </Button>
        <Toggle on={g.recording} icon="gimbal.record" label={t('gimbal.record')} disabled={off} title={why} data="record" onClick={() => void send('record', onBody('record', !g.recording))}>
          {g.recording ? t('gimbal.recording') : t('gimbal.record')}
        </Toggle>
        <Toggle on={g.laser.on} icon="gimbal.laser" label={t('gimbal.laser')} disabled={off} title={why} data="laser" onClick={() => void send('laser', onBody('laser', !g.laser.on))}>
          {t('gimbal.laser')}
          {g.laser.on ? <span className="num text-[var(--g200)]" data-gimbal-laser-range="">{range ?? t('gimbal.laser.noReturn')}</span> : null}
        </Toggle>
      </div>
      <div className="flex items-center gap-2" data-gimbal-hold={g.mode}>
        <span className="text-hud-sub text-[var(--g400)]" title={t('gimbal.holdHint')}>{t('gimbal.hold')}</span>
        <span className="flex-1" />
        {/* in rate, angle, track or scan neither is pressed: the pod is not holding a heading of its own */}
        <Segmented<'follow' | 'lock'>
          value={g.mode as 'follow' | 'lock'}
          onChange={(m) => void send(m, onBody(m, true))}
          label={t('gimbal.hold')}
          options={[
            { value: 'follow', label: t('gimbal.follow') },
            { value: 'lock', label: t('gimbal.lock') },
          ]}
        />
      </div>
      {ir ? (
        <div className="flex items-center gap-2" data-gimbal-palette={g.palette}>
          <span className="text-hud-sub text-[var(--g400)]">{t('gimbal.palette')}</span>
          <span className="flex-1" />
          <Segmented<Palette>
            value={g.palette}
            onChange={(palette) => void send('palette', { action: 'palette', palette })}
            label={t('gimbal.palette')}
            options={[
              { value: 'white_hot', label: t('gimbal.palette.white_hot') },
              { value: 'black_hot', label: t('gimbal.palette.black_hot') },
              { value: 'pseudo', label: t('gimbal.palette.pseudo') },
            ]}
          />
        </div>
      ) : null}
      <div className="flex min-h-6 items-center gap-2" data-gimbal-tracking={g.tracking.state}>
        <Icon icon="crosshair" className={cn('size-3.5', tracking ? 'text-[var(--g50)]' : 'text-[var(--g500)]')} />
        <span className={cn('text-hud-sub', tracking ? 'text-[var(--g100)]' : 'text-[var(--g500)]')}>
          {tracking ? t(g.tracking.state === 'locked' ? 'gimbal.track.lockedOn' : 'gimbal.track.searching', { algorithm: t(`gimbal.algorithm.${g.tracking.algorithm}` as MessageKey) }) : g.kind === 'sim' ? t('gimbal.track.hint') : t('gimbal.track.off')}
        </span>
        <span className="flex-1" />
        {tracking ? (
          <Button variant="ghost" size="sm" disabled={off} onClick={() => void send('track_stop', { action: 'track_stop' })} data-gimbal-track-stop="">
            {t('gimbal.track.stop')}
          </Button>
        ) : null}
      </div>
      <Captures id={v.id} g={g} />
      {block ? <p className="text-hud-sub text-[var(--g500)]" data-gimbal-block={block}>{t(block)}</p> : null}
    </div>
  )
}

function HeaderRight({ v, g, operator }: { v: Vehicle; g: GimbalView | null; operator: boolean }) {
  const t = useT()
  const hidden = useVideo((s) => Boolean(s.hidden[v.id]))
  const setHidden = useVideo((s) => s.setHidden)
  const bound = useVideo((s) => Boolean(s.bindings[v.id]))
  const [busy, setBusy] = React.useState(false)
  if (!g) return null
  const detach = () => {
    setBusy(true)
    api
      .gimbalDetach(v.id)
      .then(() => notify(t('gimbal.detached', { aircraft: v.label })))
      .catch(notifyError)
      .finally(() => setBusy(false))
  }
  // the PiP shows a simulated pod's picture, or the HUD of a real pod (over its video when one is bound)
  const showable = g.kind === 'sim' || g.kind === 'dyt' || bound
  return (
    <span className="flex items-center gap-1.5">
      {g.kind === 'dyt' ? (
        <span className="num flex items-center gap-1.5 text-[11px] text-[var(--g400)]" data-gimbal-link={g.link} title={g.address ?? undefined}>
          <LinkDot state={g.link === 'ok' ? 'ok' : g.link === 'connecting' ? 'waiting' : 'lost'} />
          {t(`gimbal.link.${g.link}` as MessageKey)}
        </span>
      ) : null}
      {showable && hidden ? (
        <Button variant="ghost" size="xs" onClick={() => setHidden(v.id, false)} data-gimbal-show="">
          <Icon icon="gimbal.view" />
          {t('gimbal.showView')}
        </Button>
      ) : null}
      {g.kind === 'dyt' ? (
        <Button variant="ghost" size="xs" disabled={busy || !operator} onClick={detach} title={operator ? undefined : t('gimbal.block.operatorAttach')} data-gimbal-detach="">
          <Icon icon="gimbal.detach" />
          {t('gimbal.detach')}
        </Button>
      ) : null}
    </span>
  )
}

/** The inspector's Gimbal block; nothing for a simulator without a pod. */
export function GimbalGroup({ vehicleId }: { vehicleId: string }) {
  const t = useT()
  const v = useVehicle(vehicleId)
  const operator = Boolean(useHello()?.operator)
  React.useEffect(() => watchCaptures(), [])
  const g = v?.gimbal ?? null
  if (!v || (!g && v.kind !== 'real')) return null
  return (
    <Block title={t('gimbal.title')} right={<HeaderRight v={v} g={g} operator={operator} />}>
      {g ? <Pod v={v} g={g} operator={operator} /> : <AttachForm v={v} operator={operator} />}
      {g?.kind === 'dyt' && g.address ? <p className="addr pt-1">{g.address}</p> : null}
    </Block>
  )
}
