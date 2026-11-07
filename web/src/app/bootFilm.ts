// Director's-cut opening (05 §2), the pure part: which opening this launch plays (Full / Short / Minimal, or the
// reduced-motion one), the act timeline built from the duration tokens, and the act state machine (backend ready,
// skip, failure, retry). No DOM and no timers: app/bootCinema.ts paints the acts and app/boot.ts asks this module
// when the reveal may start, so the earliest reveal and the timeline come from one place (05 §2.4).

export type BootMode = 'full' | 'short' | 'minimal'
/** `reduced` is forced by reduced motion (or ?motion=off) whatever the setting says. */
export type BootVariant = BootMode | 'reduced'

export const BOOT_MODES: readonly BootMode[] = ['full', 'short', 'minimal']
export const DEFAULT_BOOT_MODE: BootMode = 'short'

/** Boot films in web/public/boot/ (WebM VP9 with a JPEG poster each); the short cut rotates in this order. */
export const CLIPS = ['cut-chase', 'cut-breakthrough', 'cut-split'] as const
export type Clip = (typeof CLIPS)[number]
/** The director's cut always opens on the breakthrough and continues on the chase. */
export const FULL_CLIPS: readonly Clip[] = ['cut-breakthrough', 'cut-chase']

/** localStorage keys. `mode` mirrors Settings > General because the boot runs before the backend answers. */
export const BOOT_KEYS = { mode: 'relay.boot.mode', seen: 'relay.boot.seen', n: 'relay.boot.n' } as const

// ------------------------------------------------------------------------------------------------ tokens

export interface BootTokens {
  fullDur: number
  fullAssemble: number
  fullCut: number
  fullFilm: number
  shortDur: number
  shortAssemble: number
  shortCut: number
  shortFilm: number
  minimalDur: number
  minimalHold: number
  reducedDur: number
  cold: number
  voxelFly: number
  flash: number
  wordHold: number
  cutFlash: number
  indicatorFlash: number
  revealWave: number
  revealWaveShort: number
  revealCell: number
}

/** The CSS custom property behind each value (styles/motion/ext-tokens.css). */
export const BOOT_TOKEN_NAMES: Record<keyof BootTokens, string> = {
  fullDur: '--boot-full-dur',
  fullAssemble: '--boot-full-assemble',
  fullCut: '--boot-full-cut',
  fullFilm: '--boot-full-film',
  shortDur: '--boot-short-dur',
  shortAssemble: '--boot-short-assemble',
  shortCut: '--boot-short-cut',
  shortFilm: '--boot-short-film',
  minimalDur: '--boot-minimal-dur',
  minimalHold: '--boot-minimal-hold',
  reducedDur: '--boot-reduced-dur',
  cold: '--boot-cold',
  voxelFly: '--boot-voxel-fly',
  flash: '--boot-flash',
  wordHold: '--boot-word-hold',
  cutFlash: '--boot-cut-flash',
  indicatorFlash: '--boot-indicator-flash',
  revealWave: '--boot-reveal-wave',
  revealWaveShort: '--boot-reveal-wave-short',
  revealCell: '--boot-reveal-cell',
}

/** Fallbacks for node tests and a stylesheet that has not applied; bootFilm.test.ts pins them to ext-tokens.css. */
export const BOOT_DEFAULTS: BootTokens = {
  fullDur: 5600,
  fullAssemble: 1100,
  fullCut: 1500,
  fullFilm: 5000,
  shortDur: 3200,
  shortAssemble: 700,
  shortCut: 1000,
  shortFilm: 2800,
  minimalDur: 1200,
  minimalHold: 160,
  reducedDur: 300,
  cold: 80,
  voxelFly: 150,
  flash: 60,
  wordHold: 180,
  cutFlash: 120,
  indicatorFlash: 80,
  revealWave: 350,
  revealWaveShort: 150,
  revealCell: 250,
}

/** Reads every boot token through `read(name, fallback)` (ui/motion/tokens.ts msToken at run time). */
export function readBootTokens(read: (name: string, fallback: number) => number): BootTokens {
  const out = { ...BOOT_DEFAULTS }
  for (const k of Object.keys(BOOT_TOKEN_NAMES) as (keyof BootTokens)[]) out[k] = read(BOOT_TOKEN_NAMES[k], BOOT_DEFAULTS[k])
  return out
}

// ------------------------------------------------------------------------------------------------ timeline

export interface FilmSegment {
  clip: Clip
  start: number
  end: number
}

/** Act boundaries in ms from the clock start (05 §2.3). */
export interface Timeline {
  variant: BootVariant
  /** act 0 ends (g950 only) */
  cold: number
  /** act 1 ends: the voxel logo has landed and flashed; RELAY cuts in */
  assemble: number
  /** act 2 ends: the diagonal wave has uncovered the film */
  cut: number
  /** act 3 ends: the earliest reveal (the boot's minimum duration) */
  film: number
  /** length of act 5, the reveal */
  reveal: number
  /** minimal and reduced count from navigation start (the v0.2 rule); the films from the first visible frame */
  fromNavigation: boolean
  clips: readonly Clip[]
  /** clip playback windows inside act 3 */
  segments: FilmSegment[]
  /** white fields between two clips */
  flashes: { start: number; end: number }[]
  tokens: BootTokens
}

