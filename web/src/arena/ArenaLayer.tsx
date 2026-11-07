// LAN arena layer of the stage scene (05 §7.5), mounted next to <CourseLayer/>. Remote simulators fly the same
// airframe in g400 graphite with g200 arm lamps and a "callsign / SIM-N" label, smoothed in their own target table
// (never live.state, so they stay out of camera framing, the fleet panel and records). Bullets are one InstancedMesh
// (at most 512 thin tracer capsules, white for this terminal's rounds and g300 for others, toneMapped off) with a
// short tail; every frame lands on the shared 1/240 s integrator lattice (./ballistics). A hit on one of our
// aircraft flashes its shell, the only red of the arena (damage to our own armed aircraft: damageTone, single-red
// rule 2 in store/red.ts), and throws voxel debris; a shoot-down throws grey voxels. Software rendering keeps the
// bullets as points and drops the tail and the debris; reduced motion drops the flash and all debris, remote
// shoot-downs included (the fx queue itself refuses them).
// Hidden while Records replays a flight.
import * as React from 'react'
import * as THREE from 'three'
import { useFrame } from '@react-three/fiber'
import { Html, useGLTF } from '@react-three/drei'
import { useReplay } from '@/records/replayState'
import { backendNow } from '@/store/relay'
import { damageTone, type RedOwner } from '@/store/red'
import { useRedOwner } from '@/store/selectors'
import { motionReduced } from '@/ui/motion/tier'
import { live } from '@/stage/live'
import { enuToScene, expAlpha, smoothAngle } from '@/stage/math'
import { stageQuality } from '@/stage/scene/gpu'
import { fallbackTemplate, instantiate, MODEL_URL, prepareTemplate, PROP_KEY, type AirframeTemplate } from '@/stage/scene/model'
import { imageTextures } from '@/stage/scene/StageCanvas'
import { useStage } from '@/stage/store'
import { token } from '@/stage/tokens'
import { flightAt, type Flight } from './ballistics'
import { useTA } from './copy'
import { useArena, watchArenaEvents, type ArenaMark, type ArenaRemote } from './store'
import { ARENA_TOKENS, tokenMs } from './timing'

const MAX_BULLETS = 512
const MAX_DEBRIS = 192
const MAX_FLASHES = 8
const SMOOTH_TAU = 0.12
const LEAD_MAX_S = 0.15
const SNAP_DISTANCE = 3
const TRACER_LENGTH = 0.06
const TRACER_RADIUS = 0.0035
const VOXEL = 0.012
const HIT_VOXELS = 8
const DOWN_VOXELS = 18
const FLASH_RADIUS = 0.085
const LABEL_LIFT = 0.12

const tmpV = new THREE.Vector3()
const tmpQ = new THREE.Quaternion()
const tmpM = new THREE.Matrix4()
const tmpS = new THREE.Vector3(1, 1, 1)
const tmpD = new THREE.Vector3()
const ONE = new THREE.Vector3(1, 1, 1)
const AXIS_Y = new THREE.Vector3(0, 1, 0)

function scene(p: readonly number[], out = new THREE.Vector3()): THREE.Vector3 {
  const [x, y, z] = enuToScene(p[0], p[1], p[2])
  return out.set(x, y, z)
}

// ---------------------------------------------------------------- remote targets

interface RemoteTarget {
  v: ArenaRemote
  label: string
}

let targetsOf: { view: unknown; map: Map<string, RemoteTarget> } = { view: null, map: new Map() }

/** Remote aircraft of the current view, keyed "<peer>/sim-N" (rebuilt once per arena message). */
function remoteTargets(): Map<string, RemoteTarget> {
  const view = useArena.getState().view
  if (targetsOf.view === view) return targetsOf.map
  const map = new Map<string, RemoteTarget>()
  if (view?.enabled) for (const p of view.peers) for (const v of p.vehicles) map.set(v.key, { v, label: `${p.callsign} / ${v.label}` })
  targetsOf = { view, map }
  return map
}

