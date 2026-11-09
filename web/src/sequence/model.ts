// Stick timelines (.ars, docs/design/10-v053 §2): the document, sampling and the block edits of the timeline dock.
// A track is a row of blocks; each key starts one block that lasts until the next key: `hold` keeps the stick at v,
// `ramp` moves it linearly to the next key's value. Mirrors backend/relay/sequence.py (the backend validates again).

export const ARS_FORMAT = 'ars'
export const ARS_VERSION = 1
export const ARS_SUFFIX = '.ars'
export const TRACKS = ['throttle', 'pitch', 'roll', 'yaw'] as const
export type TrackName = (typeof TRACKS)[number]
export type Ease = 'hold' | 'ramp'
export type SeqMode = 'stabilize' | 'althold' | 'poshold'
export type SeqEnd = 'disarm' | 'hold'
export const MAX_DURATION_S = 600
export const MIN_BLOCK_S = 0.1
export const TRIM_LIMITS: Record<TrackName, number> = { roll: 0.3, pitch: 0.3, yaw: 0.3, throttle: 0.2 }

export interface ArsKey {
  t: number
  v: number
  ease: Ease
  label?: string
}

export interface ArsDoc {
  format: 'ars'
  version: 1
  name: string
  mode: SeqMode
  duration_s: number
  trim: Record<TrackName, number>
  tracks: Partial<Record<TrackName, ArsKey[]>>
  end: SeqEnd
  source?: Record<string, unknown>
}

export interface Block {
  track: TrackName
  index: number
  start: number
  end: number
  v: number
  ease: Ease
  /** ramp blocks: the value they arrive at (the next key's) */
  to: number
  label?: string
}

const r3 = (x: number) => Math.round(x * 1000) / 1000
const r4 = (x: number) => Math.round(x * 10000) / 10000
export const range = (track: TrackName): [number, number] => (track === 'throttle' ? [0, 1] : [-1, 1])
export const clampTo = (track: TrackName, v: number) => {
  const [lo, hi] = range(track)
  return Math.min(hi, Math.max(lo, v))
}
export const zeroTrim = (): Record<TrackName, number> => ({ roll: 0, pitch: 0, yaw: 0, throttle: 0 })

export function restValue(track: TrackName, mode: SeqMode): number {
  return track === 'throttle' ? (mode === 'stabilize' ? 0 : 0.5) : 0
}

/** Takeoff, hover, land in altitude hold: the starting point of the dock. Every block is named. */
export function templateHover(hold = 5): ArsDoc {
  const climbEnd = 3
  const hoverEnd = climbEnd + hold
  return {
    format: 'ars',
    version: 1,
    name: 'Takeoff, hover, land',
    mode: 'althold',
    duration_s: hoverEnd + 11,
    trim: zeroTrim(),
    tracks: {
      throttle: [
        { t: 0, v: 0.5, ease: 'hold', label: 'spool' },
        { t: 0.5, v: 0.75, ease: 'hold', label: 'climb' },
        { t: climbEnd, v: 0.5, ease: 'hold', label: 'hover' },
        { t: hoverEnd, v: 0.3, ease: 'hold', label: 'descend' },
        { t: hoverEnd + 9, v: 0, ease: 'hold', label: 'settle' },
      ],
      pitch: [{ t: 0, v: 0, ease: 'hold' }],
      roll: [{ t: 0, v: 0, ease: 'hold' }],
      yaw: [{ t: 0, v: 0, ease: 'hold' }],
    },
    end: 'disarm',
  }
}

export function valueAt(keys: ArsKey[], t: number): number {
  if (t <= keys[0].t) return keys[0].v
  const last = keys[keys.length - 1]
  if (t >= last.t) return last.v
  let lo = 0
  let hi = keys.length - 1
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (keys[mid].t <= t) lo = mid
    else hi = mid
  }
  const a = keys[lo]
  const b = keys[hi]
  if (a.ease === 'hold' || b.t <= a.t) return a.v
  return a.v + ((b.v - a.v) * (t - a.t)) / (b.t - a.t)
}

/** The stick a track sends at t, trim included. */
export function sampleTrack(doc: ArsDoc, track: TrackName, t: number, trim = doc.trim): number {
  const keys = doc.tracks[track]
  const v = keys && keys.length ? valueAt(keys, t) : restValue(track, doc.mode)
  return clampTo(track, v + (trim[track] ?? 0))
}

