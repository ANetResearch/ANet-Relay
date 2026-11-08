// Dev fixture stand-in for the Lab (06 §2, §3, §7; WP-3): programs (the six real built-in examples read from the
// backend's examples folder, plus two of "mine" with a revision history by the operator and an agent), a small
// validator and test runner (not the backend's: PARAMS / PARAM_RANGES are read from the text, imports other than
// math and missing step() are reported), deployment on the fixture's aircraft with a moving state name and log,
// live parameters, the challenge list, evaluation and duel jobs that finish after a few seconds, the ladder, and
// synthetic two-aircraft traces with rounds for the stage replay.
import type { Program, ProgramRevision, ProgramRunView, ProgramSummary, ProgramTestResult, Vehicle } from '@/api'

type Obj = Record<string, unknown>

const sources = import.meta.glob<string>('../../../backend/relay/programs/examples/*.py', { query: '?raw', import: 'default', eager: true })
const metas = import.meta.glob<string>('../../../backend/relay/programs/examples/*.json', { query: '?raw', import: 'default', eager: true })
const BUILTIN_IDS = ['hover_trim', 'liftoff', 'patrol', 'hunter_easy', 'hunter_medium', 'hunter_hard']

const MY_V1 = `PARAMS = {"target_alt": 1.0, "hover": 0.55, "kp": 0.3, "kd": 0.15}


def init(ctx):
    ctx.state = "climb"


def step(obs, ctx):
    p = ctx.params
    error = p["target_alt"] - obs.alt
    throttle = p["hover"] + p["kp"] * error - p["kd"] * obs.vz
    return Sticks(throttle=clamp(throttle, 0.0, 1.0))
`

const MY_V2 = `# Hover with a learned trim: the integrator finds the hover throttle on this airframe.
PARAMS = {"target_alt": 1.0, "hover": 0.55, "kp": 0.3, "kd": 0.15, "ki": 0.1}
PARAM_RANGES = {"target_alt": [0.3, 3.0], "hover": [0.2, 0.8]}


def init(ctx):
    ctx.mem["trim"] = ctx.params["hover"]
    ctx.state = "climb"


def step(obs, ctx):
    p = ctx.params
    if not obs.armed or obs.alt is None:
        ctx.state = "idle"
        return Sticks(throttle=0.0)
    error = p["target_alt"] - obs.alt
    if obs.airborne:
        ctx.mem["trim"] = clamp(ctx.mem["trim"] + p["ki"] * error * obs.dt, 0.3, 0.8)
    ctx.state = "hold" if abs(error) < 0.1 else "climb"
    throttle = ctx.mem["trim"] + p["kp"] * error - p["kd"] * obs.vz
    return Sticks(throttle=clamp(throttle, 0.0, 1.0))
`

const MY_V3 = MY_V2.replace('    ctx.state = "hold" if abs(error) < 0.1 else "climb"\n', '    ctx.state = "hold" if abs(error) < 0.1 else "climb"\n    if ctx.state == "hold":\n        ctx.log("trim", round(ctx.mem["trim"], 3))\n')

const DUELIST = `# Duelist: search, approach, fire, evade.
import math

PARAMS = {"target_alt": 1.5, "fire_dist": 6.0, "aim_deg": 5.0, "turn_gain": 0.012, "speed": 0.25, "evade_s": 0.8}


def init(ctx):
    ctx.mem["trim"] = 0.5
    ctx.state = "search"


def step(obs, ctx):
    p = ctx.params
    hold = ctx.mem["trim"] + 0.25 * (p["target_alt"] - (obs.alt or 0)) - 0.18 * (obs.vz or 0)
    enemies = [c for c in obs.contacts if c["team"] != "us"]
    if not enemies:
        ctx.state = "search"
        return Sticks(yaw=0.25, throttle=clamp(hold, 0, 1))
    c = min(enemies, key=lambda e: e["dist"])
    yaw = clamp(p["turn_gain"] * c["bearing"], -1, 1)
    if c["dist"] > p["fire_dist"]:
        ctx.state = "approach"
        return Sticks(pitch=-p["speed"], yaw=yaw, throttle=clamp(hold, 0, 1))
    ctx.state = "fire" if abs(c["bearing"]) < p["aim_deg"] else "aim"
    return Sticks(yaw=yaw, throttle=clamp(hold, 0, 1), fire=ctx.state == "fire")
`

