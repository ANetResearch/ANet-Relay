// 05 §2.3-2.4 and 06 §8: the opening's timeline (Full / Short / Minimal / reduced; film first, then the voxel logo),
// its plan (setting, first launch of a version, clip rotation, storage failures) and the act machine (skip to the
// complete logo, ready before and after the hold, failure, retry). The timeline's defaults must equal the duration
// tokens of styles/motion/ext-tokens.css.
import { readdirSync } from 'node:fs'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { parseMs } from '@/ui/motion/tokens'
import {
  actAt,
  assembleMarks,
  assembleStarts,
  BOOT_DEFAULTS,
  BOOT_KEYS,
  BOOT_TOKEN_NAMES,
  canSkip,
  CLIPS,
  commitBootPlan,
  COOP_CLIPS,
  ACTION_CLIPS,
  fullClips,
  INITIAL_FILM,
  logoAt,
  minBootMs,
  planBoot,
  readBootStore,
  readBootTokens,
  revealGate,
  revealWave,
  stepFilm,
  timeline,
  writeBootMode,
  type FilmEvent,
  type FilmState,
  type KV,
  type Timeline,
} from './bootFilm'

const css = readFileSync(resolve(import.meta.dirname, '../styles/motion/ext-tokens.css'), 'utf8')

function cssMs(name: string): number {
  const m = css.match(new RegExp(`${name}:\\s*([^;]+);`))
  return m ? parseMs(m[1], NaN) : NaN
}

function run(tl: Timeline, events: FilmEvent[]): FilmState {
  return events.reduce((s, e) => stepFilm(tl, s, e), INITIAL_FILM)
}

class MemKV implements KV {
  m = new Map<string, string>()
  getItem(k: string) {
    return this.m.get(k) ?? null
  }
  setItem(k: string, v: string) {
    this.m.set(k, v)
  }
}

const broken: KV = {
  getItem() {
    throw new Error('denied')
  },
  setItem() {
    throw new Error('denied')
  },
}

describe('boot tokens', () => {
  it('match styles/motion/ext-tokens.css (one source for the timeline and the CSS)', () => {
    for (const [k, name] of Object.entries(BOOT_TOKEN_NAMES)) expect(cssMs(name), name).toBe(BOOT_DEFAULTS[k as keyof typeof BOOT_DEFAULTS])
  })
  it('reads through the token reader and keeps fallbacks', () => {
    const tk = readBootTokens((name, d) => (name === '--boot-full-film' ? 4100 : d))
    expect(tk.fullFilm).toBe(4100)
    expect(tk.shortFilm).toBe(BOOT_DEFAULTS.shortFilm)
  })
})

describe('clips', () => {
  it('lists the twenty-one films on disk, the rejected ones gone', () => {
    const disk = readdirSync(resolve(import.meta.dirname, '../../public/boot'))
    for (const c of CLIPS) {
      expect(disk).toContain(`${c}.webm`)
      expect(disk).toContain(`${c}.jpg`)
    }
    expect(CLIPS).toHaveLength(21)
    expect(new Set(CLIPS).size).toBe(21)
    expect(COOP_CLIPS).toHaveLength(8)
    expect(ACTION_CLIPS).toHaveLength(12)
    for (const gone of ['cut-cam-spiral', 'cut-cam-vortex', 'cut-cam-pullspin']) expect(CLIPS as readonly string[]).not.toContain(gone)
    expect(CLIPS as readonly string[]).not.toContain('cut-breakthrough')
    expect(disk.filter((f) => f.endsWith('.webm')).sort()).toEqual([...CLIPS].map((c) => `${c}.webm`).sort())
  })
  it('the director’s cut is an action film, then a cooperation film, rotating by launch count', () => {
    expect(fullClips(0)).toEqual(['cut-chase', 'cut-formation'])
    expect(ACTION_CLIPS.map((_, n) => fullClips(n)[0])).toEqual([...ACTION_CLIPS])
    expect(COOP_CLIPS.map((_, n) => fullClips(n)[1])).toEqual([...COOP_CLIPS])
    expect(fullClips(24)).toEqual(fullClips(0))
    expect(fullClips(-1)).toEqual(fullClips(0))
  })
})