export function blocks(doc: ArsDoc, track: TrackName): Block[] {
  const keys = doc.tracks[track] ?? []
  return keys.map((k, i) => {
    const next = keys[i + 1]
    return {
      track,
      index: i,
      start: k.t,
      end: next ? next.t : doc.duration_s,
      v: k.v,
      ease: k.ease,
      to: k.ease === 'ramp' && next ? next.v : k.v,
      label: k.label,
    }
  })
}

// ---------------------------------------------------------------- edits (all return a new document)

function withKeys(doc: ArsDoc, track: TrackName, keys: ArsKey[]): ArsDoc {
  const last = keys.length ? keys[keys.length - 1].t : 0
  return { ...doc, tracks: { ...doc.tracks, [track]: keys }, duration_s: r3(Math.max(doc.duration_s, last + MIN_BLOCK_S)) }
}

function keysOf(doc: ArsDoc, track: TrackName): ArsKey[] {
  const keys = doc.tracks[track]
  return keys && keys.length ? keys.map((k) => ({ ...k })) : [{ t: 0, v: restValue(track, doc.mode), ease: 'hold' }]
}

export function setValue(doc: ArsDoc, track: TrackName, index: number, v: number): ArsDoc {
  const keys = keysOf(doc, track)
  if (!keys[index]) return doc
  keys[index].v = r4(clampTo(track, v))
  return withKeys(doc, track, keys)
}

export function setEase(doc: ArsDoc, track: TrackName, index: number, ease: Ease): ArsDoc {
  const keys = keysOf(doc, track)
  if (!keys[index]) return doc
  keys[index].ease = ease
  return withKeys(doc, track, keys)
}

export function setLabel(doc: ArsDoc, track: TrackName, index: number, label: string): ArsDoc {
  const keys = keysOf(doc, track)
  if (!keys[index]) return doc
  const text = label.trim().slice(0, 40)
  if (text) keys[index].label = text
  else delete keys[index].label
  return withKeys(doc, track, keys)
}

/**
 * Change one block's length; the blocks after it move with it (ripple), so "hold the climb 0.3 s longer" is one
 * edit. The last block of a track stretches to the end of the sequence, which grows or shrinks with it.
 */
export function setDuration(doc: ArsDoc, track: TrackName, index: number, seconds: number): ArsDoc {
  const keys = keysOf(doc, track)
  const k = keys[index]
  if (!k) return doc
  const len = Math.max(MIN_BLOCK_S, seconds)
  const next = keys[index + 1]
  const old = (next ? next.t : doc.duration_s) - k.t
  // the keys after it move by the change; none may pass the 600 s limit
  const room = next ? MAX_DURATION_S - MIN_BLOCK_S - lastKey(doc, track) : Infinity
  const shift = r3(Math.min(len - old, room))
  for (let i = index + 1; i < keys.length; i++) keys[i].t = r3(keys[i].t + shift)
  const out = withKeys(doc, track, keys)
  const lastOther = Math.max(0, ...TRACKS.filter((n) => n !== track).map((n) => lastKey(out, n)))
  const end = next ? doc.duration_s + shift : k.t + len
  return { ...out, duration_s: r3(Math.min(MAX_DURATION_S, Math.max(end, lastOther + MIN_BLOCK_S, lastKey(out, track) + MIN_BLOCK_S))) }
}

/** Move the boundary between block index-1 and index (drag the left edge): nothing else moves. */
export function moveStart(doc: ArsDoc, track: TrackName, index: number, t: number): ArsDoc {
  const keys = keysOf(doc, track)
  if (index <= 0 || !keys[index]) return doc
  const lo = keys[index - 1].t + MIN_BLOCK_S
  const hi = (keys[index + 1] ? keys[index + 1].t : doc.duration_s) - MIN_BLOCK_S
  keys[index].t = r3(Math.min(hi, Math.max(lo, t)))
  return withKeys(doc, track, keys)
}

/** Split the block under t into two equal-valued blocks (a new key at t). */
export function splitAt(doc: ArsDoc, track: TrackName, t: number): { doc: ArsDoc; index: number } {
  const keys = keysOf(doc, track)
  const at = r3(t)
  let i = keys.length - 1
  while (i > 0 && keys[i].t > at) i--
  const k = keys[i]
  const next = keys[i + 1]
  const end = next ? next.t : doc.duration_s
  if (at - k.t < MIN_BLOCK_S || end - at < MIN_BLOCK_S) return { doc, index: i }
  const v = r4(valueAt(keys, at))
  keys.splice(i + 1, 0, { t: at, v, ease: k.ease })
  return { doc: withKeys(doc, track, keys), index: i + 1 }
}

