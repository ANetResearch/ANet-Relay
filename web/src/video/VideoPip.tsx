// Video (02 §4.4, §9; R-17). VideoPip: stage picture-in-picture of the selected aircraft's bound source, 320 x 180 by
// default, resizable to 640 x 360 from its inner corner, draggable to any corner where it snaps, closable; it keeps
// clear of the course HUD and the stage HUD strip. VideoSourceGroup: the inspector Overview "Video" group with the
// 16:9 thumbnail, source select and binding. States: No video device, Camera access is off, No signal, LIVE.
import * as React from 'react'
import { createPortal } from 'react-dom'
import { env } from '@/api'
import { useT } from '@/i18n'
import { cn } from '@/lib/utils'
import { useRelay } from '@/store/relay'
import { useVehicleField } from '@/store/selectors'
import { Button } from '@/ui/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/ui/components/ui/select'
import { Icon } from '@/ui/icons'
import { Group } from '@/ui/relay'
import { useStageFrame } from '@/control/stageFrame'
import { useCT } from '@/control/strings'
import '@/control/control.css'
import {
  acquireStream,
  BLACK_LUMA,
  classifyMediaError,
  clampWidth,
  getDevices,
  meanLuma,
  nearestCorner,
  refreshDevices,
  releaseStream,
  resolveDevice,
  signalState,
  subscribeDevices,
  useVideo,
  type PipCorner,
  type StreamError,
  type VideoDevice,
} from './devices'

export interface VideoPipProps {
  vehicleId: string | null
}

type FeedState = 'unbound' | 'nodevice' | 'denied' | 'missing' | 'failed' | 'starting' | 'live' | 'nosignal'

function useDevices() {
  return React.useSyncExternalStore(subscribeDevices, getDevices, getDevices)
}

/** The bound source of an aircraft and its stream (shared between thumbnail and PiP). */
function useFeed(vehicleId: string | null, active: boolean): { state: FeedState; stream: MediaStream | null; device: VideoDevice | null } {
  const binding = useVideo((s) => (vehicleId ? s.bindings[vehicleId] : undefined))
  const list = useDevices()
  const device = resolveDevice(binding, list.devices)
  const [res, setRes] = React.useState<{ id: string | null; stream: MediaStream | null; error: StreamError | null }>({ id: null, stream: null, error: null })
  const deviceId = device?.deviceId || null
  React.useEffect(() => {
    if (!active || !deviceId) return
    let alive = true
    acquireStream(deviceId).then(
      (stream) => alive && setRes({ id: deviceId, stream, error: null }),
      (e: unknown) => alive && setRes({ id: deviceId, stream: null, error: classifyMediaError(e) }),
    )
    return () => {
      alive = false
      releaseStream(deviceId)
    }
  }, [active, deviceId])
  if (!binding) return { state: 'unbound', stream: null, device: null }
  if (list.status === 'ok' && list.devices.length === 0) return { state: 'nodevice', stream: null, device: null }
  if (list.status === 'loading') return { state: 'starting', stream: null, device }
  if (!device) return { state: 'missing', stream: null, device: null }
  if (res.id !== deviceId) return { state: 'starting', stream: null, device }
  if (res.error) return { state: res.error, stream: null, device }
  return { state: 'live', stream: res.stream, device }
}

/** <video> for a stream with black-frame detection (No signal after 2 s). */
function VideoSurface({ stream, onSignal }: { stream: MediaStream; onSignal(s: 'live' | 'nosignal'): void }) {
  const ref = React.useRef<HTMLVideoElement>(null)
  React.useEffect(() => {
    const el = ref.current
    if (!el) return
    el.srcObject = stream
    void el.play().catch(() => undefined)
    const canvas = document.createElement('canvas')
    canvas.width = 16
    canvas.height = 9
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    let blackSince: number | null = null
    let last: 'live' | 'nosignal' | null = null
    const id = setInterval(() => {
      const now = performance.now()
      let black = el.readyState < 2 || el.videoWidth === 0
      const track = stream.getVideoTracks()[0]
      if (!track || track.readyState === 'ended' || track.muted) black = true
      if (!black && ctx) {
        try {
          ctx.drawImage(el, 0, 0, 16, 9)
          black = meanLuma(ctx.getImageData(0, 0, 16, 9).data) < BLACK_LUMA
        } catch {
          black = false
        }
      }
      blackSince = black ? (blackSince ?? now) : null
      const s = signalState(blackSince, now)
      if (s !== last) {
        last = s
        onSignal(s)
      }
    }, 500)
    return () => {
      clearInterval(id)
      el.srcObject = null
    }
  }, [stream, onSignal])
  return <video ref={ref} muted playsInline autoPlay />
}

