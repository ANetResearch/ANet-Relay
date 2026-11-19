// The gimbal pod in the picture-in-picture (12 §5.5). GimbalPicture: the simulated pod's picture, rendered by the
// stage from the pod camera (feed.ts) at the PiP's size and a capped rate, infrared lenses through the thermal
// palette, with the pod's small picture when it has one. GimbalHud: the restrained overlay over that picture or over
// the bound video of a real pod: centre reticle, lens and zoom, frame angles and mode, laser range, REC with its
// time, the tracking gate, the capture flash. Pointer events are the PiP's (a click without a drag tracks).
import * as React from 'react'
import type { GimbalView } from '@/api'
import { useT } from '@/i18n'
import { cn } from '@/lib/utils'
import { useVehicleField } from '@/store/selectors'
import { motionReduced } from '@/ui/motion/tier'
import { feedFps, feedHost, feedSize, openFeed } from './feed'
import { lensText, LENS_SHORT, rangeText, recTime, signedDeg, trackBox } from './model'
import { shownCanvas } from './store'
import './gimbal.css'

/** The rendered picture of a simulated pod; reports whether a frame has arrived. */
export function GimbalPicture({ vehicleId, cssWidth, onState }: { vehicleId: string; cssWidth: number; onState?(s: 'starting' | 'live' | 'unavailable'): void }) {
  const main = React.useRef<HTMLCanvasElement>(null)
  const pip = React.useRef<HTMLCanvasElement>(null)
  const [hasPip, setHasPip] = React.useState(false)
  const size = feedSize(cssWidth, typeof window === 'undefined' ? 1 : window.devicePixelRatio || 1, feedHost.software)
  const stateRef = React.useRef<'starting' | 'live' | 'unavailable'>('starting')
  const report = React.useCallback(
    (s: 'starting' | 'live' | 'unavailable') => {
      if (stateRef.current === s) return
      stateRef.current = s
      onState?.(s)
    },
    [onState],
  )
  React.useEffect(() => {
    if (!feedHost.mounted) {
      report('unavailable')
      return
    }
    report('starting')
    const c = main.current
    if (c) shownCanvas.set(vehicleId, c)
    let pipShown = false
    const close = openFeed({
      vehicleId,
      width: size.width,
      height: size.height,
      fps: feedFps(feedHost.software),
      draw: (f) => {
        const cv = main.current
        if (!cv) return
        if (cv.width !== f.image.width) cv.width = f.image.width
        if (cv.height !== f.image.height) cv.height = f.image.height
        cv.getContext('2d')?.putImageData(f.image, 0, 0)
        const pc = pip.current
        if (f.pip && pc) {
          if (pc.width !== f.pip.width) pc.width = f.pip.width
          if (pc.height !== f.pip.height) pc.height = f.pip.height
          pc.getContext('2d')?.putImageData(f.pip, 0, 0)
        }
        if (Boolean(f.pip) !== pipShown) {
          pipShown = Boolean(f.pip)
          setHasPip(pipShown)
        }
        report('live')
      },
    })
    return () => {
      close()
      if (shownCanvas.get(vehicleId) === c) shownCanvas.delete(vehicleId)
    }
  }, [vehicleId, size.width, size.height, report])
  return (
    <>
      <canvas ref={main} className="gimbal-canvas" width={size.width} height={size.height} data-gimbal-picture={vehicleId} />
      <canvas ref={pip} className="gimbal-pip-small" hidden={!hasPip} aria-hidden="true" />
    </>
  )
}

type HudView = Pick<GimbalView, 'kind' | 'link' | 'lens' | 'pip' | 'zoom' | 'yaw_deg' | 'pitch_deg' | 'mode' | 'recording' | 'photos' | 'laser' | 'tracking'>

/** The HUD fields of a vehicle's pod as one stable string, so the overlay renders on changes only. */
function useHudView(vehicleId: string): HudView | null {
  const key = useVehicleField(vehicleId, (v) => {
    const g = v.gimbal
    if (!g) return ''
    return JSON.stringify({
      kind: g.kind, link: g.link, lens: g.lens, pip: g.pip, zoom: Math.round(g.zoom * 10) / 10,
      yaw_deg: Math.round(g.yaw_deg * 10) / 10, pitch_deg: Math.round(g.pitch_deg * 10) / 10, mode: g.mode,
      recording: g.recording, photos: g.photos, laser: g.laser, tracking: g.tracking,
    } satisfies HudView)
  })
  return React.useMemo(() => (key ? (JSON.parse(key) as HudView) : null), [key])
}