/** Remove a block: the block before it takes its time (the first block takes the next one's value). */
export function removeBlock(doc: ArsDoc, track: TrackName, index: number): ArsDoc {
  const keys = keysOf(doc, track)
  if (keys.length <= 1 || !keys[index]) return doc
  if (index === 0) {
    keys[1].t = 0
    keys.shift()
  } else keys.splice(index, 1)
  return withKeys(doc, track, keys)
}

/** Insert a block of `seconds` after block `index`, holding its value; the rest of the track moves later. */
export function insertAfter(doc: ArsDoc, track: TrackName, index: number, seconds: number): { doc: ArsDoc; index: number } {
  const keys = keysOf(doc, track)
  const k = keys[index]
  if (!k) return { doc, index }
  const next = keys[index + 1]
  const at = next ? next.t : doc.duration_s
  const len = Math.max(MIN_BLOCK_S, seconds)
  if (doc.duration_s + len > MAX_DURATION_S) return { doc, index }
  for (let j = index + 1; j < keys.length; j++) keys[j].t = r3(keys[j].t + len)
  keys.splice(index + 1, 0, { t: r3(at), v: k.v, ease: 'hold' })
  const out = withKeys(doc, track, keys)
  return { doc: { ...out, duration_s: r3(Math.min(MAX_DURATION_S, doc.duration_s + len)) }, index: index + 1 }
}

/**
 * Both sticks at one moment (the stick pads): each given axis gets a block starting at t with that value (the block
 * under t is split there first, or reused when it already starts at t).
 */
export function setSticksAt(doc: ArsDoc, t: number, values: Partial<Record<TrackName, number>>): ArsDoc {
  let out = doc
  const at = r3(Math.max(0, Math.min(t, doc.duration_s - MIN_BLOCK_S)))
  for (const track of TRACKS) {
    const v = values[track]
    if (v === undefined) continue
    const keys = keysOf(out, track)
    let i = keys.findIndex((k) => Math.abs(k.t - at) < 1e-6)
    if (i < 0) {
      const r = splitAt(withKeys(out, track, keys), track, at)
      out = r.doc
      i = (out.tracks[track] ?? []).findIndex((k) => Math.abs(k.t - at) < 1e-6)
      if (i < 0) i = blockAt(out, track, at) // too close to a block edge to split: change that block
    } else out = withKeys(out, track, keys)
    out = setValue(out, track, i, v)
  }
  return out
}

/** Every moment at which some stick changes (a key on any track), in time order. */
export function keyTimes(doc: ArsDoc): number[] {
  const set = new Set<number>()
  for (const track of TRACKS) for (const k of doc.tracks[track] ?? []) set.add(k.t)
  return [...set].sort((a, b) => a - b)
}

/** All four sticks at t, without trim (what the pads show). */
export function sticksAt(doc: ArsDoc, t: number): Record<TrackName, number> {
  const z = zeroTrim()
  return { throttle: sampleTrack(doc, 'throttle', t, z), pitch: sampleTrack(doc, 'pitch', t, z), roll: sampleTrack(doc, 'roll', t, z), yaw: sampleTrack(doc, 'yaw', t, z) }
}

export function setDocDuration(doc: ArsDoc, seconds: number): ArsDoc {
  const last = Math.max(0, ...TRACKS.map((n) => lastKey(doc, n)))
  return { ...doc, duration_s: r3(Math.min(MAX_DURATION_S, Math.max(last + MIN_BLOCK_S, seconds))) }
}

export function setTrim(doc: ArsDoc, track: TrackName, v: number): ArsDoc {
  const lim = TRIM_LIMITS[track]
  return { ...doc, trim: { ...doc.trim, [track]: r4(Math.min(lim, Math.max(-lim, v))) } }
}

function lastKey(doc: ArsDoc, track: TrackName): number {
  const keys = doc.tracks[track]
  return keys && keys.length ? keys[keys.length - 1].t : 0
}

/** The block of `track` that covers t. */
export function blockAt(doc: ArsDoc, track: TrackName, t: number): number {
  const keys = doc.tracks[track] ?? []
  let i = keys.length - 1
  while (i > 0 && keys[i].t > t) i--
  return Math.max(0, i)
}

