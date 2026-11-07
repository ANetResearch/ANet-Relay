// 05 §2.3-2.4: the opening's timeline (Full / Short / Minimal / reduced), its plan (setting, first launch of a
// version, clip rotation, storage failures) and the act machine (skip, ready before and after the film, failure,
// retry). The timeline's defaults must equal the duration tokens of styles/motion/ext-tokens.css.
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
  FULL_CLIPS,
  INITIAL_FILM,
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
    const tk = readBootTokens((name, d) => (name === '--boot-full-film' ? 4800 : d))
    expect(tk.fullFilm).toBe(4800)
    expect(tk.shortFilm).toBe(BOOT_DEFAULTS.shortFilm)
  })
})

describe('timeline', () => {
  it('full: about 5.6 s, acts at 80 / 1100 / 1500 / 5000, two clips with a white field between', () => {
    const tl = timeline('full')
    expect([tl.cold, tl.assemble, tl.cut, tl.film, tl.film + tl.reveal]).toEqual([80, 1100, 1500, 5000, 5600])
    expect(tl.clips).toEqual(FULL_CLIPS)
    expect(tl.segments.map((s) => s.clip)).toEqual(['cut-breakthrough', 'cut-chase'])
    expect(tl.segments[0].start).toBe(1500)
    expect(tl.segments[1].end).toBe(5000)
    expect(tl.flashes).toHaveLength(1)
    expect(tl.flashes[0].end - tl.flashes[0].start).toBe(120)
    expect(tl.flashes[0].start).toBe(tl.segments[0].end)
    expect(tl.segments[1].start).toBe(tl.flashes[0].end)
    expect(tl.fromNavigation).toBe(false)
    expect(revealWave(tl) + tl.tokens.revealCell).toBe(tl.reveal)
  })
  it('short: about 3.2 s, acts at 80 / 700 / 1000 / 2800, one clip', () => {
    const tl = timeline('short', ['cut-split'])
    expect([tl.cold, tl.assemble, tl.cut, tl.film, tl.film + tl.reveal]).toEqual([80, 700, 1000, 2800, 3200])
    expect(tl.segments).toEqual([{ clip: 'cut-split', start: 1000, end: 2800 }])
    expect(tl.flashes).toEqual([])
    expect(revealWave(tl) + tl.tokens.revealCell).toBe(tl.reveal)
  })
  it('minimal is the v0.2 opening: 1200 ms from navigation start, hold and fade', () => {
    const tl = timeline('minimal')
    expect(tl.film).toBe(1200)
    expect(tl.fromNavigation).toBe(true)
    expect(tl.clips).toEqual([])
    expect(tl.reveal).toBe(160 + 250)
  })
  it('reduced: no film, reveal at 300 ms with nothing animated (total <= 400 ms)', () => {
    const tl = timeline('reduced', ['cut-chase'])
    expect(tl.film + tl.reveal).toBeLessThanOrEqual(400)
    expect(tl.reveal).toBe(0)
    expect(tl.clips).toEqual([])
    expect(tl.segments).toEqual([])
  })
  it('minimum boot times come from the timeline', () => {
    expect(minBootMs('full')).toBe(5000)
    expect(minBootMs('short')).toBe(2800)
    expect(minBootMs('minimal')).toBe(1200)
    expect(minBootMs('reduced')).toBe(300)
  })
  it('act 1: the voxels land, flash, and the crisp mark holds before act 2', () => {
    for (const v of ['full', 'short'] as const) {
      const tl = timeline(v)
      const { landed, mark } = assembleMarks(tl)
      expect(mark - landed).toBe(tl.tokens.flash)
      expect(tl.assemble - mark).toBe(tl.tokens.wordHold)
      const starts = assembleStarts(tl, 50)
      expect(starts[0]).toBe(tl.cold)
      expect(starts[49] + tl.tokens.voxelFly).toBeCloseTo(landed)
      expect([...starts].sort((a, b) => a - b)).toEqual(starts)
    }
    expect(assembleStarts(timeline('full'), 0)).toEqual([])
    expect(assembleStarts(timeline('full'), 1)).toEqual([80])
  })
})