export function timeline(variant: BootVariant, clips: readonly Clip[] = [], tk: BootTokens = BOOT_DEFAULTS): Timeline {
  if (variant === 'minimal' || variant === 'reduced') {
    const min = variant === 'minimal' ? tk.minimalDur : tk.reducedDur
    const reveal = variant === 'minimal' ? tk.minimalHold + tk.revealCell : 0
    return { variant, cold: 0, assemble: min, cut: min, film: min, reveal, fromNavigation: true, clips: [], segments: [], flashes: [], tokens: tk }
  }
  const full = variant === 'full'
  const cold = tk.cold
  const assemble = full ? tk.fullAssemble : tk.shortAssemble
  const cut = full ? tk.fullCut : tk.shortCut
  const film = full ? tk.fullFilm : tk.shortFilm
  const total = full ? tk.fullDur : tk.shortDur
  const list = clips.length ? clips : full ? FULL_CLIPS : [CLIPS[0]]
  // act 3 is shared evenly between the clips, with a white field of cutFlash between two of them
  const n = list.length
  const span = (film - cut - (n - 1) * tk.cutFlash) / n
  const segments: FilmSegment[] = []
  const flashes: { start: number; end: number }[] = []
  let at = cut
  list.forEach((clip, i) => {
    const end = i === n - 1 ? film : at + span
    segments.push({ clip, start: at, end })
    if (i < n - 1) flashes.push({ start: end, end: end + tk.cutFlash })
    at = end + tk.cutFlash
  })
  return { variant, cold, assemble, cut, film, reveal: total - film, fromNavigation: false, clips: list, segments, flashes, tokens: tk }
}

/** The shortest boot of a variant: the reveal never starts earlier (boot.ts MIN_BOOT_MS comes from here). */
export function minBootMs(variant: BootVariant, tk: BootTokens = BOOT_DEFAULTS): number {
  return timeline(variant, [], tk).film
}

/**
 * Act 1 inside: voxels fly in until `landed`, the g50 flash runs until `mark`, then the crisp mark and RELAY hold
 * (hard cut) until act 2.
 */
export function assembleMarks(tl: Timeline): { landed: number; mark: number } {
  const mark = Math.max(tl.cold, tl.assemble - tl.tokens.wordHold)
  return { landed: Math.max(tl.cold, mark - tl.tokens.flash), mark }
}

/** Start time of every voxel's flight in act 1 for `count` voxels in landing order (the last lands at `landed`). */
export function assembleStarts(tl: Timeline, count: number): number[] {
  const window = Math.max(0, assembleMarks(tl).landed - tl.cold - tl.tokens.voxelFly)
  if (count <= 0) return []
  if (count === 1) return [tl.cold]
  return Array.from({ length: count }, (_, i) => tl.cold + (window * i) / (count - 1))
}

/** Spread of the reveal wave (first to last cell start) for this variant. */
export function revealWave(tl: Timeline): number {
  if (tl.variant === 'full') return tl.tokens.revealWave
  if (tl.variant === 'short') return tl.tokens.revealWaveShort
  return 0
}

// ------------------------------------------------------------------------------------------------ plan

export interface PlanInput {
  mode: BootMode | null
  /** relay.boot.seen: the version whose director's cut was shown; null = never; undefined = storage unreadable */
  seen: string | null | undefined
  version: string
  /** relay.boot.n: launches so far; null = never; undefined = storage unreadable */
  n: number | null | undefined
  reduced: boolean
}

export interface BootPlan {
  variant: BootVariant
  clips: readonly Clip[]
  /** write relay.boot.seen = this version */
  writeSeen: string | null
  /** write relay.boot.n = this value */
  writeN: number | null
}

/**
 * Short (the default) turns into the director's cut once per version: the first launch after an install or an
 * update. Full and Minimal are taken as set. Reduced motion never loads a film. Storage that cannot be read counts
 * as "already seen" and plays the first short clip.
 */
export function planBoot(i: PlanInput): BootPlan {
  if (i.reduced) return { variant: 'reduced', clips: [], writeSeen: null, writeN: null }
  const mode = i.mode ?? DEFAULT_BOOT_MODE
  if (mode === 'minimal') return { variant: 'minimal', clips: [], writeSeen: null, writeN: null }
  const firstRun = mode === 'short' && i.seen !== undefined && i.seen !== i.version
  if (mode === 'full' || firstRun) return { variant: 'full', clips: FULL_CLIPS, writeSeen: i.version, writeN: null }
  const n = typeof i.n === 'number' && Number.isFinite(i.n) && i.n >= 0 ? Math.floor(i.n) : 0
  return { variant: 'short', clips: [CLIPS[n % CLIPS.length]], writeSeen: null, writeN: i.n === undefined ? null : n + 1 }
}