const CHALLENGES = [
  ['liftoff', 'Liftoff', '起飞', 'Payload puts the hover throttle near 0.62; no position source.', '载重使悬停油门约 0.62；无定位源。', 'Off the ground within 5 s and 1.0 ± 0.2 m held for 3 s.', '5 s 内离地，并在 1.0 ± 0.2 m 保持 3 s。', { score: 100, passed: true }],
  ['hover', 'Hover', '悬停', 'Forward centre of gravity and a level calibration error; barometer, no position source.', '重心前偏，并有水平校准误差；有气压计，无定位源。', 'Within 20 s: horizontal drift at most 0.8 m, height 1.0 ± 0.25 m (judged on truth).', '20 s 内水平漂移不超过 0.8 m，高度 1.0 ± 0.25 m（按真值判分）。', { score: 64, passed: false }],
  ['heading', 'Heading', '定向', 'Unequal motor efficiency makes the yaw drift.', '电机效率不一致导致偏航漂移。', 'Heading error at most 10° for 15 s.', '15 s 内航向误差不超过 10°。', null],
  ['square', 'Square', '方形航线', 'UWB-style position at 10 Hz, σ 5 cm.', 'UWB 式定位，10 Hz，σ 5 cm。', 'Pass the four corners in order (radius 0.4 m); faster scores higher.', '按序经过四角（半径 0.4 m），用时越短分越高。', null],
  ['rings', 'Rings', '穿环', 'The ring course of 03 §8.', '现有穿环航线（03 §8）。', 'Fly through every ring; timed.', '全部过环，计时。', null],
  ['pursuit', 'Pursuit', '追踪', 'A target aircraft flies a scripted path.', '一架目标机按脚本飞行。', 'Share of 60 s spent within 2 m of the target.', '60 s 内保持在目标 2 m 内的时间比例。', null],
  ['duel_easy', 'Duel (easy)', '决斗（简单）', 'The opponent is the easy built-in hunter.', '对手为简单档内置机器人。', 'Win: shoot the opponent down and survive; a draw counts remaining hit points.', '胜利：击落对手且自身存活；平局按剩余生命值。', { score: 100, passed: true }],
  ['duel_medium', 'Duel (medium)', '决斗（中等）', 'The opponent is the medium built-in hunter.', '对手为中等档内置机器人。', 'Win: shoot the opponent down and survive; a draw counts remaining hit points.', '胜利：击落对手且自身存活；平局按剩余生命值。', { score: 40, passed: false }],
  ['duel_hard', 'Duel (hard)', '决斗（困难）', 'The opponent is the hard built-in hunter.', '对手为困难档内置机器人。', 'Win: shoot the opponent down and survive; a draw counts remaining hit points.', '胜利：击落对手且自身存活；平局按剩余生命值。', null],
] as const

// ---------------------------------------------------------------- validation and tests (fixture-grade)

function braceBlock(text: string, name: string): string | null {
  const m = new RegExp(`^${name}\\s*=\\s*\\{`, 'm').exec(text)
  if (!m) return null
  let depth = 0
  for (let i = m.index + m[0].length - 1; i < text.length; i++) {
    if (text[i] === '{') depth++
    else if (text[i] === '}' && --depth === 0) return text.slice(m.index + m[0].length - 1, i + 1)
  }
  return null
}

