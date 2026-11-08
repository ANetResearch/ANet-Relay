import { describe, expect, it } from 'vitest'
import { defaultForm, defaultRoles, dogfightBody, validateDogfight, withAircraft } from './dogfight'
import { accuracyText, BOARD_TABS, matchLeft, metricCells, normBoard, normDogfight, normRow, ranks, sortRows, type LeaderboardRow } from './leaderboard'
import { liveRows } from './LeaderboardPanel'
import { ARENA_DICTS } from './copy'

const row = (key: string, over: Partial<LeaderboardRow> = {}): LeaderboardRow => ({
  key, aircraft: key, callsign: '', local: false, controller: { kind: 'none', name: '' },
  downs: 0, hits: 0, shots: 0, accuracy: null, rams: 0, deaths: 0, crashes: 0, alive_s: 0, best_alive_s: 0, damage: 0, ...over,
})

describe('leaderboard model (07 §4, §5)', () => {
  it('reads rows tolerantly: clamps counts, derives accuracy, caps peer text and drops rows without a key', () => {
    const b = normBoard({ by: 'rams', match: { id: 'm1', started_at: 10, ends_at: 190, running: true }, rows: [
      { key: 'Bravo / SIM-1', aircraft: 'SIM-1', callsign: 'Bravo', controller: { kind: 'agent', name: 'x'.repeat(80) }, hits: 3, shots: 12, downs: -2 },
      { key: 'SIM-2', local: true, controller: { kind: 'robot' }, hits: 1, shots: 4, accuracy: 25, alive_s: 30, best_alive_s: 12 },
      { aircraft: 'no key' },
    ] }, 'downs')
    expect(b.by).toBe('rams')
    expect(b.match).toEqual({ id: 'm1', started_at: 10, ends_at: 190, running: true })
    expect(b.rows).toHaveLength(2)
    expect(b.rows[0]).toMatchObject({ downs: 0, accuracy: 0.25, controller: { kind: 'agent' } })
    expect(b.rows[0].controller.name).toHaveLength(32)
    // a percentage is read as a fraction; best never trails the current run; unknown controller kinds read as none
    expect(b.rows[1]).toMatchObject({ accuracy: 0.25, best_alive_s: 30, controller: { kind: 'none', name: '' }, local: true })
    expect(normRow({ key: 'a', shots: 0, accuracy: 0.9 })?.accuracy).toBeNull()
    expect(normBoard(null, 'survival')).toEqual({ by: 'survival', match: null, rows: [] })
  })

  it('orders each tab by its metric, breaks ties stably and shares places', () => {
    const rows = [
      row('c', { downs: 2, hits: 5, deaths: 1, rams: 1, crashes: 2, best_alive_s: 40, alive_s: 10, shots: 10, accuracy: 0.5 }),
      row('a', { downs: 2, hits: 5, deaths: 1, rams: 0, best_alive_s: 90, shots: 0 }),
      row('b', { downs: 3, hits: 4, deaths: 4, rams: 1, crashes: 1, best_alive_s: 40, alive_s: 20, shots: 4, accuracy: 0.75 }),
    ]
    expect(sortRows(rows, 'downs').map((r) => r.key)).toEqual(['b', 'a', 'c'])
    expect(sortRows(rows, 'survival').map((r) => r.key)).toEqual(['a', 'b', 'c'])
    expect(sortRows(rows, 'accuracy').map((r) => r.key)).toEqual(['b', 'c', 'a'])
    expect(sortRows(rows, 'rams').map((r) => r.key)).toEqual(['b', 'c', 'a'])
    expect(rows.map((r) => r.key)).toEqual(['c', 'a', 'b'])
    const sorted = sortRows(rows, 'downs')
    expect(ranks(sorted, 'downs')).toEqual([1, 2, 2])
  })

  it('formats the primary and secondary metric of each tab', () => {
    const r = row('x', { downs: 3, deaths: 1, hits: 7, shots: 13, accuracy: 7 / 13, rams: 2, crashes: 1, best_alive_s: 125.4, alive_s: 0 })
    expect(metricCells(r, 'downs')).toEqual(['3', '1'])
    expect(metricCells(r, 'survival')).toEqual(['02:05', null])
    expect(metricCells({ ...r, alive_s: 61 }, 'survival')).toEqual(['02:05', '01:01'])
    expect(metricCells(r, 'accuracy')).toEqual(['54 %', '7 / 13'])
    expect(accuracyText(row('y'))).toBeNull()
    expect(metricCells(r, 'rams')).toEqual(['2', '1'])
  })

  it('counts the match down on the backend clock and runs survival on between refetches', () => {
    const m = { id: 'm', started_at: 0, ends_at: 180, running: true }
    expect(matchLeft(m, 100)).toBe(80)
    expect(matchLeft(m, 200)).toBe(0)
    expect(matchLeft({ ...m, running: false }, 100)).toBeNull()
    expect(matchLeft(null, 100)).toBeNull()
    const live = liveRows([row('a', { alive_s: 10, best_alive_s: 12 }), row('b', { alive_s: 0, best_alive_s: 30 })], 5)
    expect(live.map((r) => [r.alive_s, r.best_alive_s])).toEqual([[15, 15], [0, 30]])
  })

  it('reads the dogfight scenario status', () => {
    const s = normDogfight({ running: true, started_at: 5, ends_at: 185, config: { aircraft: 6 }, aircraft: [{ vid: 'sim-1', role: 'rammer', program: 'rammer', team: 2 }, { role: 'x' }] })
    expect(s).toEqual({ running: true, started_at: 5, ends_at: 185, config: { aircraft: 6 }, aircraft: [{ vid: 'sim-1', role: 'rammer', program: 'rammer', team: '2' }] })
    expect(normDogfight(undefined).running).toBe(false)
  })

  it('labels every tab and column in both languages', () => {
    for (const lang of ['en', 'zh'] as const) {
      for (const k of BOARD_TABS) expect(ARENA_DICTS[lang][`board.tab.${k}`]).toBeTruthy()
    }
    expect([ARENA_DICTS.en['board.tab.downs'], ARENA_DICTS.en['board.tab.survival'], ARENA_DICTS.en['board.tab.accuracy'], ARENA_DICTS.en['board.tab.rams']]).toEqual(['Downs', 'Survival', 'Accuracy', 'Rams'])
    expect([ARENA_DICTS.zh['board.tab.downs'], ARENA_DICTS.zh['board.tab.survival'], ARENA_DICTS.zh['board.tab.accuracy'], ARENA_DICTS.zh['board.tab.rams']]).toEqual(['击落', '存活', '命中率', '撞毁'])
    expect([ARENA_DICTS.en['board.endMatch'], ARENA_DICTS.zh['board.endMatch'], ARENA_DICTS.en['arena.menu.start'], ARENA_DICTS.zh['arena.menu.start']]).toEqual(['End match', '结束对局', 'Start dogfight…', '开始狗斗…'])
  })
})