function StateText({ state, device }: { state: FeedState; device: string }) {
  const t = useT()
  const ct = useCT()
  const text =
    state === 'nodevice'
      ? t('video.noDevice')
      : state === 'denied'
        ? t('video.denied')
        : state === 'nosignal'
          ? t('video.noSignal')
          : state === 'missing'
            ? ct('vid.missing', { device })
            : state === 'starting'
              ? ct('vid.starting')
              : state === 'failed'
                ? t('video.noSignal')
                : null
  if (!text) return null
  return (
    <div className="absolute inset-0 flex items-center justify-center p-3 text-center text-hud-sub text-[var(--g400)]" data-video-state={state}>
      {text}
    </div>
  )
}

function LiveBadge() {
  const t = useT()
  return <span className="num absolute top-2 left-2 z-[1] rounded-[3px] bg-[var(--hud)] px-1.5 text-[10px] leading-4 font-[560] tracking-[0.06em] text-[var(--g50)]">{t('video.live')}</span>
}

/** The feed in a box: video when live, otherwise the honest state. */
function Feed({ vehicleId, className, onClick, label }: { vehicleId: string; className?: string; onClick?(): void; label?: string }) {
  const feed = useFeed(vehicleId, true)
  const bound = useVideo((s) => s.bindings[vehicleId]?.label ?? '')
  const [signal, setSignal] = React.useState<'live' | 'nosignal'>('live')
  const onSignal = React.useCallback((s: 'live' | 'nosignal') => setSignal(s), [])
  const state: FeedState = feed.state === 'live' && signal === 'nosignal' ? 'nosignal' : feed.state
  return (
    <div className={cn('relative overflow-hidden bg-[var(--g950)]', className)} onClick={onClick} title={label} data-video-feed={state}>
      {feed.stream ? <VideoSurface stream={feed.stream} onSignal={onSignal} /> : null}
      {state === 'live' ? <LiveBadge /> : null}
      <StateText state={state} device={bound} />
    </div>
  )
}

// ------------------------------------------------------------------------------------------------ PiP

const INSET = 16

/** Offsets (02 §4.4): bottom corners 16 px (the stage lifts its HUD strip above the PiP, WP6); top corners below
 *  the view switch, or below the course HUD while a course runs. */
function insets(corner: PipCorner, courseActive: boolean): { x: number; y: number } {
  if (corner[0] === 't') return { x: INSET, y: courseActive ? 16 + 56 + 8 : 16 + 28 + 8 }
  return { x: INSET, y: INSET }
}

