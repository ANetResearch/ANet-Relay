// Voxel tracers, hit bursts, explosions, collision sparks and ground dust (06 §11, 07 §5), shared by the LAN arena
// (ArenaLayer) and the Lab trace replay (TraceLayer). A round is a short chain of cubes along its path: a white-hot
// head inside an additive glow, then a tail from r400 (ours, or the first aircraft of a replay) or r500 (anyone
// else) down to r700, shrinking. A hit throws a white core that swells and fades, then red and white shards that fly
// out, fall and shrink; a shoot-down adds a larger burst of grey and red. An explosion (shoot-down, ram, hard crash)
// swells a white-hot core for its first 120 ms, then throws 60 voxels outwards that cool white -> r400 -> r600 ->
// g500 under gravity and drag, settle on the ground and shrink away, with a ring of grey dust along the ground when
// it happens below 1 m. A collision that crashes nobody throws 16 white and red sparks and a brief white shock cube;
// an ordinary crash on the ground raises a dust ring and a few grey chunks. Every effect is a pure function of its
// age (burstVoxels), so a replay can be scrubbed back and forth. Every cube has a floor on its size in screen pixels,
// so rounds and effects stay readable from far away; close up they keep their true size. Red here is the weapon and
// collision semantics of the arena, the exception to the single-red rule (06 §11, 07 §5, store/red.ts).
import * as THREE from 'three'
import { token } from '@/stage/tokens'

/**
 * hit: a round struck; down: the v0.4 shoot-down debris; explosion: shoot-down, ram or hard crash (07 §5);
 * spark: a collision that crashed nobody; dust: an ordinary crash on the ground; still: the reduced-motion marker of
 * a hit (any other kind with `still` set shows its own marker for its own life).
 */
export type BurstKind = 'hit' | 'down' | 'still' | 'explosion' | 'spark' | 'dust'

/** One burst: scene position, start time in the caller's clock (seconds) and a seed for its shard directions. */
export interface Burst {
  p: readonly [number, number, number]
  t0: number
  seed: number
  kind: BurstKind
  /** reduced motion: one still white cube at `p` for the life of the kind */
  still?: boolean
}

/** core: additive white core; heat: a cooling voxel (white -> r400 -> r600 -> g500 by `heat`); dust: g500 / g600 */
export type VoxelTone = 'core' | 'white' | 'red' | 'deep' | 'grey' | 'heat' | 'dust' | 'dustDark'

export interface BurstVoxel {
  x: number
  y: number
  z: number
  /** edge length, scene metres (before the screen-size floor) */
  size: number
  tone: VoxelTone
  /** 0..1, used as brightness on the additive core */
  glow: number
  /** 0 (white-hot) .. 1 (cooled to g500), tone 'heat' only */
  heat?: number
}

export const HIT_SHARDS = 28
export const DOWN_SHARDS = 48
export const EXPLOSION_SHARDS = 60
export const EXPLOSION_DUST = 28
export const SPARKS = 16
export const DUST_RING = 24
export const DUST_CHUNKS = 8
/** below this height (m) an explosion also raises a dust ring */
export const DUST_HEIGHT = 1
const GRAVITY = 9.81 * 0.6
const DRAG = 3
const G_FULL = 9.81
const EXPLOSION_DRAG = 2.2
const SPARK_DRAG = 7
const TAIL_CUBES = 7
const HEAD_SIZE = 0.03
const TAIL_SIZE = 0.014
const HEAD_MIN_PX = 4
const TAIL_MIN_PX = 3
const SHARD_MIN_PX = 4
const CORE_MIN_PX = 10
const EXPLOSION_CORE_MIN_PX = 18
const DUST_MIN_PX = 3
const HALO = 3.4
const MAX_CUBES = 4096