describe('dogfight form (07 §3)', () => {
  it('splits the aircraft two gunners to one rammer and keeps a hand-set split that still adds up', () => {
    expect(defaultRoles(6)).toEqual({ gunner: 4, rammer: 2 })
    expect(defaultRoles(2)).toEqual({ gunner: 2, rammer: 0 })
    expect(defaultRoles(12)).toEqual({ gunner: 8, rammer: 4 })
    const f = withAircraft(defaultForm(), '9')
    expect([f.gunners, f.rammers]).toEqual(['6', '3'])
    const hand = { ...defaultForm(), aircraft: '6', gunners: '1', rammers: '5' }
    expect(withAircraft(hand, '6')).toMatchObject({ gunners: '1', rammers: '5' })
  })

  it('checks the request limits and builds the body', () => {
    expect(validateDogfight(defaultForm())).toEqual({})
    const bad = validateDogfight({ ...defaultForm(), aircraft: '13', gunners: '4', rammers: '1', duration: '20', radius: '2', seed: '-1' })
    expect(Object.keys(bad).sort()).toEqual(['aircraft', 'arena', 'duration', 'roles', 'seed'])
    expect(bad.roles).toEqual({ key: 'df.err.roles', params: { n: 13 } })
    expect(dogfightBody({ ...defaultForm(), teams: '2' })).toEqual({
      aircraft: 6, teams: 2, roles: { gunner: 4, rammer: 2 }, programs: { gunner: 'hunter_medium', rammer: 'rammer' },
      arena: { radius: 8, ceiling: 4 }, airframe: 'typical', hover_assist: false, duration_s: 180, respawn: true, seed: 7,
    })
  })
})