describe('timeline', () => {
  it('full: about 7.8 s, film 80-4300, cut to 4700, logo to 6900, hold to 7300, reveal to 7800', () => {
    const tl = timeline('full')
    expect([tl.cold, tl.film, tl.cut, tl.assemble, tl.hold, tl.hold + tl.reveal]).toEqual([80, 4300, 4700, 6900, 7300, 7800])
    expect(tl.clips).toEqual(fullClips(0))
    expect(tl.segments.map((s) => s.clip)).toEqual(['cut-chase', 'cut-formation'])
    expect(tl.segments[0].start).toBe(80)
    expect(tl.segments[1].end).toBe(4300)
    expect(tl.flashes).toHaveLength(1)
    expect(tl.flashes[0].end - tl.flashes[0].start).toBe(120)
    expect(tl.flashes[0].start).toBe(tl.segments[0].end)
    expect(tl.segments[1].start).toBe(tl.flashes[0].end)
    expect(tl.segments[0].end - tl.segments[0].start).toBe(tl.segments[1].end - tl.segments[1].start)
    expect(tl.fromNavigation).toBe(false)
    expect(revealWave(tl) + tl.tokens.revealCell).toBe(tl.reveal)
  })
  it('short: about 4.4 s, film 80-2200, cut to 2500, logo to 3800, hold to 4000, one clip', () => {
    const tl = timeline('short', ['cut-split'])
    expect([tl.cold, tl.film, tl.cut, tl.assemble, tl.hold, tl.hold + tl.reveal]).toEqual([80, 2200, 2500, 3800, 4000, 4400])
    expect(tl.segments).toEqual([{ clip: 'cut-split', start: 80, end: 2200 }])
    expect(tl.flashes).toEqual([])
    expect(revealWave(tl) + tl.tokens.revealCell).toBe(tl.reveal)
  })
  it('the voxel logo spans about 2.2 s (full) and 1.3 s (short)', () => {
    expect(timeline('full').assemble - timeline('full').cut).toBe(2200)
    expect(timeline('short').assemble - timeline('short').cut).toBe(1300)
  })
  it('minimal is the v0.2 opening: 1200 ms from navigation start, hold and fade', () => {
    const tl = timeline('minimal')
    expect(tl.hold).toBe(1200)
    expect(tl.fromNavigation).toBe(true)
    expect(tl.clips).toEqual([])
    expect(tl.reveal).toBe(160 + 250)
  })
  it('reduced: no film, reveal at 300 ms with nothing animated (total <= 400 ms)', () => {
    const tl = timeline('reduced', ['cut-chase'])
    expect(tl.hold + tl.reveal).toBeLessThanOrEqual(400)
    expect(tl.reveal).toBe(0)
    expect(tl.clips).toEqual([])
    expect(tl.segments).toEqual([])
  })
  it('minimum boot times come from the timeline', () => {
    expect(minBootMs('full')).toBe(7300)
    expect(minBootMs('short')).toBe(4000)
    expect(minBootMs('minimal')).toBe(1200)
    expect(minBootMs('reduced')).toBe(300)
  })
  it('act 3: 260 ms flights from the end of the cut, flash, then RELAY tracks in until act 4', () => {
    for (const v of ['full', 'short'] as const) {
      const tl = timeline(v)
      expect(tl.tokens.voxelFly).toBe(260)
      const { landed, mark } = assembleMarks(tl)
      expect(mark - landed).toBe(tl.tokens.flash)
      expect(tl.assemble - mark).toBe(tl.tokens.wordHold)
      const starts = assembleStarts(tl, 50)
      expect(starts[0]).toBe(tl.cut)
      expect(starts[49] + tl.tokens.voxelFly).toBeCloseTo(landed)
      expect([...starts].sort((a, b) => a - b)).toEqual(starts)
      expect(starts.every((t) => t >= tl.cut && t + tl.tokens.voxelFly <= landed + 1e-9)).toBe(true)
    }
    expect(assembleStarts(timeline('full'), 0)).toEqual([])
    expect(assembleStarts(timeline('full'), 1)).toEqual([4700])
  })
})

