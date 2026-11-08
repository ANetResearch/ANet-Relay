// Voxel ring body (07 §2, §5): a ring is a solid torus with major radius `r` and tube radius RING_TUBE (0.03 m), the
// same volume the backend's collision sweep uses (backend/relay/sim/collide.py). The stage draws it as a closed
// chain of cubes along the centre line, each cube 2 x tube on a side and turned along the ring, so the body reads as
// the voxel tube it is. ringVoxels is pure (course.test.ts); voxelRingGeometry bakes the chain into one geometry
// (in the ring's own plane: x right, y up, normal +z), and `scale` > 1 gives the outline hull of the next ring.
import * as THREE from 'three'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'

export interface RingVoxel {
  x: number
  y: number
  /** turn about the ring normal so the cube runs along the ring */
  angle: number
}

/** Cubes along a ring of major radius `r`: enough that neighbours touch (edge 2 x tube), at least 12. */
export function ringVoxels(r: number, tube: number): { edge: number; cubes: RingVoxel[] } {
  const edge = 2 * tube
  const n = Math.max(12, Math.ceil((2 * Math.PI * r) / edge))
  const cubes: RingVoxel[] = []
  for (let i = 0; i < n; i++) {
    const a = (i / n) * Math.PI * 2
    cubes.push({ x: Math.cos(a) * r, y: Math.sin(a) * r, angle: a })
  }
  return { edge, cubes }
}

/** One geometry holding every cube of the ring; `scale` grows each cube in place (outline hull). */
export function voxelRingGeometry(r: number, tube: number, scale = 1): THREE.BufferGeometry {
  const { edge, cubes } = ringVoxels(r, tube)
  const m = new THREE.Matrix4()
  const q = new THREE.Quaternion()
  const z = new THREE.Vector3(0, 0, 1)
  const s = new THREE.Vector3(edge * scale, edge * scale, edge * scale)
  const parts = cubes.map((c) => {
    const g = new THREE.BoxGeometry(1, 1, 1)
    g.applyMatrix4(m.compose(new THREE.Vector3(c.x, c.y, 0), q.setFromAxisAngle(z, c.angle), s))
    return g
  })
  const merged = mergeGeometries(parts, false) ?? new THREE.BoxGeometry(edge, edge, edge)
  for (const p of parts) p.dispose()
  return merged
}
