// Director's-cut opening (05 §2, reordered by 06 §8), the DOM part. Starts the boot clock (first visible frame, or
// navigation start after 1 s), runs the act machine of app/bootFilm.ts, and for the Full and Short cuts paints the acts
// into #boot-mask: the film with its letterbox, info line and readiness cells (act 1), the diagonal cut out of the
// film (act 2), the voxel logo assembling with RELAY tracking in (act 3), the held logo (act 4, and the waiting frame
// with the progress line under the logo while the backend is not ready) and the voxel reveal (act 5). Any key, click
// or controller button skips to the complete logo, which stays at least --boot-skip-logo before the reveal; key events
// are never consumed, so the stop shortcut keeps working during the opening. Minimal and reduced openings use the same
// machine and no layers: index.html draws them as in v0.2. The failure clock is not here: boot.ts keeps it from
// startBoot().
import { cssToken, msToken } from '@/ui/motion/tokens'
import { translate, useLangStore, type MessageKey } from '@/i18n'
import { clear, paintDissolve, paintWave, surface, waveTiming, type Surface } from '@/ui/voxel/paint'
import { cellKeys, easeSpringSnappy, flightStart, grid, landingOrder, logoCells, scatterVectors, type Grid, type LogoCell } from '@/ui/voxel/wave'
import {
  actAt,
  assembleMarks,
  assembleStarts,
  commitBootPlan,
  INITIAL_FILM,
  planBoot,
  readBootStore,
  readBootTokens,
  revealGate,
  revealWave,
  stepFilm,
  timeline,
  type Act,
  type FilmEvent,
  type KV,
  type Timeline,
} from './bootFilm'
import './boot.css'

declare const __RELAY_VERSION__: string

/** The film clock falls back to navigation start when no visible frame arrives within this time (05 §2.3). */
const CLOCK_FALLBACK_MS = 1000
/** act 3 voxel pitch and the act 2 / act 5 wave pitch (05 §2.3) */
const LOGO_CELL = 12
const WAVE_CELL = 24
const LOGO_MAX_W = 720
const LOGO_VIEW_SHARE = 0.56
/** index.html: .boot-mark is centred 24 px above the middle of the window */
const MARK_LIFT = 24
const SCATTER_COUNT = 24

export interface BootRunHooks {
  /** the reveal may start now (backend ready and the film over, or skipped) */
  onReveal(): void
}

export interface BootRun {
  readonly tl: Timeline
  /** Full or Short: the layers exist and act 5 is a voxel reveal */
  readonly painted: boolean
  /** ms on the boot clock */
  now(): number
  stage(s: number): void
  ready(): void
  fail(): void
  retry(): void
  /** Act 5 for the films; `done` runs once the window is uncovered (at once for Minimal and reduced). */
  dissolve(done: () => void): void
}

/** The app version for "first launch of this version": the desktop shell's, or the renderer package's. */
export function bootVersion(): string {
  const v = typeof window !== 'undefined' ? window.relay?.version : undefined
  return typeof v === 'string' && v ? v : __RELAY_VERSION__
}

function storage(): KV | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

const asset = (path: string) => `${import.meta.env.BASE_URL}${path}`

