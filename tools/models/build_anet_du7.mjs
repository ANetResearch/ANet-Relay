// Procedural model of the ANetDU7 (YiX Tangyuan U7, 汤圆 U7) quadrotor for the ANet Relay 3D stage.
//
//   node tools/models/build_anet_du7.mjs [--out <file.glb>]     (default: web/public/models/anet_du7.glb)
//
// Deterministic: the same script and three.js version produce a byte-identical file. Uses three 0.186 from
// web/node_modules for geometry only; the GLB container is written here (core glTF 2.0, no extensions, no textures).
//
// CONVENTIONS
//   Units metres, real size. +Y up, nose along +Z. Origin at the aircraft centre in X/Z, Y = 0 at the bottom of the
//   landing pads. Right-handed frame: for a pilot behind the aircraft looking along the nose, right = nose x up =
//   (0,0,1) x (0,1,0) = (-1,0,0), so the pilot's LEFT is +X and RIGHT is -X.
//   Propellers (quad X, 500 mm motor-to-motor diagonal, motors at +-0.1768 m in X and Z):
//     Propeller_FL_ROTATABLE (+X,+Z) clockwise seen from above      Propeller_FR_ROTATABLE (-X,+Z) counter-clockwise
//     Propeller_RL_ROTATABLE (+X,-Z) counter-clockwise              Propeller_RR_ROTATABLE (-X,-Z) clockwise
//   Each is one mesh, one material, origin on its axis at the hub centre, spin axis local +Y, node = translation.
//   Gimbal: Gimbal_Yaw (spins about local Y, origin on the yaw axis) > Gimbal_Pitch (tilts about local X, origin on
//   the pitch axis) > Gimbal_Camera (empty, optical centre of the zoom lens, looks along local +Z, +Y up at zero).
//   All three are translation-only at rest; meshes of each stage are children of its node (GimbalYawStage__*,
//   GimbalHead__*). Right-hand angles: +yaw turns the camera from the nose towards +X (left), +pitch looks down.
//   Every other node is static and named <Part>__<material>; only the three pivots start with "Gimbal_" and only
//   the four propellers with "Propeller_".
//
// SOURCES
//   Dimensions follow the airframe CAD export in vehicles/anet-du7/onboard/new_u7_src/yydrone_description/meshes/
//   yix.stl (214 820 triangles, metres, Z up, nose +Y; measured with a component split, nothing is copied): 684 mm
//   octagonal hoop (13 x 4 mm strip, straight sides +-342 mm, 45-degree chamfers 412 mm out on the diagonals) at
//   130 mm, two diagonal arches meeting in a clamp over the centre, diagonal lower struts with pads 170 mm out,
//   16 mm arms at 84 mm, 28 mm motors on printed mounts, 10-inch props at about 138 mm, MID360 tilted about 40
//   degrees forward at the front of the top deck, Jetson on an X-braced stand behind it, battery bracket below.
//   The CAD arch clamp reaches 230 mm; the product sheet gives 684 x 684 x 211 mm, so the arches are flattened to
//   end at 211 mm and everything below them keeps the CAD heights. Colours and the white printed parts follow the
//   product photo (assets/tangyuan-u7.jpg) and the poster. The EO/IR gimbal (zoom, wide, thermal, laser
//   rangefinder) is new in this version and has no CAD; it hangs in front of the battery bracket with 24 mm of
//   ground clearance at any yaw and pitch.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const threeDir = path.join(ROOT, 'web/node_modules/three')
const THREE = await import(pathToFileURL(path.join(threeDir, 'build/three.module.js')).href)
const { RoundedBoxGeometry } = await import(pathToFileURL(path.join(threeDir, 'examples/jsm/geometries/RoundedBoxGeometry.js')).href)

const argOut = process.argv.indexOf('--out')
const OUT = argOut > 0 ? path.resolve(process.argv[argOut + 1]) : path.join(ROOT, 'web/public/models/anet_du7.glb')

const { Vector3, Vector2, Matrix4, Quaternion, BufferGeometry, Float32BufferAttribute } = THREE
const DEG = Math.PI / 180
const v3 = (x, y, z) => new Vector3(x, y, z)

// ---------------------------------------------------------------------------------------------------------------
// Materials (PBR metallic-roughness factors only). Colours are sRGB hex here, converted to linear for glTF.
// The stage has no environment map: a hemisphere light (g200 over g950) and one key light, ACES at exposure 1.35.
// Metals therefore keep moderate metalness so they are not black outside the key highlight, and the carbon is a
// lifted graphite with a glossy specular so the cage reads against the g950 floor.
const MATERIALS = {
  carbon: { color: '#4b5058', metal: 0.35, rough: 0.24 }, // tubes, hoop, arches: lacquered carbon
  carbonPlate: { color: '#45484e', metal: 0.15, rough: 0.5 }, // cut plates, matte
  white: { color: '#e6e6e2', metal: 0, rough: 0.58 }, // printed parts
  blackPlastic: { color: '#1e2024', metal: 0, rough: 0.5 },
  rubber: { color: '#141516', metal: 0, rough: 0.9 },
  aluminium: { color: '#c3c7cc', metal: 0.7, rough: 0.3 }, // screws, standoffs, shafts, ports, X7+ case
  anodisedDark: { color: '#2c2f35', metal: 0.55, rough: 0.36 }, // motor bells, heatsink, gimbal drives
  anodisedRed: { color: '#a3241d', metal: 0.45, rough: 0.34 }, // motor caps, battery lead
  prop: { color: '#b4b8bd', metal: 0, rough: 0.42 },
  lidarBody: { color: '#d4d7db', metal: 0.4, rough: 0.36 }, // MID360S body, battery label
  lidarDome: { color: '#1442b0', metal: 0.2, rough: 0.12 }, // deep-blue glossy window
  podShell: { color: '#cdd0d4', metal: 0.2, rough: 0.4 }, // gimbal shell
  glass: { color: '#070a10', metal: 0.4, rough: 0.06 }, // lens glass
  lensCoat: { color: '#3c2b7a', metal: 0.6, rough: 0.12 }, // coating glint inside the lenses
  thermal: { color: '#2a2b22', metal: 0.6, rough: 0.14 }, // germanium window
  pcb: { color: '#18221f', metal: 0.1, rough: 0.55 },
  ledGreen: { color: '#1f6b3a', metal: 0, rough: 0.4, emissive: '#2bff7a' },
  ledRed: { color: '#6b1f1f', metal: 0, rough: 0.4, emissive: '#ff3a24' },
}

function srgbToLinear(c) {
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
}
function hexLinear(hex) {
  const n = parseInt(hex.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => +srgbToLinear(c / 255).toFixed(5))
}

// ---------------------------------------------------------------------------------------------------------------
// Geometry plumbing. Every builder yields a non-indexed BufferGeometry with position + normal; normals are either
// analytic (sweeps, three's round primitives) or creased: smooth across edges below the crease angle, hard above,
// so tubes and fillets shade round while machined edges and chamfers stay crisp.

/** non-indexed copy with only position (+ normal unless `crease` asks for recomputed creased normals) */
function soup(geo, crease) {
  const g = geo.index ? geo.toNonIndexed() : geo.clone()
  for (const k of Object.keys(g.attributes)) if (k !== 'position' && k !== 'normal') g.deleteAttribute(k)
  g.clearGroups()
  if (crease !== undefined || !g.attributes.normal) creaseNormals(g, crease ?? 40)
  return g
}

/** creased normals with corner-angle weighting; positions are matched to 1 micrometre */
function creaseNormals(g, deg) {
  const p = g.attributes.position.array
  const nt = p.length / 9
  const fn = new Float64Array(nt * 3)
  const ang = new Float64Array(nt * 3)
  const groups = new Map()
  const a = new Vector3(), b = new Vector3(), c = new Vector3(), e1 = new Vector3(), e2 = new Vector3(), n = new Vector3()
  for (let t = 0; t < nt; t++) {
    a.fromArray(p, t * 9); b.fromArray(p, t * 9 + 3); c.fromArray(p, t * 9 + 6)
    n.crossVectors(e1.subVectors(b, a), e2.subVectors(c, a))
    if (n.lengthSq() > 0) n.normalize()
    fn[t * 3] = n.x; fn[t * 3 + 1] = n.y; fn[t * 3 + 2] = n.z
    const corners = [[a, b, c], [b, c, a], [c, a, b]]
    for (let k = 0; k < 3; k++) {
      const [o, q, r] = corners[k]
      ang[t * 3 + k] = e1.subVectors(q, o).angleTo(e2.subVectors(r, o)) || 0
      const key = `${Math.round(o.x * 1e6)},${Math.round(o.y * 1e6)},${Math.round(o.z * 1e6)}`
      let list = groups.get(key)
      if (!list) groups.set(key, (list = []))
      list.push(t * 3 + k)
    }
  }
  const out = new Float32Array(p.length)
  const cosC = Math.cos(deg * DEG)
  for (const list of groups.values()) {
    for (const ci of list) {
      const t = (ci / 3) | 0
      let sx = 0, sy = 0, sz = 0
      for (const cj of list) {
        const u = (cj / 3) | 0
        const d = fn[t * 3] * fn[u * 3] + fn[t * 3 + 1] * fn[u * 3 + 1] + fn[t * 3 + 2] * fn[u * 3 + 2]
        if (d >= cosC) {
          const w = ang[cj]
          sx += fn[u * 3] * w; sy += fn[u * 3 + 1] * w; sz += fn[u * 3 + 2] * w
        }
      }
      const l = Math.hypot(sx, sy, sz) || 1
      out[ci * 3] = sx / l; out[ci * 3 + 1] = sy / l; out[ci * 3 + 2] = sz / l
    }
  }
  g.setAttribute('normal', new Float32BufferAttribute(out, 3))
  return g
}

function fromArrays(pos, nrm) {
  const g = new BufferGeometry()
  g.setAttribute('position', new Float32BufferAttribute(pos, 3))
  if (nrm) g.setAttribute('normal', new Float32BufferAttribute(nrm, 3))
  return g
}

function flipWinding(g) {
  for (const name of ['position', 'normal']) {
    const arr = g.attributes[name]?.array
    if (!arr) continue
    for (let i = 0; i < arr.length; i += 9) for (let k = 0; k < 3; k++) {
      const t = arr[i + 3 + k]; arr[i + 3 + k] = arr[i + 6 + k]; arr[i + 6 + k] = t
    }
  }
}

