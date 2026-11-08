// Dogfight end to end (07 §3-§5, v0.5.0): a four-aircraft dogfight started over REST against the real backend runs
// in practice mode; rounds appear in the arena view, a hit or a collision arrives as an event, the leaderboard has
// a row per aircraft and the Fly stage shows its panel; stopping the dogfight ends it.
import path from 'node:path'
import { expect, test } from '@playwright/test'
import { ARTIFACTS, launchApp, rest, until, type RelayApp } from './harness'

type Dogfight = { running: boolean; aircraft: { vid: string; role: string; program: string }[] }
type Arena = { enabled: boolean; shots: unknown[]; match: { running: boolean } | null; board_rev: number }
type Ev = { seq: number; kind: string; vehicle: string | null; data: Record<string, unknown> }
type Board = { rows: { key: string; local: boolean; shots: number | null; controller: { kind: string } }[] }

let r: RelayApp

test.afterAll(async () => {
  await r?.close()
})

test('dogfight: four programs fight, rounds fly, hits or collisions land, the leaderboard fills, then it stops', async () => {
  r = await launchApp({ lang: 'en', size: { width: 1440, height: 900 } })
  const { page } = r
  const shots = path.join(ARTIFACTS, 'dogfight')

  const started = await rest<Dogfight>(r, 'POST', '/scenarios/dogfight', {
    aircraft: 4,
    roles: { gunner: 3, rammer: 1 },
    duration_s: 90,
    seed: 3,
  })
  expect(started.running).toBe(true)
  expect(started.aircraft.map((a) => a.role).sort()).toEqual(['gunner', 'gunner', 'gunner', 'rammer'])

  // practice mode: the arena is on without a LAN, and the match runs with the dogfight
  const arena = await until(async () => {
    const a = await rest<Arena>(r, 'GET', '/arena')
    return a.enabled && a.match?.running ? a : null
  }, 'arena in practice mode with a running match', 60000)
  expect(arena.board_rev).toBeGreaterThanOrEqual(0)

  // the gunners take off by themselves and fire: rounds in the arena view
  await until(async () => (await rest<Arena>(r, 'GET', '/arena')).shots.length > 0, 'rounds in the arena view', 90000, 100)

  // a hit or a collision arrives as an event
  let after = 0
  const hit = await until(async () => {
    const page_ = await rest<{ events: Ev[]; last_seq: number }>(r, 'GET', `/events?after=${after}&limit=1000`)
    after = page_.events.length ? page_.events[page_.events.length - 1].seq : after
    return page_.events.find((e) => e.kind === 'arena_hit' || e.kind === 'arena_down' || e.kind === 'collision') ?? null
  }, 'a hit or a collision event', 120000, 250)
  expect(['arena_hit', 'arena_down', 'collision']).toContain(hit.kind)

  // the leaderboard has a line for every aircraft, flown by programs, with the shots counted
  const board = await until(async () => {
    const b = await rest<Board>(r, 'GET', '/arena/leaderboard?by=downs')
    return b.rows.length >= 4 && b.rows.some((x) => (x.shots ?? 0) > 0) ? b : null
  }, 'leaderboard rows', 30000)
  expect(board.rows.every((x) => x.local)).toBe(true)
  expect(board.rows.filter((x) => x.controller.kind === 'program').length).toBeGreaterThanOrEqual(1)

  // the Fly stage shows the leaderboard panel with those rows
  await expect(page.locator('[data-board-row]').first()).toBeVisible({ timeout: 20000 })
  await page.screenshot({ path: path.join(shots, '01-dogfight.png') })

  // stopping ends the dogfight and the match
  await rest(r, 'DELETE', '/scenarios/dogfight')
  await until(async () => !(await rest<Dogfight>(r, 'GET', '/scenarios/dogfight')).running, 'dogfight stopped', 60000)
  await until(async () => !(await rest<Arena>(r, 'GET', '/arena')).match?.running, 'match ended', 30000)
})
