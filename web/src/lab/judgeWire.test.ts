// The Lab against real evaluator bodies (WP-5): fixtures/judge-bodies.json holds the responses of the backend's
// judge routes (a liftoff submission on its visible seeds and a one-round duel of hunter_easy against
// hunter_hard, traces cut to a window around the first hit). Regenerate it from the backend when the judge
// response shapes change.
import { describe, expect, it } from 'vitest'

import { normChallenges, normJob, normLadder, type ChallengeResult, type DuelResult } from './api'
import bodies from './fixtures/judge-bodies.json'
import { parseTrace, sampleAt, shotsAt } from './trace'
import { craftsAt } from './traceReplay'

describe('judge wire shapes', () => {
  it('reads the challenge list with names, scene and pass condition', () => {
    const list = normChallenges(bodies.challenges)
    expect(list.map((c) => c.id)).toEqual(['liftoff', 'hover', 'heading', 'square', 'rings', 'pursuit', 'duel_easy', 'duel_medium', 'duel_hard'])
    const lift = list[0]
    expect(lift.name).toBe(bodies.challenges.challenges[0].name_en)
    expect(lift.name_zh).toBe(bodies.challenges.challenges[0].name_zh)
    expect(lift.description).toBe(bodies.challenges.challenges[0].scene_en)
    expect(lift.pass_en).toBe(bodies.challenges.challenges[0].goal_en)
    expect(lift.pass_zh).toBe(bodies.challenges.challenges[0].goal_zh)
    expect(lift.submissions).toBe(1)
    // a visible-only run is an attempt but never the best result
    expect(lift.best).toBeNull()
  })

  it('reads a finished challenge job, with the program from the job', () => {
    expect(['queued', 'running']).toContain(normJob(bodies.submit).state)
    const job = normJob(bodies.challenge_job)
    expect(job).toMatchObject({ kind: 'challenge', state: 'done', progress: 1, error: null })
    const r = job.result as ChallengeResult
    expect(r).toMatchObject({ kind: 'challenge', challenge: 'liftoff', program: 'liftoff', rev: 1, passed: true })
    expect(r.score).toBeGreaterThan(50)
    expect(r.seeds.map((s) => s.seed)).toEqual([1, 2])
    expect(r.seeds.every((s) => s.trace_id !== null)).toBe(true)
    expect(r.metrics.length).toBeGreaterThan(0)
  })

  it('reads a finished duel job and the ladder', () => {
    const job = normJob(bodies.duel_job)
    expect(job).toMatchObject({ kind: 'duel', state: 'done' })
    const r = job.result as DuelResult
    expect(r.rounds).toHaveLength(1)
    const round = r.rounds[0]
    expect(round.winner).toBe('hunter_hard')
    expect(round.entries.map((e) => e.program).sort()).toEqual(['hunter_easy', 'hunter_hard'])
    const loser = round.entries.find((e) => e.program === 'hunter_easy')!
    const winner = round.entries.find((e) => e.program === 'hunter_hard')!
    expect(winner.survived).toBe(true)
    expect(loser.survived).toBe(false)
    expect(loser.alive_s).toBeGreaterThan(0)
    expect(winner.hits).toBeGreaterThan(0)
    expect(round.trace_id).toMatch(/^tr-/)
    expect(r.ladder.map((l) => l.program)).toEqual(['hunter_hard', 'hunter_easy'])
    expect(r.ladder[0].delta).toBeGreaterThan(0)
    const ladder = normLadder(bodies.ladder)
    expect(ladder[0]).toMatchObject({ program: 'hunter_hard', played: 1, wins: 1, losses: 0 })
    expect(ladder[1].elo).toBeLessThan(ladder[0].elo)
  })

  it('replays a challenge trace: radians kept, states held, armed from the flags', () => {
    const raw = bodies.challenge_trace
    const tr = parseTrace(raw)!
    expect(tr).not.toBeNull()
    expect(tr.kind).toBe('challenge')
    expect(tr.title).toContain('liftoff')
    expect(tr.aircraft).toHaveLength(1)
    const c = tr.aircraft[0]
    expect(c.t).toHaveLength(raw.t.length)
    const yawCol = raw.fields.indexOf('yaw')
    expect(c.yaw[5]).toBeCloseTo(Number(raw.aircraft[0].frames[5][yawCol]), 5)
    expect(c.state[0]).toBe(raw.aircraft[0].states[0][1])
    const flags = Number(raw.aircraft[0].frames[raw.t.length - 1][raw.fields.indexOf('flags')])
    expect(c.armed[c.armed.length - 1]).toBe((flags & 1) === 1)
  })

  it('replays a duel trace with both aircraft, the rounds and the hits', () => {
    const raw = bodies.duel_trace
    const tr = parseTrace(raw)!
    expect(tr.kind).toBe('duel')
    expect(tr.aircraft.map((c) => c.program).sort()).toEqual(['hunter_easy', 'hunter_hard'])
    expect(tr.arena?.radius).toBeGreaterThan(0)
    expect(tr.shots.length).toBe(raw.shots.length)
    const hits = tr.events.filter((e) => e.kind === 'hit')
    expect(hits.length).toBeGreaterThan(0)
    expect(hits[0].by).toBe(raw.events.find((e) => e.kind === 'hit')!.shooter)
    // each hit ends one round of its shooter, and that round is gone from the air from then on
    const ended = tr.shots.filter((s) => s.t_end !== null)
    expect(ended.length).toBe(hits.length)
    for (const s of ended) expect(shotsAt(tr.shots, s.t_end!).includes(s)).toBe(false)
    const mid = tr.aircraft[0].t[Math.floor(tr.aircraft[0].t.length / 2)]
    const crafts = craftsAt(tr, mid)
    expect(crafts).toHaveLength(2)
    const s = sampleAt(tr.aircraft[0], mid)
    expect(Number.isFinite(s.x) && Number.isFinite(s.z)).toBe(true)
  })
})
