// LAN arena layer of the stage scene (05 §7.5), mounted next to <CourseLayer/>. Remote simulators fly the same
// airframe in g400 graphite with g200 arm lamps and a "callsign / SIM-N" label, smoothed in their own target table
// (never live.state, so they stay out of camera framing, the fleet panel and records). Bullets are voxel tracers
// (./voxelFx, 06 §11: a white-hot head and a red tail, r400 for this terminal's rounds and r500 for others, with a
// floor on their size in pixels so they read from far away); every frame lands on the shared 1/240 s integrator
// lattice (./ballistics). Every hit throws a voxel burst at the hit point; a hit on one of our aircraft also flashes
// its shell (damageTone, store/red.ts). v0.5 (07 §5, WP-C): a shoot-down, a ram or a hard crash explodes, a
// collision that crashes nobody throws sparks, an ordinary ground crash raises dust (./effects, ./fx); those event
// effects show whether or not the arena is on, because local simulators collide with each other and with rings too.
// The collision-volume layer draws each simulator's collision sphere and bullet hit sphere (./Volumes). Software
// rendering drops the tracer tails. Reduced motion drops the flash and replaces each effect with a still white cube.
// Hidden while Records replays a flight.
import * as React from 'react'
import * as THREE from 'three'
import { useFrame } from '@react-three/fiber'
import { Html, useGLTF } from '@react-three/drei'
import { useReplay } from '@/records/replayState'
import { backendNow } from '@/store/relay'
import { damageTone, type RedOwner } from '@/store/red'
import { useRedOwner } from '@/store/selectors'
import { live } from '@/stage/live'
import { enuToScene, expAlpha, sceneToEnu, smoothAngle } from '@/stage/math'
import { stageQuality } from '@/stage/scene/gpu'
import { fallbackTemplate, instantiate, MODEL_URL, prepareTemplate, PROP_KEY, type AirframeTemplate } from '@/stage/scene/model'
import { imageTextures } from '@/stage/scene/StageCanvas'
import { useStage } from '@/stage/store'
import { token } from '@/stage/tokens'
import { ringTouch } from '@/course/touch'
import { useRelay } from '@/store/relay'
import { flightAt, type Flight } from './ballistics'
import { useTA } from './copy'
import { effectsOf, type Vec3 } from './effects'
import { applyEventEffects, applyMarks, explosionLifeS, fx, fxNow, localEnu } from './fx'
import { useArena, watchArenaEvents, type ArenaRemote } from './store'
import { ARENA_TOKENS, tokenMs } from './timing'
import { Volumes } from './Volumes'
import { burstLife, seedOf, VoxelFx } from './voxelFx'

export { applyMarks, fx } from './fx'

const MAX_BULLETS = 512
const MAX_FLASHES = 8
const SMOOTH_TAU = 0.12
const LEAD_MAX_S = 0.15
const SNAP_DISTANCE = 3
const FLASH_RADIUS = 0.085
const LABEL_LIFT = 0.12

const tmpV = new THREE.Vector3()
const tmpD = new THREE.Vector3()

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
    if (v.down && !this.down) fx.explode(v.key, this.root.position, seedOf(`${v.key}:down`))
    this.down = v.down
    const hp = String(v.hp)
    if (this.hpEl && this.hpText !== hp) {
      this.hpText = hp
      this.hpEl.textContent = hp
    }
  }
}

/** Smoothed scene positions of the remote simulators drawn this frame (collision volumes read them). */
export const remoteBodies = new Map<string, THREE.Vector3>()