describe('plan', () => {
  const base = { mode: null, seen: '0.4.0', version: '0.4.0', n: 0, reduced: false }
  it('defaults to Short, rotating through all twenty-one clips by launch count', () => {
    const plans = Array.from({ length: 22 }, (_, n) => planBoot({ ...base, n }))
    expect(plans.every((p) => p.variant === 'short')).toBe(true)
    expect(plans.map((p) => p.clips[0])).toEqual([...CLIPS, CLIPS[0]])
    expect(plans.map((p) => p.writeN)).toEqual(Array.from({ length: 22 }, (_, n) => n + 1))
  })
  it('plays the director’s cut on the first launch of each version, then Short', () => {
    expect(planBoot({ ...base, seen: null })).toMatchObject({ variant: 'full', clips: fullClips(0), writeSeen: '0.4.0', writeN: 1 })
    expect(planBoot({ ...base, seen: '0.3.1', n: 5 })).toMatchObject({ variant: 'full', clips: fullClips(5), writeSeen: '0.4.0', writeN: 6 })
    expect(planBoot({ ...base, seen: '0.4.0' }).variant).toBe('short')
  })
  it('takes Full and Minimal as set; Full rotates its cooperation film', () => {
    expect(planBoot({ ...base, mode: 'full' }).variant).toBe('full')
    expect([0, 1, 2].map((n) => planBoot({ ...base, mode: 'full', n }).clips[0])).toEqual(ACTION_CLIPS.slice(0, 3))
    expect(planBoot({ ...base, mode: 'minimal', seen: null })).toMatchObject({ variant: 'minimal', clips: [], writeSeen: null })
  })
  it('reduced motion never loads a film', () => {
    expect(planBoot({ ...base, mode: 'full', seen: null, reduced: true })).toEqual({ variant: 'reduced', clips: [], writeSeen: null, writeN: null })
  })
  it('unreadable storage counts as seen and plays the first clip', () => {
    const p = planBoot({ ...base, seen: undefined, n: undefined })
    expect(p.variant).toBe('short')
    expect(p.clips).toEqual([CLIPS[0]])
    expect(p.writeN).toBeNull()
    expect(planBoot({ ...base, mode: 'full', seen: undefined, n: undefined })).toMatchObject({ clips: fullClips(0), writeN: null })
  })
  it('round-trips through storage and survives storage that throws', () => {
    const kv = new MemKV()
    expect(readBootStore(kv)).toEqual({ mode: null, seen: null, n: null })
    const first = planBoot({ ...base, ...readBootStore(kv) })
    expect(first.variant).toBe('full')
    commitBootPlan(kv, first)
    expect(kv.getItem(BOOT_KEYS.seen)).toBe('0.4.0')
    const second = planBoot({ ...base, ...readBootStore(kv) })
    expect(second).toMatchObject({ variant: 'short', clips: [CLIPS[1]], writeN: 2 })
    commitBootPlan(kv, second)
    expect(planBoot({ ...base, ...readBootStore(kv) }).clips).toEqual([CLIPS[2]])
    writeBootMode(kv, 'minimal')
    expect(readBootStore(kv).mode).toBe('minimal')
    kv.setItem(BOOT_KEYS.mode, 'cinematic')
    kv.setItem(BOOT_KEYS.n, 'x')
    expect(readBootStore(kv)).toMatchObject({ mode: null, n: null })

    expect(readBootStore(broken)).toEqual({ mode: null, seen: undefined, n: undefined })
    expect(readBootStore(null)).toEqual({ mode: null, seen: undefined, n: undefined })
    expect(() => commitBootPlan(broken, first)).not.toThrow()
    expect(() => writeBootMode(broken, 'full')).not.toThrow()
  })
})