export function startBootRun(hooks: BootRunHooks, reduced: boolean): BootRun {
  const kv = storage()
  const plan = planBoot({ ...readBootStore(kv), version: bootVersion(), reduced })
  commitBootPlan(kv, plan)
  const tl = timeline(plan.variant, plan.clips, readBootTokens(msToken))
  const mask = document.getElementById('boot-mask')
  const painted = (plan.variant === 'full' || plan.variant === 'short') && mask !== null
  // index.html hid the CSS mark for a film before any script ran; a Minimal plan lets its animation run
  document.documentElement.dataset.boot = painted ? 'film' : 'minimal'

  let s = INITIAL_FILM
  let t0: number | null = tl.fromNavigation ? 0 : null
  let gateTimer: ReturnType<typeof setTimeout> | null = null
  const now = () => (t0 === null ? 0 : performance.now() - t0)

  const armGate = () => {
    if (gateTimer) clearTimeout(gateTimer)
    gateTimer = null
    if (t0 === null || s.revealAt !== null || s.readyAt === null || s.failed) return
    gateTimer = setTimeout(() => dispatch('tick'), Math.max(0, revealGate(tl, s) - now()))
  }

  let painter: Painter | null = null
  const dispatch = (type: FilmEvent['type']) => {
    const prev = s
    s = stepFilm(tl, s, { type, t: now() })
    if (prev.revealAt === null && s.revealAt !== null) {
      if (gateTimer) clearTimeout(gateTimer)
      painter?.kick()
      hooks.onReveal()
      return
    }
    if (s !== prev) {
      armGate()
      painter?.kick()
    }
  }

  if (t0 === null) {
    // 05 §2.3: the clock starts on the first animation frame of a visible window, else at navigation start after 1 s
    const begin = (at: number) => {
      if (t0 !== null) return
      t0 = at
      document.removeEventListener('visibilitychange', tryVisible)
      armGate()
      painter?.kick()
    }
    const tryVisible = () => {
      if (document.visibilityState === 'visible') requestAnimationFrame(() => begin(performance.now()))
    }
    tryVisible()
    document.addEventListener('visibilitychange', tryVisible)
    setTimeout(() => begin(0), CLOCK_FALLBACK_MS)
  }

  const run: BootRun = {
    tl,
    painted,
    now,
    stage: (n) => painter?.stage(n),
    ready: () => dispatch('ready'),
    fail: () => dispatch('fail'),
    retry: () => dispatch('retry'),
    dissolve: (done) => {
      if (painter) painter.dissolve(done)
      else done()
    },
  }
  if (painted && mask) painter = createPainter(mask, tl, () => s, now, dispatch)
  return run
}

// ------------------------------------------------------------------------------------------------ painter

interface Painter {
  kick(): void
  stage(n: number): void
  dissolve(done: () => void): void
}

interface Colors {
  g950: string
  g800: string
  g50: string
  r500: string
}

interface Layout {
  w: number
  h: number
  surf: Surface | null
  wave: Grid
  keys: Float32Array
  logo: { left: number; top: number; w: number; h: number }
}

interface Voxel {
  cell: LogoCell
  start: number
  to: { x: number; y: number }
  from: { x: number; y: number }
}