/** world transform helpers */
const M = {
  t: (x, y, z) => new Matrix4().makeTranslation(x, y, z),
  rx: (a) => new Matrix4().makeRotationX(a),
  ry: (a) => new Matrix4().makeRotationY(a),
  rz: (a) => new Matrix4().makeRotationZ(a),
  s: (x, y, z) => new Matrix4().makeScale(x, y, z),
  /** product of matrices, applied right to left (last argument first) */
  of: (...ms) => ms.reduce((acc, m) => acc.multiply(m), new Matrix4()),
  /** frame with local x, y, z axes given in world space, origin o */
  basis: (o, x, y, z) => new Matrix4().makeBasis(x, y, z).setPosition(o),
}

// buckets: one node per (stage, part, material)
const STAGES = { root: v3(0, 0, 0), yaw: null, pitch: null }
const buckets = new Map()
function add(part, mat, geo, m, stage = 'root') {
  if (!MATERIALS[mat]) throw new Error(`unknown material ${mat}`)
  const g = geo.clone()
  if (m) {
    g.applyMatrix4(m)
    if (m.determinant() < 0) flipWinding(g)
  }
  const key = `${stage}|${part}__${mat}`
  let bkt = buckets.get(key)
  if (!bkt) buckets.set(key, (bkt = { stage, name: `${part}__${mat}`, mat, geos: [] }))
  bkt.geos.push(g)
}

/** merge triangle soups, drop degenerate triangles, weld identical (position, normal) vertices into an index */
function weld(geos, offset) {
  const keyOf = new Map()
  const pos = [], nrm = [], idx = []
  const P = [new Vector3(), new Vector3(), new Vector3()], e1 = new Vector3(), e2 = new Vector3()
  for (const g of geos) {
    const p = g.attributes.position.array, n = g.attributes.normal.array
    for (let i = 0; i < p.length; i += 9) {
      for (let k = 0; k < 3; k++) P[k].set(p[i + k * 3] - offset.x, p[i + k * 3 + 1] - offset.y, p[i + k * 3 + 2] - offset.z)
      if (e1.subVectors(P[1], P[0]).cross(e2.subVectors(P[2], P[0])).lengthSq() < 1e-16) continue
      for (let k = 0; k < 3; k++) {
        const j = i + k * 3
        const x = P[k].x, y = P[k].y, z = P[k].z
        let nx = n[j], ny = n[j + 1], nz = n[j + 2]
        const l = Math.hypot(nx, ny, nz) || 1
        nx /= l; ny /= l; nz /= l
        const key = `${Math.round(x * 1e6)},${Math.round(y * 1e6)},${Math.round(z * 1e6)},${Math.round(nx * 1e3)},${Math.round(ny * 1e3)},${Math.round(nz * 1e3)}`
        let id = keyOf.get(key)
        if (id === undefined) {
          id = pos.length / 3
          keyOf.set(key, id)
          pos.push(x, y, z)
          nrm.push(nx, ny, nz)
        }
        idx.push(id)
      }
    }
  }
  return { pos: new Float32Array(pos), nrm: new Float32Array(nrm), idx }
}

// ---------------------------------------------------------------------------------------------------------------
// Primitive builders (all return soups positioned in a local frame)

/**
 * Box with bevelled edges, 44 triangles: every vertex keeps the exact normal of the face it belongs to, so the bevel
 * strips shade as rounded edges while the faces stay perfectly flat. seg > 1 uses three's RoundedBoxGeometry.
 */
function rbox(w, h, d, r, seg = 1) {
  r = Math.min(r, w / 2, h / 2, d / 2) * 0.999
  if (seg > 1) return soup(new RoundedBoxGeometry(w, h, d, seg, r))
  const half = [w / 2, h / 2, d / 2]
  const P = (k, sg) => sg.map((s, i) => s * (i === k ? half[i] : half[i] - r))
  const N = (k, sg) => [0, 1, 2].map((i) => (i === k ? sg[i] : 0))
  const pos = [], nrm = []
  const tri = (...vs) => {
    const [a, b, c] = vs.map((v) => new Vector3(...v.p))
    const n = new Vector3().crossVectors(b.clone().sub(a), c.clone().sub(a))
    const avg = vs.reduce((acc, v) => acc.add(new Vector3(...v.n)), new Vector3())
    if (n.dot(avg) < 0) vs = [vs[0], vs[2], vs[1]]
    for (const v of vs) { pos.push(...v.p); nrm.push(...v.n) }
  }
  const V = (k, sg) => ({ p: P(k, sg), n: N(k, sg) })
  const quad = (a, b, c, d) => { tri(a, b, c); tri(a, c, d) }
  for (let k = 0; k < 3; k++) for (const s of [-1, 1]) {
    const [i, j] = [0, 1, 2].filter((x) => x !== k)
    const sg = (si, sj) => { const g = [0, 0, 0]; g[k] = s; g[i] = si; g[j] = sj; return g }
    quad(V(k, sg(-1, -1)), V(k, sg(1, -1)), V(k, sg(1, 1)), V(k, sg(-1, 1)))
  }
  for (let k = 0; k < 3; k++) for (let l = k + 1; l < 3; l++) for (const sk of [-1, 1]) for (const sl of [-1, 1]) {
    const m = 3 - k - l
    const sg = (sm) => { const g = [0, 0, 0]; g[k] = sk; g[l] = sl; g[m] = sm; return g }
    quad(V(k, sg(-1)), V(k, sg(1)), V(l, sg(1)), V(l, sg(-1)))
  }
  for (const sx of [-1, 1]) for (const sy of [-1, 1]) for (const sz of [-1, 1]) tri(V(0, [sx, sy, sz]), V(1, [sx, sy, sz]), V(2, [sx, sy, sz]))
  return fromArrays(pos, nrm)
}
const box = (w, h, d) => soup(new THREE.BoxGeometry(w, h, d), 30)

/** cylinder along Y from y0 to y1 */
function cyl(r, y0, y1, seg = 16, { r1 = r, open = false } = {}) {
  const g = new THREE.CylinderGeometry(r1, r, y1 - y0, seg, 1, open)
  g.translate(0, (y0 + y1) / 2, 0)
  return soup(g, 50)
}

/** surface of revolution about Y from a profile [[r, y], ...] listed bottom to top (outer surface outward) */
function lathe(profile, seg = 24, crease = 40) {
  return soup(new THREE.LatheGeometry(profile.map(([r, y]) => new Vector2(r, y)), seg), crease)
}

/** plan shape in X/Z (points [x, z], counter-clockwise seen from above) extruded upward from y0 by h, edge bevel b */
function slab(points, y0, h, { bevel = 0, holes = [], curveSeg = 6, crease = 35 } = {}) {
  const shape = new THREE.Shape(points.map(([x, z]) => new Vector2(x, -z)))
  for (const hole of holes) shape.holes.push(new THREE.Path(hole.map(([x, z]) => new Vector2(x, -z))))
  const g = new THREE.ExtrudeGeometry(shape, {
    depth: h - 2 * bevel, bevelEnabled: bevel > 0, bevelThickness: bevel, bevelSize: bevel, bevelOffset: -bevel,
    bevelSegments: 1, curveSegments: curveSeg,
  })
  g.translate(0, 0, bevel)
  g.rotateX(-Math.PI / 2)
  g.translate(0, y0, 0)
  return soup(g, crease)
}

/** profile in the local (u, v) plane extruded along local w; returns soup with u->x, v->y, w->z */
function plate(points, w0, w1, { bevel = 0, holes = [], crease = 35 } = {}) {
  const shape = new THREE.Shape(points.map(([u, v]) => new Vector2(u, v)))
  for (const hole of holes) shape.holes.push(new THREE.Path(hole.map(([u, v]) => new Vector2(u, v))))
  const g = new THREE.ExtrudeGeometry(shape, {
    depth: w1 - w0 - 2 * bevel, bevelEnabled: bevel > 0, bevelThickness: bevel, bevelSize: bevel, bevelOffset: -bevel,
    bevelSegments: 1, curveSegments: 6,
  })
  g.translate(0, 0, w0 + bevel)
  return soup(g, crease)
}

/** rounded rectangle outline (counter-clockwise) centred at (cx, cy) */
function rrect(w, h, r, cx = 0, cy = 0, seg = 4) {
  const pts = []
  const corners = [[w / 2 - r, h / 2 - r, 0], [-w / 2 + r, h / 2 - r, 90], [-w / 2 + r, -h / 2 + r, 180], [w / 2 - r, -h / 2 + r, 270]]
  for (const [x, y, a0] of corners) for (let i = 0; i <= seg; i++) {
    const a = (a0 + (90 * i) / seg) * DEG
    pts.push([cx + x + r * Math.cos(a), cy + y + r * Math.sin(a)])
  }
  return pts
}
const circle = (r, cx = 0, cy = 0, seg = 16) => Array.from({ length: seg }, (_, i) => [cx + r * Math.cos((i / seg) * 2 * Math.PI), cy + r * Math.sin((i / seg) * 2 * Math.PI)])
const reversed = (pts) => pts.slice().reverse()

// Sweep sections: closed outlines in (a, b) with outward normals, counter-clockwise.
function secCircle(r, n = 12) {
  const pts = [], nrm = []
  for (let i = 0; i < n; i++) {
    const t = (i / n) * 2 * Math.PI
    pts.push([r * Math.cos(t), r * Math.sin(t)]); nrm.push([Math.cos(t), Math.sin(t)])
  }
  return { pts, nrm }
}
/** rounded rectangle, extent wa along a and wb along b, corner radius r */
function secRRect(wa, wb, r, seg = 3) {
  const pts = [], nrm = []
  const cs = [[wa / 2 - r, wb / 2 - r, 0], [-wa / 2 + r, wb / 2 - r, 90], [-wa / 2 + r, -wb / 2 + r, 180], [wa / 2 - r, -wb / 2 + r, 270]]
  for (const [x, y, a0] of cs) for (let i = 0; i <= seg; i++) {
    const t = (a0 + (90 * i) / seg) * DEG
    pts.push([x + r * Math.cos(t), y + r * Math.sin(t)]); nrm.push([Math.cos(t), Math.sin(t)])
  }
  return { pts, nrm }
}

