// Caddx GoFilm 20 template (02 §6.2). Real size, never scaled or recoloured. The file faces +Z with +Y up; the
// airframe group's nose is -Z, so the template turns half a revolution about Y (setFromUnitVectors(+Z, -Z), which
// swaps left and right without mirroring the mesh). Static parts are merged per material (about 20 draw calls
// instead of 229 per aircraft, which is what keeps 16 aircraft above 45 fps); the four Propeller_*_ROTATABLE nodes
// stay separate pivots that spin about their local Y.
import * as THREE from 'three'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'

export const MODEL_URL = `${import.meta.env.BASE_URL}models/caddxfpv_gofilm20.glb`

const FILE_FRONT = new THREE.Vector3(0, 0, 1)
const NOSE = new THREE.Vector3(0, 0, -1)

export interface AirframeTemplate {
  root: THREE.Group
  /** distance from the body origin to the lowest point, so z = 0 puts the feet on the ground */
  lift: number
  /** nose tip distance ahead of the body origin (m) */
  noseAhead: number
}

/** userData flag on propeller pivots; value +1 spins counter-clockwise seen from above, -1 clockwise */
export const PROP_KEY = 'relayProp'

function signature(g: THREE.BufferGeometry): string {
  return `${g.index ? 'i' : 'n'}:${Object.keys(g.attributes).sort().join(',')}`
}

export function prepareTemplate(scene: THREE.Object3D): AirframeTemplate {
  scene.updateMatrixWorld(true)
  const groups = new Map<string, { material: THREE.Material; geos: THREE.BufferGeometry[] }>()
  const props: { mesh: THREE.Mesh; matrix: THREE.Matrix4 }[] = []
  scene.traverse((o) => {
    const mesh = o as THREE.Mesh
    if (!mesh.isMesh) return
    if (/^Propeller_.*_ROTATABLE/.test(mesh.name)) {
      props.push({ mesh, matrix: mesh.matrixWorld.clone() })
      return
    }
    const material = Array.isArray(mesh.material) ? mesh.material[0] : mesh.material
    const geo = mesh.geometry.clone().applyMatrix4(mesh.matrixWorld)
    for (const k of Object.keys(geo.attributes)) if (k !== 'position' && k !== 'normal' && k !== 'uv') geo.deleteAttribute(k)
    const key = `${material.uuid}|${signature(geo)}`
    let g = groups.get(key)
    if (!g) {
      g = { material, geos: [] }
      groups.set(key, g)
    }
    g.geos.push(geo)
  })

  const body = new THREE.Group()
  body.name = 'gofilm20'
  for (const { material, geos } of groups.values()) {
    const merged = geos.length === 1 ? geos[0] : mergeGeometries(geos, false)
    if (!merged) continue
    const m = new THREE.Mesh(merged, material)
    m.castShadow = true
    m.raycast = () => {}
    body.add(m)
  }
  for (const { mesh, matrix } of props) {
    const pivot = new THREE.Group()
    pivot.name = mesh.name
    const pos = new THREE.Vector3()
    const quat = new THREE.Quaternion()
    const scl = new THREE.Vector3()
    matrix.decompose(pos, quat, scl)
    pivot.position.copy(pos)
    pivot.quaternion.copy(quat)
    pivot.scale.copy(scl)
    const blade = new THREE.Mesh(mesh.geometry, mesh.material)
    blade.castShadow = true
    blade.raycast = () => {}
    pivot.add(blade)
    body.add(pivot)
  }

  const box = new THREE.Box3().setFromObject(body)
  body.quaternion.setFromUnitVectors(FILE_FRONT, NOSE)
  const root = new THREE.Group()
  root.add(body)
  const lift = -box.min.y
  body.position.y = lift
  // after the half turn the file's +Z extent becomes the nose distance along -Z
  const noseAhead = box.max.z
  markProps(root)
  return { root, lift, noseAhead }
}

/** Tags propeller pivots with their spin direction (quad X: front-right and rear-left turn counter-clockwise). */
function markProps(root: THREE.Group): void {
  root.updateMatrixWorld(true)
  const p = new THREE.Vector3()
  root.traverse((o) => {
    if (!/^Propeller_.*_ROTATABLE/.test(o.name) || !(o as THREE.Group).isGroup) return
    o.getWorldPosition(p)
    o.userData[PROP_KEY] = p.x * p.z < 0 ? 1 : -1
  })
}

/** Procedural stand-in used only when the model fails to load: an X frame with four ducts at the same size. */
export function fallbackTemplate(color: THREE.Color): AirframeTemplate {
  const mat = new THREE.MeshStandardMaterial({ color, metalness: 0.2, roughness: 0.6 })
  const root = new THREE.Group()
  const body = new THREE.Group()
  const arm = new THREE.BoxGeometry(0.012, 0.006, 0.12)
  for (const a of [Math.PI / 4, -Math.PI / 4]) {
    const m = new THREE.Mesh(arm, mat)
    m.rotation.y = a
    body.add(m)
  }
  const duct = new THREE.TorusGeometry(0.028, 0.003, 6, 24)
  const blade = new THREE.BoxGeometry(0.05, 0.002, 0.006)
  for (const [x, z] of [[0.031, -0.035], [-0.031, -0.035], [0.031, 0.035], [-0.031, 0.035]]) {
    const d = new THREE.Mesh(duct, mat)
    d.rotation.x = Math.PI / 2
    d.position.set(x, 0.004, z)
    body.add(d)
    const pivot = new THREE.Group()
    pivot.name = `Propeller_${x > 0 ? 'R' : 'L'}${z < 0 ? 'F' : 'R'}_ROTATABLE`
    pivot.position.set(x, 0.006, z)
    pivot.add(new THREE.Mesh(blade, mat))
    body.add(pivot)
  }
  const canopy = new THREE.Mesh(new THREE.BoxGeometry(0.03, 0.02, 0.05), mat)
  canopy.position.y = 0.01
  body.add(canopy)
  body.traverse((o) => {
    const m = o as THREE.Mesh
    if (m.isMesh) {
      m.castShadow = true
      m.raycast = () => {}
    }
  })
  body.position.y = 0.012
  root.add(body)
  markProps(root)
  return { root, lift: 0.012, noseAhead: 0.06 }
}

/** Per-aircraft instance sharing geometry and materials with the template. */
export function instantiate(t: AirframeTemplate): { root: THREE.Group; props: THREE.Object3D[] } {
  const root = t.root.clone(true)
  const props: THREE.Object3D[] = []
  root.traverse((o) => {
    if (o.userData[PROP_KEY]) props.push(o)
  })
  return { root, props }
}
