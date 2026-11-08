// Scene layer of an evaluation-trace replay (06 §3.4, §7, §11), mounted next to <ArenaLayer/> on the Fly stage.
// Draws the rounds in flight at the replay cursor as voxel tracers (arena/voxelFx; each shot flown on the arena
// integrator from its spawn, ./trace shotsAt + arena/ballistics flightAt), a voxel burst at every hit and
// shoot-down (./trace impactsAt; a pure function of the cursor, so scrubbing back shows the same frame), and the
// arena boundary ring on the ground. The first aircraft's rounds are r400, the others r500 (who fired is told apart
// by lightness). Reads the trace store in the frame loop; renders nothing while no trace is open.
import * as React from 'react'
import * as THREE from 'three'
import { useFrame } from '@react-three/fiber'
import { flightAt } from '@/arena/ballistics'
import { ARENA_TOKENS, tokenMs } from '@/arena/timing'
import { burstLife, seedOf, VoxelFx } from '@/arena/voxelFx'
import { enuToScene } from '@/stage/math'
import { stageQuality } from '@/stage/scene/gpu'
import { token } from '@/stage/tokens'
import { motionReduced } from '@/ui/motion/tier'
import { impactsAt, shotsAt } from './trace'
import { useTraceReplay } from './traceReplay'

const MAX_BULLETS = 256
const tmpP = new THREE.Vector3()
const tmpV = new THREE.Vector3()

function toScene(p: readonly number[], out: THREE.Vector3): THREE.Vector3 {
  const [x, y, z] = enuToScene(p[0], p[1], p[2])
  return out.set(x, y, z)
}

class TraceFx {
  readonly vox = new VoxelFx()

  constructor(private readonly light: boolean) {}

  update(cam: THREE.Camera, heightPx: number): void {
    const { trace, t } = useTraceReplay.getState()
    this.vox.begin(cam, heightPx)
    if (trace) {
      const lead = trace.aircraft[0]?.id
      const tailS = this.light ? 0 : tokenMs(ARENA_TOKENS.trail) / 1000
      let n = 0
      for (const s of shotsAt(trace.shots, t)) {
        if (n >= MAX_BULLETS) break
        const f = flightAt(s, t)
        if (f.done) continue
        this.vox.tracer(toScene(f.p, tmpP), toScene(f.v, tmpV), s.owner === lead, tailS)
        n++
      }
      const coreS = tokenMs(ARENA_TOKENS.core) / 1000
      const lives = { burst: tokenMs(ARENA_TOKENS.burst) / 1000, down: (2 * tokenMs(ARENA_TOKENS.downDebris)) / 1000 }
      const still = motionReduced()
      for (const m of impactsAt(trace, t, (k) => burstLife(k, lives))) {
        const [x, y, z] = enuToScene(m.p[0], m.p[1], m.p[2])
        const kind = still ? 'still' : m.kind
        this.vox.burst({ p: [x, y, z], t0: m.t, seed: seedOf(m.key), kind }, t - m.t, coreS, burstLife(kind, lives))
      }
    }
    this.vox.end()
  }
}

function Effects() {
  const [view] = React.useState(() => new TraceFx(stageQuality().light))
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
      <Effects />
    </group>
  )
}