describe('plan', () => {
  const base = { mode: null, seen: '0.3.0', version: '0.3.0', n: 0, reduced: false }
  it('defaults to Short, rotating the three clips by launch count', () => {
    const plans = [0, 1, 2, 3].map((n) => planBoot({ ...base, n }))
    expect(plans.map((p) => p.variant)).toEqual(['short', 'short', 'short', 'short'])
    expect(plans.map((p) => p.clips[0])).toEqual([CLIPS[0], CLIPS[1], CLIPS[2], CLIPS[0]])
    expect(plans.map((p) => p.writeN)).toEqual([1, 2, 3, 4])
  })
  it('plays the director’s cut on the first launch of each version, then Short', () => {
    expect(planBoot({ ...base, seen: null })).toMatchObject({ variant: 'full', clips: FULL_CLIPS, writeSeen: '0.3.0' })
    expect(planBoot({ ...base, seen: '0.2.1' })).toMatchObject({ variant: 'full', writeSeen: '0.3.0' })
    expect(planBoot({ ...base, seen: '0.3.0' }).variant).toBe('short')
  })
  it('takes Full and Minimal as set', () => {
    expect(planBoot({ ...base, mode: 'full' }).variant).toBe('full')
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
  })
  it('round-trips through storage and survives storage that throws', () => {
    const kv = new MemKV()
    expect(readBootStore(kv)).toEqual({ mode: null, seen: null, n: null })
    const first = planBoot({ ...base, ...readBootStore(kv) })
    expect(first.variant).toBe('full')
    commitBootPlan(kv, first)
    expect(kv.getItem(BOOT_KEYS.seen)).toBe('0.3.0')
    const second = planBoot({ ...base, ...readBootStore(kv) })
    expect(second).toMatchObject({ variant: 'short', clips: [CLIPS[0]], writeN: 1 })
    commitBootPlan(kv, second)
    expect(planBoot({ ...base, ...readBootStore(kv) }).clips).toEqual([CLIPS[1]])
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

  it('walks the acts by time while nothing happens', () => {
    const s = INITIAL_FILM
    expect([0, 100, 1200, 2000, 5200].map((t) => actAt(full, s, t))).toEqual(['cold', 'assemble', 'cut', 'film', 'wait'])
    expect([0, 100, 800, 1500, 2900].map((t) => actAt(short, s, t))).toEqual(['cold', 'assemble', 'cut', 'film', 'wait'])
  })

  it('backend ready before the film ends: the reveal starts exactly at the end of act 3', () => {
    let s = run(full, [{ type: 'ready', t: 900 }, { type: 'tick', t: 4000 }])
    expect(s.revealAt).toBeNull()
    expect(revealGate(full, s)).toBe(5000)
    s = stepFilm(full, s, { type: 'tick', t: 5016 })
    expect(s.revealAt).toBe(5000)
    expect(actAt(full, s, 5300)).toBe('reveal')
    expect(actAt(full, s, 5600)).toBe('done')
  })

  it('backend ready after the film ends: the frame waits, then reveals at once', () => {
    let s = run(short, [{ type: 'tick', t: 2900 }])
    expect(actAt(short, s, 2900)).toBe('wait')
    s = stepFilm(short, s, { type: 'ready', t: 6400 })
    expect(s.revealAt).toBe(6400)
    expect(actAt(short, s, 6400 + short.reveal)).toBe('done')
  })

  it('skip while ready goes straight to the reveal', () => {
    const s = run(full, [{ type: 'ready', t: 300 }, { type: 'skip', t: 1300 }])
    expect(s.revealAt).toBe(1300)
    expect(actAt(full, s, 1301)).toBe('reveal')
  })

  it('skip before ready holds the waiting frame until the backend answers', () => {
    let s = run(full, [{ type: 'skip', t: 400 }])
    expect(s.skippedAt).toBe(400)
    expect(actAt(full, s, 500)).toBe('wait')
    expect(canSkip(full, s, 500)).toBe(false)
    s = stepFilm(full, s, { type: 'ready', t: 2100 })
    expect(s.revealAt).toBe(2100)
  })

  it('skip is ignored outside acts 0-3', () => {
    const waited = run(short, [{ type: 'tick', t: 3000 }, { type: 'skip', t: 3000 }])
    expect(waited.skippedAt).toBeNull()
    const failed = run(short, [{ type: 'fail', t: 500 }, { type: 'skip', t: 600 }])
    expect(failed.skippedAt).toBeNull()
    expect(canSkip(short, INITIAL_FILM, 0)).toBe(true)
  })

  it('failure interrupts the film; Retry goes to the waiting frame without replaying', () => {
    let s = run(full, [{ type: 'fail', t: 700 }])
    expect(actAt(full, s, 800)).toBe('failed')
    expect(actAt(full, s, 3000)).toBe('failed')
    s = stepFilm(full, s, { type: 'tick', t: 6000 })
    expect(actAt(full, s, 6000)).toBe('failed')
    s = stepFilm(full, s, { type: 'retry', t: 21000 })
    expect(s.failed).toBe(false)
    expect(actAt(full, s, 21000)).toBe('wait')
    s = stepFilm(full, s, { type: 'ready', t: 23000 })
    expect(s.revealAt).toBe(23000)
  })

  it('a failure during the film holds even when the film would have ended; a late stream still reveals', () => {
    let s = run(short, [{ type: 'fail', t: 1500 }, { type: 'tick', t: 2800 }])
    expect(s.revealAt).toBeNull()
    s = stepFilm(short, s, { type: 'ready', t: 24000 })
    expect(s.failed).toBe(false)
    expect(s.revealAt).toBe(24000)
  })

  it('the reveal is final', () => {
    const s = run(short, [{ type: 'ready', t: 100 }, { type: 'tick', t: 2800 }])
    expect(s.revealAt).toBe(2800)
    expect(stepFilm(short, s, { type: 'fail', t: 2900 })).toBe(s)
    expect(stepFilm(short, s, { type: 'skip', t: 2900 })).toBe(s)
  })

  it('minimal and reduced reveal at their minimum when ready earlier', () => {
    const min = timeline('minimal')
    expect(run(min, [{ type: 'ready', t: 200 }, { type: 'tick', t: 1200 }]).revealAt).toBe(1200)
    const red = timeline('reduced')
    expect(run(red, [{ type: 'ready', t: 50 }, { type: 'tick', t: 300 }]).revealAt).toBe(300)
    expect(run(red, [{ type: 'ready', t: 900 }]).revealAt).toBe(900)
  })
})
