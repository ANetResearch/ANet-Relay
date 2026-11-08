// Voxel tracers and hit bursts (06 §11), shared by the LAN arena (ArenaLayer) and the Lab trace replay
// (TraceLayer). A round is a short chain of cubes along its path: a white-hot head inside an additive glow, then a
// tail from r400 (ours, or the first aircraft of a replay) or r500 (anyone else) down to r700, shrinking. A hit
// throws a white core that swells and fades, then red and white shards that fly out, fall and shrink; a shoot-down
// adds a larger burst of grey and red. Bursts are a pure function of their age (burstVoxels), so a replay can be
// scrubbed back and forth. Every cube has a floor on its size in screen pixels, so rounds and hits stay readable
// from far away; close up they keep their true size. Red here is the weapon semantics of the arena, the fourth
// exception to the single-red rule (06 §11, store/red.ts).
import * as THREE from 'three'
import { token } from '@/stage/tokens'

export type BurstKind = 'hit' | 'down' | 'still'

/** One burst: scene position, start time in the caller's clock (seconds) and a seed for its shard directions. */
export interface Burst {
  p: readonly [number, number, number]
  t0: number
  seed: number
  kind: BurstKind
}

export type VoxelTone = 'core' | 'white' | 'red' | 'deep' | 'grey'

export interface BurstVoxel {
  x: number
  y: number
  z: number
  /** edge length, scene metres (before the screen-size floor) */
  size: number
  tone: VoxelTone
  /** 0..1, used as brightness on the additive core */
  glow: number
}

export const HIT_SHARDS = 28
export const DOWN_SHARDS = 48
const GRAVITY = 9.81 * 0.6
const DRAG = 3
const TAIL_CUBES = 6
const HEAD_SIZE = 0.03
const TAIL_SIZE = 0.014
const HEAD_MIN_PX = 4
const TAIL_MIN_PX = 3
const SHARD_MIN_PX = 4
const CORE_MIN_PX = 10
const HALO = 2.6
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

/** How long a burst lasts, in seconds. */
export function burstLife(kind: BurstKind, lives: { burst: number; down: number }): number {
  return kind === 'down' ? lives.down : lives.burst
}

/**
 * The cubes of burst `b` at `age` seconds (empty once it is over). `coreS` is the span of the white core, `lifeS`
 * the life of this burst. Pure: the same burst at the same age always gives the same cubes.
 */
export function burstVoxels(b: Burst, age: number, coreS: number, lifeS: number, out: BurstVoxel[] = []): BurstVoxel[] {
  out.length = 0
  if (!(age >= 0) || age > lifeS || lifeS <= 0) return out
  const [px, py, pz] = b.p
  if (b.kind === 'still') {
    out.push({ x: px, y: py, z: pz, size: 0.06, tone: 'core', glow: 1 })
    return out
  }
  if (coreS > 0 && age < coreS) {
    const k = age / coreS
    out.push({ x: px, y: py, z: pz, size: 0.05 + (b.kind === 'down' ? 0.4 : 0.25) * k, tone: 'core', glow: 1 - k })
  }
  const r = rng(b.seed)
  const n = b.kind === 'down' ? DOWN_SHARDS : HIT_SHARDS
  const fall = 0.5 * GRAVITY * age * age
  const travel = (1 - Math.exp(-DRAG * age)) / DRAG
  const left = 1 - age / lifeS
  for (let i = 0; i < n; i++) {
    // a uniform direction on the sphere, lifted a little so the burst opens upwards
    const u = r() * 2 - 1
    const a = r() * Math.PI * 2
    const s = Math.sqrt(1 - u * u)
    const dx = s * Math.cos(a)
    const dy = u * 0.8 + 0.25
    const dz = s * Math.sin(a)
    const speed = b.kind === 'down' ? 1.5 + r() * 2.5 : 2.5 + r() * 3.5
    const size0 = b.kind === 'down' ? 0.04 + r() * 0.04 : 0.025 + r() * 0.03
    const pick = r()
    const tone: VoxelTone = b.kind === 'down' ? (pick < 0.55 ? 'grey' : pick < 0.85 ? 'red' : 'white') : pick < 0.35 ? 'white' : pick < 0.8 ? 'red' : 'deep'
    const y = Math.max(size0 / 2, py + dy * speed * travel - fall)
    out.push({ x: px + dx * speed * travel, y, z: pz + dz * speed * travel, size: size0 * Math.pow(Math.max(0, left), 0.7), tone, glow: 0 })
  }
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
    this.tones = { core: this.head, white: this.head, red: token('r400').color, deep: token('r600').color, grey: token('g400').color }
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
    this.halo(p, HEAD_SIZE * HALO, HEAD_MIN_PX * HALO, base, 0.55)
    this.cube(p, HEAD_SIZE, HEAD_MIN_PX, this.head)
    const cubes = tailS > 0 ? TAIL_CUBES : 1
    for (let k = 1; k < cubes; k++) {
      const f = k / (cubes - 1)
      tmpP.copy(p).addScaledVector(v, -tailS * f)
      tmpC.copy(base).lerp(this.tailEnd, f)
      this.cube(tmpP, HEAD_SIZE + (TAIL_SIZE - HEAD_SIZE) * f, HEAD_MIN_PX + (TAIL_MIN_PX - HEAD_MIN_PX) * f, tmpC)
    }
  }

  /** Burst `b` at `age` seconds. */
  burst(b: Burst, age: number, coreS: number, lifeS: number): void {
    // shards shrink to nothing, so their pixel floor shrinks with them
    const shardPx = SHARD_MIN_PX * Math.pow(Math.max(0, 1 - age / lifeS), 0.7)
    for (const c of burstVoxels(b, age, coreS, lifeS, scratch)) {
      tmpP.set(c.x, c.y, c.z)
      if (c.tone === 'core') {
        this.halo(tmpP, c.size * 1.8, CORE_MIN_PX * 1.8, this.mine, c.glow)
        this.cube(tmpP, c.size, CORE_MIN_PX, this.head)
      } else if (c.size > 0) {
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
