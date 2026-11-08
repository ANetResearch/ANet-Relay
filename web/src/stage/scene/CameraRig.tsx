// Camera rig of 02 §6.5. Overview (default): automatic framing of every placeable aircraft (and the selected
// aircraft's course) until the user orbits or zooms; double click flies to an aircraft (medium 350 ms). Chase:
// 1.2 m behind and 0.45 m above the chased aircraft, yaw smoothed over 200 ms. Top: orthographic from 8 m, north up.
// Switching views interpolates the pose (slow 400 ms, smooth-out); reduced motion cuts directly. Transitions to and
// from Top run on the perspective camera at the height that shows the same ground area, then swap cameras.
import * as React from 'react'
import * as THREE from 'three'
import { useFrame, useThree } from '@react-three/fiber'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { useRelay } from '@/store/relay'
import { useUi } from '@/store/ui'
import { motionReduced } from '@/ui/motion/tier'
import {
  chasePose, FOV_DEG, overviewPose, overviewPoseAt, TOP_HEIGHT, topEquivalentHeight, topPose, type ViewMode,
} from '../camera'
import { live } from '../live'
import { easeSmoothOut, expAlpha, sceneToEnu, smoothAngle, type Vec3 } from '../math'
import { addPoint, emptyBounds, framed, type Bounds } from '../placement'
import { useStage } from '../store'
import { rig } from './rigState'
import { cameraShake } from '@/arena/shake' // WP-C v0.5

const SLOW_MS = 400
const MEDIUM_MS = 350
const UP = new THREE.Vector3(0, 1, 0)
const NORTH_UP = new THREE.Vector3(0, 0, -1)
const FLY_TO_DISTANCE = 1.6
/**
 * Chase keeps 02 §6.5's pose (1.2 m behind, 0.45 m above, looking 2 m ahead), which puts the aircraft at about
 * 89 % of the stage height, under the bottom HUD strip. A vertical lens shift (NDC units) moves the image up
 * without changing the pose, so the aircraft sits at about 70 %.
 */
const CHASE_LENS_SHIFT = 0.38

/** Extra areas the overview must keep in frame (ENU), e.g. the selected aircraft's ring course. */
export const framingExtras = new Map<string, { minX: number; maxX: number; minY: number; maxY: number; maxZ: number }>()

/** The aircraft Chase follows: the selected one when it has a position, else a human-possessed one that has. */
export function chaseTargetId(): string | null {
  const sel = useUi.getState().selectedId
  const st = sel ? live.state.get(sel) : null
  if (sel && st && st.ready && st.kind !== 'slot') return sel
  const vs = useRelay.getState().fleet?.vehicles ?? []
  for (const v of vs) {
    if (v.controller !== 'human') continue
    const s = live.state.get(v.id)
    if (s && s.ready && s.kind !== 'slot') return v.id
  }
  return null
}

function currentBounds(): { b: Bounds; aircraft: number } {
  const b = emptyBounds()
  let aircraft = 0
  for (const s of live.state.values()) {
    if (!s.ready) continue
    const [x, y, z] = sceneToEnu(s.pos.x, s.pos.y, s.pos.z)
    addPoint(b, x, y, z)
    aircraft++
  }
  for (const e of framingExtras.values()) {
    addPoint(b, e.minX, e.minY, e.maxZ)
    addPoint(b, e.maxX, e.maxY, e.maxZ)
  }
  return { b: framed(b, aircraft), aircraft }
}

function lookQuat(pos: THREE.Vector3, target: THREE.Vector3, up: THREE.Vector3, out: THREE.Quaternion): THREE.Quaternion {
  const m = new THREE.Matrix4().lookAt(pos, target, up)
  return out.setFromRotationMatrix(m)
}

interface Pose {
  pos: THREE.Vector3
  quat: THREE.Quaternion
  target: THREE.Vector3
}

const v3 = (a: Vec3) => new THREE.Vector3(a[0], a[1], a[2])

/** Perspective projection with a vertical lens shift (positive moves the image up). */
function applyProjection(c: THREE.PerspectiveCamera, shift: number): void {
  c.updateProjectionMatrix()
  if (shift === 0) return
  c.projectionMatrix.elements[9] -= shift
  c.projectionMatrixInverse.copy(c.projectionMatrix).invert()
}

type Transition = { from: Pose; start: number; dur: number; fly?: { id: string; target: THREE.Vector3; distance: number } }