/**
 * Sweep a section along a path (Vector3[]). The section's b axis follows `planeN` (normal of the plane the path
 * lies in) when given, otherwise a parallel-transported frame; a = b x tangent. Optional flat caps.
 */
function sweep(path, sec, { closed = false, planeN = null, caps = false } = {}) {
  const n = path.length
  const frames = []
  let prevB = null
  for (let i = 0; i < n; i++) {
    const prev = path[closed ? (i - 1 + n) % n : Math.max(i - 1, 0)]
    const next = path[closed ? (i + 1) % n : Math.min(i + 1, n - 1)]
    const T = next.clone().sub(prev).normalize()
    let B
    if (planeN) B = planeN.clone()
    else if (prevB) B = prevB.clone()
    else B = Math.abs(T.y) < 0.9 ? v3(0, 1, 0) : v3(1, 0, 0)
    B.sub(T.clone().multiplyScalar(B.dot(T))).normalize()
    const A = new Vector3().crossVectors(B, T).normalize()
    prevB = B
    frames.push({ P: path[i], A, B, T })
  }
  const ring = (f) => sec.pts.map(([a, b], k) => ({
    p: f.P.clone().addScaledVector(f.A, a).addScaledVector(f.B, b),
    n: f.A.clone().multiplyScalar(sec.nrm[k][0]).addScaledVector(f.B, sec.nrm[k][1]).normalize(),
  }))
  const rings = frames.map(ring)
  const pos = [], nrm = []
  const tri = (v0, v1, v2) => { for (const v of [v0, v1, v2]) { pos.push(v.p.x, v.p.y, v.p.z); nrm.push(v.n.x, v.n.y, v.n.z) } }
  const m = sec.pts.length
  const segs = closed ? n : n - 1
  for (let i = 0; i < segs; i++) {
    const r0 = rings[i], r1 = rings[(i + 1) % n]
    for (let k = 0; k < m; k++) {
      const k1 = (k + 1) % m
      tri(r0[k], r0[k1], r1[k1]); tri(r0[k], r1[k1], r1[k])
    }
  }
  if (caps && !closed) {
    for (const [i, dir] of [[0, -1], [n - 1, 1]]) {
      const f = frames[i], r = rings[i]
      const c = { p: f.P, n: f.T.clone().multiplyScalar(dir) }
      for (let k = 0; k < m; k++) {
        const a = { p: r[k].p, n: c.n }, b = { p: r[(k + 1) % m].p, n: c.n }
        if (dir > 0) tri(c, a, b); else tri(c, b, a)
      }
    }
  }
  return fromArrays(pos, nrm)
}

/** smooth path through control points (centripetal Catmull-Rom) */
function spline(points, samples, closed = false) {
  return new THREE.CatmullRomCurve3(points, closed, 'centripetal').getPoints(samples)
}

/** pan-head screw (local Y up, base at y = 0) */
const SCREW = lathe([[1, 0], [1, 0.45], [0.72, 1], [0.0001, 1]], 8, 50)
function screw(part, at, axis = v3(0, 1, 0), r = 0.0019, h = 0.0014, mat = 'aluminium', stage) {
  const q = new Quaternion().setFromUnitVectors(v3(0, 1, 0), axis.clone().normalize())
  add(part, mat, SCREW, new Matrix4().compose(at, q, v3(r, h, r)), stage)
}

// ---------------------------------------------------------------------------------------------------------------
// Layout constants (metres)
const MOTOR = 0.1768 // motor axes at (+-MOTOR, +-MOTOR)
const RM = MOTOR * Math.SQRT2 // 0.25 along the diagonal
const DIAG = [ // unit diagonals: FL (+X,+Z), FR (-X,+Z), RL (+X,-Z), RR (-X,-Z)
  { id: 'FL', d: v3(1, 0, 1).normalize(), spin: -1 },
  { id: 'FR', d: v3(-1, 0, 1).normalize(), spin: 1 },
  { id: 'RL', d: v3(1, 0, -1).normalize(), spin: 1 },
  { id: 'RR', d: v3(-1, 0, -1).normalize(), spin: -1 },
]
const UP = v3(0, 1, 0)
/** frame on a diagonal: local x = outward along the diagonal, y = up, z = x cross... (horizontal, right-handed) */
function diagFrame(d, r = 0, y = 0) {
  const zAxis = new Vector3().crossVectors(d, UP).normalize() // horizontal, perpendicular to d
  return M.basis(d.clone().multiplyScalar(r).setY(y), d.clone(), UP.clone(), zAxis)
}

const HOOP_Y = 0.1305 // hoop centreline height
const HOOP_OUT = 0.342 // outer half extent of the straight sides (684 mm overall)
const HOOP_W = 0.010, HOOP_H = 0.0072 // section: radial x vertical
const HOOP_A = HOOP_OUT - HOOP_W / 2 // centreline half extent
const HOOP_D = 0.4125 - HOOP_W / 2 // centreline distance of the chamfers along the diagonals
const CORNER_R = HOOP_D - 0.0125 // where arches and lower struts meet the corner brackets (0.395)
const PLATE_LO = 0.0705, PLATE_HI = 0.0945, PLATE_T = 0.002 // centre plates: bottom at 0.0705, top at 0.0945
const ARM_Y = 0.0835, ARM_R = 0.008
const MOUNT_TOP = PLATE_HI + PLATE_T // 0.0965, motor base
const PROP_Y = 0.1325 // propeller hub centre
const TOP = 0.211 // overall height

// ---------------------------------------------------------------------------------------------------------------
// Cage: octagonal hoop, two diagonal arches with a crossing clamp, corner brackets, lower struts and pads

function hoopPath() {
  // filleted octagon: straight sides at |x| or |z| = HOOP_A, chamfers at (|x| + |z|) / sqrt2 = HOOP_D
  const s = HOOP_D * Math.SQRT2 - HOOP_A
  const verts = [[HOOP_A, -s], [HOOP_A, s], [s, HOOP_A], [-s, HOOP_A], [-HOOP_A, s], [-HOOP_A, -s], [-s, -HOOP_A], [s, -HOOP_A]]
  const rf = 0.055, trim = rf * Math.tan(22.5 * DEG)
  const pts = []
  for (let i = 0; i < 8; i++) {
    const p = new Vector2(...verts[i]), prev = new Vector2(...verts[(i + 7) % 8]), next = new Vector2(...verts[(i + 1) % 8])
    const din = p.clone().sub(prev).normalize(), dout = next.clone().sub(p).normalize()
    const a = p.clone().addScaledVector(din, -trim), b = p.clone().addScaledVector(dout, trim)
    // arc from a to b (quadratic Bezier is close enough for a 45-degree fillet, sampled finely)
    for (let k = 0; k <= 6; k++) {
      const t = k / 6
      const q = a.clone().multiplyScalar((1 - t) ** 2).addScaledVector(p, 2 * t * (1 - t)).addScaledVector(b, t * t)
      pts.push(v3(q.x, HOOP_Y, q.y))
    }
    // straight run to the next fillet, subdivided so the shading stays even
    const nextA = next.clone().addScaledVector(next.clone().sub(p).normalize(), -trim)
    const runs = 1
    for (let k = 1; k < runs; k++) {
      const q = b.clone().lerp(nextA, k / runs)
      pts.push(v3(q.x, HOOP_Y, q.y))
    }
  }
  return pts
}

/** arch half-profile [r, y] from the corner bracket to the apex; the arch is mirrored through the centre */
const ARCH_Y = TOP - 0.0015 - 0.006 // tube centre at the apex, below the clamp top and its screw heads
const ARCH_PROFILE = [[CORNER_R, 0.124], [CORNER_R, 0.141], [CORNER_R - 0.006, 0.1565], [0.365, 0.1695], [0.31, 0.1825], [0.22, 0.1945], [0.11, 0.2015], [0, ARCH_Y]]
const ARCH_R = 0.0037
/** lower strut profile [r, y]: out of the corner bracket, down to the pad, level into the battery bracket */
const STRUT = [[CORNER_R + 0.001, 0.120], [CORNER_R - 0.002, 0.107], [0.33, 0.0805], [0.22, 0.0385], [0.175, 0.0195], [0.15, 0.0165], [0.115, 0.0205], [0.086, 0.0235]]
const strutYAt = (r) => STRUT[3][1] + ((r - STRUT[3][0]) / (STRUT[2][0] - STRUT[3][0])) * (STRUT[2][1] - STRUT[3][1])

