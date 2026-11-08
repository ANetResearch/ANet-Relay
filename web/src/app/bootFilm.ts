// Director's-cut opening (05 §2, reordered by 06 §8: film first, then the voxel logo), the pure part: which opening this launch plays (Full / Short / Minimal, or the
// reduced-motion one), the act timeline built from the duration tokens, and the act state machine (backend ready,
// skip, failure, retry). No DOM and no timers: app/bootCinema.ts paints the acts and app/boot.ts asks this module
// when the reveal may start, so the earliest reveal and the timeline come from one place (05 §2.4).

export type BootMode = 'full' | 'short' | 'minimal'
/** `reduced` is forced by reduced motion (or ?motion=off) whatever the setting says. */
export type BootVariant = BootMode | 'reduced'

export const BOOT_MODES: readonly BootMode[] = ['full', 'short', 'minimal']
export const DEFAULT_BOOT_MODE: BootMode = 'short'

/** The eight cooperation films of 06 §8. */
export const COOP_CLIPS = ['cut-formation', 'cut-handover', 'cut-groundair', 'cut-quadruped', 'cut-vtol', 'cut-assembly', 'cut-gateduel', 'cut-search'] as const
/** The action films of 06 §8 and §12: the chase and its five siblings (tight chase camera, shake, speed), then the six
 * camera-move films (barrel roll, fisheye, FPV flip, helix, dolly zoom, whip pan). */
export const ACTION_CLIPS = [
  'cut-chase', 'cut-chase-tunnel', 'cut-chase-pillars', 'cut-chase-dogfight', 'cut-chase-dive', 'cut-chase-skim',
  'cut-cam-roll', 'cut-cam-fisheye', 'cut-cam-fpvflip', 'cut-cam-helix', 'cut-cam-dolly', 'cut-cam-whip',
] as const
/** Boot films in web/public/boot/ (WebM VP9 with a JPEG poster each); the short cut rotates through all twenty-one in this order, action films first. */
export const CLIPS = [...ACTION_CLIPS, ...COOP_CLIPS, 'cut-split'] as const
export type Clip = (typeof CLIPS)[number]

/** The director's cut of launch `n`: an action film, then a cooperation film, each rotating by launch count (06 §8). */
export function fullClips(n = 0): readonly Clip[] {
  const k = Number.isFinite(n) && n >= 0 ? Math.floor(n) : 0
  return [ACTION_CLIPS[k % ACTION_CLIPS.length], COOP_CLIPS[k % COOP_CLIPS.length]]
}

/** localStorage keys. `mode` mirrors Settings > General because the boot runs before the backend answers. */
export const BOOT_KEYS = { mode: 'relay.boot.mode', seen: 'relay.boot.seen', n: 'relay.boot.n' } as const

// ------------------------------------------------------------------------------------------------ tokens

export interface BootTokens {
  fullDur: number
  fullFilm: number
  fullCut: number
  fullAssemble: number
  fullHold: number
  shortDur: number
  shortFilm: number
  shortCut: number
  shortAssemble: number
  shortHold: number
  minimalDur: number
  minimalHold: number
  reducedDur: number
  cold: number
  voxelFly: number
  flash: number
  wordHold: number
  skipLogo: number
  cutFlash: number
  indicatorFlash: number
  revealWave: number
  revealWaveShort: number
  revealCell: number
}

/** The CSS custom property behind each value (styles/motion/ext-tokens.css). */
export const BOOT_TOKEN_NAMES: Record<keyof BootTokens, string> = {
  fullDur: '--boot-full-dur',
  fullFilm: '--boot-full-film',
  fullCut: '--boot-full-cut',
  fullAssemble: '--boot-full-assemble',
  fullHold: '--boot-full-hold',
  shortDur: '--boot-short-dur',
  shortFilm: '--boot-short-film',
  shortCut: '--boot-short-cut',
  shortAssemble: '--boot-short-assemble',
  shortHold: '--boot-short-hold',
  minimalDur: '--boot-minimal-dur',
  minimalHold: '--boot-minimal-hold',
  reducedDur: '--boot-reduced-dur',
  cold: '--boot-cold',
  voxelFly: '--boot-voxel-fly',
  flash: '--boot-flash',
  wordHold: '--boot-word-hold',
  skipLogo: '--boot-skip-logo',
  cutFlash: '--boot-cut-flash',
  indicatorFlash: '--boot-indicator-flash',
  revealWave: '--boot-reveal-wave',
  revealWaveShort: '--boot-reveal-wave-short',
  revealCell: '--boot-reveal-cell',
}

