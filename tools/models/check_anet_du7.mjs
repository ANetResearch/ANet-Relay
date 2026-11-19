// Structural check of the ANetDU7 model against what the stage loader relies on.
//
//   node tools/models/check_anet_du7.mjs [file.glb]     (default: web/public/models/anet_du7.glb)
//
// Exits 1 when a check fails. Reads the GLB directly (no three.js), so it also catches container mistakes.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const file = path.resolve(process.argv[2] ?? path.join(ROOT, 'web/public/models/anet_du7.glb'))
const buf = fs.readFileSync(file)
const failures = []
const check = (ok, msg) => { if (!ok) failures.push(msg) }

// container
check(buf.readUInt32LE(0) === 0x46546c67 && buf.readUInt32LE(4) === 2, 'not a glTF 2.0 binary')
check(buf.readUInt32LE(8) === buf.length, 'GLB length field does not match the file size')
const jsonLen = buf.readUInt32LE(12)
check(buf.readUInt32LE(16) === 0x4e4f534a, 'first chunk is not JSON')
const gltf = JSON.parse(buf.subarray(20, 20 + jsonLen).toString('utf8'))
const binStart = 20 + jsonLen + 8
check(buf.readUInt32LE(20 + jsonLen + 4) === 0x004e4942, 'second chunk is not BIN')
const bin = buf.subarray(binStart, binStart + buf.readUInt32LE(20 + jsonLen))

// forbidden content
for (const key of ['images', 'textures', 'samplers', 'cameras', 'animations', 'skins', 'extensionsUsed', 'extensionsRequired']) {
  check(!gltf[key] || gltf[key].length === 0, `file contains ${key}`)
}
check(gltf.asset?.version === '2.0', 'asset.version is not 2.0')

// materials: factors only
const mats = gltf.materials ?? []
check(mats.length >= 12 && mats.length <= 20, `material count ${mats.length} outside 12..20`)
for (const m of mats) {
  const pbr = m.pbrMetallicRoughness ?? {}
  check(!pbr.baseColorTexture && !pbr.metallicRoughnessTexture && !m.normalTexture && !m.occlusionTexture && !m.emissiveTexture, `material ${m.name} uses a texture`)
  check(!m.extensions, `material ${m.name} uses extensions`)
}

// accessors
const acc = gltf.accessors
const view = (a) => gltf.bufferViews[a.bufferView]
function readVec3(i) {
  const a = acc[i], v = view(a)
  const out = new Float32Array(a.count * 3)
  const stride = v.byteStride ?? 12
  for (let k = 0; k < a.count; k++) for (let c = 0; c < 3; c++) out[k * 3 + c] = bin.readFloatLE((v.byteOffset ?? 0) + (a.byteOffset ?? 0) + k * stride + c * 4)
  return out
}

// nodes
const nodes = gltf.nodes
const byName = new Map()
for (const [i, n] of nodes.entries()) {
  check(!byName.has(n.name), `duplicate node name ${n.name}`)
  byName.set(n.name, i)
  check(!n.rotation && !n.scale && !n.matrix, `node ${n.name} has rotation, scale or matrix (translation only is allowed)`)
}
const pivots = ['Gimbal_Yaw', 'Gimbal_Pitch', 'Gimbal_Camera', ...['FL', 'FR', 'RL', 'RR'].map((id) => `Propeller_${id}_ROTATABLE`)]
for (const n of nodes) if (/^(Gimbal_|Propeller_)/.test(n.name)) check(pivots.includes(n.name), `node ${n.name} uses a pivot prefix but is not a pivot`)
const parent = new Map()
for (const [i, n] of nodes.entries()) for (const c of n.children ?? []) parent.set(c, i)
const world = (i) => {
  const t = [0, 0, 0]
  for (let k = i; k !== undefined; k = parent.get(k)) { const tr = nodes[k].translation ?? [0, 0, 0]; t[0] += tr[0]; t[1] += tr[1]; t[2] += tr[2] }
  return t
}

let triangles = 0
const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity]
for (const [i, n] of nodes.entries()) {
  if (n.mesh === undefined) continue
  const mesh = gltf.meshes[n.mesh]
  const w = world(i)
  for (const p of mesh.primitives) {
    check(p.indices !== undefined, `${n.name}: primitive is not indexed`)
    check(p.attributes.NORMAL !== undefined, `${n.name}: primitive has no normals`)
    check((p.mode ?? 4) === 4, `${n.name}: primitive is not a triangle list`)
    triangles += acc[p.indices].count / 3
    const pa = acc[p.attributes.POSITION]
    check(Array.isArray(pa.min) && Array.isArray(pa.max), `${n.name}: POSITION has no min/max`)
    for (let c = 0; c < 3; c++) { min[c] = Math.min(min[c], pa.min[c] + w[c]); max[c] = Math.max(max[c], pa.max[c] + w[c]) }
  }
  const isPivot = /^Propeller_|^Gimbal_(Yaw|Pitch|Camera)$/.test(n.name)
  if (!isPivot) {
    const m = /^[A-Za-z0-9_]+__([A-Za-z0-9]+)$/.exec(n.name)
    check(m && mesh.primitives.length === 1 && mats[mesh.primitives[0].material]?.name === m[1], `static node ${n.name} is not <Part>__<material> with one primitive of that material`)
  }
}