// ---------------------------------------------------------------- files

export class ArsError extends Error {
  constructor(
    readonly field: string,
    readonly reason: string,
  ) {
    super(`${field}: ${reason}`)
  }
}

const num = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x)

/** Read an .ars file's JSON; the shape checks the backend makes too (it checks again before flying). */
export function parseArs(text: string): ArsDoc {
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch {
    throw new ArsError('file', 'not JSON')
  }
  if (!raw || typeof raw !== 'object') throw new ArsError('file', 'not an object')
  const o = raw as Record<string, unknown>
  if (o.format !== ARS_FORMAT) throw new ArsError('format', `must be "${ARS_FORMAT}"`)
  if (o.version !== ARS_VERSION) throw new ArsError('version', `unsupported (this Relay reads version ${ARS_VERSION})`)
  const mode = (o.mode ?? 'althold') as SeqMode
  if (!['stabilize', 'althold', 'poshold'].includes(mode)) throw new ArsError('mode', 'must be stabilize, althold or poshold')
  const end = (o.end ?? 'disarm') as SeqEnd
  if (end !== 'disarm' && end !== 'hold') throw new ArsError('end', 'must be disarm or hold')
  const tracksRaw = o.tracks
  if (!tracksRaw || typeof tracksRaw !== 'object') throw new ArsError('tracks', 'missing')
  const tracks: ArsDoc['tracks'] = {}
  let last = 0
  for (const [name, keysRaw] of Object.entries(tracksRaw as Record<string, unknown>)) {
    if (!(TRACKS as readonly string[]).includes(name)) throw new ArsError(`tracks.${name}`, 'unknown track')
    if (!Array.isArray(keysRaw)) throw new ArsError(`tracks.${name}`, 'must be a list')
    const track = name as TrackName
    const [lo, hi] = range(track)
    let prev = -1
    const keys: ArsKey[] = keysRaw.map((k, i) => {
      const where = `tracks.${name}[${i}]`
      const kk = k as Record<string, unknown>
      if (!kk || !num(kk.t) || kk.t < 0 || kk.t > MAX_DURATION_S || kk.t < prev) throw new ArsError(`${where}.t`, 'bad time')
      if (!num(kk.v) || kk.v < lo || kk.v > hi) throw new ArsError(`${where}.v`, `must be within ${lo}..${hi}`)
      const ease = (kk.ease ?? 'hold') as Ease
      if (ease !== 'hold' && ease !== 'ramp') throw new ArsError(`${where}.ease`, 'must be hold or ramp')
      prev = kk.t
      return { t: kk.t, v: kk.v, ease, ...(typeof kk.label === 'string' ? { label: kk.label.slice(0, 40) } : {}) }
    })
    if (keys.length) {
      tracks[track] = keys
      last = Math.max(last, keys[keys.length - 1].t)
    }
  }
  if (!Object.keys(tracks).length) throw new ArsError('tracks', 'at least one track needs a key')
  const duration = o.duration_s === undefined ? last : o.duration_s
  if (!num(duration) || duration < last || duration <= 0 || duration > MAX_DURATION_S) throw new ArsError('duration_s', 'bad length')
  const trim = zeroTrim()
  if (o.trim && typeof o.trim === 'object') {
    for (const [name, v] of Object.entries(o.trim as Record<string, unknown>)) {
      if (!(name in trim) || !num(v) || Math.abs(v) > TRIM_LIMITS[name as TrackName]) throw new ArsError(`trim.${name}`, 'bad trim')
      trim[name as TrackName] = v
    }
  }
  return {
    format: 'ars',
    version: 1,
    name: typeof o.name === 'string' ? o.name.slice(0, 80) : '',
    mode,
    duration_s: duration,
    trim,
    tracks,
    end,
    ...(o.source && typeof o.source === 'object' ? { source: o.source as Record<string, unknown> } : {}),
  }
}

export function serialiseArs(doc: ArsDoc): string {
  return `${JSON.stringify(doc, null, 2)}\n`
}

export function fileName(doc: ArsDoc): string {
  const base = (doc.name || 'sequence').replace(/[^\p{L}\p{N}_.-]+/gu, '-').replace(/-+/g, '-').replace(/^-|-$/g, '').slice(0, 60) || 'sequence'
  return `${base}${ARS_SUFFIX}`
}