/** Fallbacks for node tests and a stylesheet that has not applied; bootFilm.test.ts pins them to ext-tokens.css. */
export const BOOT_DEFAULTS: BootTokens = {
  fullDur: 7800,
  fullFilm: 4300,
  fullCut: 4700,
  fullAssemble: 6900,
  fullHold: 7300,
  shortDur: 4400,
  shortFilm: 2200,
  shortCut: 2500,
  shortAssemble: 3800,
  shortHold: 4000,
  minimalDur: 1200,
  minimalHold: 160,
  reducedDur: 300,
  cold: 80,
  voxelFly: 260,
  flash: 60,
  wordHold: 400,
  skipLogo: 300,
  cutFlash: 120,
  indicatorFlash: 80,
  revealWave: 250,
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

/** Act boundaries in ms from the clock start (06 §8; the order of 05 §2.3 was logo first). */
export interface Timeline {
  variant: BootVariant
  /** act 0 ends (g950 only) */
  cold: number
  /** act 1 ends: the last clip stops and the diagonal cut starts */
  film: number
  /** act 2 ends: the wave has cut the film away and left g950 */
  cut: number
  /** act 3 ends: the voxel logo has landed and flashed, RELAY has tracked in */
  assemble: number
  /** act 4 ends: the logo has held; the earliest reveal (the boot's minimum duration) */
  hold: number
  /** length of act 5, the reveal */
  reveal: number
  /** minimal and reduced count from navigation start (the v0.2 rule); the films from the first visible frame */
  fromNavigation: boolean
  clips: readonly Clip[]
  /** clip playback windows inside act 1 */
  segments: FilmSegment[]
  /** white fields between two clips */
  flashes: { start: number; end: number }[]
  tokens: BootTokens
}

export function timeline(variant: BootVariant, clips: readonly Clip[] = [], tk: BootTokens = BOOT_DEFAULTS): Timeline {
  if (variant === 'minimal' || variant === 'reduced') {
    const min = variant === 'minimal' ? tk.minimalDur : tk.reducedDur
    const reveal = variant === 'minimal' ? tk.minimalHold + tk.revealCell : 0
    return { variant, cold: 0, film: min, cut: min, assemble: min, hold: min, reveal, fromNavigation: true, clips: [], segments: [], flashes: [], tokens: tk }
  }
  const full = variant === 'full'
  const cold = tk.cold
  const film = full ? tk.fullFilm : tk.shortFilm
  const cut = full ? tk.fullCut : tk.shortCut
  const assemble = full ? tk.fullAssemble : tk.shortAssemble
  const hold = full ? tk.fullHold : tk.shortHold
  const total = full ? tk.fullDur : tk.shortDur
  const list = clips.length ? clips : full ? fullClips(0) : [CLIPS[0]]
  // act 1 is shared evenly between the clips, with a white field of cutFlash between two of them
  const n = list.length
  const span = (film - cold - (n - 1) * tk.cutFlash) / n
  const segments: FilmSegment[] = []
  const flashes: { start: number; end: number }[] = []
  let at = cold
  list.forEach((clip, i) => {
    const end = i === n - 1 ? film : at + span
    segments.push({ clip, start: at, end })
    if (i < n - 1) flashes.push({ start: end, end: end + tk.cutFlash })
    at = end + tk.cutFlash
  })
  return { variant, cold, film, cut, assemble, hold, reveal: total - hold, fromNavigation: false, clips: list, segments, flashes, tokens: tk }
}

/** The shortest boot of a variant: the reveal never starts earlier (boot.ts MIN_BOOT_MS comes from here). */
export function minBootMs(variant: BootVariant, tk: BootTokens = BOOT_DEFAULTS): number {
  return timeline(variant, [], tk).hold
}

/**
 * Act 3 inside: voxels fly in until `landed`, the g50 flash runs until `mark`, then the crisp mark stands and RELAY
 * tracks in (letter spacing closing, --boot-word-hold) until act 4.
 */
export function assembleMarks(tl: Timeline): { landed: number; mark: number } {
  const mark = Math.max(tl.cut, tl.assemble - tl.tokens.wordHold)
  return { landed: Math.max(tl.cut, mark - tl.tokens.flash), mark }
}

/**
 * Start time of every voxel's flight in act 3 for `count` voxels in landing order: the first leaves when the cut
 * ends, each flies --boot-voxel-fly, and the last lands at `landed` (about 2.2 s / 1.3 s for the whole act).
 */
export function assembleStarts(tl: Timeline, count: number): number[] {
  const window = Math.max(0, assembleMarks(tl).landed - tl.cut - tl.tokens.voxelFly)
  if (count <= 0) return []
  if (count === 1) return [tl.cut]
  return Array.from({ length: count }, (_, i) => tl.cut + (window * i) / (count - 1))
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
  /** relay.boot.n: launches so far (both films rotate on it); null = never; undefined = storage unreadable */
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
 * update. Full and Minimal are taken as set. Reduced motion never loads a film. Both films rotate on the launch
 * count (the director's cut through the eight cooperation films, the short cut through all ten). Storage that
 * cannot be read counts as "already seen" and plays the first short clip.
 */
export function planBoot(i: PlanInput): BootPlan {
  if (i.reduced) return { variant: 'reduced', clips: [], writeSeen: null, writeN: null }
  const mode = i.mode ?? DEFAULT_BOOT_MODE
  if (mode === 'minimal') return { variant: 'minimal', clips: [], writeSeen: null, writeN: null }
  const n = typeof i.n === 'number' && Number.isFinite(i.n) && i.n >= 0 ? Math.floor(i.n) : 0
  const writeN = i.n === undefined ? null : n + 1
  const firstRun = mode === 'short' && i.seen !== undefined && i.seen !== i.version
  if (mode === 'full' || firstRun) return { variant: 'full', clips: fullClips(n), writeSeen: i.version, writeN }
  return { variant: 'short', clips: [CLIPS[n % CLIPS.length]], writeSeen: null, writeN }
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

export type Act = 'cold' | 'film' | 'cut' | 'assemble' | 'hold' | 'wait' | 'failed' | 'reveal' | 'done'

export interface FilmState {
  /** stage 3 (first WebSocket frame) reached */
  readyAt: number | null
  /** skipped by a key, a click or a controller button: the complete logo stands from here */
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

/** When the complete logo first stood: the end of act 3, or a skip before it. */
export function logoAt(tl: Timeline, s: FilmState): number {
  return s.skippedAt === null ? tl.assemble : Math.min(s.skippedAt, tl.assemble)
}

/**
 * The earliest moment the reveal may start, readiness aside: the end of act 4, or after a skip once the complete
 * logo has been seen for --boot-skip-logo (never later than the end of act 4, never earlier than the skip).
 */
export function revealGate(tl: Timeline, s: FilmState): number {
  if (s.skippedAt === null) return tl.hold
  return Math.min(tl.hold, Math.max(s.skippedAt, logoAt(tl, s) + tl.tokens.skipLogo))
}

export function actAt(tl: Timeline, s: FilmState, t: number): Act {
  if (s.revealAt !== null) return t < s.revealAt + tl.reveal ? 'reveal' : 'done'
  if (s.failed) return 'failed'
  if (s.skippedAt !== null) return t < revealGate(tl, s) ? 'hold' : 'wait'
  if (t >= tl.hold) return 'wait'
  if (t < tl.cold) return 'cold'
  if (t < tl.film) return 'film'
  if (t < tl.cut) return 'cut'
  if (t < tl.assemble) return 'assemble'
  return 'hold'
}

/** Skipping works in acts 0-4 and once (the waiting frame and the failure layout ignore it). */
export function canSkip(tl: Timeline, s: FilmState, t: number): boolean {
  if (s.skippedAt !== null) return false
  const a = actAt(tl, s, t)
  return a === 'cold' || a === 'film' || a === 'cut' || a === 'assemble' || a === 'hold'
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