/** Deterministic 0..1 sequence of one burst (mulberry32). */
function rng(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** A stable seed from a string (a shot id, a mark sequence). */
export function seedOf(s: string | number): number {
  const str = String(s)
  let h = 2166136261
  for (let i = 0; i < str.length; i++) h = Math.imul(h ^ str.charCodeAt(i), 16777619)
  return h >>> 0
}

/** Lives of the effect kinds in seconds: hit and spark bursts, the v0.4 shoot-down debris, explosions and dust. */
export interface FxLives {
  burst: number
  down: number
  /** --duration-arena-explosion; falls back to twice `down` */
  explosion?: number
}

/** How long a burst lasts, in seconds. */
export function burstLife(kind: BurstKind, lives: FxLives): number {
  if (kind === 'down') return lives.down
  if (kind === 'explosion' || kind === 'dust') return lives.explosion ?? 2 * lives.down
  return lives.burst
}

/** Edge length of the reduced-motion marker of a kind (scene metres). */
export function stillSize(kind: BurstKind): number {
  return kind === 'explosion' || kind === 'down' ? 0.16 : kind === 'dust' ? 0.12 : 0.06
}

/** A uniform direction on the sphere from two draws, lifted by `lift` so the burst opens upwards. */
function direction(r: () => number, lift: number, flat = 0.8): [number, number, number] {
  const u = r() * 2 - 1
  const a = r() * Math.PI * 2
  const s = Math.sqrt(1 - u * u)
  return [s * Math.cos(a), u * flat + lift, s * Math.sin(a)]
}

/** Distance travelled after `t` seconds at initial speed 1 under linear drag `k` (exact: (1 - e^-kt) / k). */
const dragTravel = (t: number, k: number) => (1 - Math.exp(-k * t)) / k

/** Height change after `t` seconds from vertical speed `vy` under gravity `g` and linear drag `k` (exact). */
const dragRise = (t: number, vy: number, g: number, k: number) => (vy + g / k) * dragTravel(t, k) - (g * t) / k

function hitVoxels(b: Burst, age: number, coreS: number, lifeS: number, out: BurstVoxel[]): void {
  const [px, py, pz] = b.p
  if (coreS > 0 && age < coreS) {
    const k = age / coreS
    out.push({ x: px, y: py, z: pz, size: 0.05 + (b.kind === 'down' ? 0.4 : 0.25) * k, tone: 'core', glow: 1 - k })
  }
  const r = rng(b.seed)
  const n = b.kind === 'down' ? DOWN_SHARDS : HIT_SHARDS
  const fall = 0.5 * GRAVITY * age * age
  const travel = dragTravel(age, DRAG)
  const left = 1 - age / lifeS
  for (let i = 0; i < n; i++) {
    const [dx, dy, dz] = direction(r, 0.25)
    const speed = b.kind === 'down' ? 1.5 + r() * 2.5 : 2.5 + r() * 3.5
    const size0 = b.kind === 'down' ? 0.04 + r() * 0.04 : 0.025 + r() * 0.03
    const pick = r()
    const tone: VoxelTone = b.kind === 'down' ? (pick < 0.55 ? 'grey' : pick < 0.85 ? 'red' : 'white') : pick < 0.35 ? 'white' : pick < 0.8 ? 'red' : 'deep'
    const y = Math.max(size0 / 2, py + dy * speed * travel - fall)
    out.push({ x: px + dx * speed * travel, y, z: pz + dz * speed * travel, size: size0 * Math.pow(Math.max(0, left), 0.7), tone, glow: 0 })
  }
}

/** A ring of `n` dust voxels spreading along the ground from (px, pz), starting `delay` s in, reaching `reach` m. */
function dustRing(r: () => number, px: number, pz: number, age: number, lifeS: number, n: number, reach: number, delay: number, out: BurstVoxel[]): void {
  const t = age - delay
  if (t < 0) return
  const spread = 0.12 + reach * (1 - Math.exp(-3.5 * t))
  const fade = Math.pow(Math.max(0, 1 - age / lifeS), 0.6)
  for (let i = 0; i < n; i++) {
    const a = ((i + r() * 0.6) / n) * Math.PI * 2
    const rr = spread * (0.85 + r() * 0.3)
    const size = (0.05 + r() * 0.04) * fade
    // a puff rises a little and sinks back as it spreads
    const lift = 0.06 * (1 - Math.exp(-6 * t)) * Math.exp(-1.5 * t)
    out.push({ x: px + Math.cos(a) * rr, y: size / 2 + lift, z: pz + Math.sin(a) * rr, size, tone: i % 3 === 0 ? 'dustDark' : 'dust', glow: 0 })
  }
}

function explosionVoxels(b: Burst, age: number, coreS: number, lifeS: number, out: BurstVoxel[]): void {
  const [px, py, pz] = b.p
  const core = coreS > 0 ? coreS : 0.12
  // 0..core: the white-hot core swells; it then fades over another 60 % of that span
  if (age < core * 1.6) {
    const k = Math.min(1, age / core)
    const swell = 1 - (1 - k) * (1 - k)
    const glow = age < core ? 1 : 1 - (age - core) / (0.6 * core)
    out.push({ x: px, y: py, z: pz, size: 0.1 + 0.55 * swell, tone: 'core', glow })
  }
  const r = rng(b.seed)
  // shards leave from the core's edge from half-way through the swell
  const t = age - core * 0.5
  const settle = 0.65 * lifeS
  const fade = Math.pow(Math.max(0, 1 - Math.max(0, age - settle) / (lifeS - settle)), 0.8)
  for (let i = 0; i < EXPLOSION_SHARDS; i++) {
    const [dx, dy, dz] = direction(r, 0.3, 0.9)
    const speed = 2.5 + r() * 4
    const size0 = 0.045 + r() * 0.05
    const jitter = r() * 0.18
    if (t < 0) continue
    const h = dragTravel(t, EXPLOSION_DRAG) * speed
    // a settled voxel rests on the ground as it shrinks away
    const size = size0 * fade
    const y = Math.max(size / 2, py + 0.08 * dy + dragRise(t, dy * speed, G_FULL, EXPLOSION_DRAG))
    const heat = Math.min(1, Math.max(0, t / (0.55 * lifeS) + jitter))
    out.push({ x: px + dx * (0.08 + h), y, z: pz + dz * (0.08 + h), size, tone: 'heat', glow: 0, heat })
  }
  if (py < DUST_HEIGHT) dustRing(r, px, pz, age, lifeS, EXPLOSION_DUST, 1.3 * (1 - py / (2 * DUST_HEIGHT)), 0.04 + py * 0.12, out)
}

function sparkVoxels(b: Burst, age: number, coreS: number, lifeS: number, out: BurstVoxel[]): void {
  const [px, py, pz] = b.p
  // the white shock cube of the contact, over the core span
  const shock = coreS > 0 ? coreS : 0.08
  if (age < shock) {
    const k = age / shock
    out.push({ x: px, y: py, z: pz, size: 0.04 + 0.16 * k, tone: 'core', glow: 1 - k })
  }
  const r = rng(b.seed)
  const left = Math.max(0, 1 - age / lifeS)
  const travel = dragTravel(age, SPARK_DRAG)
  for (let i = 0; i < SPARKS; i++) {
    const [dx, dy, dz] = direction(r, 0.15)
    const speed = 3 + r() * 5
    const size0 = 0.02 + r() * 0.02
    const pick = r()
    const tone: VoxelTone = pick < 0.4 ? 'white' : pick < 0.85 ? 'red' : 'deep'
    const y = Math.max(size0 / 2, py + dragRise(age, dy * speed, GRAVITY, SPARK_DRAG))
    out.push({ x: px + dx * speed * travel, y, z: pz + dz * speed * travel, size: size0 * Math.pow(left, 0.7), tone, glow: 0 })
  }
}

function dustVoxels(b: Burst, age: number, lifeS: number, out: BurstVoxel[]): void {
  const [px, py, pz] = b.p
  const r = rng(b.seed)
  dustRing(r, px, pz, age, lifeS, DUST_RING, 1.0, 0, out)
  const settle = 0.65 * lifeS
  const fade = Math.pow(Math.max(0, 1 - Math.max(0, age - settle) / (lifeS - settle)), 0.8)
  const base = Math.max(0, py)
  for (let i = 0; i < DUST_CHUNKS; i++) {
    const [dx, dy, dz] = direction(r, 0.9, 0.3)
    const speed = 1 + r() * 1.2
    const size0 = 0.04 + r() * 0.03
    const size = size0 * fade
    const y = Math.max(size / 2, base + dragRise(age, dy * speed, G_FULL, 2))
    const h = dragTravel(age, 2) * speed
    out.push({ x: px + dx * h, y, z: pz + dz * h, size, tone: i % 2 ? 'grey' : 'dustDark', glow: 0 })
  }
}

/**
 * The cubes of burst `b` at `age` seconds (empty once it is over). `coreS` is the span of the white core (hit and
 * spark: --duration-micro; explosion: --duration-arena-explosion-core), `lifeS` the life of this burst. Pure: the
 * same burst at the same age always gives the same cubes.
 */
export function burstVoxels(b: Burst, age: number, coreS: number, lifeS: number, out: BurstVoxel[] = []): BurstVoxel[] {
  out.length = 0
  if (!(age >= 0) || age > lifeS || lifeS <= 0) return out
  const [px, py, pz] = b.p
  if (b.kind === 'still' || b.still) {
    out.push({ x: px, y: py, z: pz, size: stillSize(b.kind), tone: 'core', glow: 1 })
    return out
  }
  if (b.kind === 'explosion') explosionVoxels(b, age, coreS, lifeS, out)
  else if (b.kind === 'spark') sparkVoxels(b, age, coreS, lifeS, out)
  else if (b.kind === 'dust') dustVoxels(b, age, lifeS, out)
  else hitVoxels(b, age, coreS, lifeS, out)
  return out
}

/** Edge length in scene metres that shows as `minPx` pixels at `p` (perspective cameras; others keep `size`). */
export function floorSize(p: THREE.Vector3, size: number, cam: THREE.Camera, heightPx: number, minPx: number): number {
  const pc = cam as THREE.PerspectiveCamera
  if (!pc.isPerspectiveCamera || heightPx <= 0) return size
  const dist = cam.position.distanceTo(p)
  const perPx = (2 * dist * Math.tan(THREE.MathUtils.degToRad(pc.fov) / 2)) / heightPx
  return Math.max(size, minPx * perPx)
}

const tmpP = new THREE.Vector3()
const tmpM = new THREE.Matrix4()
const tmpQ = new THREE.Quaternion()
const tmpS = new THREE.Vector3()
const tmpC = new THREE.Color()
const scratch: BurstVoxel[] = []

/** Two instanced cube meshes (solid, and additive glow) filled once per frame between begin() and end(). */
export class VoxelFx {
  readonly objects: THREE.Object3D[]
  private readonly solid: THREE.InstancedMesh
  private readonly glow: THREE.InstancedMesh
  private readonly geo = new THREE.BoxGeometry(1, 1, 1)
  private ns = 0
  private ng = 0
  private cam: THREE.Camera | null = null
  private h = 0
  private readonly tones: Record<VoxelTone, THREE.Color>
  /** cooling stops of an explosion voxel: white, r400, r600, g500 */
  private readonly heatStops: THREE.Color[]
  private readonly mine = token('r400').color
  private readonly theirs = token('r500').color
  private readonly tailEnd = token('r700').color
  private readonly head = token('white').color

  constructor(capacity = MAX_CUBES) {
    this.solid = new THREE.InstancedMesh(this.geo, new THREE.MeshBasicMaterial({ toneMapped: false }), capacity)
    this.glow = new THREE.InstancedMesh(
      this.geo,
      new THREE.MeshBasicMaterial({ toneMapped: false, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }),
      Math.ceil(capacity / 4),
    )
    for (const m of [this.solid, this.glow]) {
      m.instanceMatrix.setUsage(THREE.DynamicDrawUsage)
      m.setColorAt(0, this.head)
      m.count = 0
      m.frustumCulled = false
      m.raycast = () => {}
    }
    const r400 = token('r400').color
    const r600 = token('r600').color
    const g500 = token('g500').color
    this.tones = { core: this.head, white: this.head, red: r400, deep: r600, grey: token('g400').color, heat: r400, dust: g500, dustDark: token('g600').color }
    this.heatStops = [this.head, r400, r600, g500]
    this.objects = [this.solid, this.glow]
  }

  begin(cam: THREE.Camera, heightPx: number): void {
    this.cam = cam
    this.h = heightPx
    this.ns = 0
    this.ng = 0
  }

  private put(mesh: THREE.InstancedMesh, i: number, p: THREE.Vector3, size: number, color: THREE.Color): void {
    tmpS.setScalar(size)
    tmpM.compose(p, tmpQ.identity(), tmpS)
    mesh.setMatrixAt(i, tmpM)
    mesh.setColorAt(i, color)
  }

  private cube(p: THREE.Vector3, size: number, minPx: number, color: THREE.Color): void {
    if (this.ns >= this.solid.instanceMatrix.count) return
    const s = this.cam ? floorSize(p, size, this.cam, this.h, minPx) : size
    this.put(this.solid, this.ns++, p, s, color)
  }

  private halo(p: THREE.Vector3, size: number, minPx: number, color: THREE.Color, k: number): void {
    if (this.ng >= this.glow.instanceMatrix.count) return
    const s = this.cam ? floorSize(p, size, this.cam, this.h, minPx) : size
    this.put(this.glow, this.ng++, p, s, tmpC.copy(color).multiplyScalar(k))
  }

  /** One round at scene position `p` with scene velocity `v` (m/s); `tailS` is the span of flight time shown. */
  tracer(p: THREE.Vector3, v: THREE.Vector3, mine: boolean, tailS: number): void {
    const base = mine ? this.mine : this.theirs
    this.halo(p, HEAD_SIZE * HALO, HEAD_MIN_PX * HALO, base, 0.7)
    this.cube(p, HEAD_SIZE, HEAD_MIN_PX, this.head)
    const cubes = tailS > 0 ? TAIL_CUBES : 1
    for (let k = 1; k < cubes; k++) {
      const f = k / (cubes - 1)
      tmpP.copy(p).addScaledVector(v, -tailS * f)
      tmpC.copy(base).lerp(this.tailEnd, f)
      this.cube(tmpP, HEAD_SIZE + (TAIL_SIZE - HEAD_SIZE) * f, HEAD_MIN_PX + (TAIL_MIN_PX - HEAD_MIN_PX) * f, tmpC)
    }
  }

  /** Colour of a cooling voxel at `heat` (0 white-hot .. 1 g500), into tmpC. */
  private heatColor(heat: number): THREE.Color {
    const f = Math.min(1, Math.max(0, heat)) * (this.heatStops.length - 1)
    const i = Math.min(this.heatStops.length - 2, Math.floor(f))
    return tmpC.copy(this.heatStops[i]).lerp(this.heatStops[i + 1], f - i)
  }

  /** Burst `b` at `age` seconds. */
  burst(b: Burst, age: number, coreS: number, lifeS: number): void {
    // shards shrink to nothing, so their pixel floor shrinks with them
    const left = Math.max(0, 1 - age / lifeS)
    const shardPx = SHARD_MIN_PX * Math.pow(left, 0.7)
    const big = b.kind === 'explosion' || b.kind === 'dust'
    const corePx = b.kind === 'explosion' ? EXPLOSION_CORE_MIN_PX : CORE_MIN_PX
    for (const c of burstVoxels(b, age, coreS, lifeS, scratch)) {
      if (c.size <= 0) continue
      tmpP.set(c.x, c.y, c.z)
      if (c.tone === 'core') {
        this.halo(tmpP, c.size * (big ? 2.2 : 1.8), corePx * (big ? 2.2 : 1.8), this.mine, c.glow)
        this.cube(tmpP, c.size, corePx, this.head)
      } else if (c.tone === 'heat') {
        const heat = c.heat ?? 1
        const color = this.heatColor(heat)
        // hot voxels glow while they are still near white
        if (heat < 0.35) this.halo(tmpP, c.size * 1.9, shardPx * 1.9, color, (0.35 - heat) * 1.6)
        this.cube(tmpP, c.size, big ? Math.max(2, shardPx) : shardPx, this.heatColor(heat))
      } else if (c.tone === 'dust' || c.tone === 'dustDark') {
        this.cube(tmpP, c.size, DUST_MIN_PX * Math.pow(left, 0.6), this.tones[c.tone])
      } else {
        this.cube(tmpP, c.size, shardPx, this.tones[c.tone])
      }
    }
  }

  end(): void {
    this.solid.count = this.ns
    this.glow.count = this.ng
    for (const m of [this.solid, this.glow]) {
      m.instanceMatrix.needsUpdate = true
      if (m.instanceColor) m.instanceColor.needsUpdate = true
    }
  }

  dispose(): void {
    this.geo.dispose()
    ;(this.solid.material as THREE.Material).dispose()
    ;(this.glow.material as THREE.Material).dispose()
  }
}