describe('act machine', () => {
  const full = timeline('full')
  const short = timeline('short')

  it('walks the acts by time while nothing happens: film, cut, logo, hold, then the waiting frame', () => {
    const s = INITIAL_FILM
    expect([0, 100, 4400, 5000, 7000, 7400].map((t) => actAt(full, s, t))).toEqual(['cold', 'film', 'cut', 'assemble', 'hold', 'wait'])
    expect([0, 100, 2300, 3000, 3900, 4100].map((t) => actAt(short, s, t))).toEqual(['cold', 'film', 'cut', 'assemble', 'hold', 'wait'])
  })

  it('backend ready before the hold ends: the reveal starts exactly at the end of act 4', () => {
    let s = run(full, [{ type: 'ready', t: 900 }, { type: 'tick', t: 7000 }])
    expect(s.revealAt).toBeNull()
    expect(revealGate(full, s)).toBe(7300)
    s = stepFilm(full, s, { type: 'tick', t: 7316 })
    expect(s.revealAt).toBe(7300)
    expect(actAt(full, s, 7600)).toBe('reveal')
    expect(actAt(full, s, 7800)).toBe('done')
  })

  it('backend ready after the hold: the logo waits with the progress line, then reveals at once', () => {
    let s = run(short, [{ type: 'tick', t: 4100 }])
    expect(actAt(short, s, 4100)).toBe('wait')
    s = stepFilm(short, s, { type: 'ready', t: 6400 })
    expect(s.revealAt).toBe(6400)
    expect(actAt(short, s, 6400 + short.reveal)).toBe('done')
  })

  it('skip while ready: the complete logo for 300 ms, then the reveal', () => {
    let s = run(full, [{ type: 'ready', t: 300 }, { type: 'skip', t: 1300 }])
    expect(s.revealAt).toBeNull()
    expect(logoAt(full, s)).toBe(1300)
    expect(revealGate(full, s)).toBe(1600)
    expect(actAt(full, s, 1301)).toBe('hold')
    s = stepFilm(full, s, { type: 'tick', t: 1600 })
    expect(s.revealAt).toBe(1600)
    expect(actAt(full, s, 1601)).toBe('reveal')
    expect(actAt(full, s, 1600 + full.reveal)).toBe('done')
  })

  it('skip during the logo hold reveals at once when the logo has been seen long enough', () => {
    const s = run(full, [{ type: 'ready', t: 300 }, { type: 'skip', t: 7250 }])
    expect(s.revealAt).toBe(7250)
    // during act 3, the logo is complete from the skip on
    const early = run(short, [{ type: 'ready', t: 300 }, { type: 'skip', t: 3700 }])
    expect(revealGate(short, early)).toBe(4000)
    expect(run(short, [{ type: 'ready', t: 300 }, { type: 'skip', t: 3700 }, { type: 'tick', t: 4000 }]).revealAt).toBe(4000)
    // never later than the natural end of act 4
    const late = run(short, [{ type: 'skip', t: 3850 }])
    expect(revealGate(short, late)).toBe(4000)
  })

  it('skip before ready holds the logo, then the waiting frame until the backend answers', () => {
    let s = run(full, [{ type: 'skip', t: 400 }])
    expect(s.skippedAt).toBe(400)
    expect(actAt(full, s, 500)).toBe('hold')
    expect(actAt(full, s, 800)).toBe('wait')
    expect(canSkip(full, s, 500)).toBe(false)
    s = stepFilm(full, s, { type: 'ready', t: 2100 })
    expect(s.revealAt).toBe(2100)
  })

  it('a ready that lands inside the 300 ms after a skip waits for it', () => {
    const s = run(short, [{ type: 'skip', t: 1000 }, { type: 'ready', t: 1100 }, { type: 'tick', t: 1300 }])
    expect(s.revealAt).toBe(1300)
  })

  it('skip is ignored outside acts 0-4 and only counts once', () => {
    const waited = run(short, [{ type: 'tick', t: 4100 }, { type: 'skip', t: 4100 }])
    expect(waited.skippedAt).toBeNull()
    const failed = run(short, [{ type: 'fail', t: 500 }, { type: 'skip', t: 600 }])
    expect(failed.skippedAt).toBeNull()
    const twice = run(short, [{ type: 'skip', t: 500 }, { type: 'skip', t: 700 }])
    expect(twice.skippedAt).toBe(500)
    expect(canSkip(short, INITIAL_FILM, 0)).toBe(true)
    expect(canSkip(short, INITIAL_FILM, 3900)).toBe(true)
  })

  it('failure interrupts the film; Retry goes to the waiting frame without replaying', () => {
    let s = run(full, [{ type: 'fail', t: 700 }])
    expect(actAt(full, s, 800)).toBe('failed')
    expect(actAt(full, s, 3000)).toBe('failed')
    s = stepFilm(full, s, { type: 'tick', t: 8000 })
    expect(actAt(full, s, 8000)).toBe('failed')
    s = stepFilm(full, s, { type: 'retry', t: 21000 })
    expect(s.failed).toBe(false)
    expect(actAt(full, s, 21000)).toBe('wait')
    s = stepFilm(full, s, { type: 'ready', t: 23000 })
    expect(s.revealAt).toBe(23000)
  })

  it('a failure during the film holds even when the hold would have ended; a late stream still reveals', () => {
    let s = run(short, [{ type: 'fail', t: 1500 }, { type: 'tick', t: 4000 }])
    expect(s.revealAt).toBeNull()
    s = stepFilm(short, s, { type: 'ready', t: 24000 })
    expect(s.failed).toBe(false)
    expect(s.revealAt).toBe(24000)
  })

  it('the reveal is final', () => {
    const s = run(short, [{ type: 'ready', t: 100 }, { type: 'tick', t: 4000 }])
    expect(s.revealAt).toBe(4000)
    expect(stepFilm(short, s, { type: 'fail', t: 4100 })).toBe(s)
    expect(stepFilm(short, s, { type: 'skip', t: 4100 })).toBe(s)
  })

  it('minimal and reduced reveal at their minimum when ready earlier', () => {
    const min = timeline('minimal')
    expect(run(min, [{ type: 'ready', t: 200 }, { type: 'tick', t: 1200 }]).revealAt).toBe(1200)
    const red = timeline('reduced')
    expect(run(red, [{ type: 'ready', t: 50 }, { type: 'tick', t: 300 }]).revealAt).toBe(300)
    expect(run(red, [{ type: 'ready', t: 900 }]).revealAt).toBe(900)
  })
})
