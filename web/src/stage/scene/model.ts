// Airframe templates (02 §6.2, 12 §5.5): one per model (stage/models.ts), real size, never scaled or recoloured.
// The files face +Z with +Y up; the airframe group's nose is -Z, so a template turns half a revolution about Y
// (setFromUnitVectors(+Z, -Z), which swaps left and right without mirroring the mesh). Static parts are merged per
// material (about 20 draw calls instead of hundreds per aircraft, which is what keeps 16 aircraft above 45 fps).
// Articulated nodes stay separate pivots, each with its own meshes merged per material in its local frame:
//   Propeller_*_ROTATABLE   spins about its local Y
//   Gimbal_Yaw              turns about its local Y      (ANetDU7)
//   Gimbal_Pitch            tilts about its local X      (child of Gimbal_Yaw)
//   Gimbal_Camera           empty, optical centre; looks along local +Z with +Y up (child of Gimbal_Pitch)
// A missing or broken file falls back to a procedural airframe of the same size with the same node names.
import * as THREE from 'three'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'
import { MODELS, modelUrl, type ModelId } from '../models'

/** The micro's file (the LAN arena's remote aircraft are micros) */
export const MODEL_URL = modelUrl('micro')

const FILE_FRONT = new THREE.Vector3(0, 0, 1)
const NOSE = new THREE.Vector3(0, 0, -1)

export interface AirframeTemplate {
  model: ModelId
  root: THREE.Group
  /** height of the attitude pivot (the body centre) above the feet; the template's root has its feet at y = 0 */
  lift: number
  /** nose tip distance ahead of the body origin (m) */
  noseAhead: number
  /** true for the procedural stand-in */
  fallback: boolean
}

/** userData flag on propeller pivots; value +1 spins counter-clockwise seen from above, -1 clockwise */
export const PROP_KEY = 'relayProp'
/** userData of articulated pivots: their rest orientation, which the stage composes the joint angle onto */
export const REST_KEY = 'relayRest'

/** The rest orientation of an articulated node (kept in userData as [x, y, z, w]: Object3D.clone copies userData
 * through JSON, so a Quaternion would come back as a plain object). */
export function restQuaternion(o: THREE.Object3D, out: THREE.Quaternion): THREE.Quaternion {
  const r = o.userData[REST_KEY] as unknown
  return Array.isArray(r) && r.length === 4 && r.every((n) => typeof n === 'number' && Number.isFinite(n)) ? out.fromArray(r as number[]) : out.identity()
}

export const GIMBAL_YAW = 'Gimbal_Yaw'
export const GIMBAL_PITCH = 'Gimbal_Pitch'
export const GIMBAL_CAMERA = 'Gimbal_Camera'
const PROP_RE = /^Propeller_.*_ROTATABLE/
const isArticulated = (name: string) => PROP_RE.test(name) || name === GIMBAL_YAW || name === GIMBAL_PITCH || name === GIMBAL_CAMERA

function signature(g: THREE.BufferGeometry): string {
  return `${g.index ? 'i' : 'n'}:${Object.keys(g.attributes).sort().join(',')}`
}

/** The nearest articulated node at or above `o` (below `stop`), or null for the static body. */
function ownerOf(o: THREE.Object3D, stop: THREE.Object3D): THREE.Object3D | null {
  for (let p: THREE.Object3D | null = o; p && p !== stop; p = p.parent) if (isArticulated(p.name)) return p
  return null
}

