// Relay timeline (02 §4.5): the signature strip. One lane per aircraft: agent segments g500, human segments g50,
// a g500 dashed line under a human segment while a claim waits, a 1 px armed line (single-red rule), ticks for
// handback / stop / crash, hatching while the link is lost. Canvas, redrawn once per second and on change.
import * as React from 'react'
import { useT, useLang, translate, type Lang } from '@/i18n'
import { clock } from '@/lib/format'
import { cn } from '@/lib/utils'
import { backendNow } from '@/store/relay'
import { useControllerSummary, useRedOwner, useVehicleGroups } from '@/store/selectors'
import { useUi, type TimelineWindow } from '@/store/ui'
import { armedTone, type RedOwner } from '@/store/red'
import { ToggleGroup, ToggleGroupItem } from '@/ui/components/ui/toggle-group'
import { Button } from '@/ui/components/ui/button'
import { StateIcon } from '@/ui/icons'
import { SimTag } from '@/ui/relay'
import { timeline } from './feed'
import type { Lane, Segment } from './model'

const NAME_W = 160
const LANE_H = 14
const LANE_GAP = 4
const BAR_H = 9

interface Palette {
  [k: string]: string
}
function readPalette(): Palette {
  const cs = getComputedStyle(document.documentElement)
  const keys = ['--g950', '--g900', '--g850', '--g800', '--g700', '--g600', '--g500', '--g400', '--g300', '--g200', '--g100', '--g50', '--r500']
  const p: Palette = {}
  for (const k of keys) p[k] = cs.getPropertyValue(k).trim() || '#808080'
  return p
}

function inputText(lang: Lang, token: string | null): string {
  if (!token) return ''
  if (token.startsWith('pad:')) return translate(lang, 'input.pad', { n: token.slice(4) })
  return translate(lang, token === 'screen' ? 'input.screen' : 'input.keyboard')
}

function describe(lang: Lang, s: Segment | null): string {
  if (!s || s.kind === 'none') return translate(lang, 'tl.seg.idle')
  if (s.kind === 'agent') return translate(lang, 'tl.seg.agent', { name: s.label ?? '' })
  const base = translate(lang, 'tl.seg.human', { input: inputText(lang, s.label) })
  return s.waiting ? `${base} · ${translate(lang, 'tl.seg.waiting', { name: s.waiting })}` : base
}

function draw(ctx: CanvasRenderingContext2D, w: number, lanes: Lane[], now: number, span: number, pal: Palette, owner: RedOwner, hoverX: number | null) {
  ctx.clearRect(0, 0, w, ctx.canvas.height)
  const x = (t: number) => ((t - (now - span)) / span) * w
  // minute grid
  ctx.fillStyle = pal['--g800']
  const step = span <= 60 ? 10 : span <= 300 ? 60 : 180
  for (let t = Math.ceil((now - span) / step) * step; t < now; t += step) ctx.fillRect(Math.round(x(t)), 0, 1, ctx.canvas.height)
  const armedColor = armedTone(owner) === 'red' ? pal['--r500'] : pal['--g50']
  lanes.forEach((l, i) => {
    const y = i * (LANE_H + LANE_GAP)
    // lane track
    ctx.fillStyle = pal['--g900']
    ctx.fillRect(0, y, w, BAR_H)
    for (const s of l.segs) {
      if (s.kind === 'none') continue
      const x0 = Math.max(0, x(s.start))
      const x1 = Math.min(w, x(s.end ?? now))
      if (x1 <= 0 || x0 >= w || x1 - x0 < 0.5) continue
      ctx.fillStyle = s.kind === 'human' ? pal['--g50'] : pal['--g500']
      ctx.fillRect(x0, y, x1 - x0, BAR_H)
      if (s.kind === 'agent' && s.label && x1 - x0 > 56) {
        ctx.save()
        ctx.beginPath()
        ctx.rect(x0, y, x1 - x0, BAR_H)
        ctx.clip()
        ctx.fillStyle = pal['--g50']
        ctx.font = '500 9px "JetBrains Mono Variable", ui-monospace, monospace'
        ctx.textBaseline = 'middle'
        ctx.fillText(s.label, x0 + 4, y + BAR_H / 2 + 0.5)
        ctx.restore()
      }
      if (s.kind === 'human' && s.waiting) {
        ctx.fillStyle = pal['--g500']
        for (let dx = x0; dx < x1; dx += 6) ctx.fillRect(dx, y + BAR_H + 1, Math.min(3, x1 - dx), 2)
      }
    }
    // link lost hatching
    for (const iv of l.lost) {
      const x0 = Math.max(0, x(iv.start))
      const x1 = Math.min(w, x(iv.end ?? now))
      if (x1 <= x0) continue
      ctx.save()
      ctx.beginPath()
      ctx.rect(x0, y, x1 - x0, BAR_H)
      ctx.clip()
      ctx.strokeStyle = pal['--g300']
      ctx.lineWidth = 1
      for (let hx = x0 - BAR_H; hx < x1; hx += 4) {
        ctx.beginPath()
        ctx.moveTo(hx, y + BAR_H)
        ctx.lineTo(hx + BAR_H, y)
        ctx.stroke()
      }
      ctx.restore()
    }
    // armed line at the lane bottom
    ctx.fillStyle = armedColor
    for (const iv of l.armed) {
      const x0 = Math.max(0, x(iv.start))
      const x1 = Math.min(w, x(iv.end ?? now))
      if (x1 > x0) ctx.fillRect(x0, y + LANE_H - 1, x1 - x0, 1)
    }
    // ticks
    for (const k of l.ticks) {
      const tx = Math.round(x(k.t)) + 0.5
      if (tx < 0 || tx > w) continue
      if (k.kind === 'handback') {
        ctx.fillStyle = pal['--g50']
        ctx.strokeStyle = pal['--g950']
        ctx.beginPath()
        ctx.moveTo(tx - 3.5, y - 1)
        ctx.lineTo(tx + 3.5, y - 1)
        ctx.lineTo(tx, y + 4)
        ctx.closePath()
        ctx.fill()
        ctx.stroke()
      } else if (k.kind === 'stop') {
        ctx.fillStyle = pal['--g50']
        ctx.fillRect(tx - 0.5, y - 1, 1, LANE_H + 1)
      } else if (k.kind === 'crash') {
        ctx.strokeStyle = pal['--g50']
        ctx.lineWidth = 1.25
        ctx.beginPath()
        ctx.moveTo(tx - 3, y + 1)
        ctx.lineTo(tx + 3, y + BAR_H - 1)
        ctx.moveTo(tx + 3, y + 1)
        ctx.lineTo(tx - 3, y + BAR_H - 1)
        ctx.stroke()
      }
    }
  })
  // now edge and hover cursor
  ctx.fillStyle = pal['--g600']
  ctx.fillRect(w - 1, 0, 1, ctx.canvas.height)
  if (hoverX !== null) {
    ctx.fillStyle = pal['--g300']
    ctx.fillRect(Math.round(hoverX), 0, 1, ctx.canvas.height)
  }
}

