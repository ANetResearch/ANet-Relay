// Scene layer of an evaluation-trace replay (06 §3.4, §7), mounted next to <ArenaLayer/> on the Fly stage. Draws
// the rounds in flight at the replay cursor (one InstancedMesh of thin tracers flown on the arena integrator from
// each shot's spawn, ./trace shotsAt + arena/ballistics flightAt) and the arena boundary ring on the ground. The
// first aircraft's rounds are white, the others g300 (who fired is told apart by lightness, never by red). Reads
// the trace store in the frame loop; renders nothing while no trace is open.
import * as React from 'react'
import * as THREE from 'three'
import { useFrame } from '@react-three/fiber'
import { flightAt } from '@/arena/ballistics'
import { enuToScene } from '@/stage/math'
import { token } from '@/stage/tokens'
import { shotsAt } from './trace'
import { useTraceReplay } from './traceReplay'

const MAX_BULLETS = 256
const TRACER_LENGTH = 0.06
const TRACER_RADIUS = 0.0035
const AXIS_Y = new THREE.Vector3(0, 1, 0)
const ONE = new THREE.Vector3(1, 1, 1)
const tmpP = new THREE.Vector3()
const tmpV = new THREE.Vector3()
const tmpQ = new THREE.Quaternion()
const tmpM = new THREE.Matrix4()

function toScene(p: readonly number[], out: THREE.Vector3): THREE.Vector3 {
  const [x, y, z] = enuToScene(p[0], p[1], p[2])
  return out.set(x, y, z)
}

class TraceBullets {
  readonly mesh: THREE.InstancedMesh
  private readonly first = token('white').color
  private readonly other = token('g300').color

  constructor() {
    const geo = new THREE.CapsuleGeometry(TRACER_RADIUS, TRACER_LENGTH - 2 * TRACER_RADIUS, 2, 6)
    this.mesh = new THREE.InstancedMesh(geo, new THREE.MeshBasicMaterial({ toneMapped: false }), MAX_BULLETS)
    this.mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage)
    this.mesh.setColorAt(0, this.first)
    this.mesh.count = 0
    this.mesh.frustumCulled = false
    this.mesh.raycast = () => {}
  }

  update(): void {
    const { trace, t } = useTraceReplay.getState()
    let n = 0
    if (trace) {
      const lead = trace.aircraft[0]?.id
      for (const s of shotsAt(trace.shots, t)) {
        if (n >= MAX_BULLETS) break
        const f = flightAt(s, t)
        if (f.done) continue
        toScene(f.p, tmpP)
        tmpQ.setFromUnitVectors(AXIS_Y, toScene(f.v, tmpV).normalize())
        tmpM.compose(tmpP, tmpQ, ONE)
        this.mesh.setMatrixAt(n, tmpM)
        this.mesh.setColorAt(n, s.owner === lead ? this.first : this.other)
        n++
      }
    }
    this.mesh.count = n
    this.mesh.instanceMatrix.needsUpdate = true
    if (this.mesh.instanceColor) this.mesh.instanceColor.needsUpdate = true
  }

  dispose(): void {
    this.mesh.geometry.dispose()
    ;(this.mesh.material as THREE.Material).dispose()
  }
}

function Bullets() {
  const [view] = React.useState(() => new TraceBullets())
  React.useEffect(() => () => view.dispose(), [view])
  useFrame(() => view.update())
  return <primitive object={view.mesh} />
}

/** The arena's radius as a ring on the ground (scene metres). */
function Boundary({ radius }: { radius: number }) {
  const line = React.useMemo(() => {
    const pts: THREE.Vector3[] = []
    for (let i = 0; i < 128; i++) {
      const a = (i / 128) * Math.PI * 2
      pts.push(new THREE.Vector3(Math.cos(a) * radius, 0.002, Math.sin(a) * radius))
    }
    const l = new THREE.LineLoop(
      new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineDashedMaterial({ color: token('g500').color, dashSize: 0.12, gapSize: 0.08, toneMapped: false, transparent: true, opacity: 0.8 }),
    )
    l.computeLineDistances()
    l.raycast = () => {}
    return l
  }, [radius])
  React.useEffect(
    () => () => {
      line.geometry.dispose()
      ;(line.material as THREE.Material).dispose()
    },
    [line],
  )
  return <primitive object={line} />
}

export function TraceLayer() {
  const open = useTraceReplay((s) => s.trace !== null)
  const radius = useTraceReplay((s) => s.trace?.arena?.radius ?? null)
  if (!open) return null
  return (
    <group name="trace-replay">
      {radius ? <Boundary radius={radius} /> : null}
      <Bullets />
    </group>
  )
}