export function VideoPip({ vehicleId }: VideoPipProps) {
  const ct = useCT()
  const frame = useStageFrame()
  const binding = useVideo((s) => (vehicleId ? s.bindings[vehicleId] : undefined))
  const hidden = useVideo((s) => (vehicleId ? Boolean(s.hidden[vehicleId]) : true))
  const corner = useVideo((s) => s.corner)
  const width = useVideo((s) => s.width)
  const setPip = useVideo((s) => s.setPip)
  const setHidden = useVideo((s) => s.setHidden)
  const courseActive = useRelay((s) => {
    const c = s.fleet?.vehicles.find((v) => v.id === vehicleId)?.course
    return Boolean(c && c.state !== 'finished' && c.state !== 'aborted')
  })
  const [drag, setDrag] = React.useState<{ x: number; y: number; dx: number; dy: number } | null>(null)
  const [resize, setResize] = React.useState<{ w: number; x0: number } | null>(null)
  const [snapping, setSnapping] = React.useState(false)
  const box = React.useRef<HTMLDivElement>(null)

  if (!frame || !vehicleId || !binding || hidden) return null
  const fw = frame.clientWidth
  const fh = frame.clientHeight
  const maxW = Math.min(640, fw - 2 * INSET, ((fh - 2 * INSET - 120) * 16) / 9)
  const w = clampWidth(resize ? resize.w : width, maxW)
  const h = Math.round((w * 9) / 16)
  const off = insets(corner, courseActive)
  const left = drag ? drag.x : corner[1] === 'l' ? off.x : fw - w - off.x
  const top = drag ? drag.y : corner[0] === 't' ? off.y : fh - h - off.y

  const onDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || (e.target as HTMLElement).closest('[data-pip-control]')) return
    e.currentTarget.setPointerCapture(e.pointerId)
    const r = frame.getBoundingClientRect()
    setDrag({ x: left, y: top, dx: e.clientX - r.left - left, dy: e.clientY - r.top - top })
  }
  const onMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!drag) return
    const r = frame.getBoundingClientRect()
    setDrag({ ...drag, x: Math.max(0, Math.min(fw - w, e.clientX - r.left - drag.dx)), y: Math.max(0, Math.min(fh - h, e.clientY - r.top - drag.dy)) })
  }
  const onUp = () => {
    if (!drag) return
    const c = nearestCorner(drag.x + w / 2, drag.y + h / 2, fw, fh)
    setDrag(null)
    setSnapping(true)
    setPip({ corner: c })
    setTimeout(() => setSnapping(false), 260)
  }
  const onResizeDown = (e: React.PointerEvent<HTMLSpanElement>) => {
    e.stopPropagation()
    e.currentTarget.setPointerCapture(e.pointerId)
    setResize({ w, x0: e.clientX })
  }
  const onResizeMove = (e: React.PointerEvent<HTMLSpanElement>) => {
    if (!resize) return
    const dx = e.clientX - resize.x0
    // the handle sits on the inner corner: dragging away from the snapped side grows the PiP
    const grow = corner[1] === 'r' ? -dx : dx
    setResize({ ...resize, w: clampWidth(resize.w + grow, maxW), x0: e.clientX })
  }
  const onResizeUp = () => {
    if (!resize) return
    setPip({ width: resize.w })
    setResize(null)
  }

  return createPortal(
    <div
      ref={box}
      className="video-pip hud-surface pointer-events-auto"
      data-corner={corner}
      data-snapping={snapping ? '' : undefined}
      data-video-pip={vehicleId}
      style={{ left, top, width: w, height: h, cursor: drag ? 'grabbing' : 'grab' }}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onUp}
    >
      <Feed vehicleId={vehicleId} className="h-full w-full" />
      <button
        type="button"
        data-pip-control=""
        onClick={() => setHidden(vehicleId, true)}
        className="absolute top-1.5 right-1.5 z-[2] flex size-6 items-center justify-center rounded-md bg-[var(--hud)] text-[var(--g300)] outline-none hover:text-[var(--g50)] focus-visible:ring-1 focus-visible:ring-ring"
        aria-label={ct('vid.hide')}
        title={ct('vid.hide')}
      >
        <Icon icon="close" className="size-3.5" />
      </button>
      <span
        data-pip-control=""
        className="video-pip-resize"
        onPointerDown={onResizeDown}
        onPointerMove={onResizeMove}
        onPointerUp={onResizeUp}
        onPointerCancel={onResizeUp}
        aria-hidden="true"
      />
    </div>,
    frame,
  )
}

// ------------------------------------------------------------------------------------------------ inspector group

function openSystemSettings(): void {
  const mac = env().platform === 'darwin'
  const url = mac ? 'x-apple.systempreferences:com.apple.preference.security?Privacy_Camera' : null
  if (url) void window.relay?.openExternal(url)
}

/** 16:9 thumbnail for the top of the inspector Overview; click shows the stage PiP. */
export function VideoThumb({ vehicleId, className }: { vehicleId: string; className?: string }) {
  const ct = useCT()
  const binding = useVideo((s) => s.bindings[vehicleId])
  const setHidden = useVideo((s) => s.setHidden)
  if (!binding) return null
  return (
    <button type="button" className={cn('video-thumb block aspect-video w-full overflow-hidden rounded-md border border-[var(--border)] outline-none focus-visible:ring-1 focus-visible:ring-ring', className)} onClick={() => setHidden(vehicleId, false)} aria-label={ct('vid.thumbHint')}>
      <Feed vehicleId={vehicleId} className="h-full w-full" label={ct('vid.thumbHint')} />
    </button>
  )
}

/**
 * Body of the inspector Overview "Video" block. The host provides the block title and side padding (WP8 Block);
 * pass `framed` to get a standalone group with its own "Video" label.
 */