/** Imperative camera controller; the React component only feeds it frames. */
class ViewRig {
  readonly persp = new THREE.PerspectiveCamera(FOV_DEG, 1, 0.02, 200)
  readonly ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 60)
  readonly controls: OrbitControls
  private applied: ViewMode = 'overview'
  private user = false
  private first = true
  private transition: Transition | null = null
  private flySeq = 0
  private recenter = useStage.getState().recenter
  private count = -1
  private autoPos = new THREE.Vector3()
  private autoTarget = new THREE.Vector3()
  private topCenter = new THREE.Vector3()
  private topHalf = 3
  private chaseYaw = 0
  private chaseId: string | null = null
  private shift = 0
  private shiftFrom = 0
  private readonly onStart = () => {
    this.user = true
  }

  constructor(
    private readonly dom: HTMLElement,
    private readonly use: (c: THREE.Camera) => void,
  ) {
    this.persp.position.set(3, 2.4, 4)
    this.ortho.up.copy(NORTH_UP)
    // R3F leaves projection updates of "manual" cameras to their owner (this rig sizes both itself)
    Object.assign(this.persp, { manual: true })
    Object.assign(this.ortho, { manual: true })
    const c = new OrbitControls(this.persp)
    c.enableDamping = true
    c.dampingFactor = 0.12
    c.minDistance = 0.25
    c.maxDistance = 80
    c.maxPolarAngle = (86 * Math.PI) / 180
    c.zoomSpeed = 0.9
    c.rotateSpeed = 0.6
    c.panSpeed = 0.8
    c.screenSpacePanning = false
    this.controls = c
  }

  attach(): void {
    this.controls.connect(this.dom)
    this.controls.addEventListener('start', this.onStart)
    this.use(this.persp)
  }

  detach(): void {
    this.controls.removeEventListener('start', this.onStart)
    this.controls.disconnect()
  }

  private topPersp(): Pose {
    const h = topEquivalentHeight(this.topHalf)
    const pos = new THREE.Vector3(this.topCenter.x, h, this.topCenter.z)
    return { pos, target: this.topCenter.clone(), quat: lookQuat(pos, this.topCenter, NORTH_UP, new THREE.Quaternion()) }
  }

  private currentPose(active: THREE.Camera): Pose {
    if (active === this.ortho) return this.topPersp()
    return { pos: this.persp.position.clone(), quat: this.persp.quaternion.clone(), target: this.controls.target.clone() }
  }

  private goal(chase: Pose | null): Pose {
    const tr = this.transition
    if (tr?.fly) {
      const f = tr.fly
      const fs = live.state.get(f.id)
      if (fs && fs.ready) f.target.copy(fs.pos)
      const enu = sceneToEnu(f.target.x, f.target.y, f.target.z)
      const p = overviewPoseAt([enu[0], enu[1], enu[2]], f.distance)
      const pos = v3(p.position)
      const target = v3(p.target)
      return { pos, target, quat: lookQuat(pos, target, UP, new THREE.Quaternion()) }
    }
    if (this.applied === 'chase' && chase) return chase
    if (this.applied === 'top') return this.topPersp()
    if (this.user) return { pos: this.persp.position.clone(), quat: this.persp.quaternion.clone(), target: this.controls.target.clone() }
    return { pos: this.autoPos.clone(), target: this.autoTarget.clone(), quat: lookQuat(this.autoPos, this.autoTarget, UP, new THREE.Quaternion()) }
  }

  frame(active: THREE.Camera, size: { width: number; height: number }, dt: number): void {
    const { persp, ortho, controls } = this
    const aspect = size.width / Math.max(1, size.height)
    persp.aspect = aspect
    const stage = useStage.getState()
    const now = performance.now()
    const { b, aircraft } = currentBounds()
    rig.extent = Math.max(b.maxX - b.minX, b.maxY - b.minY) / 2

    // chase needs a target; fall back to overview when it disappears
    let want = stage.view
    const chaseId = chaseTargetId()
    if (want === 'chase' && !chaseId) want = 'overview'

    if (stage.recenter !== this.recenter) {
      this.recenter = stage.recenter
      this.user = false
    }
    if (aircraft !== this.count) {
      if (this.count >= 0 && !this.transition?.fly) this.user = false
      this.count = aircraft
    }

    // automatic overview and top targets, smoothed so framing glides as aircraft move
    const ov = overviewPose(b, aspect)
    const tp = topPose(b, aspect)
    const a = this.first ? 1 : expAlpha(dt, 0.45)
    this.autoPos.lerp(v3(ov.position), a)
    this.autoTarget.lerp(v3(ov.target), a)
    this.topCenter.lerp(v3(tp.center), a)
    this.topHalf += (tp.halfHeight - this.topHalf) * a

    // chase pose follows the aircraft's smoothed state; yaw smoothed again over 200 ms
    let chase: Pose | null = null
    if (chaseId) {
      const cs = live.state.get(chaseId)!
      if (this.chaseId !== chaseId) {
        this.chaseId = chaseId
        this.chaseYaw = cs.yaw
      }
      this.chaseYaw = smoothAngle(this.chaseYaw, cs.yaw, expAlpha(dt, 0.2))
      const cp = chasePose(sceneToEnu(cs.pos.x, cs.pos.y, cs.pos.z), this.chaseYaw)
      const pos = v3(cp.position)
      const target = v3(cp.target)
      chase = { pos, target, quat: lookQuat(pos, target, UP, new THREE.Quaternion()) }
    }
    const reduced = motionReduced()

    // a fly-to request (double click) is a transition inside the overview
    if (stage.flyTo && stage.flyTo.seq !== this.flySeq) {
      this.flySeq = stage.flyTo.seq
      const fs = live.state.get(stage.flyTo.id)
      if (fs && fs.ready) {
        const from = this.currentPose(active)
        this.use(persp)
        this.applied = 'overview'
        this.shiftFrom = this.shift
        this.transition = { from, start: now, dur: reduced ? 0 : MEDIUM_MS, fly: { id: stage.flyTo.id, target: fs.pos.clone(), distance: FLY_TO_DISTANCE } }
      }
    }

    if (want !== this.applied) {
      const from = this.currentPose(active)
      this.use(persp)
      this.applied = want
      this.shiftFrom = this.shift
      this.transition = { from, start: now, dur: reduced || this.first ? 0 : SLOW_MS }
      if (want === 'overview') this.user = false
    }

    const tr = this.transition
    if (tr) {
      const k = tr.dur <= 0 ? 1 : Math.min(1, (now - tr.start) / tr.dur)
      const e = easeSmoothOut(k)
      const g = this.goal(chase)
      this.shift = this.shiftFrom + ((this.applied === 'chase' ? CHASE_LENS_SHIFT : 0) - this.shiftFrom) * e
      persp.position.lerpVectors(tr.from.pos, g.pos, e)
      persp.quaternion.slerpQuaternions(tr.from.quat, g.quat, e)
      rig.target.lerpVectors(tr.from.target, g.target, e)
      if (k >= 1) {
        this.transition = null
        persp.up.copy(UP)
        if (tr.fly) {
          this.user = true
          controls.target.copy(g.target)
          persp.position.copy(g.pos)
          persp.lookAt(g.target)
          controls.update()
        } else if (this.applied === 'overview') {
          controls.target.copy(g.target)
          persp.lookAt(g.target)
          controls.update()
        } else if (this.applied === 'top') {
          this.use(ortho)
        }
      }
    } else if (this.applied === 'overview') {
      if (this.user) {
        controls.update()
      } else {
        persp.position.copy(this.autoPos)
        controls.target.copy(this.autoTarget)
        persp.up.copy(UP)
        persp.lookAt(this.autoTarget)
      }
      rig.target.copy(controls.target)
    } else if (this.applied === 'chase' && chase) {
      persp.position.copy(chase.pos)
      persp.up.copy(UP)
      persp.lookAt(chase.target)
      // WP-C v0.5 (07 §5): an explosion or collision nearby jolts the chase camera (none under reduced motion)
      const [sx, sy, sz] = cameraShake(persp.position)
      persp.position.set(persp.position.x + sx, persp.position.y + sy, persp.position.z + sz)
      rig.target.copy(chase.target)
    } else if (this.applied === 'top') {
      const half = this.topHalf
      ortho.left = -half * aspect
      ortho.right = half * aspect
      ortho.top = half
      ortho.bottom = -half
      ortho.position.set(this.topCenter.x, TOP_HEIGHT, this.topCenter.z)
      ortho.up.copy(NORTH_UP)
      ortho.lookAt(this.topCenter.x, 0, this.topCenter.z)
      ortho.updateProjectionMatrix()
      rig.target.copy(this.topCenter)
    }
    if (!this.transition) this.shift = this.applied === 'chase' ? CHASE_LENS_SHIFT : 0
    applyProjection(persp, this.shift)
    controls.enabled = this.applied === 'overview' && !this.transition
    rig.camera = active
    this.first = false
  }
}

export function CameraRig() {
  const gl = useThree((s) => s.gl)
  const set = useThree((s) => s.set)
  const [ctl] = React.useState(() => new ViewRig(gl.domElement, (camera) => set({ camera: camera as THREE.PerspectiveCamera })))
  React.useEffect(() => {
    ctl.attach()
    return () => ctl.detach()
  }, [ctl])
  useFrame((state, dt) => ctl.frame(state.camera, state.size, dt))
  return null
}