// propellers
const M = 0.1768
const PROPS = { FL: [M, M], FR: [-M, M], RL: [M, -M], RR: [-M, -M] } // left = +X with nose +Z and up +Y
const propReport = []
for (const [id, [x, z]] of Object.entries(PROPS)) {
  const name = `Propeller_${id}_ROTATABLE`
  const i = byName.get(name)
  check(i !== undefined, `missing ${name}`)
  if (i === undefined) continue
  const n = nodes[i]
  const w = world(i)
  check(Math.abs(w[0] - x) < 1e-4 && Math.abs(w[2] - z) < 1e-4, `${name} at ${w} instead of (${x}, y, ${z})`)
  const mesh = gltf.meshes[n.mesh]
  check(mesh && mesh.primitives.length === 1, `${name} must be one mesh with one primitive`)
  if (!mesh) continue
  const pos = readVec3(mesh.primitives[0].attributes.POSITION)
  let r = 0, cx = 0, cz = 0
  for (let k = 0; k < pos.length; k += 3) { r = Math.max(r, Math.hypot(pos[k], pos[k + 2])); cx += pos[k]; cz += pos[k + 2] }
  cx /= pos.length / 3; cz /= pos.length / 3
  check(Math.hypot(cx, cz) < 0.002, `${name}: mesh is not centred on its axis (centroid ${cx.toFixed(4)}, ${cz.toFixed(4)})`)
  propReport.push(`${name.padEnd(24)} translation [${(n.translation ?? [0, 0, 0]).map((v) => v.toFixed(4)).join(', ')}]  tip radius ${r.toFixed(4)} m  material ${mats[mesh.primitives[0].material].name}`)
}

// gimbal chain
const gy = byName.get('Gimbal_Yaw'), gp = byName.get('Gimbal_Pitch'), gc = byName.get('Gimbal_Camera')
check(gy !== undefined && gp !== undefined && gc !== undefined, 'gimbal nodes missing')
check(parent.get(gp) === gy && parent.get(gc) === gp, 'gimbal hierarchy must be Gimbal_Yaw > Gimbal_Pitch > Gimbal_Camera')
check(gc === undefined || nodes[gc].mesh === undefined, 'Gimbal_Camera must be empty')
check((nodes[gy]?.children ?? []).some((c) => nodes[c].mesh !== undefined), 'Gimbal_Yaw has no yaw-stage meshes')
check((nodes[gp]?.children ?? []).some((c) => nodes[c].mesh !== undefined), 'Gimbal_Pitch has no pitch-stage meshes')
// worst-case ground clearance of the moving stages: pitch stage swept about the pitch axis, yaw stage about the yaw axis
let clearance = Infinity
if (gp !== undefined) {
  const po = world(gp)
  for (const c of nodes[gp].children ?? []) {
    if (nodes[c].mesh === undefined) continue
    const pos = readVec3(gltf.meshes[nodes[c].mesh].primitives[0].attributes.POSITION)
    const t = nodes[c].translation ?? [0, 0, 0]
    for (let k = 0; k < pos.length; k += 3) clearance = Math.min(clearance, po[1] - Math.hypot(pos[k + 1] + t[1], pos[k + 2] + t[2]))
  }
  for (const c of nodes[gy].children ?? []) {
    if (nodes[c].mesh === undefined) continue
    const pa = acc[gltf.meshes[nodes[c].mesh].primitives[0].attributes.POSITION]
    clearance = Math.min(clearance, world(c)[1] + pa.min[1])
  }
}

// envelope
const size = max.map((v, c) => v - min[c])
check(Math.abs(size[0] - 0.684) <= 0.002 && Math.abs(size[2] - 0.684) <= 0.002, `X/Z extent ${size[0].toFixed(4)} x ${size[2].toFixed(4)} not within 2 mm of 0.684`)
check(Math.abs(size[1] - 0.211) <= 0.002, `height ${size[1].toFixed(4)} not within 2 mm of 0.211`)
check(Math.abs(min[1]) < 1e-4, `lowest point at y = ${min[1]} instead of 0`)
check(Math.abs(min[0] + max[0]) < 0.002 && Math.abs(min[2] + max[2]) < 0.002, 'model is not centred in X/Z')
check(triangles <= 80000, `${triangles} triangles (budget 80000)`)
check(buf.length < 2.5 * 1024 * 1024, `${buf.length} bytes (budget 2.5 MB)`)
check(clearance > 0.005, `gimbal ground clearance ${clearance.toFixed(4)} m`)

const fmt = (v) => `[${v.map((x) => x.toFixed(4)).join(', ')}]`
console.log(`${path.relative(ROOT, file)}`)
console.log(`  size ${(buf.length / 1024).toFixed(1)} KiB, ${triangles} triangles, ${mats.length} materials, ${gltf.meshes.length} meshes, ${nodes.length} nodes`)
console.log(`  bounds min ${fmt(min)} max ${fmt(max)} size ${fmt(size)}`)
for (const line of propReport) console.log(`  ${line}`)
for (const name of ['Gimbal_Yaw', 'Gimbal_Pitch', 'Gimbal_Camera']) {
  const i = byName.get(name)
  if (i !== undefined) console.log(`  ${name.padEnd(24)} translation ${fmt(nodes[i].translation ?? [0, 0, 0])}  world ${fmt(world(i))}`)
}
console.log(`  gimbal worst-case ground clearance ${(clearance * 1000).toFixed(1)} mm (any yaw, any pitch)`)
console.log(`  materials: ${mats.map((m) => m.name).join(', ')}`)
if (failures.length) {
  for (const f of failures) console.error(`FAIL ${f}`)
  process.exit(1)
}
console.log('  all checks passed')
