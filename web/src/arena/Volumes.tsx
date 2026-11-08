// Collision-volume layer of the stage (07 §2, §5; layer "Collision volumes", default off): a translucent g500
// wireframe sphere of CRAFT_RADIUS around every simulator (local, remote and evaluation-trace aircraft), and inside
// it the bullet hit sphere (05 §7.4) as three dashed great circles, so the two volumes are told apart. Real aircraft
// never collide with remote aircraft or bullets and get no volume. Meshes are pooled; the frame loop places them
// from the smoothed stage state (live.state) and the remote bodies of the arena layer. No motion of its own.
import * as React from 'react'
import * as THREE from 'three'
import { useFrame } from '@react-three/fiber'
import { live } from '@/stage/live'
import { useStage } from '@/stage/store'
import { token } from '@/stage/tokens'
import { CRAFT_RADIUS, HIT_RADIUS } from './collide'

const POOL = 32

/** Scene positions of the bodies that carry a collision volume this frame. */
export function volumeBodies(remote: ReadonlyMap<string, THREE.Vector3> | null, out: THREE.Vector3[] = []): THREE.Vector3[] {
  out.length = 0
  for (const [id, st] of live.state) {
    if (!st.ready || st.kind === 'slot') continue
    if (!live.targets.get(id)?.sim) continue
    out.push(st.pos)
  }
  if (remote) for (const p of remote.values()) out.push(p)
  return out
}

class VolumeView {
  readonly root = new THREE.Group()
  private readonly items: THREE.Group[] = []
  private readonly sphereGeo = new THREE.SphereGeometry(CRAFT_RADIUS, 18, 10)
  private readonly circleGeo: THREE.BufferGeometry
  private readonly sphereMat = new THREE.MeshBasicMaterial({ color: token('g500').color, wireframe: true, transparent: true, opacity: 0.45, depthWrite: false, toneMapped: false })
  private readonly hitMat = new THREE.LineDashedMaterial({ color: token('g300').color, dashSize: 0.025, gapSize: 0.018, transparent: true, opacity: 0.85, toneMapped: false })
  private readonly bodies: THREE.Vector3[] = []

  constructor() {
    const pts: THREE.Vector3[] = []
    for (let i = 0; i < 48; i++) {
      const a = (i / 48) * Math.PI * 2
      pts.push(new THREE.Vector3(Math.cos(a) * HIT_RADIUS, Math.sin(a) * HIT_RADIUS, 0))
    }
    this.circleGeo = new THREE.BufferGeometry().setFromPoints(pts)
    for (let i = 0; i < POOL; i++) {
      const g = new THREE.Group()
      const sphere = new THREE.Mesh(this.sphereGeo, this.sphereMat)
      sphere.raycast = () => {}
      g.add(sphere)
      for (const rot of [[0, 0, 0], [Math.PI / 2, 0, 0], [0, Math.PI / 2, 0]] as const) {
        const ring = new THREE.LineLoop(this.circleGeo, this.hitMat)
        ring.rotation.set(rot[0], rot[1], rot[2])
        ring.computeLineDistances()
        ring.raycast = () => {}
        g.add(ring)
      }
      g.visible = false
      this.items.push(g)
      this.root.add(g)
    }
  }

  frame(remote: ReadonlyMap<string, THREE.Vector3> | null): void {
    const bodies = volumeBodies(remote, this.bodies)
    this.items.forEach((g, i) => {
      const p = bodies[i]
      g.visible = Boolean(p)
      if (p) g.position.copy(p)
    })
  }

  dispose(): void {
    this.sphereGeo.dispose()
    this.circleGeo.dispose()
    this.sphereMat.dispose()
    this.hitMat.dispose()
  }
}

function VolumeLayer({ remote }: { remote: ReadonlyMap<string, THREE.Vector3> | null }) {
  const [view] = React.useState(() => new VolumeView())
  React.useEffect(() => () => view.dispose(), [view])
  useFrame(() => view.frame(remote))
  return <primitive object={view.root} />
}

/** Mounted by ArenaLayer (live) and TraceLayer (replay); `remote` is the arena's remote bodies, if shown. */
export function Volumes({ remote }: { remote: ReadonlyMap<string, THREE.Vector3> | null }) {
  const on = useStage((s) => s.layers.volumes)
  return on ? <VolumeLayer remote={remote} /> : null
}