/** Local ENU position of a local aircraft or a remote key (for event effects). */
function locate(key: string): Vec3 | null {
  const local = localEnu(key)
  if (local) return local
  const body = remoteBodies.get(key)
  if (body) return sceneToEnu(body.x, body.y, body.z)
  const v = remoteTargets().get(key)?.v
  return v ? [v.x, v.y, Math.max(0, v.z)] : null
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
    remoteBodies.clear()
    for (const [key, c] of this.crafts) {
      c.update(targets.get(key)?.v, lead, a, dt)
      if (c.root.visible) remoteBodies.set(key, c.root.position)
    }
  }

  dispose(): void {
    remoteBodies.clear()
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

/** All rounds in flight as voxel tracers (heads only on software renderers). */
class BulletView {
  readonly vox = new VoxelFx()
  private readonly flights = new Map<string, Flight>()

  constructor(private readonly light: boolean) {}

  update(cam: THREE.Camera, heightPx: number): void {
    const view = useArena.getState().view
    const shots = view?.enabled ? view.shots : []
    const now = backendNow()
    const tailS = this.light ? 0 : tokenMs(ARENA_TOKENS.trail) / 1000
    const seen = new Set<string>()
    let n = 0
    this.vox.begin(cam, heightPx)
    for (const s of shots) {
      if (n >= MAX_BULLETS) break
      seen.add(s.id)
      const f = flightAt(s, now, this.flights.get(s.id))
      this.flights.set(s.id, f)
      if (f.done || (f.steps === 0 && now < s.t0)) continue
      this.vox.tracer(scene(f.p, tmpV), scene(f.v, tmpD), s.mine, tailS)
      n++
    }
    this.vox.end()
    for (const id of [...this.flights.keys()]) if (!seen.has(id)) this.flights.delete(id)
  }
}

function Bullets({ light }: { light: boolean }) {
  const [view] = React.useState(() => new BulletView(light))
  React.useEffect(() => () => view.vox.dispose(), [view])
  useFrame(({ camera, size }) => view.update(camera, size.height))
  return (
    <>
      {view.vox.objects.map((o) => (
        <primitive key={o.uuid} object={o} />
      ))}
    </>
  )
}

// ---------------------------------------------------------------- hit flash and bursts

/** Voxel bursts, explosions, sparks and dust, and the hit flash shells of our aircraft. */
class EffectsView {
  readonly vox = new VoxelFx(4096)
  readonly objects: THREE.Object3D[]
  private readonly shells: THREE.Mesh[]
  private readonly shellGeo = new THREE.SphereGeometry(FLASH_RADIUS, 20, 14)

  constructor() {
    this.shells = Array.from({ length: MAX_FLASHES }, () => {
      const m = new THREE.Mesh(this.shellGeo, new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false, toneMapped: false }))
      m.visible = false
      m.raycast = () => {}
      return m
    })
    this.objects = [...this.vox.objects, ...this.shells]
  }

  update(cam: THREE.Camera, heightPx: number, owner: RedOwner): void {
    const t = fxNow()
    const coreS = tokenMs(ARENA_TOKENS.core) / 1000
    const bigCoreS = tokenMs(ARENA_TOKENS.explosionCore) / 1000
    const lives = { burst: tokenMs(ARENA_TOKENS.burst) / 1000, down: (2 * tokenMs(ARENA_TOKENS.downDebris)) / 1000, explosion: explosionLifeS() }
    fx.bursts = fx.bursts.filter((b) => t - b.t0 <= burstLife(b.kind, lives))
    this.vox.begin(cam, heightPx)
    for (const b of fx.bursts) this.vox.burst(b, t - b.t0, b.kind === 'explosion' ? bigCoreS : coreS, burstLife(b.kind, lives))
    this.vox.end()

    const now = performance.now()
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
    this.vox.dispose()
    this.shellGeo.dispose()
    for (const s of this.shells) (s.material as THREE.Material).dispose()
  }
}

function Effects({ owner }: { owner: RedOwner }) {
  const [view] = React.useState(() => new EffectsView())
  const ownerRef = React.useRef(owner)
  React.useEffect(() => {
    ownerRef.current = owner
  }, [owner])
  React.useEffect(() => () => view.dispose(), [view])
  // hits arrive as events: turn each new mark into a burst (and a flash on our aircraft) once
  React.useEffect(() => {
    let last = useArena.getState().marks.at(-1)?.seq ?? 0
    return useArena.subscribe((s) => {
      const fresh = s.marks.filter((m) => m.seq > last)
      if (!fresh.length) return
      last = fresh[fresh.length - 1].seq
      applyMarks(fresh, fx)
    })
  }, [])
  // v0.5 (07 §5): collisions, crashes, shoot-downs and ring touches arrive as events; history is skipped
  React.useEffect(() => {
    let last = useRelay.getState().lastSeq
    return useRelay.subscribe((s) => {
      if (s.lastSeq === last) return
      const fresh = s.events.filter((e) => e.seq > last)
      last = s.lastSeq
      if (fresh.length) applyEventEffects(effectsOf(fresh, locate), fx, ringTouch)
    })
  }, [])
  useFrame(({ camera, size }) => view.update(camera, size.height, ownerRef.current))
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
  if (replaying) return null
  const arena = enabled && layer
  return (
    <group name="arena">
      {arena ? (
        <>
          <RemoteBoundary>
            <React.Suspense fallback={null}>
              <ModelRemoteFleet />
            </React.Suspense>
          </RemoteBoundary>
          <Bullets light={light} />
        </>
      ) : null}
      {/* event effects show without the arena too: local simulators collide with each other and with rings */}
      <Effects owner={owner} />
      <Volumes remote={arena ? remoteBodies : null} />
    </group>
  )
}
