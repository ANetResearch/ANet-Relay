// Ring course 3D layer (02 §10.2, §10.3), a child of <Stage> inside its scene. Rings are solid tori with the
// collision volume's dimensions (07 §2: major radius = the course radius, tube RING_TUBE 3 cm), drawn as a chain of
// voxels along the centre line (./voxelRing; MeshStandard metalness 0.1 / roughness 0.6), each with a 1 px drop line
// and its number on the ground. States (07 §5): next (g300 body with a white voxel outline, four 3 cm ticks turning
// once per 20 s), later (g300, 0.75), passed (g700, 0.35); never red. The selected aircraft's run is featured
// (states, outline, ticks, dashed g400 guide line from the aircraft to the next ring, pass flash and split pop-in);
// other runs are drawn quietly so neighbouring courses stay readable. A pass flashes the ring to white (80 ms), then a
// copy grows 1.0 -> 1.18 and fades (350 ms). An aircraft grazing the tube (`ring_touch`, ./touch) flashes that ring
// white for --duration-arena-ring-flash, held still under reduced motion.
// Contract: web/CONTRACTS.md#courselayer.
import * as React from 'react'
import * as THREE from 'three'
import { Html } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { useShallow } from 'zustand/react/shallow'
import { split as splitText } from '@/lib/format'
import { useReplay } from '@/records/replayState'
import { useRelay } from '@/store/relay'
import { getVehicle, useSelectedId } from '@/store/selectors'
import { motionReduced } from '@/ui/motion/tier'
import { live, useFrameOffsets } from '@/stage/live'
import { easeSmoothOut, enuToScene } from '@/stage/math'
import { framingExtras } from '@/stage/scene/CameraRig'
import { useStage } from '@/stage/store'
import { token } from '@/stage/tokens'
import { RING_TUBE } from '@/arena/collide'
import { ARENA_TOKENS, tokenMs } from '@/arena/timing'
import { PopNumber } from './PopNumber'
import { ringTouchedAt } from './touch'
import { voxelRingGeometry } from './voxelRing'
import { isActive, ringNormal, ringsExtent, ringState, runRings, type CourseView, type Ring, type RingState } from './geometry'
import { useCourse, watchCourses } from './store'
import './course.css'

/** backend/relay/sim/collide.py RING_TUBE */
const TUBE = RING_TUBE
const TICK = 0.03
const TICK_PERIOD_S = 20
const FLASH_MS = 80
const GROW_MS = 350
const SPLIT_MS = 500
const SPLIT_FADE_MS = 250
const Z_AXIS = new THREE.Vector3(0, 0, 1)

interface Look {
  color: THREE.Color
  opacity: number
  emissive: number
}

function look(state: RingState, featured: boolean): Look {
  if (featured) {
    if (state === 'next') return { color: token('g300').color, opacity: 1, emissive: 0.35 }
    if (state === 'later') return { color: token('g300').color, opacity: 0.75, emissive: 0.2 }
    return { color: token('g700').color, opacity: 0.35, emissive: 0.15 }
  }
  if (state === 'next') return { color: token('g400').color, opacity: 0.6, emissive: 0.2 }
  if (state === 'later') return { color: token('g500').color, opacity: 0.45, emissive: 0.15 }
  return { color: token('g700').color, opacity: 0.22, emissive: 0.1 }
}

const WHITE_LOOK = (): Look => ({ color: token('white').color, opacity: 1, emissive: 0.9 })

function ringMaterial(l: Look): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({
    color: l.color, emissive: l.color, emissiveIntensity: l.emissive, metalness: 0.1, roughness: 0.6,
    transparent: l.opacity < 1, opacity: l.opacity, depthWrite: l.opacity >= 1,
  })
}

function applyLook(m: THREE.MeshStandardMaterial, l: Look): void {
  m.color.copy(l.color)
  m.emissive.copy(l.color)
  m.emissiveIntensity = l.emissive
  m.opacity = l.opacity
  m.transparent = l.opacity < 1
  m.depthWrite = l.opacity >= 1
  m.needsUpdate = true
}