export function prepareTemplate(scene: THREE.Object3D, model: ModelId = 'micro'): AirframeTemplate {
  scene.updateMatrixWorld(true)
  const inv = new Map<THREE.Object3D | null, THREE.Matrix4>([[null, new THREE.Matrix4()]])
  const nodes: THREE.Object3D[] = []
  scene.traverse((o) => {
    if (o !== scene && isArticulated(o.name) && !nodes.some((n) => n.name === o.name)) {
      nodes.push(o)
      inv.set(o, o.matrixWorld.clone().invert())
    }
  })
  // meshes per owner and material; geometry in the owner's frame
  const groups = new Map<THREE.Object3D | null, Map<string, { material: THREE.Material; geos: THREE.BufferGeometry[]; own: THREE.Mesh | null }>>()
  scene.traverse((o) => {
    const mesh = o as THREE.Mesh
    if (!mesh.isMesh) return
    const owner = ownerOf(mesh, scene)
    if (owner && !inv.has(owner)) return // a duplicate node name: ignore its meshes
    const material = Array.isArray(mesh.material) ? mesh.material[0] : mesh.material
    let byMat = groups.get(owner)
    if (!byMat) {
      byMat = new Map()
      groups.set(owner, byMat)
    }
    if (owner === mesh) {
      // a propeller that is its own mesh keeps its geometry untouched (identity in its own frame)
      byMat.set(`own|${mesh.uuid}`, { material, geos: [mesh.geometry], own: mesh })
      return
    }
    const local = new THREE.Matrix4().multiplyMatrices(inv.get(owner)!, mesh.matrixWorld)
    const geo = mesh.geometry.clone().applyMatrix4(local)
    for (const k of Object.keys(geo.attributes)) if (k !== 'position' && k !== 'normal' && k !== 'uv') geo.deleteAttribute(k)
    const key = `${material.uuid}|${signature(geo)}`
    let g = byMat.get(key)
    if (!g) {
      g = { material, geos: [], own: null }
      byMat.set(key, g)
    }
    g.geos.push(geo)
  })

  const meshesOf = (owner: THREE.Object3D | null): THREE.Mesh[] => {
    const out: THREE.Mesh[] = []
    for (const { material, geos, own } of groups.get(owner)?.values() ?? []) {
      const merged = own ? own.geometry : geos.length === 1 ? geos[0] : mergeGeometries(geos, false)
      if (!merged) continue
      const m = new THREE.Mesh(merged, own ? own.material : material)
      m.castShadow = true
      m.raycast = () => {}
      out.push(m)
    }
    return out
  }

  const body = new THREE.Group()
  body.name = model === 'micro' ? 'gofilm20' : model
  for (const m of meshesOf(null)) body.add(m)
  // pivots in traversal order (parents first), each placed relative to its articulated parent
  const pivots = new Map<THREE.Object3D, THREE.Group>()
  for (const n of nodes) {
    const parentNode = n.parent ? ownerOf(n.parent, scene) : null
    const parent = parentNode ? pivots.get(parentNode) ?? body : body
    const rel = new THREE.Matrix4().multiplyMatrices(inv.get(parentNode && pivots.has(parentNode) ? parentNode : null)!, n.matrixWorld)
    const pivot = new THREE.Group()
    pivot.name = n.name
    rel.decompose(pivot.position, pivot.quaternion, pivot.scale)
    pivot.userData[REST_KEY] = pivot.quaternion.toArray()
    for (const m of meshesOf(n)) pivot.add(m)
    parent.add(pivot)
    pivots.set(n, pivot)
  }

  const box = new THREE.Box3().setFromObject(body)
  body.quaternion.setFromUnitVectors(FILE_FRONT, NOSE)
  const root = new THREE.Group()
  root.add(body)
  // the feet on the ground; the attitude pivot at the file origin when it lies inside the airframe (GoFilm 20:
  // the body centre), else at half height (ANetDU7: the origin is at the feet)
  body.position.y = -box.min.y
  const lift = box.min.y < -0.002 ? -box.min.y : (box.max.y - box.min.y) / 2
  // after the half turn the file's +Z extent becomes the nose distance along -Z
  const noseAhead = box.max.z
  markProps(root)
  return { model, root, lift, noseAhead, fallback: false }
}

