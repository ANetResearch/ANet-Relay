// Geometry of the VoxelLoader cube (05 §2.5): 3 x 3 x 3 voxels in isometric projection, painted back to front, with
// the order they light up in (bottom layer first) and the direction each one scatters to. Pure, unit-tested.

export const CUBE_N = 3
/** share of a cell the voxel fills (the rest is the gap between voxels) */
const FILL = 0.82
const COS30 = Math.cos(Math.PI / 6)

export interface CubeVoxel {
  key: string
  /** paint order (back to front) is the array order; `light` is the build order 0..26 */
  light: number
  /** polygon points for the top, +y (screen left) and +x (screen right) faces */
  top: string
  left: string
  right: string
  /** unit direction on screen from the cube centre (StageLoading scatter) */
  sx: number
  sy: number
}

type P3 = [number, number, number]

function iso([x, y, z]: P3, u: number): [number, number] {
  return [(x - y) * COS30 * u, ((x + y) * 0.5 - z) * u]
}

const pts = (ps: P3[], u: number) =>
  ps
    .map((p) => iso(p, u))
    .map(([x, y]) => `${+x.toFixed(2)},${+y.toFixed(2)}`)
    .join(' ')

export interface CubeModel {
  voxels: CubeVoxel[]
  viewBox: string
}

/** The cube at `u` user units per cell. */
export function cubeModel(u = 10): CubeModel {
  const g = (1 - FILL) / 2
  const cells: [number, number, number][] = []
  for (let k = 0; k < CUBE_N; k++) for (let j = 0; j < CUBE_N; j++) for (let i = 0; i < CUBE_N; i++) cells.push([i, j, k])
  // build order: bottom layer first, then front to back within a layer
  const build = [...cells].sort((a, b) => a[2] - b[2] || a[0] + a[1] - (b[0] + b[1]) || a[0] - b[0])
  const lightOf = new Map(build.map((c, n) => [c.join(','), n]))
  // painter's order for this projection: farther voxels (smaller i + j + k) first
  const paint = [...cells].sort((a, b) => a[0] + a[1] + a[2] - (b[0] + b[1] + b[2]) || a[2] - b[2] || a[0] - b[0])
  const mid = (CUBE_N - 1) / 2
  const [cx, cy] = iso([mid + 0.5, mid + 0.5, mid + 0.5], u)
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  const voxels = paint.map(([i, j, k]) => {
    const x0 = i + g
    const x1 = i + 1 - g
    const y0 = j + g
    const y1 = j + 1 - g
    const z0 = k + g
    const z1 = k + 1 - g
    const corners: P3[] = [
      [x0, y0, z1],
      [x1, y0, z1],
      [x1, y1, z1],
      [x0, y1, z1],
      [x1, y0, z0],
      [x1, y1, z0],
      [x0, y1, z0],
    ]
    for (const c of corners) {
      const [px, py] = iso(c, u)
      minX = Math.min(minX, px)
      maxX = Math.max(maxX, px)
      minY = Math.min(minY, py)
      maxY = Math.max(maxY, py)
    }
    const [vx, vy] = iso([i + 0.5, j + 0.5, k + 0.5], u)
    const d = Math.hypot(vx - cx, vy - cy)
    return {
      key: `${i},${j},${k}`,
      light: lightOf.get(`${i},${j},${k}`) ?? 0,
      top: pts([[x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], u),
      left: pts([[x0, y1, z1], [x1, y1, z1], [x1, y1, z0], [x0, y1, z0]], u),
      right: pts([[x1, y0, z1], [x1, y1, z1], [x1, y1, z0], [x1, y0, z0]], u),
      sx: d < 1e-6 ? 0 : (vx - cx) / d,
      sy: d < 1e-6 ? -1 : (vy - cy) / d,
    }
  })
  const pad = u * 0.1
  return { voxels, viewBox: `${+(minX - pad).toFixed(2)} ${+(minY - pad).toFixed(2)} ${+(maxX - minX + 2 * pad).toFixed(2)} ${+(maxY - minY + 2 * pad).toFixed(2)}` }
}