/** Keys and labels of the remote aircraft; React re-renders only when that list changes. */
function useRemoteList(): { key: string; label: string }[] {
  const key = useArena((s) => {
    if (!s.view?.enabled) return ''
    return s.view.peers.flatMap((p) => p.vehicles.map((v) => `${v.key}\u0001${p.callsign} / ${v.label}`)).join('\u0002')
  })
  return React.useMemo(() => (key ? key.split('\u0002').map((x) => { const [k, label] = x.split('\u0001'); return { key: k, label } }) : []), [key])
}

// ---------------------------------------------------------------- effects pool

interface Voxel {
  born: number
  life: number
  p: THREE.Vector3
  v: THREE.Vector3
  color: THREE.Color
}

interface FlashState {
  vehicle: string
  born: number
  life: number
}

/** Imperative effect queue shared by the remote fleet (shoot-downs) and the mark handler (hits); refuses every
 * effect under reduced motion, whoever asks. Exported for tests. */
export const fx = {
  voxels: [] as Voxel[],
  flashes: [] as FlashState[],
  enabled: true,
  debris(at: THREE.Vector3, n: number, color: THREE.Color, lifeMs: number, speed: number): void {
    if (!this.enabled || lifeMs <= 0 || motionReduced()) return
    const now = performance.now()
    for (let i = 0; i < n; i++) {
      if (this.voxels.length >= MAX_DEBRIS) this.voxels.shift()
      const a = (i / n) * Math.PI * 2 + Math.random() * 0.6
      const up = 0.4 + Math.random() * 0.8
      this.voxels.push({
        born: now,
        life: lifeMs * (0.75 + Math.random() * 0.25),
        p: at.clone(),
        v: new THREE.Vector3(Math.cos(a), up, Math.sin(a)).normalize().multiplyScalar(speed * (0.6 + Math.random() * 0.4)),
        color,
      })
    }
  },
  flash(vehicle: string, lifeMs: number): void {
    if (lifeMs <= 0 || motionReduced()) return
    if (this.flashes.length >= MAX_FLASHES) this.flashes.shift()
    this.flashes.push({ vehicle, born: performance.now(), life: lifeMs })
  },
}

/** Effects for a batch of hit marks (pure enough to unit-test with a fake fx). */
export function applyMarks(marks: readonly ArenaMark[], api: { debris: typeof fx.debris; flash: typeof fx.flash }, at = (vid: string | null) => (vid ? live.state.get(vid)?.pos ?? null : null)): void {
  const hitColor = token('g200').color
  const downColor = token('g500').color
  for (const m of marks) {
    const where = m.point ? scene(m.point) : at(m.vehicle)?.clone() ?? null
    if (m.kind === 'taken') {
      if (m.vehicle) api.flash(m.vehicle, tokenMs(ARENA_TOKENS.flash))
      if (where) api.debris(where, HIT_VOXELS, hitColor, tokenMs(ARENA_TOKENS.debris), 0.9)
      const body = at(m.vehicle)
      if (m.down && body) api.debris(body.clone(), DOWN_VOXELS, downColor, tokenMs(ARENA_TOKENS.downDebris), 0.6)
    } else if (where) {
      api.debris(where, HIT_VOXELS, hitColor, tokenMs(ARENA_TOKENS.debris), 0.9)
    }
  }
}

// ---------------------------------------------------------------- remote aircraft

/** One remote simulator: the airframe in g400 with g200 arm lamps, smoothed towards its target. */
class Craft {
  readonly root = new THREE.Group()
  private readonly yaw = new THREE.Group()
  private readonly att = new THREE.Group()
  private readonly props: THREE.Object3D[]
  private ready = false
  private heading = 0
  private spin = 0
  private down = false
  private hpEl: HTMLSpanElement | null = null
  private hpText = ''