/** Tags propeller pivots with their spin direction (quad X: front-right and rear-left turn counter-clockwise). */
function markProps(root: THREE.Group): void {
  root.updateMatrixWorld(true)
  const p = new THREE.Vector3()
  root.traverse((o) => {
    if (!PROP_RE.test(o.name) || !(o as THREE.Group).isGroup) return
    o.getWorldPosition(p)
    o.userData[PROP_KEY] = p.x * p.z < 0 ? 1 : -1
  })
}

function finish(body: THREE.Group, model: ModelId, lift: number, noseAhead: number): AirframeTemplate {
  body.traverse((o) => {
    const m = o as THREE.Mesh
    if (m.isMesh) {
      m.castShadow = true
      m.raycast = () => {}
    }
    if (isArticulated(o.name)) o.userData[REST_KEY] = o.quaternion.toArray()
  })
  body.position.y = lift
  const root = new THREE.Group()
  root.add(body)
  markProps(root)
  return { model, root, lift, noseAhead, fallback: true }
}

/** Procedural micro used only when its model fails to load: an X frame with four ducts at the same size. */
function fallbackMicro(mat: THREE.Material): AirframeTemplate {
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
  return finish(body, 'micro', 0.012, 0.06)
}

/**
 * Procedural ANetDU7 at real size (684 x 684 x 211 mm) in the stage's own frame (nose -Z, right +X, feet at
 * -lift): the octagonal hoop, two arches, four arms with 10-inch propellers, the deck, battery and the gimbal pod
 * under the nose with the same Gimbal_Yaw > Gimbal_Pitch > Gimbal_Camera chain as the file.
 */
function fallbackDu7(mat: THREE.Material, dark: THREE.Material): AirframeTemplate {
  const body = new THREE.Group()
  const lift = 0.1 // body origin above the feet; the hoop sits at 0.13 m above the feet
  const y = (h: number) => h - lift
  // octagonal hoop: straight sides at +-0.342, chamfers on the diagonals
  const pts: THREE.Vector3[] = []
  const s = 0.342
  const c = 0.2
  for (const [x, z] of [[c, -s], [s, -c], [s, c], [c, s], [-c, s], [-s, c], [-s, -c], [-c, -s]]) pts.push(new THREE.Vector3(x, y(0.13), z))
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i]
    const b = pts[(i + 1) % pts.length]
    const len = a.distanceTo(b)
    const seg = new THREE.Mesh(new THREE.BoxGeometry(0.004, 0.013, len), mat)
    seg.position.copy(a).add(b).multiplyScalar(0.5)
    seg.lookAt(b.x, seg.position.y, b.z)
    body.add(seg)
  }
  // arches over the top, diagonal
  for (const a of [Math.PI / 4, -Math.PI / 4]) {
    const arch = new THREE.Mesh(new THREE.TorusGeometry(0.4, 0.004, 6, 32, Math.PI), mat)
    arch.scale.set(1, 0.2, 1)
    arch.rotation.y = a
    arch.position.y = y(0.13)
    body.add(arch)
  }
  // arms, motors and propellers on the 500 mm diagonal
  const r = 0.1768
  const arm = new THREE.BoxGeometry(0.016, 0.016, 0.21)
  for (const a of [Math.PI / 4, -Math.PI / 4]) {
    const m = new THREE.Mesh(arm, dark)
    m.rotation.y = a
    m.position.y = y(0.084)
    body.add(m)
    const m2 = m.clone()
    m2.rotation.y = a + Math.PI
    body.add(m2)
  }
  const motor = new THREE.CylinderGeometry(0.014, 0.014, 0.03, 16)
  const blade = new THREE.BoxGeometry(0.254, 0.003, 0.02)
  for (const [x, z] of [[r, -r], [-r, -r], [r, r], [-r, r]]) {
    const m = new THREE.Mesh(motor, dark)
    m.position.set(x, y(0.11), z)
    body.add(m)
    const pivot = new THREE.Group()
    pivot.name = `Propeller_${z < 0 ? 'F' : 'R'}${x > 0 ? 'R' : 'L'}_ROTATABLE`
    pivot.position.set(x, y(0.133), z)
    pivot.add(new THREE.Mesh(blade, mat))
    body.add(pivot)
  }
  // deck, compute and battery
  const deck = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.012, 0.2), dark)
  deck.position.y = y(0.09)
  const battery = new THREE.Mesh(new THREE.BoxGeometry(0.07, 0.05, 0.15), dark)
  battery.position.y = y(0.05)
  const lidar = new THREE.Mesh(new THREE.CylinderGeometry(0.033, 0.033, 0.06, 20), mat)
  lidar.position.set(0, y(0.135), -0.07)
  body.add(deck, battery, lidar)
  // landing pads
  const leg = new THREE.BoxGeometry(0.01, 0.09, 0.01)
  for (const [x, z] of [[0.12, -0.12], [-0.12, -0.12], [0.12, 0.12], [-0.12, 0.12]]) {
    const l = new THREE.Mesh(leg, dark)
    l.position.set(x, y(0.045), z)
    body.add(l)
  }
  // gimbal: yaw stage under the nose, pitch head, camera looking along the nose (local +Z after the half turn below)
  const yawNode = new THREE.Group()
  yawNode.name = GIMBAL_YAW
  yawNode.position.set(0, y(0.075), -0.106)
  // the file's gimbal frame looks along +Z; the stage's nose is -Z: turn the chain half a revolution so the
  // joint axes and the camera match the file exactly (yaw about local Y, pitch about local X, camera +Z)
  yawNode.quaternion.setFromUnitVectors(FILE_FRONT, NOSE)
  const yawShell = new THREE.Mesh(new THREE.CylinderGeometry(0.022, 0.022, 0.02, 20), dark)
  yawShell.position.y = -0.01
  yawNode.add(yawShell)
  const pitchNode = new THREE.Group()
  pitchNode.name = GIMBAL_PITCH
  pitchNode.position.set(0, -0.03, 0)
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.03, 20, 14), mat)
  const lens = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, 0.012, 16), dark)
  lens.rotation.x = Math.PI / 2
  lens.position.set(0, 0, 0.028)
  pitchNode.add(head, lens)
  const cam = new THREE.Group()
  cam.name = GIMBAL_CAMERA
  cam.position.set(0, 0.002, 0.032)
  pitchNode.add(cam)
  yawNode.add(pitchNode)
  body.add(yawNode)
  return finish(body, 'anet-du7', lift, 0.36)
}