function createPainter(mask: HTMLElement, tl: Timeline, state: () => typeof INITIAL_FILM, now: () => number, dispatch: (type: FilmEvent['type']) => void): Painter {
  const skip = () => dispatch('skip')
  const colors: Colors = { g950: cssToken('--g950'), g800: cssToken('--g800'), g50: cssToken('--g50'), r500: cssToken('--r500') }
  const lang = useLangStore.getState().lang
  const tr = (k: MessageKey) => translate(lang, k)
  const marks = assembleMarks(tl)
  const logoEl = mask.querySelector<HTMLElement>('.boot-logo')
  const markEl = mask.querySelector<HTMLElement>('.boot-mark')
  const wordEl = mask.querySelector<HTMLElement>('.boot-word')

  mask.dataset.film = tl.variant
  mask.dataset.act = 'cold'

  // ---- layers: [videos] letterbox (top, bottom) canvas, then the index.html mark, bar and status above them
  const el = <K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string) => {
    const n = document.createElement(tag)
    if (cls) n.className = cls
    return n
  }
  const canvas = el('canvas')
  canvas.id = 'boot-voxels'
  canvas.setAttribute('aria-hidden', 'true')
  const lbTop = el('div', 'boot-lb')
  lbTop.dataset.edge = 'top'
  const lbBottom = el('div', 'boot-lb')
  lbBottom.dataset.edge = 'bottom'
  const info = el('span', 'boot-info')
  info.textContent = `ANET RELAY  v${bootVersion()}`
  const inds = el('span', 'boot-inds')
  const cells = (['boot.ind.backend', 'boot.ind.health', 'boot.ind.stream'] as const).map((k) => {
    const c = el('span', 'boot-ind')
    c.append(el('i'), document.createTextNode(tr(k)))
    inds.append(c)
    return c
  })
  lbBottom.append(info, inds)
  mask.prepend(lbTop, lbBottom, canvas)

  // the film opens the cut (06 §8): every clip is created and starts buffering in act 0
  const videos: HTMLVideoElement[] = tl.clips.map((clip, i) => {
    const v = el('video', 'boot-video')
    if (i === 0) v.id = 'boot-film'
    v.dataset.clip = clip
    v.muted = true
    v.defaultMuted = true
    v.playsInline = true
    v.setAttribute('playsinline', '')
    v.setAttribute('muted', '')
    v.setAttribute('aria-hidden', 'true')
    v.disablePictureInPicture = true
    v.preload = 'auto'
    v.poster = asset(`boot/${clip}.jpg`)
    v.src = asset(`boot/${clip}.webm`)
    mask.insertBefore(v, lbTop)
    return v
  })
  let active = -1
  const showClip = (i: number) => {
    if (i === active || !videos[i]) return
    videos.forEach((v, k) => {
      if (k === i) v.dataset.on = ''
      else {
        delete v.dataset.on
        v.pause()
      }
    })
    active = i
    const v = videos[i]
    try {
      v.currentTime = 0
    } catch {
      /* not seekable yet: plays from the start anyway */
    }
    // a decode failure leaves the JPEG poster in place (05 §2.2)
    void v.play()?.catch(() => undefined)
  }
  const stopFilm = () => {
    for (const v of videos) v.pause()
  }

  // ---- logo matrix: the mark rasterised at one pixel per 12 px voxel (05 §2.3, 06 §8 act 3)
  let lay = measure()
  let voxels: Voxel[] = []
  let logoImg: HTMLImageElement | null = null
  const img = new Image()
  img.decoding = 'async'
  img.onload = () => {
    logoImg = img
    voxels = buildVoxels(img, lay)
  }
  img.src = asset('brand/anet-logo.svg')

  function measure(): Layout {
    const w = mask.clientWidth || innerWidth
    const h = mask.clientHeight || innerHeight
    const lw = Math.max(LOGO_CELL * 8, Math.floor(Math.min(LOGO_MAX_W, w * LOGO_VIEW_SHARE) / LOGO_CELL) * LOGO_CELL)
    mask.style.setProperty('--boot-logo-w', `${lw}px`)
    const lh = logoEl?.offsetHeight || Math.round((lw * 493) / 2846)
    const mh = markEl?.offsetHeight || lh
    // the waiting frame puts the progress line under the mark (boot.css reads the half height)
    mask.style.setProperty('--boot-mark-half', `${Math.round(mh / 2)}px`)
    // index.html centres the mark (logo over RELAY) 24 px above the middle; the logo is its top part
    const logo = { left: Math.round(w / 2 - lw / 2), top: Math.round(h / 2 - MARK_LIFT - mh / 2), w: lw, h: lh }
    const wave = grid(w, h, WAVE_CELL)
    return { w, h, surf: surface(canvas, w, h), wave, keys: cellKeys(wave, w, h), logo }
  }

  function buildVoxels(image: HTMLImageElement, l: Layout): Voxel[] {
    const cols = Math.max(1, Math.round(l.logo.w / LOGO_CELL))
    const rows = Math.max(1, Math.round(l.logo.h / LOGO_CELL))
    const off = document.createElement('canvas')
    off.width = cols
    off.height = rows
    const x = off.getContext('2d', { willReadFrequently: true })
    if (!x) return []
    try {
      x.drawImage(image, 0, 0, cols, rows)
      const order = landingOrder(logoCells(x.getImageData(0, 0, cols, rows).data, cols, rows), cols, rows)
      const starts = assembleStarts(tl, order.length)
      const centre = { x: l.logo.left + l.logo.w / 2, y: l.logo.top + l.logo.h / 2 }
      return order.map((cell, i) => {
        const to = { x: l.logo.left + cell.c * LOGO_CELL, y: l.logo.top + cell.r * LOGO_CELL }
        return { cell, start: starts[i], to, from: flightStart(to, centre, l.w, l.h, i + 1) }
      })
    } catch {
      return [] // the crisp mark still cuts in at the end of act 3
    }
  }

  const onResize = () => {
    lay = measure()
    if (logoImg) voxels = buildVoxels(logoImg, lay)
    kick()
  }
  addEventListener('resize', onResize)

  // ---- skip: any key, a click, a controller button (rising edge); nothing is consumed
  const onKey = (e: KeyboardEvent) => {
    if (!e.repeat) skip()
  }
  const onPointer = () => skip()
  addEventListener('keydown', onKey, true)
  mask.addEventListener('pointerdown', onPointer)
  let padHeld: Set<string> | null = null
  const pollPads = () => {
    const pads = typeof navigator !== 'undefined' && navigator.getGamepads ? navigator.getGamepads() : []
    const held = new Set<string>()
    for (const p of pads) p?.buttons.forEach((b, i) => b.pressed && held.add(`${p.index}:${i}`))
    const fresh = padHeld !== null && [...held].some((k) => !padHeld!.has(k))
    padHeld = held
    if (fresh) skip()
  }
  const unbindInput = () => {
    removeEventListener('keydown', onKey, true)
    mask.removeEventListener('pointerdown', onPointer)
  }

  // ---- readiness cells (act 1): g600 -> g50 with an 80 ms voxel flash
  const stage = (n: number) => {
    cells.forEach((c, i) => {
      const on = n > i
      if (on && c.dataset.on === undefined) {
        c.dataset.on = ''
        c.dataset.flash = ''
        setTimeout(() => delete c.dataset.flash, tl.tokens.indicatorFlash)
      } else if (!on) delete c.dataset.on
    })
  }

  // ---- frame loop
  let raf = 0
  let act: Act | null = null
  let snap: { canvas: HTMLCanvasElement; scale: number } | null = null
  let onDone: (() => void) | null = null
  let finished = false

  /** The complete mark: in act 3 it cuts in after the flash and RELAY tracks in; a skip shows it as it ends. */
  const showMark = (instant: boolean) => {
    if (mask.dataset.mark !== undefined) return
    if (instant) mask.dataset.instant = ''
    mask.dataset.mark = 'word'
  }

  const enter = (next: Act) => {
    act = next
    mask.dataset.act = next
    if (next === 'assemble' || next === 'hold' || next === 'wait' || next === 'failed') stopFilm()
    // reached without act 3 running to its end (a skip, a failure): the end of act 3 at once
    if (next === 'hold' || next === 'wait' || next === 'failed') showMark(true)
    if (next === 'reveal') {
      unbindInput()
      showMark(true)
      snap = snapshot()
      stopFilm()
    }
    if (next !== 'reveal') snap = null
  }

  const frame = () => {
    raf = 0
    // the frame clock moves the machine too, so no frame ever shows the waiting state between the hold's end and
    // the gate timer
    dispatch('tick')
    if (finished) return
    const t = now()
    const st = state()
    const a = actAt(tl, st, t)
    if (a !== act) enter(a)
    if (a !== 'reveal' && a !== 'done') pollPads()
    const surf = lay.surf
    if (surf) paint(surf, a, t, st.revealAt)
    if (a === 'done') return finish()
    // the waiting frame and the failure layout are static: no loop until the next event
    if (a !== 'wait' && a !== 'failed' && !raf) raf = requestAnimationFrame(frame)
  }

  const paint = (surf: Surface, a: Act, t: number, revealAt: number | null) => {
    const { ctx } = surf
    if (a === 'film') {
      clear(surf)
      const seg = tl.segments.findIndex((g) => t >= g.start && t < g.end)
      if (seg >= 0) showClip(seg)
      for (const f of tl.flashes) {
        if (t >= f.start && t < f.end) {
          // white field between two clips
          ctx.fillStyle = colors.g50
          ctx.fillRect(0, 0, surf.w, surf.h)
          return
        }
        const k = (t - f.end) / tl.tokens.revealCell
        if (k >= 0 && k < 1) {
          // the white field breaks into voxels that scatter out
          ctx.fillStyle = colors.g50
          ctx.globalAlpha = 1 - k
          const reach = Math.max(surf.w, surf.h) * 0.6 * easeSpringSnappy(k)
          for (const d of scatterVectors(SCATTER_COUNT)) ctx.fillRect(surf.w / 2 + d.x * reach - WAVE_CELL / 2, surf.h / 2 + d.y * reach - WAVE_CELL / 2, WAVE_CELL, WAVE_CELL)
          ctx.globalAlpha = 1
        }
      }
      return
    }
    if (a === 'cut') {
      // the diagonal wave breaks the film into voxels; g950 stays behind it
      const { spread, cell } = waveTiming(lay.wave, tl.cut - tl.film)
      paintWave(surf, lay.wave, lay.keys, t, tl.film, spread, cell, { cover: null, dark: colors.g800, light: colors.g50, clear: colors.g950 })
      return
    }
    if (a === 'assemble') {
      clear(surf)
      if (t >= marks.mark) return showMark(false)
      const size = LOGO_CELL - 1
      if (t >= marks.landed) {
        // the luminance flash: every voxel g50 for --boot-flash
        ctx.fillStyle = colors.g50
        ctx.beginPath()
        for (const v of voxels) ctx.rect(v.to.x, v.to.y, size, size)
        ctx.fill()
        return
      }
      const fly = tl.tokens.voxelFly
      for (const v of voxels) {
        const p = (t - v.start) / fly
        if (p <= 0) continue
        const e = p >= 1 ? 1 : easeSpringSnappy(p)
        ctx.fillStyle = v.cell.tone === 'red' ? colors.r500 : v.cell.tone === 'light' ? colors.g50 : colors.g800
        ctx.fillRect(v.from.x + (v.to.x - v.from.x) * e, v.from.y + (v.to.y - v.from.y) * e, size, size)
      }
      return
    }
    if (a === 'reveal' && snap && revealAt !== null) {
      paintDissolve(surf, snap.canvas, snap.scale, lay.wave, lay.keys, t, revealAt, revealWave(tl), tl.tokens.revealCell)
      return
    }
    // cold, hold, wait, failed: the crisp mark (DOM) on g950 or nothing
    clear(surf)
  }

  /** The frame as it stands, flattened for act 5: g950, the crisp mark and RELAY at their final spacing. */
  const snapshot = (): { canvas: HTMLCanvasElement; scale: number } => {
    const scale = Math.min(2, Math.max(1, devicePixelRatio || 1))
    const c = document.createElement('canvas')
    c.width = Math.max(1, Math.round(lay.w * scale))
    c.height = Math.max(1, Math.round(lay.h * scale))
    const x = c.getContext('2d')
    if (!x) return { canvas: c, scale }
    x.scale(scale, scale)
    x.fillStyle = colors.g950
    x.fillRect(0, 0, lay.w, lay.h)
    if (logoImg && logoEl) {
      const r = logoEl.getBoundingClientRect()
      x.drawImage(logoImg, r.left, r.top, r.width, r.height)
    }
    if (wordEl) {
      const r = wordEl.getBoundingClientRect()
      const cs = getComputedStyle(wordEl)
      x.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`
      x.letterSpacing = cs.letterSpacing === 'normal' ? '0px' : cs.letterSpacing
      x.fillStyle = cs.color
      x.textBaseline = 'middle'
      x.fillText(wordEl.textContent ?? '', r.left, r.top + r.height / 2)
    }
    return { canvas: c, scale }
  }

  const finish = () => {
    if (finished) return
    finished = true
    if (raf) cancelAnimationFrame(raf)
    raf = 0
    removeEventListener('resize', onResize)
    unbindInput()
    // release the decoders (05 §2.3): no src, then load() to drop the media resource
    for (const v of videos) {
      v.pause()
      v.removeAttribute('src')
      v.load()
      v.remove()
    }
    videos.length = 0
    canvas.width = 0
    canvas.height = 0
    const done = onDone
    onDone = null
    done?.()
  }

  const kick = () => {
    if (!raf && !finished) raf = requestAnimationFrame(frame)
  }
  kick()

  return {
    kick,
    stage,
    dissolve: (done) => {
      onDone = done
      kick()
      // a hidden window gets no animation frames: never leave the mask up
      setTimeout(finish, tl.reveal + tl.tokens.revealCell)
    },
  }
}