export function parseBootMode(raw: unknown): BootMode | null {
  return raw === 'full' || raw === 'short' || raw === 'minimal' ? raw : null
}

/** The subset of Storage the boot uses (localStorage at run time, a map in tests). */
export interface KV {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}

/** Reads the three boot keys; every read failure is reported as undefined (05 §2.2, §2.3). */
export function readBootStore(kv: KV | null): Pick<PlanInput, 'mode' | 'seen' | 'n'> {
  const get = (k: string): string | null | undefined => {
    if (!kv) return undefined
    try {
      return kv.getItem(k)
    } catch {
      return undefined
    }
  }
  const mode = get(BOOT_KEYS.mode)
  const n = get(BOOT_KEYS.n)
  const parsed = n === null || n === undefined ? n : Number.parseInt(n, 10)
  return { mode: parseBootMode(mode), seen: get(BOOT_KEYS.seen), n: typeof parsed === 'number' && !Number.isFinite(parsed) ? null : parsed }
}

/** Persists what the plan asks for; write failures are ignored (the next launch plays the short cut). */
export function commitBootPlan(kv: KV | null, plan: BootPlan): void {
  if (!kv) return
  try {
    if (plan.writeSeen !== null) kv.setItem(BOOT_KEYS.seen, plan.writeSeen)
    if (plan.writeN !== null) kv.setItem(BOOT_KEYS.n, String(plan.writeN))
  } catch {
    /* storage unavailable */
  }
}

/** Mirrors the Settings value for the next launch. */
export function writeBootMode(kv: KV | null, mode: BootMode): void {
  if (!kv) return
  try {
    kv.setItem(BOOT_KEYS.mode, mode)
  } catch {
    /* storage unavailable: the backend copy of the setting still holds it */
  }
}

// ------------------------------------------------------------------------------------------------ act machine

export type Act = 'cold' | 'assemble' | 'cut' | 'film' | 'wait' | 'failed' | 'reveal' | 'done'

export interface FilmState {
  /** stage 3 (first WebSocket frame) reached */
  readyAt: number | null
  /** skipped by a key, a click or a controller button */
  skippedAt: number | null
  failed: boolean
  /** start of act 5; final once set */
  revealAt: number | null
}

export const INITIAL_FILM: FilmState = { readyAt: null, skippedAt: null, failed: false, revealAt: null }

export type FilmEvent =
  | { type: 'tick'; t: number }
  | { type: 'ready'; t: number }
  | { type: 'skip'; t: number }
  | { type: 'fail'; t: number }
  | { type: 'retry'; t: number }

/** The earliest moment the reveal may start, readiness aside: the film's end, or the skip. */
export function revealGate(tl: Timeline, s: FilmState): number {
  return s.skippedAt ?? tl.film
}

export function actAt(tl: Timeline, s: FilmState, t: number): Act {
  if (s.revealAt !== null) return t < s.revealAt + tl.reveal ? 'reveal' : 'done'
  if (s.failed) return 'failed'
  if (s.skippedAt !== null || t >= tl.film) return 'wait'
  if (t < tl.cold) return 'cold'
  if (t < tl.assemble) return 'assemble'
  if (t < tl.cut) return 'cut'
  return 'film'
}

/** Skipping works in acts 0-3 (the waiting frame and the failure layout ignore it). */
export function canSkip(tl: Timeline, s: FilmState, t: number): boolean {
  const a = actAt(tl, s, t)
  return a === 'cold' || a === 'assemble' || a === 'cut' || a === 'film'
}

/**
 * One transition. Failure stops the film and holds the failure layout; Retry does not replay it but goes to the
 * waiting frame (a stream that arrives anyway does the same and reveals). The reveal starts at max(gate, ready) and
 * nothing changes after it has started.
 */
export function stepFilm(tl: Timeline, s: FilmState, e: FilmEvent): FilmState {
  if (s.revealAt !== null) return s
  let next = s
  switch (e.type) {
    case 'ready':
      // a stream that arrives after the failure layout reveals at once (as in v0.2): the failure is over
      if (s.readyAt === null) next = s.failed ? { ...s, readyAt: e.t, failed: false, skippedAt: s.skippedAt ?? e.t } : { ...s, readyAt: e.t }
      break
    case 'skip':
      if (canSkip(tl, s, e.t)) next = { ...s, skippedAt: e.t }
      break
    case 'fail':
      if (!s.failed) next = { ...s, failed: true }
      break
    case 'retry':
      if (s.failed) next = { ...s, failed: false, skippedAt: s.skippedAt ?? e.t }
      break
    case 'tick':
      break
  }
  if (!next.failed && next.readyAt !== null) {
    const gate = revealGate(tl, next)
    if (e.t >= gate) next = { ...next, revealAt: Math.max(gate, next.readyAt) }
  }
  return next
}