function pyDict(block: string | null): Obj {
  if (!block) return {}
  try {
    return JSON.parse(block.replace(/\bTrue\b/g, 'true').replace(/\bFalse\b/g, 'false').replace(/'/g, '"').replace(/,\s*}/g, '}')) as Obj
  } catch {
    return {}
  }
}

export function fixtureValidate(source: string) {
  const errors: { line: number; col: number; end_line: number; end_col: number; code: string; message_en: string; message_zh: string }[] = []
  source.split('\n').forEach((line, i) => {
    const imp = /^\s*(?:import|from)\s+([A-Za-z_][\w.]*)/.exec(line)
    if (imp && imp[1] !== 'math') {
      const col = line.indexOf(imp[1]) + 1
      errors.push({ line: i + 1, col, end_line: i + 1, end_col: col + imp[1].length, code: 'import', message_en: `Only math can be imported, not ${imp[1]}.`, message_zh: `只能导入 math，不能导入 ${imp[1]}。` })
    }
    const under = /\b(__\w+|_[a-z]\w*)\b/.exec(line)
    if (under && !line.trimStart().startsWith('#')) {
      errors.push({ line: i + 1, col: under.index + 1, end_line: i + 1, end_col: under.index + 1 + under[1].length, code: 'underscore', message_en: `Names starting with _ are not allowed: ${under[1]}.`, message_zh: `不允许以 _ 开头的名称：${under[1]}。` })
    }
    if (/^\s*class\s/.test(line)) errors.push({ line: i + 1, col: 1, end_line: i + 1, end_col: line.length + 1, code: 'class', message_en: 'Class definitions are not allowed.', message_zh: '不允许定义类。' })
  })
  const hasStep = /^def step\(/m.test(source)
  if (!hasStep) errors.push({ line: 1, col: 1, end_line: 1, end_col: 2, code: 'no_step', message_en: 'The program must define step(obs, ctx).', message_zh: '程序必须定义 step(obs, ctx)。' })
  const ranges = pyDict(braceBlock(source, 'PARAM_RANGES')) as Record<string, number[]>
  return { ok: errors.length === 0, errors, params: pyDict(braceBlock(source, 'PARAMS')), param_ranges: ranges, has_init: /^def init\(/m.test(source), has_step: hasStep }
}

function fixtureTests(source: string, tests: Obj[]): { results: ProgramTestResult[]; passed: number; total: number } {
  const params = pyDict(braceBlock(source, 'PARAMS'))
  const target = typeof params.target_alt === 'number' ? params.target_alt : 1
  const results = tests.map((test, i): ProgramTestResult => {
    const obs = (test.obs as Obj) ?? {}
    const alt = typeof obs.alt === 'number' ? obs.alt : 1
    const armed = obs.armed !== false
    const throttle = armed ? Math.min(1, Math.max(0, 0.5 + 0.35 * (target - alt))) : 0
    const state = !armed ? 'disarmed' : Math.abs(target - alt) < 0.1 ? 'hold' : 'climb'
    const out = { roll: 0, pitch: 0, yaw: 0, throttle: Math.round(throttle * 1000) / 1000, fire: false }
    const failures: Obj[] = []
    for (const [k, want] of Object.entries((test.expect as Obj) ?? {})) {
      const actual = k === 'state' ? state : (out as Obj)[k]
      const ok = Array.isArray(want) ? typeof actual === 'number' && actual >= (want[0] as number) - 1e-9 && actual <= (want[1] as number) + 1e-9 : actual === want
      if (!ok) failures.push({ field: k, expected: want, actual })
    }
    return { name: typeof test.name === 'string' ? test.name : `test ${i + 1}`, passed: failures.length === 0, out, state, error: null, failures }
  })
  return { results, passed: results.filter((r) => r.passed).length, total: results.length }
}

// ---------------------------------------------------------------- traces

function seeded(seed: number): () => number {
  let s = seed >>> 0 || 1
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0
    return s / 2 ** 32
  }
}

/** A synthetic duel or judged run: aircraft circling the arena centre, the first firing at the second. */
export function fixtureTrace(id: string, names: string[], seed: number, title: string): Obj {
  const rnd = seeded(seed)
  const hz = 10
  const duration = 18
  const n = duration * hz + 1
  const R = 3.2
  const aircraft = names.map((name, k) => {
    const phase = (k / names.length) * Math.PI * 2 + rnd() * 0.4
    const w = (k % 2 ? -1 : 1) * (0.32 + 0.06 * rnd())
    const cols = { t: [] as number[], x: [] as number[], y: [] as number[], z: [] as number[], roll: [] as number[], pitch: [] as number[], yaw: [] as number[], throttle: [] as number[], hp: [] as number[], state: [] as string[], armed: [] as boolean[] }
    for (let i = 0; i < n; i++) {
      const t = i / hz
      const a = phase + w * t
      const r = R - (k === 0 ? Math.min(1.2, t * 0.08) : 0)
      cols.t.push(t)
      cols.x.push(Math.round(r * Math.cos(a) * 1000) / 1000)
      cols.y.push(Math.round(r * Math.sin(a) * 1000) / 1000)
      cols.z.push(Math.round((t < 2 ? 0.75 * t : 1.5 + 0.25 * Math.sin(t * 0.9 + k)) * 1000) / 1000)
      const heading = ((90 - ((a + Math.sign(w) * Math.PI / 2) * 180) / Math.PI) % 360 + 360) % 360
      cols.yaw.push(Math.round(heading * 10) / 10)
      cols.roll.push(Math.round(Math.sign(w) * 9 * 10) / 10)
      cols.pitch.push(-6)
      cols.throttle.push(0.58 + 0.03 * Math.sin(t))
      cols.hp.push(5)
      cols.state.push(t < 2 ? 'climb' : t < 6 ? 'search' : (Math.floor(t) % 4 === 0 ? 'evade' : t % 3 < 1.5 ? 'fire' : 'approach'))
      cols.armed.push(true)
    }
    return { id: `p${k + 1}`, name, program: name, rev: 1, team: `t${k + 1}`, samples: cols }
  })
  // rounds from the first aircraft at the second, every 1.6 s after 6 s; every other one hits
  const shots: Obj[] = []
  const events: Obj[] = []
  if (aircraft.length > 1) {
    const a = aircraft[0].samples
    const b = aircraft[1].samples
    let hp = 5
    for (let t0 = 6, k = 0; t0 < duration - 1; t0 += 1.6, k++) {
      const i = Math.round(t0 * hz)
      const p = [a.x[i], a.y[i], a.z[i]]
      const q = [b.x[i], b.y[i], b.z[i]]
      const d = Math.hypot(q[0] - p[0], q[1] - p[1], q[2] - p[2]) || 1
      const v = [((q[0] - p[0]) / d) * 35, ((q[1] - p[1]) / d) * 35, ((q[2] - p[2]) / d) * 35 + 0.5]
      const hit = k % 2 === 0
      const tEnd = hit ? Math.round((t0 + d / 35) * 1000) / 1000 : null
      shots.push({ id: `s${k + 1}`, owner: 'p1', t0, p, v, t_end: tEnd })
      if (hit && tEnd !== null) {
        hp--
        events.push({ t: tEnd, kind: 'hit', vehicle: 'p2', by: 'p1' })
        for (let j = Math.ceil(tEnd * hz); j < n; j++) b.hp[j] = hp
        if (hp === 0) {
          events.push({ t: tEnd, kind: 'down', vehicle: 'p2', by: 'p1' })
          break
        }
      }
    }
  }
  events.push({ t: duration, kind: 'finish', vehicle: null, by: null })
  return { id, kind: names.length > 1 ? 'duel' : 'challenge', title, hz, duration_s: duration, arena: { radius: 6, floor: 0.3, ceiling: 4 }, aircraft, shots, events }
}

// ---------------------------------------------------------------- the stand-in

export interface LabFixtureHost {
  now(): number
  vehicle(id: string): Vehicle | undefined
  mutate(id: string, fn: (v: Vehicle) => Vehicle): void
  emit(kind: string, vehicle: string | null, data?: Obj): void
}

interface Job {
  id: string
  kind: 'challenge' | 'duel'
  started: number
  total: number
  result: Obj
}

export function startLabFixture(host: LabFixtureHost): { handle(method: string, path: string, body: unknown): unknown; stop(): void } {
  const programs = new Map<string, Program & { revisions: ProgramRevision[] }>()
  const t0 = Date.now() / 1000
  for (const id of BUILTIN_IDS) {
    const src = Object.entries(sources).find(([k]) => k.endsWith(`/${id}.py`))?.[1]
    const metaRaw = Object.entries(metas).find(([k]) => k.endsWith(`/${id}.json`))?.[1]
    if (!src) continue
    const meta = metaRaw ? (JSON.parse(metaRaw) as Obj) : {}
    const params = fixtureValidate(src).params
    const tests = (meta.tests as Obj[]) ?? []
    programs.set(id, {
      id, name: String(meta.name ?? id), name_zh: String(meta.name_zh ?? meta.name ?? id), description: String(meta.description ?? ''), description_zh: String(meta.description_zh ?? ''),
      builtin: true, rev: 1, author: 'relay', created: 0, updated: 0, test_count: tests.length, source: src, params, tests,
      revisions: [{ rev: 1, source: src, params, author: 'relay', note: 'built-in', t: 0 }],
    })
  }
  const myTests = [
    { name: 'Below target climbs', obs: { alt: 0.5, vz: 0 }, expect: { throttle: [0.55, 1.0], state: 'climb' } },
    { name: 'At target holds', obs: { alt: 1.0, vz: 0 }, expect: { throttle: [0.45, 0.6], state: 'hold' } },
    { name: 'Disarmed idles', obs: { armed: false, airborne: false }, expect: { throttle: [0, 0], state: 'idle' } },
  ]
  programs.set('trim_hover', {
    id: 'trim_hover', name: 'Trim hover', name_zh: 'Trim hover', description: '', description_zh: '', builtin: false, rev: 3, author: 'harness-a', created: t0 - 86400 * 2, updated: t0 - 600,
    test_count: myTests.length, source: MY_V3, params: { target_alt: 1.2 }, tests: myTests,
    revisions: [
      { rev: 1, source: MY_V1, params: {}, author: 'operator', note: 'Created in the Lab', t: t0 - 86400 * 2 },
      { rev: 2, source: MY_V2, params: {}, author: 'harness-a', note: 'Integrator for the hover throttle', t: t0 - 3600 },
      { rev: 3, source: MY_V3, params: { target_alt: 1.2 }, author: 'harness-a', note: 'Log the learned trim while holding', t: t0 - 600 },
    ],
  })
  programs.set('duelist', {
    id: 'duelist', name: 'Duelist', name_zh: 'Duelist', description: '', description_zh: '', builtin: false, rev: 1, author: 'operator', created: t0 - 7200, updated: t0 - 7200,
    test_count: 0, source: DUELIST, params: {}, tests: [], revisions: [{ rev: 1, source: DUELIST, params: {}, author: 'operator', note: 'Created in the Lab', t: t0 - 7200 }],
  })

  const summary = (p: Program): ProgramSummary => {
    const { source: _s, params: _p, tests: _t, ...rest } = p as Program & { revisions?: unknown }
    delete (rest as { revisions?: unknown }).revisions
    return rest
  }
  const view = (p: Program & { revisions: ProgramRevision[] }): Program => {
    const { revisions: _r, ...rest } = p
    return rest
  }
  const best: Record<string, { score: number; passed: boolean } | null> = Object.fromEntries(CHALLENGES.map((c) => [c[0], c[7] ? { ...c[7] } : null]))
  const submissions: Record<string, number> = Object.fromEntries(CHALLENGES.map((c) => [c[0], c[7] ? 3 : 0]))
  const jobs = new Map<string, Job>()
  const traces = new Map<string, Obj>()
  let ladder: Obj[] = [
    { program: 'hunter_hard', name: 'Hunter (hard)', elo: 1268, delta: null, played: 12, wins: 9, losses: 2, draws: 1 },
    { program: 'duelist', name: 'Duelist', elo: 1214, delta: null, played: 12, wins: 7, losses: 4, draws: 1 },
    { program: 'hunter_medium', name: 'Hunter (medium)', elo: 1190, delta: null, played: 12, wins: 5, losses: 6, draws: 1 },
    { program: 'hunter_easy', name: 'Hunter (easy)', elo: 1128, delta: null, played: 12, wins: 1, losses: 10, draws: 1 },
  ]
  let jobSeq = 0
  const logs = new Map<string, { seq: number; t: number; level: 'log' | 'error' | 'event'; text: string }[]>()
  const pushLog = (vid: string, level: 'log' | 'error' | 'event', text: string) => {
    const ring = logs.get(vid) ?? []
    const seq = (ring.at(-1)?.seq ?? 0) + 1
    ring.push({ seq, t: host.now() - (host.vehicle(vid)?.program?.started_at ?? host.now()), level, text })
    logs.set(vid, ring.slice(-500))
    host.mutate(vid, (v) => (v.program ? { ...v, program: { ...v.program, log_seq: seq } } : v))
  }

  // a running program moves through its states and logs once a second
  let k = 0
  const ticker = setInterval(() => {
    k++
    for (const vid of logs.keys()) {
      const v = host.vehicle(vid)
      const run = v?.program
      if (!v || !run || run.status !== 'running') continue
      const states = run.id.startsWith('hunter') || run.id === 'duelist' ? ['search', 'approach', 'fire', 'evade'] : ['climb', 'hold', 'hold', 'hold']
      const state = states[Math.floor(k / 3) % states.length]
      host.mutate(vid, (x) => (x.program ? { ...x, program: { ...x.program, state, fps: 49 + (k % 3), frames: x.program.frames + 50, overruns: x.program.overruns + (k % 17 === 0 ? 1 : 0) } } : x))
      pushLog(vid, 'log', state === 'hold' ? `trim ${(0.58 + 0.01 * Math.sin(k)).toFixed(3)}` : `state ${state}`)
    }
  }, 1000)

  const jobView = (j: Job) => {
    const p = Math.min(1, (host.now() - j.started) / j.total)
    const state = p >= 1 ? 'done' : p < 0.1 ? 'queued' : 'running'
    return { id: j.id, kind: j.kind, state, progress: Math.round(p * 100) / 100, error: null, result: state === 'done' ? j.result : null }
  }

  const handle = (method: string, path: string, body: unknown): unknown => {
    const b = (body ?? {}) as Obj
    if (path === '/programs' && method === 'GET') return { programs: [...programs.values()].map(summary) }
    if (path === '/programs' && method === 'POST') {
      const from = typeof b.copy_of === 'string' ? programs.get(b.copy_of) : undefined
      let id = String(b.name ?? from?.name ?? 'program').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') || 'program'
      while (programs.has(id)) id = `${id}_2`
      const source = typeof b.source === 'string' ? b.source : (from?.source ?? '')
      const params = (b.params as Obj) ?? {}
      const tests = (b.tests as Obj[]) ?? from?.tests ?? []
      const name = String(b.name ?? from?.name ?? id)
      const p = { id, name, name_zh: name, description: '', description_zh: '', builtin: false, rev: 1, author: 'operator', created: host.now(), updated: host.now(), test_count: tests.length, source, params, tests, revisions: [{ rev: 1, source, params, author: 'operator', note: String(b.note ?? (from ? `Copy of ${from.name}` : '')), t: host.now() }] }
      programs.set(id, p)
      return { program: view(p), validation: fixtureValidate(source) }
    }
    const prog = path.match(/^\/programs\/([^/]+)(?:\/(\w+))?$/)
    if (prog) {
      const p = programs.get(decodeURIComponent(prog[1]))
      if (!p) return { ok: false }
      const sub = prog[2]
      if (!sub && method === 'GET') return { program: view(p) }
      if (!sub && method === 'DELETE') {
        programs.delete(p.id)
        return { id: p.id, deleted: true }
      }
      if (!sub && method === 'PUT') {
        const source = typeof b.source === 'string' ? b.source : p.source
        const params = (b.params as Obj | undefined) ?? (p.params as Obj)
        const changed = source !== p.source || JSON.stringify(params) !== JSON.stringify(p.params)
        const next = { ...p, source, params, name: typeof b.name === 'string' ? b.name : p.name, name_zh: typeof b.name === 'string' ? b.name : p.name_zh, tests: (b.tests as Obj[]) ?? p.tests, updated: host.now() }
        next.test_count = next.tests.length
        if (changed) {
          next.rev = p.rev + 1
          next.author = 'operator'
          next.revisions = [...p.revisions, { rev: next.rev, source, params, author: 'operator', note: String(b.note ?? ''), t: host.now() }].slice(-50)
        }
        programs.set(p.id, next)
        return { program: view(next), validation: fixtureValidate(source) }
      }
      if (sub === 'revisions') return { id: p.id, revisions: p.revisions }
      if (sub === 'validate') return { id: p.id, ...fixtureValidate(typeof b.source === 'string' ? b.source : p.source) }
      if (sub === 'test') return { program: p.id, ...fixtureTests(typeof b.source === 'string' ? b.source : p.source, (b.tests as Obj[]) ?? (p.tests as Obj[])) }
    }
    const veh = path.match(/^\/vehicles\/([^/]+)\/program(?:\/(\w+))?/)
    if (veh) {
      const vid = decodeURIComponent(veh[1])
      const sub = veh[2]
      const v = host.vehicle(vid)
      if (!v) return { ok: false }
      if (!sub && method === 'POST') {
        const p = programs.get(String(b.program))
        if (!p) return { ok: false }
        const run: ProgramRunView = {
          id: p.id, name: p.name, rev: typeof b.rev === 'number' ? b.rev : p.rev, state: 'climb', status: 'running', reason: null, mode: b.mode === 'copilot' ? 'copilot' : 'pilot',
          fps: 50, frames: 0, faults: 0, overruns: 0, last_error: null, params: { ...fixtureValidate(p.source).params, ...(p.params as Obj), ...((b.params as Obj) ?? {}) }, seed: 7, rates_ok: true, sm: null, log_seq: 0,
          owner: { kind: 'operator', name: 'operator' }, started_at: host.now(),
        }
        host.mutate(vid, (x) => ({ ...x, controller: 'program', program: run }))
        logs.set(vid, [])
        pushLog(vid, 'event', `deployed ${p.id} rev ${run.rev}`)
        host.emit('program_started', vid, { name: p.name, program: p.id, rev: run.rev })
        return { id: vid, program: run }
      }
      if (sub === 'stop' && v.program) {
        host.mutate(vid, (x) => ({ ...x, controller: x.controller === 'program' ? 'none' : x.controller, program: x.program ? { ...x.program, status: 'stopped', reason: 'stopped', fps: 0 } : null }))
        pushLog(vid, 'event', 'stopped')
        host.emit('program_stopped', vid, { name: v.program.name, reason: 'stopped' })
        return { id: vid, program: host.vehicle(vid)?.program }
      }
      if (sub === 'resume' && v.program) {
        host.mutate(vid, (x) => ({ ...x, controller: 'program', program: x.program ? { ...x.program, status: 'running', reason: null } : null }))
        host.emit('program_resumed', vid, { name: v.program.name })
        return { id: vid, program: host.vehicle(vid)?.program }
      }
      if (sub === 'params' && v.program) {
        host.mutate(vid, (x) => (x.program ? { ...x, program: { ...x.program, params: { ...x.program.params, ...b } } } : x))
        pushLog(vid, 'event', `params ${JSON.stringify(b)}`)
        return { id: vid, params: host.vehicle(vid)?.program?.params }
      }
      if (sub === 'log') {
        const after = Number(new URLSearchParams(path.split('?')[1] ?? '').get('after') ?? 0)
        const ring = logs.get(vid) ?? []
        return { id: vid, program: v.program?.id ?? '', lines: ring.filter((l) => l.seq > after), last_seq: ring.at(-1)?.seq ?? 0 }
      }
      if (sub === 'allow') {
        host.mutate(vid, (x) => ({ ...x, programs_allowed: b.allow === true && b.confirm === 'PROGRAM' }))
        return { id: vid, programs_allowed: host.vehicle(vid)?.programs_allowed }
      }
      return { ok: true }
    }
    if (path === '/challenges') {
      return {
        challenges: CHALLENGES.map(([id, name, name_zh, description, description_zh, pass_en, pass_zh]) => ({
          id, name, name_zh, description, description_zh, pass_en, pass_zh, best: best[id] ? { ...best[id], program: 'trim_hover', rev: 3, t: t0 - 900 } : null, submissions: submissions[id],
        })),
      }
    }
    const submit = path.match(/^\/challenges\/([^/]+)\/submit$/)
    if (submit && method === 'POST') {
      const cid = decodeURIComponent(submit[1])
      const p = programs.get(String(b.program))
      const id = `job-${++jobSeq}`
      const score = cid === 'hover' ? 78 : cid.startsWith('duel') ? 100 : 91
      const passed = score >= 80
      const seeds = [1, 2, 3, 1001, 1002].map((seed, i) => {
        const traceId = `tr-${id}-${seed}`
        traces.set(traceId, fixtureTrace(traceId, cid.startsWith('duel') ? [p?.name ?? 'program', `Hunter (${cid.slice(5)})`] : [p?.name ?? 'program'], seed, `${cid} · seed ${seed}`))
        return { seed, hidden: i >= 3, score: Math.max(0, score - i * 4 + (i % 2) * 6), passed: score - i * 4 + (i % 2) * 6 >= 80, trace_id: traceId }
      })
      const metrics = cid === 'hover' ? { drift_m: 0.61, alt_error_m: 0.12, time_in_band_s: 18.4, faults: 0 } : { time_s: 7.8, faults: 0, hits: cid.startsWith('duel') ? 5 : null }
      jobs.set(id, { id, kind: 'challenge', started: host.now(), total: 3, result: { challenge: cid, program: p?.id ?? null, rev: p?.rev ?? null, score, passed, metrics, seeds, trace_id: seeds[0].trace_id } })
      submissions[cid] = (submissions[cid] ?? 0) + 1
      setTimeout(() => {
        if (!best[cid] || best[cid]!.score < score) best[cid] = { score, passed }
      }, 3000)
      return { job: { id, kind: 'challenge', state: 'queued', progress: 0, error: null, result: null } }
    }
    const job = path.match(/^\/jobs\/([^/]+)$/)
    if (job) {
      const j = jobs.get(decodeURIComponent(job[1]))
      return j ? jobView(j) : { id: job[1], state: 'failed', progress: 0, error: { code: 'not_found', message_en: 'No such job.', message_zh: '没有该任务。' } }
    }
    if (path === '/duels' && method === 'POST') {
      const ids = ((b.programs as string[]) ?? []).filter((x) => programs.has(x))
      const rounds = Math.max(1, Math.min(20, Number(b.rounds) || 1))
      const seed = Number(b.seed) || 0
      const id = `job-${++jobSeq}`
      const rnd = seeded(seed + 17)
      const wins: Record<string, number> = Object.fromEntries(ids.map((x) => [x, 0]))
      const out = Array.from({ length: rounds }, (_, r) => {
        const pair = [ids[r % ids.length], ids[(r + 1) % ids.length]]
        const winner = rnd() < 0.15 ? null : pair[rnd() < 0.55 ? 0 : 1]
        if (winner) wins[winner]++
        const traceId = `tr-${id}-${r + 1}`
        traces.set(traceId, fixtureTrace(traceId, pair.map((x) => programs.get(x)?.name ?? x), seed + r, `duel · round ${r + 1}`))
        return {
          round: r + 1, seed: seed + r, winner, trace_id: traceId,
          entries: pair.map((x) => ({ program: x, name: programs.get(x)?.name ?? x, hits: Math.floor(rnd() * 6), hp: x === winner ? 1 + Math.floor(rnd() * 4) : 0, alive_s: x === winner || !winner ? 60 : 20 + rnd() * 30, survived: x === winner || !winner })),
        }
      })
      // each program's games: the rounds it played; Elo moves by 16 per win above or below an even share
      const played: Record<string, number> = Object.fromEntries(ids.map((x) => [x, out.filter((r) => r.entries.some((e) => e.program === x)).length]))
      const draws: Record<string, number> = Object.fromEntries(ids.map((x) => [x, out.filter((r) => !r.winner && r.entries.some((e) => e.program === x)).length]))
      const rows = ids.map((x) => {
        const old = ladder.find((l) => l.program === x)
        const delta = (wins[x] - (played[x] - draws[x]) / 2) * 16
        const before = old ?? { elo: 1150, played: 0, wins: 0, losses: 0, draws: 0 }
        return {
          program: x, name: programs.get(x)?.name ?? x, elo: Math.round((before.elo as number) + delta), delta: Math.round(delta),
          played: (before.played as number) + played[x], wins: (before.wins as number) + wins[x], losses: (before.losses as number) + played[x] - wins[x] - draws[x], draws: (before.draws as number) + draws[x],
        }
      })
      const merged: Obj[] = [...ladder.filter((l) => !ids.includes(String(l.program))).map((l): Obj => ({ ...l, delta: null })), ...rows].sort((a, c) => (c.elo as number) - (a.elo as number))
      jobs.set(id, { id, kind: 'duel', started: host.now(), total: 4, result: { rounds: out, ladder: merged } })
      setTimeout(() => {
        ladder = merged
      }, 4000)
      return { job: { id, kind: 'duel', state: 'queued', progress: 0, error: null, result: null } }
    }
    if (path === '/ladder') return { ladder }
    const tr = path.match(/^\/traces\/([^/]+)$/)
    if (tr) {
      const id = decodeURIComponent(tr[1])
      return traces.get(id) ?? fixtureTrace(id, ['Duelist', 'Hunter (medium)'], 3, id)
    }
    return undefined
  }

  return { handle, stop: () => clearInterval(ticker) }
}