  constructor(template: AirframeTemplate, body: THREE.Material, lamp: THREE.Material, lampGeo: THREE.BufferGeometry) {
    this.att.rotation.order = 'XYZ'
    this.root.add(this.yaw)
    this.yaw.add(this.att)
    const inst = instantiate(template)
    inst.root.traverse((o) => {
      const m = o as THREE.Mesh
      if (!m.isMesh) return
      m.material = body
      m.castShadow = false
      m.raycast = () => {}
    })
    this.att.add(inst.root)
    for (const p of inst.props) {
      // a lamp at each arm end, under the propeller hub
      const l = new THREE.Mesh(lampGeo, lamp)
      l.position.copy(p.position)
      l.position.y -= 0.004
      l.raycast = () => {}
      p.parent?.add(l)
    }
    this.props = inst.props
    this.root.visible = false
  }

  bindHp(el: HTMLSpanElement | null): void {
    this.hpEl = el
    this.hpText = ''
  }

  update(v: ArenaRemote | undefined, lead: number, a: number, dt: number): void {
    if (!v) {
      this.root.visible = false
      return
    }
    scene([v.x + v.vx * lead, v.y + v.vy * lead, Math.max(0, v.z + v.vz * lead)], tmpV)
    if (!this.ready || this.root.position.distanceTo(tmpV) > SNAP_DISTANCE) {
      this.root.position.copy(tmpV)
      this.heading = v.yaw
      this.att.rotation.set(v.pitch, 0, -v.roll)
      this.ready = true
    } else {
      this.root.position.lerp(tmpV, a)
      this.heading = smoothAngle(this.heading, v.yaw, a)
      this.att.rotation.x = smoothAngle(this.att.rotation.x, v.pitch, a)
      this.att.rotation.z = smoothAngle(this.att.rotation.z, -v.roll, a)
    }
    this.root.visible = true
    this.yaw.rotation.y = -this.heading
    const flying = v.armed && !v.down
    this.spin += ((flying ? v.throttle : 0) - this.spin) * expAlpha(dt, 0.15)
    const rps = flying ? 8 + 40 * this.spin : 0
    if (rps > 0) for (const p of this.props) p.rotation.y += (p.userData[PROP_KEY] as number) * rps * 2 * Math.PI * dt
    if (v.down && !this.down) fx.debris(this.root.position.clone(), DOWN_VOXELS, token('g500').color, tokenMs(ARENA_TOKENS.downDebris), 0.6)
    this.down = v.down
    const hp = String(v.hp)
    if (this.hpEl && this.hpText !== hp) {
      this.hpText = hp
      this.hpEl.textContent = hp
    }
  }
}

/** The remote simulators currently in the view, built on demand and shared materials. */
class RemotePool {
  private readonly crafts = new Map<string, Craft>()
  private readonly body = new THREE.MeshStandardMaterial({ color: token('g400').color, metalness: 0.2, roughness: 0.7 })
  private readonly lamp = new THREE.MeshBasicMaterial({ color: token('g200').color, toneMapped: false })
  private readonly lampGeo = new THREE.SphereGeometry(0.0045, 10, 8)

  constructor(private readonly template: AirframeTemplate) {}

  /** The craft of `key`, built the first time it is asked for. */
  craft(key: string): Craft {
    let c = this.crafts.get(key)
    if (!c) {
      c = new Craft(this.template, this.body, this.lamp, this.lampGeo)
      this.crafts.set(key, c)
    }
    return c
  }

  retain(keys: ReadonlySet<string>): void {
    for (const k of [...this.crafts.keys()]) if (!keys.has(k)) this.crafts.delete(k)
  }

  update(dt: number): void {
    const a = expAlpha(dt, SMOOTH_TAU)
    const at = useArena.getState().at
    const lead = Math.min(LEAD_MAX_S, Math.max(0, (performance.now() - at) / 1000))
    const targets = remoteTargets()
    for (const [key, c] of this.crafts) c.update(targets.get(key)?.v, lead, a, dt)
  }

  dispose(): void {
    this.body.dispose()
    this.lamp.dispose()
    this.lampGeo.dispose()
  }
}