function useLanes(): Lane[] {
  const groups = useVehicleGroups()
  const [, bump] = React.useReducer((n: number) => n + 1, 0)
  React.useEffect(() => {
    let v = timeline.version
    const id = setInterval(() => {
      if (timeline.version !== v) {
        v = timeline.version
        bump()
      }
    }, 250)
    return () => clearInterval(id)
  }, [])
  const ids = [...groups.real, ...groups.sim]
  return ids.map((id) => timeline.lanes.get(id)).filter((l): l is Lane => !!l)
}

export function RelayTimeline({ className }: { className?: string }) {
  const t = useT()
  const lang = useLang()
  const collapsed = useUi((s) => s.timelineCollapsed)
  const toggle = useUi((s) => s.toggleTimeline)
  const win = useUi((s) => s.timelineWindow)
  const setWin = useUi((s) => s.setTimelineWindow)
  const summary = useControllerSummary()
  const owner = useRedOwner()
  const lanes = useLanes()
  const canvas = React.useRef<HTMLCanvasElement>(null)
  const wrap = React.useRef<HTMLDivElement>(null)
  const [hover, setHover] = React.useState<{ x: number; w: number; lane: number; clientX: number; clientY: number } | null>(null)
  const pal = React.useMemo(() => (typeof document !== 'undefined' ? readPalette() : {}), [])
  const span = win * 60

  const height = Math.max(LANE_H, lanes.length * (LANE_H + LANE_GAP) - LANE_GAP)

  const paint = React.useCallback(() => {
    const c = canvas.current
    const el = wrap.current
    if (!c || !el) return
    const w = el.clientWidth
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(height * dpr)) {
      c.width = Math.round(w * dpr)
      c.height = Math.round(height * dpr)
      c.style.width = `${w}px`
      c.style.height = `${height}px`
    }
    const ctx = c.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    draw(ctx, w, lanes, backendNow(), span, pal, owner, hover?.x ?? null)
  }, [lanes, span, pal, owner, hover, height])

  React.useEffect(() => {
    if (collapsed) return
    paint()
    const id = setInterval(paint, 1000)
    const ro = new ResizeObserver(paint)
    if (wrap.current) ro.observe(wrap.current)
    return () => {
      clearInterval(id)
      ro.disconnect()
    }
  }, [paint, collapsed])

  const focusRecords = useUi((s) => s.focusRecords)
  const hoverInfo = React.useMemo(() => {
    if (!hover) return null
    const lane = lanes[hover.lane]
    if (!lane) return null
    const w = hover.w
    const now = backendNow()
    const tm = now - span + (hover.x / w) * span
    const tick = lane.ticks.find((k) => Math.abs(((k.t - tm) / span) * w) < 4)
    const seg = timeline.segmentAt(lane.id, tm)
    const tickText = tick
      ? tick.kind === 'handback'
        ? t('tl.tick.handback', { name: tick.label ?? '' })
        : t(tick.kind === 'stop' ? 'tl.tick.stop' : tick.kind === 'crash' ? 'tl.tick.crash' : 'tl.tick.lost')
      : null
    return { lane, t: tm, text: tickText ?? describe(lang, seg) }
  }, [hover, lanes, span, t, lang])

  const onMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - r.left
    const y = e.clientY - r.top
    const lane = Math.floor(y / (LANE_H + LANE_GAP))
    setHover({ x, w: r.width, lane, clientX: e.clientX, clientY: r.top + lane * (LANE_H + LANE_GAP) })
  }

  return (
    <section
      className={cn('shell-timeline relative mx-2 mb-2 flex flex-col overflow-hidden', className)}
      data-collapsed={collapsed ? '' : undefined}
      aria-label={t('tl.title')}
    >
      <header className="flex h-7 shrink-0 items-center gap-2 pr-1">
        <Button variant="ghost" size="icon-sm" onClick={() => toggle()} aria-label={collapsed ? t('tl.expand') : t('tl.collapse')} className="text-[var(--g400)]">
          <StateIcon icon="chevron.down" alt={collapsed} className="size-3.5" />
        </Button>
        <span className="text-hud-title font-[560] text-[var(--g100)]">{t('tl.title')}</span>
        <span className="num text-hud-sub text-[var(--g400)]">{t('tl.summary', summary)}</span>
        <span className="flex-1" />
        {!collapsed ? (
          <>
            <span className="num text-hud-cap text-[var(--g500)]">−{t('tl.window', { n: win })}</span>
            <ToggleGroup
              value={[String(win)]}
              onValueChange={(v: unknown[]) => v[0] && setWin(Number(v[0]) as TimelineWindow)}
              size="sm"
              spacing={0}
              className="ml-2"
            >
              {[1, 5, 15].map((n) => (
                <ToggleGroupItem key={n} value={String(n)} className="num h-5 px-1.5 text-[10px]">
                  {t('tl.window', { n })}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </>
        ) : null}
      </header>
      {!collapsed ? (
        <div
          className="min-h-0 flex-1 overflow-y-auto pb-1 [scrollbar-width:thin]"
          data-overflow={lanes.length > 4 ? '' : undefined}
          style={lanes.length > 4 ? { maskImage: 'linear-gradient(to bottom, black calc(100% - 14px), transparent)' } : undefined}
        >
          {lanes.length === 0 ? (
            <p className="pl-9 text-hud-sub text-[var(--g500)]">{t('tl.empty')}</p>
          ) : (
            <div className="flex" style={{ minHeight: height }}>
              <ol className="shrink-0 pl-9" style={{ width: NAME_W }}>
                {lanes.map((l) => (
                  <li key={l.id} className="flex items-center gap-1.5 truncate" style={{ height: LANE_H, marginBottom: LANE_GAP }}>
                    {l.sim ? <SimTag className="h-3 px-0.5 text-[9px]" /> : null}
                    <span className="truncate font-mono text-[10px] leading-3 text-[var(--g300)]">{l.label}</span>
                  </li>
                ))}
              </ol>
              <div ref={wrap} className="relative min-w-0 flex-1 pr-3">
                <canvas
                  ref={canvas}
                  className="block cursor-pointer"
                  onPointerMove={onMove}
                  onPointerLeave={() => setHover(null)}
                  onClick={() => hoverInfo && focusRecords(hoverInfo.lane.id, hoverInfo.t)}
                />
              </div>
            </div>
          )}
        </div>
      ) : null}
      {hoverInfo && hover ? (
        <div
          role="tooltip"
          className="l3 pointer-events-none fixed z-(--z-popover) -translate-x-1/2 -translate-y-full rounded-md px-2 py-1 text-hud-sub whitespace-nowrap text-[var(--g100)]"
          style={{ left: hover.clientX, top: hover.clientY - 6 }}
        >
          <span className="num text-[var(--g400)]">{clock(hoverInfo.t)}</span>
          <span className="mx-1.5 text-[var(--g600)]">·</span>
          {hoverInfo.lane.label} <span className="mx-1 text-[var(--g600)]">·</span> {hoverInfo.text}
        </div>
      ) : null}
    </section>
  )
}