/** Imperative ring: voxel torus, outline hull, pass echo, the four ticks and the drop line. */
class RingView {
  readonly orient = new THREE.Group()
  readonly drop: THREE.Line
  readonly center: THREE.Vector3
  private readonly geo: THREE.BufferGeometry
  private readonly mat: THREE.MeshStandardMaterial
  private readonly echo: THREE.Mesh
  private readonly echoMat: THREE.MeshStandardMaterial
  private readonly outlineGeo: THREE.BufferGeometry
  private readonly outlineMat = new THREE.MeshBasicMaterial({ color: token('white').color, side: THREE.BackSide, toneMapped: false })
  private readonly outline: THREE.Mesh
  private state: RingState
  private featured: boolean
  private touchAt: number | null = null
  private readonly ticks = new THREE.Group()
  private readonly tickGeo = new THREE.BoxGeometry(0.004, TICK, 0.004)
  private readonly tickMat = new THREE.MeshBasicMaterial({ color: token('g50').color, toneMapped: false })
  private anim: number | null = null
  private passAt: number | null = null

  constructor(ring: Ring, state: RingState, featured: boolean, private readonly vehicle: string, private readonly index: number) {
    this.state = state
    this.featured = featured
    this.geo = voxelRingGeometry(ring.r, TUBE)
    this.mat = ringMaterial(look(state, featured))
    const torus = new THREE.Mesh(this.geo, this.mat)
    torus.castShadow = true
    torus.raycast = () => {}
    // the next ring of the featured run: an inverted hull of slightly larger cubes reads as a white outline
    this.outlineGeo = voxelRingGeometry(ring.r, TUBE, 1.32)
    this.outline = new THREE.Mesh(this.outlineGeo, this.outlineMat)
    this.outline.visible = featured && state === 'next'
    this.outline.raycast = () => {}
    this.echoMat = ringMaterial({ color: token('white').color, opacity: 0, emissive: 0.6 })
    this.echoMat.transparent = true
    this.echo = new THREE.Mesh(this.geo, this.echoMat)
    this.echo.visible = false
    this.echo.raycast = () => {}
    const rr = ring.r + 1.5 * TUBE + 0.006 + TICK / 2
    for (let i = 0; i < 4; i++) {
      const a = (i * Math.PI) / 2
      const tick = new THREE.Mesh(this.tickGeo, this.tickMat)
      tick.position.set(Math.sin(a) * rr, Math.cos(a) * rr, 0)
      tick.rotation.z = -a
      tick.raycast = () => {}
      this.ticks.add(tick)
    }
    const [nx, ny, nz] = ringNormal(ring.yaw_deg)
    this.orient.quaternion.setFromUnitVectors(Z_AXIS, new THREE.Vector3(...enuToScene(nx, ny, nz)).normalize())
    this.orient.add(this.outline, torus, this.echo, this.ticks)
    const [sx, sy, sz] = enuToScene(ring.c[0], ring.c[1], ring.c[2])
    this.center = new THREE.Vector3(sx, sy, sz)
    this.orient.position.copy(this.center)
    const bottom = Math.max(0, sy - ring.r - TUBE)
    this.drop = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(sx, 0, sz), new THREE.Vector3(sx, bottom, sz)]),
      new THREE.LineBasicMaterial({ color: token('g700').color, toneMapped: false }),
    )
    this.drop.raycast = () => {}
  }

  /** State change; a fresh pass of the featured run plays the flash unless motion is reduced. */
  update(state: RingState, featured: boolean, passAt: number | null): void {
    const fresh = passAt !== null && passAt !== this.passAt && performance.now() - passAt < FLASH_MS + GROW_MS
    this.passAt = passAt
    this.state = state
    this.featured = featured
    if (fresh && featured && state === 'passed' && !motionReduced()) this.anim = passAt
    else if (this.anim === null && this.touchAt === null) applyLook(this.mat, look(state, featured))
    this.ticks.visible = featured && state === 'next'
    this.outline.visible = featured && state === 'next'
  }

  /** A graze of the tube (ring_touch): white for the flash token, then back to the state's look. */
  private touch(): boolean {
    const at = ringTouchedAt(this.vehicle, this.index)
    const span = tokenMs(ARENA_TOKENS.ringFlash)
    const on = at !== null && span > 0 && performance.now() - at < span
    if (on && this.touchAt !== at) applyLook(this.mat, WHITE_LOOK())
    else if (!on && this.touchAt !== null) applyLook(this.mat, look(this.state, this.featured))
    this.touchAt = on ? at : null
    return on
  }

  frame(dt: number): void {
    if (this.ticks.visible && !motionReduced()) this.ticks.rotation.z -= ((2 * Math.PI) / TICK_PERIOD_S) * dt
    if (this.touch()) return
    if (this.anim === null) return
    const age = performance.now() - this.anim
    if (age < FLASH_MS) {
      applyLook(this.mat, WHITE_LOOK())
      return
    }
    if (!this.echo.visible) {
      applyLook(this.mat, look('passed', true))
      this.echo.visible = true
    }
    const e = easeSmoothOut(Math.min(1, (age - FLASH_MS) / GROW_MS))
    const sc = 1 + 0.18 * e
    this.echo.scale.set(sc, sc, sc)
    this.echoMat.opacity = 0.9 * (1 - e)
    if (e >= 1) {
      this.echo.visible = false
      this.echo.scale.set(1, 1, 1)
      this.anim = null
    }
  }

  dispose(): void {
    this.geo.dispose()
    this.mat.dispose()
    this.outlineGeo.dispose()
    this.outlineMat.dispose()
    this.echoMat.dispose()
    this.tickGeo.dispose()
    this.tickMat.dispose()
    this.drop.geometry.dispose()
    ;(this.drop.material as THREE.Material).dispose()
  }
}