function RemoteAircraft({ craft, label }: { craft: Craft; label: string }) {
  const t = useTA()
  const labels = useStage((s) => s.layers.labels)
  const root = craft.root
  const bindHp = React.useCallback((el: HTMLSpanElement | null) => craft.bindHp(el), [craft])
  return (
    <primitive object={root}>
      {labels ? (
        <Html position={[0, LABEL_LIFT, 0]} zIndexRange={[10, 0]} style={{ pointerEvents: 'none' }}>
          <div className="stage-label arena-label" data-arena-remote="">
            <div className="stage-label-name">
              <span>{label}</span>
            </div>
            <div className="stage-label-sub">
              {t('arena.hp')} <span ref={bindHp} className="num" />
            </div>
          </div>
        </Html>
      ) : null}
    </primitive>
  )
}

function RemoteFleet({ template }: { template: AirframeTemplate }) {
  const list = useRemoteList()
  const [pool] = React.useState(() => new RemotePool(template))
  React.useEffect(() => () => pool.dispose(), [pool])
  React.useEffect(() => pool.retain(new Set(list.map((x) => x.key))), [pool, list])
  useFrame((_, rawDt) => pool.update(Math.min(0.1, rawDt)))
  return (
    <>
      {list.map(({ key, label }) => (
        <RemoteAircraft key={key} craft={pool.craft(key)} label={label} />
      ))}
    </>
  )
}

function ModelRemoteFleet() {
  const gltf = useGLTF(MODEL_URL, false, false, imageTextures)
  const template = React.useMemo(() => prepareTemplate(gltf.scene), [gltf.scene])
  return <RemoteFleet template={template} />
}

function FallbackRemoteFleet() {
  const template = React.useMemo(() => fallbackTemplate(token('g400').color), [])
  return <RemoteFleet template={template} />
}

class RemoteBoundary extends React.Component<{ children: React.ReactNode }, { failed: boolean }> {
  override state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  override render() {
    return this.state.failed ? <FallbackRemoteFleet /> : this.props.children
  }
}

// ---------------------------------------------------------------- bullets

/** All rounds in flight: tracer capsules with a short tail, or points on software renderers. */
class BulletView {
  readonly objects: THREE.Object3D[] = []
  private readonly own = token('white').color
  private readonly other = token('g300').color
  private readonly mesh: THREE.InstancedMesh | null = null
  private readonly trail: THREE.LineSegments | null = null
  private readonly points: THREE.Points | null = null
  private readonly flights = new Map<string, Flight>()