function buildCage() {
  add('Cage_Hoop', 'carbon', sweep(hoopPath(), secRRect(HOOP_W, HOOP_H, 0.0028, 3), { closed: true, planeN: UP }))

  // arches along the two diagonals, one piece from corner to corner
  for (const d of [DIAG[0].d, DIAG[1].d]) {
    const ctrl = []
    for (const [r, y] of ARCH_PROFILE) ctrl.push(d.clone().multiplyScalar(r).setY(y))
    for (const [r, y] of ARCH_PROFILE.slice(0, -1).reverse()) ctrl.push(d.clone().multiplyScalar(-r).setY(y))
    const pts = spline(ctrl, 72)
    add('Cage_Arches', 'carbon', sweep(pts, secCircle(ARCH_R, 10), { planeN: new Vector3().crossVectors(d, UP).normalize() }))
  }
  // crossing clamp over the centre: two rounded bars along the diagonals, two screws on top
  const clampH = 0.012, clampTop = TOP - 0.0014
  for (const a of [45, -45]) add('Cage_Arches', 'blackPlastic', rbox(0.054, clampH, 0.0145, 0.0035), M.of(M.t(0, clampTop - clampH / 2, 0), M.ry(a * DEG)))
  add('Cage_Arches', 'blackPlastic', lathe([[0.0001, clampTop - clampH], [0.0125, clampTop - clampH], [0.0135, clampTop - clampH + 0.0012], [0.0135, clampTop - 0.0012], [0.0125, clampTop], [0.0001, clampTop]], 24, 40))
  for (const s of [-1, 1]) screw('Cage_Arches', v3(s * 0.007, clampTop, s * 0.007), UP, 0.0021, 0.0014)
  for (const s of [-1, 1]) screw('Cage_Arches', v3(s * 0.007, clampTop, -s * 0.007), UP, 0.0021, 0.0014)

  for (const { d } of DIAG) {
    const side = new Vector3().crossVectors(d, UP).normalize()
    // lower strut: corner bracket -> pad -> battery bracket side wall, a flat carbon bar on edge
    const at = (r, y) => d.clone().multiplyScalar(r).setY(y)
    const strut = spline(STRUT.map(([r, y]) => at(r, y)), 30)
    add('Cage_LowerStruts', 'carbon', sweep(strut, secRRect(0.0088, 0.0046, 0.0016, 2), { planeN: side }))

    // corner bracket in its local frame: x outward along the diagonal (0 on the hoop centreline), y up, z along the chamfer
    const F = diagFrame(d, HOOP_D, HOOP_Y)
    const part = 'Cage_CornerBrackets'
    // sleeve around the hoop: a big rounded triangle in plan, its long edge along the chamfer (as in the photo)
    const plan = [[0.0085, -0.042], [0.0085, 0.042], [-0.010, 0.035], [-0.036, 0.0095], [-0.036, -0.0095], [-0.010, -0.035]]
    add(part, 'white', slab(roundPoly(plan, 0.0045), -0.0062, 0.0124, { bevel: 0.0012 }), F)
    // spine: vertical post holding the arch above and the lower strut below
    const u0 = CORNER_R - HOOP_D // -0.0125
    add(part, 'white', rbox(0.014, 0.057, 0.0108, 0.0025), M.of(F, M.t(u0 - 0.0005, -0.0010, 0)))
    // gusset wings between spine and sleeve along the chamfer
    for (const s of [-1, 1]) {
      const g = plate([[0, 0], [0.024, 0], [0.006, 0.0115], [0, 0.0145]], -0.0016, 0.0016, { bevel: 0.0005 })
      add(part, 'white', g, M.of(F, M.t(u0 - 0.002, 0.0062, s * 0.0052), M.ry(s > 0 ? -90 * DEG : 90 * DEG)))
    }
    // fasteners: four on the sleeve, a cross bolt through the spine top and bottom
    for (const [u, w] of [[0.0025, 0.032], [0.0025, -0.032], [-0.025, 0.0090], [-0.025, -0.0090]]) screw(part, v3(u, 0.0062, w).applyMatrix4(F), UP, 0.0018, 0.0012)
    for (const y of [0.0215, -0.0235]) for (const s of [-1, 1]) {
      screw(part, v3(u0 - 0.0005, y, s * 0.0054).applyMatrix4(F), side.clone().multiplyScalar(s), 0.0017, 0.0011)
    }
    // printed brace (photo): a flat bar from the foot of the bracket spine level in to the motor mount, beside the strut
    const bo = side.clone().multiplyScalar(0.0035)
    add('Cage_Braces', 'white', sweep([at(CORNER_R - 0.003, 0.1068).add(bo), at(RM + 0.0155, ARM_Y + 0.002).add(bo)], secRRect(0.012, 0.0042, 0.0015, 2), { planeN: side, caps: true }))
    screw('Cage_Braces', at(RM + 0.026, ARM_Y + 0.0037).add(side.clone().multiplyScalar(0.0056)), side, 0.0016, 0.001)

    // landing pad: printed clamp on the strut and a rubber pad, bottom at y = 0
    const pad = d.clone().multiplyScalar(0.165)
    const PF = M.basis(pad, d.clone(), UP.clone(), side.clone())
    add('Landing_Pads', 'white', rbox(0.018, 0.0155, 0.0115, 0.003), M.of(PF, M.t(0, 0.0170, 0)))
    add('Landing_Pads', 'white', lathe([[0, 0.0068], [0.0066, 0.0068], [0.0066, 0.0098], [0.0045, 0.0105], [0, 0.0105]], 18), PF)
    add('Landing_Pads', 'rubber', lathe([[0, 0], [0.0042, 0], [0.0062, 0.0014], [0.0071, 0.0038], [0.0071, 0.0070], [0, 0.0070]], 20), PF)
    for (const s of [-1, 1]) screw('Landing_Pads', v3(0, 0.0175, s * 0.00575).applyMatrix4(PF), side.clone().multiplyScalar(s), 0.0016, 0.001)
  }
}

/** rounds the corners of a convex polygon [[x, z], ...] with radius r (keeps it counter-clockwise from above) */
function roundPoly(poly, r, seg = 4) {
  const out = []
  const n = poly.length
  for (let i = 0; i < n; i++) {
    const p = new Vector2(...poly[i]), a = new Vector2(...poly[(i + n - 1) % n]), b = new Vector2(...poly[(i + 1) % n])
    const da = a.clone().sub(p).normalize(), db = b.clone().sub(p).normalize()
    const half = Math.acos(Math.max(-1, Math.min(1, da.dot(db)))) / 2
    const t = r / Math.tan(half)
    const p0 = p.clone().addScaledVector(da, t), p1 = p.clone().addScaledVector(db, t)
    for (let k = 0; k <= seg; k++) {
      const s = k / seg
      const q = p0.clone().multiplyScalar((1 - s) ** 2).addScaledVector(p, 2 * s * (1 - s)).addScaledVector(p1, s * s)
      out.push([q.x, q.y])
    }
  }
  // slab() expects counter-clockwise seen from above with z mapped to -shape.y; flip if needed
  let area = 0
  for (let i = 0; i < out.length; i++) { const [x0, z0] = out[i], [x1, z1] = out[(i + 1) % out.length]; area += x0 * -z1 - x1 * -z0 }
  return area > 0 ? out : out.reverse()
}

// ---------------------------------------------------------------------------------------------------------------
// Centre frame, arms, motor mounts, motors