const RingMesh = React.memo(function RingMesh({ ring, index, vehicle, state, featured, passAt }: {
  ring: Ring
  index: number
  /** the aircraft whose run this ring belongs to (ring touches) */
  vehicle: string
  state: RingState
  featured: boolean
  /** performance.now() of the pass of this ring, if it was just passed */
  passAt: number | null
}) {
  // the view is built once per ring placement (the parent keys rings by position); later states are updates
  const [view] = React.useState(() => new RingView(ring, state, featured, vehicle, index))
  React.useEffect(() => view.update(state, featured, passAt), [view, state, featured, passAt])
  React.useEffect(() => () => view.dispose(), [view])
  useFrame((_, dt) => view.frame(dt))
  return (
    <>
      <primitive object={view.orient} />
      <primitive object={view.drop} />
      <Html position={[view.center.x, 0, view.center.z]} zIndexRange={[6, 0]} style={{ pointerEvents: 'none' }}>
        <span className="course-ring-no" data-featured={featured ? '' : undefined}>{index + 1}</span>
      </Html>
    </>
  )
})

/** "+3.42" at the ring centre for 500 ms after a pass, digits popping in, then fading. */
function SplitPop({ at, text, position }: { at: number; text: string; position: THREE.Vector3 }) {
  // a remount long after the pass (selection change) shows nothing
  const [phase, setPhase] = React.useState<'in' | 'out' | 'gone'>(() => (performance.now() - at > SPLIT_MS ? 'gone' : 'in'))
  React.useEffect(() => {
    const a = setTimeout(() => setPhase('out'), SPLIT_MS)
    const b = setTimeout(() => setPhase('gone'), SPLIT_MS + SPLIT_FADE_MS)
    return () => {
      clearTimeout(a)
      clearTimeout(b)
    }
  }, [at])
  if (phase === 'gone') return null
  return (
    <Html position={position} center zIndexRange={[14, 0]} style={{ pointerEvents: 'none' }}>
      <span className="course-split" data-phase={phase}>
        <PopNumber text={text} key={at} />
      </span>
    </Html>
  )
}

/** Dashed g400 line from the aircraft to the next ring centre (featured run only). */
class GuideView {
  readonly line: THREE.Line
  constructor() {
    const geo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 0, -1)])
    const mat = new THREE.LineDashedMaterial({ color: token('g400').color, dashSize: 0.06, gapSize: 0.045, toneMapped: false, transparent: true, opacity: 0.9 })
    this.line = new THREE.Line(geo, mat)
    this.line.frustumCulled = false
    this.line.raycast = () => {}
  }

  frame(vehicleId: string, target: THREE.Vector3 | null): void {
    const st = live.state.get(vehicleId)
    if (!target || !st || !st.ready || st.kind === 'slot') {
      this.line.visible = false
      return
    }
    this.line.visible = true
    const pos = this.line.geometry.getAttribute('position') as THREE.BufferAttribute
    pos.setXYZ(0, st.pos.x, st.pos.y, st.pos.z)
    pos.setXYZ(1, target.x, target.y, target.z)
    pos.needsUpdate = true
    this.line.geometry.computeBoundingSphere()
    this.line.computeLineDistances()
  }

  dispose(): void {
    this.line.geometry.dispose()
    ;(this.line.material as THREE.Material).dispose()
  }
}