  constructor(private readonly light: boolean) {
    if (light) {
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(MAX_BULLETS * 3), 3))
      geo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(MAX_BULLETS * 3), 3))
      this.points = new THREE.Points(geo, new THREE.PointsMaterial({ size: 3, sizeAttenuation: false, vertexColors: true, toneMapped: false }))
      this.objects.push(this.points)
    } else {
      const geo = new THREE.CapsuleGeometry(TRACER_RADIUS, TRACER_LENGTH - 2 * TRACER_RADIUS, 2, 6)
      this.mesh = new THREE.InstancedMesh(geo, new THREE.MeshBasicMaterial({ toneMapped: false }), MAX_BULLETS)
      this.mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage)
      this.mesh.setColorAt(0, this.own)
      this.mesh.count = 0
      const tgeo = new THREE.BufferGeometry()
      tgeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(MAX_BULLETS * 6), 3))
      tgeo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(MAX_BULLETS * 6), 3))
      this.trail = new THREE.LineSegments(
        tgeo,
        new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.45, depthWrite: false, toneMapped: false }),
      )
      this.objects.push(this.mesh, this.trail)
    }
    for (const o of this.objects) {
      o.frustumCulled = false
      o.raycast = () => {}
    }
  }

  update(): void {
    const view = useArena.getState().view
    const shots = view?.enabled ? view.shots : []
    const now = backendNow()
    const tailS = this.light ? 0 : tokenMs(ARENA_TOKENS.trail) / 1000
    const seen = new Set<string>()
    const pos = this.points?.geometry.getAttribute('position') as THREE.BufferAttribute | undefined
    const col = this.points?.geometry.getAttribute('color') as THREE.BufferAttribute | undefined
    const tpos = this.trail?.geometry.getAttribute('position') as THREE.BufferAttribute | undefined
    const tcol = this.trail?.geometry.getAttribute('color') as THREE.BufferAttribute | undefined
    let n = 0
    for (const s of shots) {
      if (n >= MAX_BULLETS) break
      seen.add(s.id)
      const f = flightAt(s, now, this.flights.get(s.id))
      this.flights.set(s.id, f)
      if (f.done || (f.steps === 0 && now < s.t0)) continue
      const color = s.mine ? this.own : this.other
      scene(f.p, tmpV)
      if (this.mesh) {
        tmpQ.setFromUnitVectors(AXIS_Y, scene(f.v, tmpD).normalize())
        tmpM.compose(tmpV, tmpQ, ONE)
        this.mesh.setMatrixAt(n, tmpM)
        this.mesh.setColorAt(n, color)
        if (tpos && tcol && tailS > 0) {
          scene([f.p[0] - f.v[0] * tailS, f.p[1] - f.v[1] * tailS, f.p[2] - f.v[2] * tailS], tmpD)
          tpos.setXYZ(n * 2, tmpV.x, tmpV.y, tmpV.z)
          tpos.setXYZ(n * 2 + 1, tmpD.x, tmpD.y, tmpD.z)
          tcol.setXYZ(n * 2, color.r, color.g, color.b)
          tcol.setXYZ(n * 2 + 1, 0, 0, 0)
        }
      } else if (pos && col) {
        pos.setXYZ(n, tmpV.x, tmpV.y, tmpV.z)
        col.setXYZ(n, color.r, color.g, color.b)
      }
      n++
    }
    for (const id of [...this.flights.keys()]) if (!seen.has(id)) this.flights.delete(id)
    if (this.mesh) {
      this.mesh.count = n
      this.mesh.instanceMatrix.needsUpdate = true
      if (this.mesh.instanceColor) this.mesh.instanceColor.needsUpdate = true
    }
    if (this.trail && tpos && tcol) {
      this.trail.geometry.setDrawRange(0, tailS > 0 ? n * 2 : 0)
      tpos.needsUpdate = true
      tcol.needsUpdate = true
    }
    if (this.points && pos && col) {
      this.points.geometry.setDrawRange(0, n)
      pos.needsUpdate = true
      col.needsUpdate = true
    }
  }

  dispose(): void {
    for (const o of this.objects) {
      const m = o as THREE.Mesh
      m.geometry.dispose()
      ;(m.material as THREE.Material).dispose()
    }
  }
}

function Bullets({ light }: { light: boolean }) {
  const [view] = React.useState(() => new BulletView(light))
  React.useEffect(() => () => view.dispose(), [view])
  useFrame(() => view.update())
  return (
    <>
      {view.objects.map((o) => (
        <primitive key={o.uuid} object={o} />
      ))}
    </>
  )
}

// ---------------------------------------------------------------- hit flash and debris

/** Voxel debris (one InstancedMesh) and the hit flash shells of our aircraft. */
class EffectsView {
  readonly objects: THREE.Object3D[] = []
  private readonly cubes: THREE.InstancedMesh
  private readonly shells: THREE.Mesh[]
  private readonly shellGeo = new THREE.SphereGeometry(FLASH_RADIUS, 20, 14)