function buildFrame() {
  // centre plates: 150 mm square with the corners cut where the arms leave
  const c = 0.075, k = 0.085 * Math.SQRT2 - c
  const oct = roundPoly([[c, -k], [c, k], [k, c], [-k, c], [-c, k], [-c, -k], [-k, -c], [k, -c]], 0.004, 2)
  add('Chassis', 'carbonPlate', slab(oct, PLATE_LO, PLATE_T, { bevel: 0.0005 }))
  const slots = [rrect(0.036, 0.010, 0.004, 0, 0.0445), rrect(0.036, 0.010, 0.004, 0, -0.0445), rrect(0.010, 0.036, 0.004, 0.0445, 0), rrect(0.010, 0.036, 0.004, -0.0445, 0)]
  add('Chassis', 'carbonPlate', slab(oct, PLATE_HI, PLATE_T, { bevel: 0.0005, holes: slots.map(reversed) }))
  // printed centre shell between the plates; the nose face carries the vent slots seen in the photo
  const ci = 0.068, ki = 0.08 * Math.SQRT2 - ci
  const shell = roundPoly([[ci, -ki], [ci, ki], [ki, ci], [-ki, ci], [-ci, ki], [-ci, -ki], [-ki, -ci], [ki, -ci]], 0.003, 2)
  add('Chassis', 'white', slab(shell, PLATE_LO + PLATE_T, PLATE_HI - PLATE_LO - PLATE_T, { bevel: 0.0006 }))
  for (let i = 0; i < 7; i++) add('Chassis', 'blackPlastic', rbox(0.0017, 0.012, 0.0012, 0.0005, 1), M.t((i - 3) * 0.0034, (PLATE_LO + PLATE_HI + PLATE_T) / 2, ci + 0.0002))
  // plate screws
  for (const [x, z] of [[0.064, 0.025], [0.064, -0.025], [-0.064, 0.025], [-0.064, -0.025], [0.025, 0.064], [-0.025, 0.064], [0.025, -0.064], [-0.025, -0.064]]) {
    screw('Chassis', v3(x, MOUNT_TOP, z))
    screw('Chassis', v3(x, PLATE_LO, z), v3(0, -1, 0))
  }

  for (const { id, d, spin } of DIAG) {
    const side = new Vector3().crossVectors(d, UP).normalize()
    const F = (r, y) => diagFrame(d, r, y)
    // arm tube with a ring clamp at each end
    const arm = new THREE.CylinderGeometry(ARM_R, ARM_R, RM - 0.085, 20, 1, true)
    add('Arms', 'carbon', soup(arm, 50), M.of(F((RM + 0.085) / 2 - 0.004, ARM_Y), M.rz(Math.PI / 2)))
    // printed arm clamp at the centre frame
    add('Arms', 'white', rbox(0.026, PLATE_HI - PLATE_LO - PLATE_T, 0.026, 0.0025), M.of(F(0.093, (PLATE_LO + PLATE_T + PLATE_HI) / 2)))
    for (const s of [-1, 1]) screw('Arms', v3(0.093, MOUNT_TOP, s * 0.0075).applyMatrix4(F(0, 0)))
    // motor-wire run under the arm
    add('Wiring', 'blackPlastic', sweep([v3(0.104, 0, 0), v3(RM - 0.022, 0, 0)].map((p) => p.applyMatrix4(F(0, ARM_Y - ARM_R - 0.0016))), secCircle(0.0017, 8), { planeN: UP }))

    // motor mount: carbon top and bottom plates, printed clamp block, standoffs, fork onto the lower strut
    const MF = F(RM, 0)
    const mplate = roundPoly([[0.022, -0.022], [0.022, 0.022], [-0.022, 0.022], [-0.022, -0.022]], 0.005, 3)
    add('Motor_Mounts', 'carbonPlate', slab(mplate, PLATE_HI, PLATE_T, { bevel: 0.0005 }), MF)
    add('Motor_Mounts', 'carbonPlate', slab(mplate, PLATE_LO, PLATE_T, { bevel: 0.0005 }), MF)
    add('Motor_Mounts', 'white', rbox(0.032, PLATE_HI - PLATE_LO - PLATE_T, 0.024, 0.003), M.of(MF, M.t(-0.002, (PLATE_LO + PLATE_T + PLATE_HI) / 2, 0)))
    for (const [u, w] of [[0.0165, 0.0165], [0.0165, -0.0165], [-0.0165, 0.0165], [-0.0165, -0.0165]]) {
      add('Motor_Mounts', 'aluminium', cyl(0.0024, PLATE_LO + PLATE_T, PLATE_HI, 10), M.of(MF, M.t(u, 0, w)))
      screw('Motor_Mounts', v3(u, PLATE_LO, w).applyMatrix4(MF), v3(0, -1, 0))
    }
    // fork: a block under the bottom plate and two prongs straddling the lower strut
    const strutY = strutYAt(RM)
    add('Motor_Mounts', 'white', rbox(0.016, 0.009, 0.017, 0.0025), M.of(MF, M.t(0, PLATE_LO - 0.0045, 0)))
    for (const s of [-1, 1]) {
      const h = PLATE_LO - 0.008 - (strutY - 0.009)
      add('Motor_Mounts', 'white', rbox(0.013, h, 0.0035, 0.0012), M.of(MF, M.t(0, PLATE_LO - 0.008 - h / 2 + 0.001, s * 0.0043)))
    }
    add('Motor_Mounts', 'aluminium', cyl(0.0012, -0.0065, 0.0065, 8), M.of(MF, M.t(0, strutY - 0.0062, 0), M.rx(Math.PI / 2)))
    // navigation LED on the outer face: green at the front, red at the rear
    const led = id[0] === 'F' ? 'ledGreen' : 'ledRed'
    add('Motor_Mounts', led, rbox(0.0024, 0.004, 0.010, 0.0009, 1), M.of(MF, M.t(0.0142, ARM_Y, 0)))

    // motor (28 mm class): anodised base and bell, red cap, shaft
    const motor = M.t(d.x * RM, MOUNT_TOP, d.z * RM)
    const part = `Motor_${id}`
    add(part, 'anodisedDark', lathe([[0.0001, 0], [0.0128, 0], [0.0131, 0.0007], [0.0131, 0.0042], [0.0122, 0.0049], [0.0001, 0.0049]], 28), motor)
    add(part, 'aluminium', lathe([[0.0118, 0.0049], [0.0122, 0.0052], [0.0122, 0.0060], [0.0118, 0.0062]], 28, 60), motor)
    add(part, 'anodisedDark', lathe([[0.0001, 0.0062], [0.0136, 0.0062], [0.0141, 0.0068], [0.0141, 0.0232], [0.0136, 0.0240], [0.0001, 0.0240]], 28), motor)
    // bell vents: dark slots around the upper bell
    for (let i = 0; i < 9; i++) {
      const a = (i / 9) * 2 * Math.PI + spin * 0.2
      add(part, 'blackPlastic', rbox(0.0012, 0.0062, 0.0042, 0.0005, 1), M.of(motor, M.ry(a), M.t(0.01405, 0.0185, 0)))
    }
    add(part, 'anodisedRed', lathe([[0.0001, 0.0240], [0.0133, 0.0240], [0.0133, 0.0262], [0.0118, 0.0278], [0.0062, 0.0283], [0.0001, 0.0283]], 28), motor)
    add(part, 'aluminium', lathe([[0.0001, 0.0283], [0.0050, 0.0283], [0.0050, 0.0310], [0.0001, 0.0310]], 16), motor)
    // shaft and prop nut above the hub (static: they are round)
    add(part, 'aluminium', cyl(0.0024, 0.031, PROP_Y + 0.0045 - MOUNT_TOP, 10), motor)
    add(part, 'aluminium', lathe([[0.0001, 0], [0.0052, 0], [0.0052, 0.0036], [0.0040, 0.0052], [0.0001, 0.0056]], 6, 30), M.of(motor, M.t(0, PROP_Y + 0.0041 - MOUNT_TOP, 0)))
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Propellers: 10 x 4.5 two-blade, NACA-style cambered sections, twist to the 4.5 inch pitch, tapered planform

function propGeometry() {
  const R = 0.127, pitch = 0.1143
  const stations = [0.045, 0.075, 0.1, 0.14, 0.19, 0.25, 0.32, 0.4, 0.48, 0.56, 0.64, 0.72, 0.8, 0.86, 0.91, 0.95, 0.975, 0.99, 1.0]
  const chordAt = interp([[0.055, 0.0095], [0.1, 0.0135], [0.18, 0.0185], [0.3, 0.0212], [0.45, 0.0205], [0.6, 0.0186], [0.75, 0.0158], [0.86, 0.0130], [0.93, 0.0103], [0.97, 0.0076], [0.99, 0.0048], [1.0, 0.0022]])
  const thickAt = interp([[0.055, 0.0042], [0.1, 0.0036], [0.2, 0.0026], [0.35, 0.0020], [0.6, 0.0014], [0.85, 0.0010], [1.0, 0.0006]])
  const sweepAt = interp([[0, 0], [0.5, 0.0005], [0.8, 0.0022], [1.0, 0.0046]]) // tip trails slightly
  // airfoil: cosine-spaced chordwise samples; upper surface from TE to LE, lower from LE back to TE
  const xs = [1, 0.84, 0.66, 0.48, 0.32, 0.19, 0.09, 0.03, 0.006, 0]
  const camber = (x) => (x < 0.4 ? (0.045 / 0.16) * (0.8 * x - x * x) : (0.045 / 0.36) * (0.2 + 0.8 * x - x * x))
  const half = (x) => 5 * (0.2969 * Math.sqrt(x) - 0.126 * x - 0.3516 * x * x + 0.2843 * x ** 3 - 0.1036 * x ** 4)
  const section = []
  for (const x of xs) section.push([x, 1])
  for (const x of xs.slice(1, -1).reverse()) section.push([x, -1])
  const blade = []
  for (const s of stations) {
    const r = s * R
    const c = chordAt(s), t = thickAt(s)
    const beta = Math.min(Math.atan(pitch / (2 * Math.PI * r)), 26 * DEG)
    // chord runs from LE (advancing, -Z for a counter-clockwise prop on the +X blade) to TE, nose up by beta
    const chord = v3(0, -Math.sin(beta), Math.cos(beta))
    const up = v3(0, Math.cos(beta), Math.sin(beta))
    const pivot = 0.32
    blade.push(section.map(([x, side]) => {
      const yc = camber(x) * c * Math.min(1, s / 0.25)
      const yt = half(x) * t
      return v3(r, 0, sweepAt(s)).addScaledVector(chord, (x - pivot) * c).addScaledVector(up, yc + side * yt)
    }))
  }
  const pos = []
  const m = section.length
  const push = (...vs) => { for (const v of vs) pos.push(v.x, v.y, v.z) }
  for (let i = 0; i < blade.length - 1; i++) for (let k = 0; k < m; k++) {
    const a = blade[i][k], b = blade[i][(k + 1) % m], c2 = blade[i + 1][(k + 1) % m], d = blade[i + 1][k]
    push(a, c2, b, a, d, c2)
  }
  // tip cap
  const tip = blade[blade.length - 1], tc = tip.reduce((acc, v) => acc.add(v), v3(0, 0, 0)).multiplyScalar(1 / m)
  for (let k = 0; k < m; k++) push(tc, tip[(k + 1) % m], tip[k])
  const one = fromArrays(pos)
  creaseNormals(one, 55)
  const two = one.clone().applyMatrix4(M.ry(Math.PI))
  // hub: short boss with a chamfered top and a recess ring for the nut
  const hub = lathe([[0.0001, -0.0042], [0.0074, -0.0042], [0.0080, -0.0030], [0.0080, 0.0028], [0.0070, 0.0040], [0.0001, 0.0040]], 24)
  const g = new BufferGeometry()
  const all = [one, two, hub]
  const P = [], N = []
  for (const x of all) { P.push(...x.attributes.position.array); N.push(...x.attributes.normal.array) }
  g.setAttribute('position', new Float32BufferAttribute(P, 3))
  g.setAttribute('normal', new Float32BufferAttribute(N, 3))
  // rest pose: blades across the arms (a counter-clockwise prop sits on the FR / RL arms, along (-1, 0, 1))
  return g.applyMatrix4(M.ry(-45 * DEG))
}

function interp(table) {
  return (s) => {
    if (s <= table[0][0]) return table[0][1]
    for (let i = 1; i < table.length; i++) if (s <= table[i][0]) {
      const [s0, a] = table[i - 1], [s1, b] = table[i]
      const t = (s - s0) / (s1 - s0)
      const e = t * t * (3 - 2 * t)
      return a + (b - a) * (0.5 * t + 0.5 * e)
    }
    return table[table.length - 1][1]
  }
}

// ---------------------------------------------------------------------------------------------------------------
// MID360S in its printed housing at the front of the top deck, tilted 40 degrees forward

const LIDAR_TILT = 40 * DEG
const LIDAR_BASE = v3(0, 0.1272, 0.0468) // centre of the lidar's base face

function buildLidar() {
  const L = M.of(M.t(LIDAR_BASE.x, LIDAR_BASE.y, LIDAR_BASE.z), M.rx(LIDAR_TILT)) // local: y = dome axis
  const part = 'LiDAR_MID360S'
  // body: 65 x 65 rounded square, fins on the two flanks
  const body = roundPoly([[0.0325, -0.0325], [0.0325, 0.0325], [-0.0325, 0.0325], [-0.0325, -0.0325]], 0.007, 3)
  add(part, 'lidarBody', slab(body, 0, 0.0345, { bevel: 0.0012 }), L)
  for (const s of [-1, 1]) for (let i = 0; i < 8; i++) {
    add(part, 'lidarBody', rbox(0.0042, 0.026, 0.0016, 0.0006, 1), M.of(L, M.t(s * 0.0335, 0.0165, -0.0245 + i * 0.007)))
  }
  // collar and dome
  add(part, 'white', lathe([[0.0001, 0.0345], [0.0290, 0.0345], [0.0296, 0.0352], [0.0296, 0.0392], [0.0284, 0.0402], [0.0001, 0.0402]], 40), L)
  const dome = []
  for (let i = 0; i <= 14; i++) {
    const a = (i / 14) * (Math.PI / 2)
    dome.push([0.0262 * Math.cos(a), 0.0402 + 0.0208 * Math.sin(a)])
  }
  dome[dome.length - 1][0] = 0.0001
  add(part, 'lidarDome', lathe(dome, 40, 60), L)

  // printed housing: base plate, face plate with the round opening, four corner posts
  const H = 0.0375
  const sq = roundPoly([[H, -H], [H, H], [-H, H], [-H, -H]], 0.008, 3)
  add('LiDAR_Housing', 'white', slab(sq, -0.004, 0.004, { bevel: 0.0008 }), L)
  add('LiDAR_Housing', 'white', slab(sq, 0.0335, 0.0048, { bevel: 0.0009, holes: [reversed(circle(0.0302, 0, 0, 40))], curveSeg: 10 }), L)
  for (const [x, z] of [[H - 0.0045, H - 0.0045], [-H + 0.0045, H - 0.0045], [-H + 0.0045, -H + 0.0045], [H - 0.0045, -H + 0.0045]]) {
    add('LiDAR_Housing', 'white', rbox(0.009, 0.0335, 0.009, 0.002), M.of(L, M.t(x, 0.0168, z)))
    screw('LiDAR_Housing', v3(x, 0.0383, z).applyMatrix4(L), v3(0, Math.cos(LIDAR_TILT), Math.sin(LIDAR_TILT)), 0.0016, 0.001)
  }
  // bezel ring proud of the face: the "round lens" of the nose seen from the front
  add('LiDAR_Housing', 'aluminium', lathe([[0.0298, 0.0383], [0.0334, 0.0383], [0.0334, 0.0396], [0.0322, 0.0404], [0.0300, 0.0404]], 40, 50), L)

  // wedge mount on the top deck: two printed cheeks with a lightening window, a cross web
  const front = v3(0, -Math.sin(LIDAR_TILT), Math.cos(LIDAR_TILT)), axis = v3(0, Math.cos(LIDAR_TILT), Math.sin(LIDAR_TILT))
  const pBase = (u) => LIDAR_BASE.clone().addScaledVector(front, u).addScaledVector(axis, -0.004)
  const pf = pBase(0.034), pr = pBase(-0.034)
  const cheek = [[MOUNT_TOP, 0.077], [pf.y, pf.z], [pr.y, pr.z], [MOUNT_TOP, pr.z - 0.002]] // (y, z) side profile
  const prof = cheek.map(([y, z]) => [z, y])
  const hole = [[0.040, MOUNT_TOP + 0.004], [0.062, MOUNT_TOP + 0.004], [0.040, MOUNT_TOP + 0.020]]
  for (const s of [-1, 1]) {
    const g = plate(prof, -0.0025, 0.0025, { bevel: 0.0005, holes: [reversed(hole)] })
    // plate(): u -> x, v -> y, w -> z; turn so u runs along world Z and w along world X
    add('LiDAR_Housing', 'white', g, M.of(M.t(s * 0.026, 0, 0), M.ry(-Math.PI / 2)))
  }
  add('LiDAR_Housing', 'white', rbox(0.050, 0.004, 0.012, 0.0012), M.t(0, MOUNT_TOP + 0.002, pr.z + 0.012))
  add('LiDAR_Housing', 'white', rbox(0.050, 0.004, 0.012, 0.0012), M.t(0, MOUNT_TOP + 0.002, 0.071))
  // data cable from the back of the lidar down to the deck
  const c0 = LIDAR_BASE.clone().addScaledVector(front, -0.03).addScaledVector(axis, 0.012)
  add('Wiring', 'blackPlastic', sweep(spline([c0, c0.clone().add(v3(0.008, -0.004, -0.012)), v3(0.014, MOUNT_TOP + 0.010, -0.004), v3(0.016, MOUNT_TOP + 0.0025, -0.012)], 16), secCircle(0.0022, 8)))
}

// ---------------------------------------------------------------------------------------------------------------
// Flight controller, compute stack, antennas

function buildCompute() {
  // CUAV X7+ class: silver core on a black carrier, centre of the deck under the Jetson stand
  const fz = -0.036
  add('Flight_Controller', 'blackPlastic', rbox(0.046, 0.0085, 0.066, 0.0025), M.t(0, MOUNT_TOP + 0.00425, fz))
  add('Flight_Controller', 'aluminium', rbox(0.038, 0.024, 0.038, 0.0035, 2), M.t(0, MOUNT_TOP + 0.0085 + 0.012, fz + 0.006))
  add('Flight_Controller', 'anodisedDark', rbox(0.030, 0.0012, 0.030, 0.0005, 1), M.t(0, MOUNT_TOP + 0.0085 + 0.0242, fz + 0.006))
  for (const s of [-1, 1]) for (let i = 0; i < 3; i++) add('Flight_Controller', 'white', rbox(0.004, 0.0045, 0.008, 0.0008, 1), M.t(s * 0.0215, MOUNT_TOP + 0.0108, fz - 0.024 + i * 0.012))
  add('Flight_Controller', 'ledGreen', rbox(0.004, 0.002, 0.0012, 0.0005, 1), M.t(0.010, MOUNT_TOP + 0.0175, fz + 0.025))

  // X-braced printed stand carrying the Jetson
  const sy0 = MOUNT_TOP, sy1 = 0.1495, z0 = -0.072, z1 = 0.0
  for (const s of [-1, 1]) {
    const x = s * 0.043
    for (const z of [z0, z1]) add('Compute_Stand', 'white', rbox(0.006, sy1 - sy0, 0.006, 0.0015), M.t(x, (sy0 + sy1) / 2, z))
    add('Compute_Stand', 'white', rbox(0.006, 0.004, z1 - z0 + 0.006, 0.0015), M.t(x, sy1 - 0.002, (z0 + z1) / 2))
    add('Compute_Stand', 'white', rbox(0.006, 0.004, z1 - z0 + 0.006, 0.0015), M.t(x, sy0 + 0.002, (z0 + z1) / 2))
    const len = Math.hypot(z1 - z0, sy1 - sy0 - 0.008), ang = Math.atan2(sy1 - sy0 - 0.008, z1 - z0)
    for (const sg of [-1, 1]) add('Compute_Stand', 'white', rbox(0.004, 0.0045, len, 0.0012, 1), M.of(M.t(x, (sy0 + sy1) / 2, (z0 + z1) / 2), M.rx(sg * ang)))
  }
  for (const z of [z0, z1]) add('Compute_Stand', 'white', rbox(0.086, 0.004, 0.005, 0.0012), M.t(0, sy1 - 0.002, z))

  // Jetson Orin Nano carrier (100 x 79), module heatsink with fins and fan, I/O on the rear edge
  const by = sy1, bz = -0.0355
  const part = 'Compute_Jetson'
  add(part, 'pcb', rbox(0.100, 0.0016, 0.079, 0.0007, 1), M.t(0, by + 0.0008, bz))
  add(part, 'pcb', rbox(0.070, 0.0014, 0.045, 0.0006, 1), M.t(0, by + 0.0035, bz + 0.004))
  add(part, 'anodisedDark', rbox(0.062, 0.004, 0.045, 0.0008, 1), M.t(0, by + 0.0062, bz + 0.004))
  for (let i = 0; i < 13; i++) add(part, 'anodisedDark', box(0.0011, 0.0105, 0.045), M.t(-0.0294 + i * 0.0049, by + 0.0134, bz + 0.004))
  const fy = by + 0.0187
  const fan = roundPoly([[0.02, -0.02], [0.02, 0.02], [-0.02, 0.02], [-0.02, -0.02]], 0.003, 2)
  add(part, 'blackPlastic', slab(fan, fy, 0.009, { bevel: 0.0005, holes: [reversed(circle(0.0175, 0, 0, 32))], curveSeg: 8 }), M.t(0, 0, bz + 0.004))
  add(part, 'blackPlastic', cyl(0.0075, fy + 0.001, fy + 0.0085, 20), M.t(0, 0, bz + 0.004))
  for (let i = 0; i < 7; i++) add(part, 'blackPlastic', rbox(0.0105, 0.0008, 0.0062, 0.0003, 1), M.of(M.t(0, fy + 0.006, bz + 0.004), M.ry((i / 7) * 2 * Math.PI), M.t(0.0122, 0, 0), M.rx(0.5)))
  add(part, 'anodisedDark', cyl(0.0042, fy + 0.0085, fy + 0.0092, 16), M.t(0, 0, bz + 0.004))
  const ez = bz - 0.0395
  const port = (mat, w, h, d, x) => add(part, mat, rbox(w, h, d, 0.0006, 1), M.t(x, by + 0.0016 + h / 2, ez + d / 2 - 0.002))
  port('aluminium', 0.0145, 0.0155, 0.017, 0.031)
  port('aluminium', 0.0145, 0.0155, 0.017, 0.013)
  port('aluminium', 0.016, 0.0135, 0.021, -0.006)
  port('blackPlastic', 0.017, 0.0068, 0.009, -0.025)
  port('blackPlastic', 0.009, 0.011, 0.014, -0.041)
  add(part, 'blackPlastic', rbox(0.051, 0.0085, 0.005, 0.0006, 1), M.t(0.012, by + 0.0016 + 0.00425, bz + 0.0365))
  for (const [x, z] of [[0.045, bz + 0.0345], [-0.045, bz + 0.0345], [0.045, bz - 0.0345], [-0.045, bz - 0.0345]]) screw(part, v3(x, by + 0.0016, z), UP, 0.0016, 0.001)

  // two dipole antennas at the back of the stand, leaning out
  for (const s of [-1, 1]) {
    const base = v3(s * 0.036, sy1 + 0.002, z0 - 0.004)
    const dir = v3(s * 0.28, 1, -0.32).normalize()
    const q = new Quaternion().setFromUnitVectors(UP, dir)
    add('Antennas', 'blackPlastic', lathe([[0.0001, 0], [0.0042, 0], [0.0042, 0.0075], [0.0030, 0.0095], [0.0028, 0.0355], [0.0020, 0.0390], [0.0001, 0.0392]], 14, 50), new Matrix4().compose(base, q, v3(1, 1, 1)))
    add('Antennas', 'aluminium', cyl(0.0043, 0.0075, 0.0088, 14), new Matrix4().compose(base, q, v3(1, 1, 1)))
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Battery bracket and 4S pack under the centre

const BOX = { x: 0.066, y0: 0.019, y1: PLATE_LO, z0: -0.084, z1: 0.066 }

function buildBattery() {
  const part = 'Battery_Bracket'
  const { x, y0, y1, z0, z1 } = BOX
  const L = z1 - z0, Hh = y1 - y0, zc = (z0 + z1) / 2
  // tray with two lightening slots
  const tray = roundPoly([[x, z0], [x, z1], [-x, z1], [-x, z0]], 0.006, 3)
  add(part, 'white', slab(tray, y0, 0.004, { bevel: 0.0007, holes: [rrect(0.088, 0.05, 0.009, 0, zc - 0.033), rrect(0.088, 0.05, 0.009, 0, zc + 0.033)] }))
  // side frames with two windows each
  for (const s of [-1, 1]) {
    const outline = rrect(L, Hh - 0.004, 0.003, zc, y0 + 0.004 + (Hh - 0.004) / 2)
    const wins = [rrect(0.056, 0.026, 0.005, zc - 0.033, y0 + 0.004 + (Hh - 0.004) / 2), rrect(0.056, 0.026, 0.005, zc + 0.033, y0 + 0.004 + (Hh - 0.004) / 2)]
    const g = plate(outline, -0.002, 0.002, { bevel: 0.0005, holes: wins.map(reversed) })
    add(part, 'white', g, M.of(M.t(s * (x - 0.002), 0, 0), M.ry(-Math.PI / 2)))
  }
  // nose wall (solid, as in the photo) and a rear strap bar
  add(part, 'white', rbox(2 * x, Hh, 0.004, 0.0012), M.t(0, y0 + Hh / 2, z1 - 0.002))
  add(part, 'white', rbox(2 * x, 0.008, 0.004, 0.0012), M.t(0, y0 + 0.008, z0 + 0.002))
  add(part, 'white', rbox(2 * x, 0.006, 0.004, 0.0012), M.t(0, y1 - 0.004, z0 + 0.002))
  for (const s of [-1, 1]) for (const z of [z0 + 0.012, z1 - 0.012]) screw(part, v3(s * 0.056, y1, z), UP, 0.0017, 0.0002)
  // rubber feet under the tray
  for (const s of [-1, 1]) for (const z of [z0 + 0.01, z1 - 0.01]) add(part, 'rubber', cyl(0.0035, y0 - 0.0015, y0 + 0.0002, 12), M.t(s * 0.054, 0, z))

  // the pack: dark wrap, light label band, red and black leads to an XT60 at the rear of the frame
  const bp = 'Battery_4S'
  const bh = 0.040, bw = 0.098, bl = 0.140
  add(bp, 'blackPlastic', rbox(bw, bh, bl, 0.004, 2), M.t(0, y0 + 0.004 + bh / 2 + 0.0005, zc + 0.002))
  for (const s of [-1, 1]) {
    add(bp, 'lidarBody', rbox(0.0008, bh * 0.55, 0.096, 0.0003), M.t(s * (bw / 2 + 0.0002), y0 + 0.004 + bh * 0.66, zc + 0.002))
    add(bp, 'anodisedRed', rbox(0.0009, 0.004, 0.096, 0.0003), M.t(s * (bw / 2 + 0.0003), y0 + 0.004 + bh * 0.66 - bh * 0.17, zc + 0.002))
  }
  add(bp, 'rubber', rbox(bw + 0.003, bh + 0.0035, 0.016, 0.0015), M.t(0, y0 + 0.004 + bh / 2 + 0.0005, zc - 0.045))
  for (const [s, mat] of [[-1, 'anodisedRed'], [1, 'blackPlastic']]) {
    const p0 = v3(s * 0.008, y0 + 0.024, zc + 0.002 - bl / 2 + 0.002)
    add('Wiring', mat, sweep(spline([p0, p0.clone().add(v3(0, 0.002, -0.012)), v3(s * 0.006, y1 - 0.012, z0 - 0.012), v3(s * 0.004, y1 - 0.004, z0 - 0.008)], 18), secCircle(0.0022, 8)))
  }
  add('Wiring', 'blackPlastic', rbox(0.016, 0.008, 0.016, 0.0015), M.t(0, y1 - 0.0045, z0 - 0.006))
}

// ---------------------------------------------------------------------------------------------------------------
// EO/IR gimbal pod (DYT class): damped mount, yaw drive, U yoke with pitch motor, 41 mm head with zoom, wide,
// thermal and laser rangefinder apertures

const GZ = 0.108 // yaw axis distance ahead of the centre
const YAW_O = v3(0, 0.0755, GZ) // yaw bearing, origin of Gimbal_Yaw
const PITCH_O = v3(0, 0.0455, GZ) // pitch axis, origin of Gimbal_Pitch
const HEAD_R = 0.0205
const ZOOM = v3(-0.0055, 0.002, 0.0148) // zoom lens glass centre relative to the pitch axis (Gimbal_Camera)
STAGES.yaw = YAW_O
STAGES.pitch = PITCH_O

function buildGimbal() {
  // static mount: printed L bracket from the nose of the centre shell, damper plate, four rubber balls, top plate
  const mp = 'GimbalMount'
  add(mp, 'white', rbox(0.050, PLATE_HI - PLATE_LO - PLATE_T, 0.005, 0.0015), M.t(0, (PLATE_LO + PLATE_T + PLATE_HI) / 2, 0.0705))
  const arm = roundPoly([[0.025, 0.068], [0.025, GZ + 0.012], [0.017, GZ + 0.022], [-0.017, GZ + 0.022], [-0.025, GZ + 0.012], [-0.025, 0.068]], 0.003, 3)
  add(mp, 'white', slab(arm, 0.0895, 0.005, { bevel: 0.0007 }))
  for (const s of [-1, 1]) add(mp, 'white', plate([[0, 0], [0.024, 0.016], [0, 0.016]], -0.0015, 0.0015, { bevel: 0.0004 }), M.of(M.t(s * 0.021, 0.0735, 0.073), M.ry(-Math.PI / 2)))
  for (const [x, z] of [[0.0135, GZ + 0.0135], [-0.0135, GZ + 0.0135], [0.0135, GZ - 0.0135], [-0.0135, GZ - 0.0135]]) {
    add(mp, 'rubber', soup(new THREE.SphereGeometry(0.0036, 12, 8).translate(x, 0.0858, z)))
    screw(mp, v3(x, 0.0945, z), UP, 0.0016, 0.001)
  }
  const gp = roundPoly([[0.019, -0.019], [0.019, 0.019], [-0.019, 0.019], [-0.019, -0.019]], 0.004, 3)
  add(mp, 'anodisedDark', slab(gp, 0.0792, 0.0032, { bevel: 0.0006 }), M.t(0, 0, GZ))
  add(mp, 'anodisedDark', lathe([[0.0001, YAW_O.y + 0.0004], [0.0140, YAW_O.y + 0.0004], [0.0146, YAW_O.y + 0.0012], [0.0146, 0.0792], [0.0001, 0.0792]], 32), M.t(0, 0, GZ))
  // cable loop into the head (static part)
  add('Wiring', 'blackPlastic', sweep(spline([v3(-0.01, 0.0895, 0.084), v3(-0.019, 0.083, 0.086), v3(-0.021, 0.079, 0.093)], 10), secCircle(0.0016, 8)))

  // yaw stage: rotor, crossbar, two yoke arms, pitch motor and bearing cap
  const yp = 'GimbalYawStage'
  add(yp, 'podShell', lathe([[0.0001, -0.0045], [0.0136, -0.0045], [0.0146, -0.0035], [0.0146, -0.0003], [0.0001, -0.0003]], 32), M.t(0, YAW_O.y, GZ), 'yaw')
  add(yp, 'anodisedDark', lathe([[0.0141, -0.0003], [0.0147, -0.0003], [0.0147, 0.0003], [0.0141, 0.0003]], 32, 60), M.t(0, YAW_O.y, GZ), 'yaw')
  const cbTop = YAW_O.y - 0.0045, cbBot = PITCH_O.y + HEAD_R + 0.0012
  const ax = HEAD_R + 0.0045 // inner face of the yoke arms
  add(yp, 'podShell', rbox(2 * ax + 0.010, cbTop - cbBot, 0.017, 0.0022), M.t(0, (cbTop + cbBot) / 2, GZ), 'yaw')
  const armBot = PITCH_O.y - 0.0105
  for (const s of [-1, 1]) {
    const yokeArm = plate(rrect(0.017, cbTop - armBot, 0.0058, 0, (cbTop + armBot) / 2, 4), -0.0025, 0.0025, { bevel: 0.0006 })
    add(yp, 'podShell', yokeArm, M.of(M.t(s * (ax + 0.0025), 0, GZ), M.ry(Math.PI / 2)), 'yaw')
  }
  // pitch motor on the left arm (+X), bearing cap on the right
  const onX = () => M.of(M.t(0, PITCH_O.y, GZ), M.rz(-Math.PI / 2)) // local +Y along world +X on the pitch axis
  add(yp, 'anodisedDark', lathe([[0.0001, 0], [0.0098, 0], [0.0102, 0.0008], [0.0102, 0.0072], [0.0094, 0.0080], [0.0001, 0.0080]], 28), M.of(onX(), M.t(0, ax + 0.005, 0)), 'yaw')
  add(yp, 'aluminium', lathe([[0.0001, 0.0080], [0.0060, 0.0080], [0.0060, 0.0090], [0.0001, 0.0092]], 20), M.of(onX(), M.t(0, ax + 0.005, 0)), 'yaw')
  add(yp, 'anodisedDark', lathe([[0.0001, 0], [0.0068, 0], [0.0068, 0.0028], [0.0058, 0.0036], [0.0001, 0.0036]], 24), M.of(M.t(0, PITCH_O.y, GZ), M.rz(Math.PI / 2), M.t(0, ax + 0.005, 0)), 'yaw')

  // pitch stage: the head. A sphere cut flat at the front, a glossy black face with the apertures, side hubs
  const hp = 'GimbalHead'
  const H = M.of(M.t(PITCH_O.x, PITCH_O.y, PITCH_O.z), M.rx(Math.PI / 2)) // local +Y -> world +Z (optical axis)
  const zf = 0.0125 // flat front at 12.5 mm
  const prof = []
  for (let i = 0; i <= 18; i++) {
    const a = -Math.PI / 2 + (i / 18) * (Math.PI / 2 + Math.asin(zf / HEAD_R))
    prof.push([HEAD_R * Math.cos(a), HEAD_R * Math.sin(a)])
  }
  prof[0][0] = 0.0001
  const rf = Math.sqrt(HEAD_R ** 2 - zf ** 2)
  prof.push([rf - 0.0006, zf + 0.0004], [0.0154, zf + 0.0004], [0.0154, zf - 0.0002])
  add(hp, 'podShell', lathe(prof, 40, 35), H, 'pitch')
  // face plate (dark anodised) with a slight dome, carrying the apertures
  add(hp, 'anodisedDark', lathe([[0.0001, zf + 0.0008], [0.0153, zf - 0.0002], [0.0153, zf + 0.0003], [0.0001, zf + 0.0011]], 40, 30), H, 'pitch')
  const face = (x, y) => v3(x, y, 0)
  const lens = (c, rBarrel, rGlass, proud, glassMat, coat) => {
    const at = M.of(M.t(PITCH_O.x + c.x, PITCH_O.y + c.y, PITCH_O.z), M.rx(Math.PI / 2))
    add(hp, 'blackPlastic', lathe([[rGlass, zf + proud - 0.0006], [rGlass + 0.0004, zf + proud], [rBarrel - 0.0009, zf + proud], [rBarrel - 0.0009, zf + proud - 0.0003]].reverse(), 28, 40), at, 'pitch')
    add(hp, 'aluminium', lathe([[rBarrel - 0.0009, zf + proud - 0.0003], [rBarrel - 0.0003, zf + proud - 0.0003], [rBarrel, zf + proud - 0.0007], [rBarrel, zf + proud - 0.0012]].reverse(), 28, 40), at, 'pitch')
    add(hp, 'blackPlastic', lathe([[rBarrel, zf + proud - 0.0012], [rBarrel, zf], [rBarrel + 0.0006, zf - 0.0002]].reverse(), 28, 40), at, 'pitch')
    add(hp, glassMat, lathe([[0.0001, zf + proud - 0.0003], [rGlass * 0.7, zf + proud - 0.0006], [rGlass, zf + proud - 0.0010], [rGlass + 0.0002, zf + proud - 0.0016]].reverse(), 28, 35), at, 'pitch')
    if (coat) add(hp, 'lensCoat', lathe([[coat * 0.55, zf + proud - 0.0002], [coat, zf + proud - 0.00045]].reverse(), 24, 80), at, 'pitch')
  }
  lens(face(ZOOM.x, ZOOM.y), 0.0068, 0.0053, 0.0026, 'glass', 0.0032) // zoom visible (main)
  lens(face(0.0075, 0.0035), 0.0056, 0.0045, 0.0020, 'thermal', 0) // thermal
  lens(face(0.0086, -0.0076), 0.0031, 0.0021, 0.0016, 'glass', 0.0012) // wide visible
  // laser rangefinder: emitter and receiver behind one dark window frame
  const lrf = M.of(M.t(PITCH_O.x - 0.0028, PITCH_O.y - 0.0101, PITCH_O.z + zf + 0.0008), M.rz(0.12))
  add(hp, 'anodisedDark', rbox(0.0122, 0.0058, 0.0016, 0.0019, 2), lrf, 'pitch')
  add(hp, 'thermal', soup(new THREE.CylinderGeometry(0.0018, 0.0018, 0.0006, 16).rotateX(Math.PI / 2).translate(-0.0032, 0, 0.0009)), lrf, 'pitch')
  add(hp, 'glass', soup(new THREE.CylinderGeometry(0.0022, 0.0022, 0.0006, 16).rotateX(Math.PI / 2).translate(0.0025, 0, 0.0009)), lrf, 'pitch')
  add(hp, 'ledRed', soup(new THREE.CylinderGeometry(0.0007, 0.0007, 0.0004, 10).rotateX(Math.PI / 2).translate(-0.0032, 0, 0.0013)), lrf, 'pitch')
  // side hubs where the head rides in the yoke, and a seam ring
  for (const s of [-1, 1]) add(hp, 'anodisedDark', lathe([[0.0001, HEAD_R - 0.0012], [0.0068, HEAD_R - 0.0012], [0.0068, HEAD_R + 0.0028], [0.0058, ax], [0.0001, ax]], 24), M.of(M.t(PITCH_O.x, PITCH_O.y, PITCH_O.z), M.rz(-s * Math.PI / 2)), 'pitch')
  add(hp, 'anodisedDark', lathe([[HEAD_R - 0.0004, -0.0006], [HEAD_R + 0.0002, -0.0004], [HEAD_R + 0.0002, 0.0004], [HEAD_R - 0.0004, 0.0006]], 40, 60), M.of(M.t(PITCH_O.x, PITCH_O.y, PITCH_O.z), M.rx(Math.PI / 2), M.t(0, -0.004, 0)), 'pitch')
}

// ---------------------------------------------------------------------------------------------------------------
// Assemble and write

buildCage()
buildFrame()
buildLidar()
buildCompute()
buildBattery()
buildGimbal()
const PROP_CCW = propGeometry()
const PROP_CW = (() => {
  const g = PROP_CCW.clone().applyMatrix4(M.s(-1, 1, 1))
  flipWinding(g)
  return g
})()

function writeGlb(file) {
  const nodes = [], meshes = [], accessors = [], views = [], chunks = []
  let byteLength = 0
  const matNames = Object.keys(MATERIALS).filter((m) => [...buckets.values()].some((b) => b.mat === m) || m === 'prop')
  const materials = matNames.map((name) => {
    const m = MATERIALS[name]
    const out = { name, pbrMetallicRoughness: { baseColorFactor: [...hexLinear(m.color), 1], metallicFactor: m.metal, roughnessFactor: m.rough } }
    if (m.emissive) out.emissiveFactor = hexLinear(m.emissive)
    return out
  })
  const matIndex = (name) => matNames.indexOf(name)
  const pushView = (typed, target) => {
    const bytes = new Uint8Array(typed.buffer, typed.byteOffset, typed.byteLength)
    const pad = (4 - (byteLength % 4)) % 4
    if (pad) { chunks.push(new Uint8Array(pad)); byteLength += pad }
    views.push({ buffer: 0, byteOffset: byteLength, byteLength: bytes.byteLength, target })
    chunks.push(bytes)
    byteLength += bytes.byteLength
    return views.length - 1
  }
  let triangles = 0
  const meshOf = new Map()
  const addMesh = (name, w, mat) => {
    const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity]
    for (let i = 0; i < w.pos.length; i += 3) for (let k = 0; k < 3; k++) {
      const v = Math.fround(w.pos[i + k])
      if (v < min[k]) min[k] = v
      if (v > max[k]) max[k] = v
    }
    const count = w.pos.length / 3
    const pa = accessors.push({ bufferView: pushView(w.pos, 34962), componentType: 5126, count, type: 'VEC3', min, max }) - 1
    const na = accessors.push({ bufferView: pushView(w.nrm, 34962), componentType: 5126, count, type: 'VEC3' }) - 1
    const big = count > 65535
    const idx = big ? new Uint32Array(w.idx) : new Uint16Array(w.idx)
    const ia = accessors.push({ bufferView: pushView(idx, 34963), componentType: big ? 5125 : 5123, count: idx.length, type: 'SCALAR' }) - 1
    triangles += idx.length / 3
    meshes.push({ name, primitives: [{ attributes: { POSITION: pa, NORMAL: na }, indices: ia, material: matIndex(mat), mode: 4 }] })
    return meshes.length - 1
  }
  const node = (o) => nodes.push(o) - 1
  const meshNodes = (stage) => {
    const ids = []
    for (const b of buckets.values()) if (b.stage === stage) {
      const w = weld(b.geos, STAGES[stage])
      if (process.env.DU7_STATS) console.log(`  ${b.name.padEnd(36)} ${String(w.idx.length / 3).padStart(6)} tris ${String(w.pos.length / 3).padStart(6)} verts`)
      ids.push(node({ name: b.name, mesh: addMesh(b.name, w, b.mat) }))
    }
    return ids
  }
  const r6 = (v) => v.map((x) => +x.toFixed(6))

  const root = node({ name: 'ANetDU7', children: [] })
  nodes[root].children.push(...meshNodes('root'))
  for (const { id, d, spin } of DIAG) {
    const key = spin > 0 ? 'ccw' : 'cw'
    if (!meshOf.has(key)) meshOf.set(key, addMesh(`Propeller_${key.toUpperCase()}`, weld([spin > 0 ? PROP_CCW : PROP_CW], v3(0, 0, 0)), 'prop'))
    nodes[root].children.push(node({ name: `Propeller_${id}_ROTATABLE`, mesh: meshOf.get(key), translation: r6([d.x * RM, PROP_Y, d.z * RM]) }))
  }
  const yaw = node({ name: 'Gimbal_Yaw', translation: r6(YAW_O.toArray()), children: [] })
  nodes[root].children.push(yaw)
  nodes[yaw].children.push(...meshNodes('yaw'))
  const pitch = node({ name: 'Gimbal_Pitch', translation: r6(PITCH_O.clone().sub(YAW_O).toArray()), children: [] })
  nodes[yaw].children.push(pitch)
  nodes[pitch].children.push(...meshNodes('pitch'))
  nodes[pitch].children.push(node({ name: 'Gimbal_Camera', translation: r6(ZOOM.toArray()) }))

  const json = {
    asset: { version: '2.0', generator: 'ANet Relay tools/models/build_anet_du7.mjs', copyright: 'Agent Network' },
    scene: 0,
    scenes: [{ name: 'ANetDU7', nodes: [root], extras: { units: 'metres', up: '+Y', nose: '+Z', left: '+X', dimensions_m: [0.684, 0.211, 0.684] } }],
    nodes, meshes, materials, accessors, bufferViews: views, buffers: [{ byteLength: 0 }],
  }
  const pad = (4 - (byteLength % 4)) % 4
  if (pad) { chunks.push(new Uint8Array(pad)); byteLength += pad }
  json.buffers[0].byteLength = byteLength
  let jsonBytes = Buffer.from(JSON.stringify(json), 'utf8')
  const jpad = (4 - (jsonBytes.length % 4)) % 4
  jsonBytes = Buffer.concat([jsonBytes, Buffer.alloc(jpad, 0x20)])
  const header = Buffer.alloc(12), jh = Buffer.alloc(8), bh = Buffer.alloc(8)
  const total = 12 + 8 + jsonBytes.length + 8 + byteLength
  header.writeUInt32LE(0x46546c67, 0); header.writeUInt32LE(2, 4); header.writeUInt32LE(total, 8)
  jh.writeUInt32LE(jsonBytes.length, 0); jh.writeUInt32LE(0x4e4f534a, 4)
  bh.writeUInt32LE(byteLength, 0); bh.writeUInt32LE(0x004e4942, 4)
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, Buffer.concat([header, jh, jsonBytes, bh, ...chunks.map((c) => Buffer.from(c.buffer, c.byteOffset, c.byteLength))]))
  return { bytes: total, triangles, materials: materials.length, meshes: meshes.length, nodes: nodes.length }
}

const stats = writeGlb(OUT)
console.log(`${path.relative(ROOT, OUT)}: ${(stats.bytes / 1024).toFixed(1)} KiB, ${stats.triangles} triangles, ${stats.materials} materials, ${stats.meshes} meshes, ${stats.nodes} nodes`)