function GuideLine({ vehicleId, target }: { vehicleId: string; target: THREE.Vector3 | null }) {
  const [view] = React.useState(() => new GuideView())
  React.useEffect(() => () => view.dispose(), [view])
  useFrame(() => view.frame(vehicleId, target))
  return <primitive object={view.line} />
}

function useRunKey(id: string): string {
  return useRelay((s) => {
    const v = s.fleet?.vehicles.find((x) => x.id === id)
    const c = v?.course as CourseView | null | undefined
    if (!c) return ''
    return `${c.course_id}|${c.state}|${c.next}|${c.total}|${c.origin.join(',')}|${c.rings?.length ?? 0}`
  })
}

const CourseRun = React.memo(function CourseRun({ id, featured }: { id: string; featured: boolean }) {
  const key = useRunKey(id)
  const defs = useCourse((s) => s.defs)
  const view = React.useMemo(() => (key ? ((getVehicle(id)?.course as CourseView | null) ?? null) : null), [key, id])
  // real aircraft fly in their own local frame, which the stage may draw beside the simulator grid
  const offsets = useFrameOffsets()
  const rings = React.useMemo(() => {
    const placed = view ? runRings(view, defs ?? []) : []
    const [dx, dy] = getVehicle(id)?.kind === 'real' ? offsets.local : [0, 0]
    return dx || dy ? placed.map((r) => ({ ...r, c: [r.c[0] + dx, r.c[1] + dy, r.c[2]] as Ring['c'] })) : placed
  }, [view, defs, id, offsets])
  const pass = useCourse((s) => s.passes[id] ?? null)

  React.useEffect(() => {
    if (!featured || !rings.length) return
    const ext = ringsExtent(rings)
    if (ext) framingExtras.set('course', ext)
    return () => {
      framingExtras.delete('course')
    }
  }, [featured, rings])

  if (!view || !rings.length) return null
  const nextRing = isActive(view) && view.next < rings.length ? rings[view.next] : null
  const target = nextRing ? new THREE.Vector3(...enuToScene(nextRing.c[0], nextRing.c[1], nextRing.c[2])) : null
  const popRing = featured && pass ? rings[pass.ring] : null
  const popText = pass ? splitText(pass.split) : null
  return (
    <>
      {rings.map((r, i) => (
        <RingMesh
          key={`${view.course_id}-${i}-${r.c.join(',')}`}
          ring={r}
          index={i}
          vehicle={id}
          state={ringState(i, view)}
          featured={featured}
          passAt={pass && pass.ring === i ? pass.at : null}
        />
      ))}
      {featured ? <GuideLine vehicleId={id} target={target} /> : null}
      {pass && popRing && popText ? (
        <SplitPop key={pass.at} at={pass.at} text={popText} position={new THREE.Vector3(...enuToScene(popRing.c[0], popRing.c[1], popRing.c[2]))} />
      ) : null}
    </>
  )
})

export function CourseLayer() {
  const visible = useStage((s) => s.layers.course)
  const replaying = useReplay((r) => r.active)
  const selected = useSelectedId()
  const ids = useRelay(useShallow((s) => (s.fleet?.vehicles ?? []).filter((v) => v.course).map((v) => v.id)))

  React.useEffect(() => watchCourses(), [])

  if (!visible || replaying || !ids.length) return null
  // featured: the selected aircraft's run, or the only run on the stage
  const featured = selected && ids.includes(selected) ? selected : ids.length === 1 ? ids[0] : null
  return (
    <>
      {ids.map((id) => (
        <CourseRun key={id} id={id} featured={id === featured} />
      ))}
    </>
  )
}