  constructor() {
    this.cubes = new THREE.InstancedMesh(new THREE.BoxGeometry(VOXEL, VOXEL, VOXEL), new THREE.MeshBasicMaterial({ toneMapped: false }), MAX_DEBRIS)
    this.cubes.instanceMatrix.setUsage(THREE.DynamicDrawUsage)
    this.cubes.setColorAt(0, token('g200').color)
    this.cubes.count = 0
    this.cubes.frustumCulled = false
    this.cubes.raycast = () => {}
    this.shells = Array.from({ length: MAX_FLASHES }, () => {
      const m = new THREE.Mesh(this.shellGeo, new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false, toneMapped: false }))
      m.visible = false
      m.raycast = () => {}
      return m
    })
    this.objects.push(this.cubes, ...this.shells)
  }

  update(dt: number, owner: RedOwner): void {
    const now = performance.now()
    fx.voxels = fx.voxels.filter((v) => now - v.born < v.life)
    let n = 0
    for (const v of fx.voxels) {
      v.v.y -= 9.81 * dt
      v.p.addScaledVector(v.v, dt)
      if (v.p.y < VOXEL / 2) {
        v.p.y = VOXEL / 2
        v.v.multiplyScalar(0.4)
      }
      tmpS.setScalar(Math.max(0.05, 1 - (now - v.born) / v.life))
      tmpM.compose(v.p, tmpQ.identity(), tmpS)
      this.cubes.setMatrixAt(n, tmpM)
      this.cubes.setColorAt(n, v.color)
      n++
    }
    this.cubes.count = n
    this.cubes.instanceMatrix.needsUpdate = true
    if (this.cubes.instanceColor) this.cubes.instanceColor.needsUpdate = true

    fx.flashes = fx.flashes.filter((f) => now - f.born < f.life)
    // damage to our own aircraft: red under the single-red rule, bold g50 while a critical alert owns red
    const color = damageTone(owner) === 'red' ? token('r500').color : token('g50').color
    this.shells.forEach((shell, i) => {
      const f = fx.flashes[i]
      const st = f ? live.state.get(f.vehicle) : undefined
      if (!f || !st) {
        shell.visible = false
        return
      }
      shell.visible = true
      shell.position.copy(st.pos)
      const m = shell.material as THREE.MeshBasicMaterial
      m.color.copy(color)
      m.opacity = 0.55 * (1 - (now - f.born) / f.life)
    })
  }

  dispose(): void {
    this.cubes.geometry.dispose()
    ;(this.cubes.material as THREE.Material).dispose()
    this.shellGeo.dispose()
    for (const s of this.shells) (s.material as THREE.Material).dispose()
  }
}

function Effects({ light, owner }: { light: boolean; owner: RedOwner }) {
  const [view] = React.useState(() => new EffectsView())
  const ownerRef = React.useRef(owner)
  React.useEffect(() => {
    ownerRef.current = owner
  }, [owner])
  React.useEffect(() => () => view.dispose(), [view])
  // hits arrive as events: turn each new mark into a flash and debris once
  React.useEffect(() => {
    fx.enabled = !light
    let last = useArena.getState().marks.at(-1)?.seq ?? 0
    return useArena.subscribe((s) => {
      const fresh = s.marks.filter((m) => m.seq > last)
      if (!fresh.length) return
      last = fresh[fresh.length - 1].seq
      if (!motionReduced()) applyMarks(fresh, fx)
    })
  }, [light])
  useFrame((_, rawDt) => view.update(Math.min(0.1, rawDt), ownerRef.current))
  return (
    <>
      {view.objects.map((o) => (
        <primitive key={o.uuid} object={o} />
      ))}
    </>
  )
}

// ---------------------------------------------------------------- layer

export function ArenaLayer() {
  const enabled = useArena((s) => Boolean(s.view?.enabled))
  const layer = useStage((s) => s.layers.arena)
  const replaying = useReplay((r) => r.active)
  const owner = useRedOwner()
  const light = React.useMemo(() => stageQuality().light, [])
  React.useEffect(() => watchArenaEvents(), [])
  if (!enabled || !layer || replaying) return null
  return (
    <group name="arena">
      <RemoteBoundary>
        <React.Suspense fallback={null}>
          <ModelRemoteFleet />
        </React.Suspense>
      </RemoteBoundary>
      <Bullets light={light} />
      <Effects light={light} owner={owner} />
    </group>
  )
}