/** Seconds since recording started (counted in the window; the view carries no start time). */
function useRecSeconds(on: boolean): number {
  const [secs, setSecs] = React.useState(0)
  React.useEffect(() => {
    if (!on) return
    const t0 = Date.now()
    const id = window.setInterval(() => setSecs((Date.now() - t0) / 1000), 500)
    return () => {
      window.clearInterval(id)
      setSecs(0)
    }
  }, [on])
  return on ? secs : 0
}

function Chip({ children, className, ...rest }: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className={cn('gimbal-chip num', className)} {...rest}>
      {children}
    </span>
  )
}

/** Overlay of the pod's state; `compact` drops the mode on a small PiP; no reticle or gate without a picture. */
export function GimbalHud({ vehicleId, compact = false, picture = true }: { vehicleId: string; compact?: boolean; picture?: boolean }) {
  const t = useT()
  const g = useHudView(vehicleId)
  const rec = useRecSeconds(Boolean(g?.recording))
  // the shutter: a short flash when a photo is taken
  const [flash, setFlash] = React.useState(0)
  const photos = g?.photos ?? 0
  const [seenPhotos, setSeenPhotos] = React.useState(photos)
  if (photos !== seenPhotos) {
    setSeenPhotos(photos)
    if (photos > seenPhotos && !motionReduced()) setFlash((n) => n + 1)
  }
  if (!g) return null
  const box = trackBox(g)
  const range = rangeText(g.laser.range_m)
  const lost = g.link !== 'ok'
  const mode = t(`gimbal.mode.${g.mode}` as Parameters<typeof t>[0])
  return (
    <div className="gimbal-hud" data-gimbal-hud={vehicleId} data-lost={lost ? '' : undefined}>
      {picture ? (
        <svg className="gimbal-reticle" viewBox="-20 -20 40 40" aria-hidden="true">
          <path d="M-14 0H-5M5 0H14M0 -14V-5M0 5V14" />
          <circle r="0.9" />
        </svg>
      ) : null}
      {box && picture ? (
        <span
          className="gimbal-track"
          style={{ left: `${box.left * 100}%`, top: `${box.top * 100}%`, width: `${box.width * 100}%`, height: `${box.height * 100}%` }}
          data-gimbal-track=""
          aria-label={t('gimbal.track.locked')}
        />
      ) : null}
      <div className="gimbal-hud-top">
        <Chip data-gimbal-lens={g.lens}>{lensText(g)}</Chip>
        {g.pip ? <Chip className="text-[var(--g400)]">{t('gimbal.hud.pip', { lens: LENS_SHORT[g.pip] })}</Chip> : null}
        {lost && !picture ? <Chip className="text-[var(--g300)]">{t(g.link === 'connecting' ? 'gimbal.link.connecting' : 'gimbal.link.lost')}</Chip> : null}
        {g.recording ? (
          <Chip className="gimbal-rec" data-gimbal-rec="">
            <i aria-hidden="true" />
            {t('gimbal.hud.rec')} {recTime(rec)}
          </Chip>
        ) : null}
      </div>
      <div className="gimbal-hud-bottom">
        <Chip className="text-[var(--g200)]">
          {compact ? null : <span className="text-[var(--g400)]">{mode}</span>}
          {compact ? null : ' '}
          {signedDeg(g.yaw_deg)} <span className="text-[var(--g500)]">/</span> {signedDeg(g.pitch_deg)}
        </Chip>
        <span className="flex-1" />
        {range ? (
          <Chip data-gimbal-range="">
            <span className="text-[var(--g400)]">{t('gimbal.hud.lrf')}</span> {range}
          </Chip>
        ) : null}
      </div>
      {lost && picture ? <div className="gimbal-hud-lost">{t(g.link === 'connecting' ? 'gimbal.link.connecting' : 'gimbal.link.lost')}</div> : null}
      {flash ? <span key={flash} className="gimbal-flash" aria-hidden="true" /> : null}
    </div>
  )
}