/** Procedural stand-in used only when a model fails to load, at the model's size and with its node names. */
export function fallbackTemplate(color: THREE.Color, model: ModelId = 'micro'): AirframeTemplate {
  const mat = new THREE.MeshStandardMaterial({ color, metalness: 0.2, roughness: 0.6 })
  if (model === 'micro') return fallbackMicro(mat)
  const dark = new THREE.MeshStandardMaterial({ color: color.clone().multiplyScalar(0.35), metalness: 0.3, roughness: 0.5 })
  return fallbackDu7(mat, dark)
}

/** Handles of one instance: the spinning propellers and, for a model with a pod, the gimbal joints. */
export interface Instance {
  root: THREE.Group
  props: THREE.Object3D[]
  gimbal: { yaw: THREE.Object3D; pitch: THREE.Object3D; camera: THREE.Object3D } | null
}

/** Per-aircraft instance sharing geometry and materials with the template. */
export function instantiate(t: AirframeTemplate): Instance {
  const root = t.root.clone(true)
  const props: THREE.Object3D[] = []
  root.traverse((o) => {
    if (o.userData[PROP_KEY]) props.push(o)
  })
  const yaw = root.getObjectByName(GIMBAL_YAW)
  const pitch = root.getObjectByName(GIMBAL_PITCH)
  const camera = root.getObjectByName(GIMBAL_CAMERA)
  return { root, props, gimbal: yaw && pitch && camera && MODELS[t.model].gimbal ? { yaw, pitch, camera } : null }
}