export function VideoSourceGroup({ vehicleId, thumbnail = true, framed = false }: { vehicleId: string; thumbnail?: boolean; framed?: boolean }) {
  const t = useT()
  const ct = useCT()
  const list = useDevices()
  const bindings = useVideo((s) => s.bindings)
  const bind = useVideo((s) => s.bind)
  const unbind = useVideo((s) => s.unbind)
  const hidden = useVideo((s) => Boolean(s.hidden[vehicleId]))
  const setHidden = useVideo((s) => s.setHidden)
  const label = useVehicleField(vehicleId, (v) => v.label) ?? vehicleId
  const labels = useRelay((s) => s.fleet?.vehicles.map((v) => `${v.id}\u0001${v.label}`).join('\u0002') ?? '')
  const binding = bindings[vehicleId]
  const bound = resolveDevice(binding, list.devices)
  const [choice, setChoice] = React.useState<string | null>(null)
  const [denied, setDenied] = React.useState(false)
  const devices = list.devices
  const items = devices.map((d, i) => ({ value: d.deviceId || `#${i}`, label: d.label || ct('vid.inputN', { n: i + 1 }) }))
  const value = choice ?? (bound ? bound.deviceId || null : (items[0]?.value ?? null))
  const picked = devices.find((d, i) => (d.deviceId || `#${i}`) === value) ?? null
  const otherVehicle = picked
    ? Object.entries(bindings).find(([v, b]) => v !== vehicleId && (b.label === picked.label || b.deviceId === picked.deviceId))?.[0]
    : undefined
  const vehicleLabel = (id: string) => labels.split('\u0002').map((x) => x.split('\u0001')).find(([vid]) => vid === id)?.[1] ?? id

  const doBind = async () => {
    if (!picked) return
    let target = picked
    if (!picked.label || !picked.deviceId) {
      // no permission yet: labels and ids are hidden until the user grants camera access once
      try {
        const s = await navigator.mediaDevices.getUserMedia({ video: picked.deviceId ? { deviceId: { exact: picked.deviceId } } : true, audio: false })
        const settingsId = s.getVideoTracks()[0]?.getSettings().deviceId
        s.getTracks().forEach((tr) => tr.stop())
        await refreshDevices()
        const fresh = getDevices().devices
        target = fresh.find((d) => d.deviceId === settingsId) ?? fresh[0] ?? picked
      } catch (e) {
        if (classifyMediaError(e) === 'denied') setDenied(true)
        return
      }
    }
    setDenied(false)
    bind(vehicleId, { label: target.label, deviceId: target.deviceId })
    setChoice(null)
  }

  const noDevice = list.status !== 'loading' && devices.length === 0
  const body = (
    <div className={cn('flex flex-col gap-2', framed && 'px-3')} data-video-group={vehicleId}>
      {thumbnail && binding ? <VideoThumb vehicleId={vehicleId} /> : null}
      {noDevice ? (
        <p className="text-hud-sub text-[var(--g400)]" data-video-state="nodevice">{t('video.noDevice')}</p>
      ) : (
        <div className="flex items-center gap-2">
          <Select items={items} value={value} onValueChange={(v) => setChoice(v as string | null)} disabled={list.status === 'loading'}>
            <SelectTrigger size="sm" className="h-7 min-w-0 flex-1" aria-label={t('video.source')}>
              <SelectValue placeholder={ct('vid.choose')} />
            </SelectTrigger>
            <SelectContent>
              {items.map((it) => (
                <SelectItem key={it.value} value={it.value}>
                  {it.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="outline" disabled={!picked || (bound !== null && picked.deviceId === bound.deviceId)} onClick={() => void doBind()} data-video-bind="">
            <Icon icon="video" />
            {t('video.bind')}
          </Button>
        </div>
      )}
      {otherVehicle && !(bound && picked && picked.deviceId === bound.deviceId) ? (
        <p className="text-hud-sub text-[var(--g500)]">{ct('vid.boundElsewhere', { aircraft: vehicleLabel(otherVehicle) })}</p>
      ) : null}
      {binding ? (
        <div className="flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-hud-sub text-[var(--g300)]">
            {ct('vid.boundTo', { aircraft: label })} · {binding.label || ct('vid.inputN', { n: 1 })}
          </span>
          {hidden ? (
            <Button size="sm" variant="ghost" onClick={() => setHidden(vehicleId, false)}>
              {ct('vid.show')}
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" onClick={() => unbind(vehicleId)} data-video-unbind="">
            {ct('vid.unbind')}
          </Button>
        </div>
      ) : null}
      {denied ? (
        <div className="flex items-center gap-2">
          <p className="flex-1 text-hud-sub text-[var(--g200)]" data-video-state="denied">{t('video.denied')}</p>
          {env().platform === 'darwin' && window.relay ? (
            <Button size="sm" variant="outline" onClick={openSystemSettings}>
              {ct('vid.openSettings')}
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  )
  return framed ? <Group label={t('video.group')}>{body}</Group> : body
}
