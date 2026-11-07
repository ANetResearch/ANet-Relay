// Camera poses of 02 §6.5 as pure functions of the framed bounds (ENU) and the viewport aspect. Results are in
// scene coordinates (see ./math). The rig in scene/CameraRig.tsx interpolates between them.
import type { Bounds } from './placement'
import { enuToScene, type Vec3 } from './math'

export const FOV_DEG = 32
const DEG = Math.PI / 180

/** Overview: camera south-east of the target (bearing 145°, i.e. azimuth -35° from south), 32° above the horizon. */
export const OVERVIEW_BEARING_DEG = 145
export const OVERVIEW_ELEVATION_DEG = 32
/** share of the viewport width the framed bounds take */
export const OVERVIEW_FILL = 0.6
/** share of the viewport width the rotated footprint may take at most */
const OVERVIEW_FIT = 0.92
/** share of the viewport height they may take at most */
const OVERVIEW_FILL_V = 0.8

export interface PerspPose {
  position: Vec3
  target: Vec3
}

export interface TopPose {
  /** scene centre under the camera */
  center: Vec3
  /** half of the visible height in metres */
  halfHeight: number
}

export const TOP_HEIGHT = 8

export function boundsCenter(b: Bounds): { x: number; y: number } {
  return { x: (b.minX + b.maxX) / 2, y: (b.minY + b.maxY) / 2 }
}

/** Unit vector (ENU) from the target towards the overview camera, horizontal part only. */
export function overviewDirection(): { e: number; n: number } {
  const b = OVERVIEW_BEARING_DEG * DEG
  return { e: Math.sin(b), n: Math.cos(b) }
}

export function overviewDistance(b: Bounds, aspect: number, fovDeg = FOV_DEG): number {
  const dir = overviewDirection()
  const w = b.maxX - b.minX
  const d = b.maxY - b.minY
  // the right vector of the view is perpendicular to the horizontal view direction
  const across = w * Math.abs(dir.n) + d * Math.abs(dir.e)
  const along = w * Math.abs(dir.e) + d * Math.abs(dir.n)
  const el = OVERVIEW_ELEVATION_DEG * DEG
  const vertical = along * Math.sin(el) + b.maxZ * Math.cos(el)
  const tanV = Math.tan((fovDeg * DEG) / 2)
  const tanH = tanV * Math.max(0.2, aspect)
  // the bounds' width takes 60 % of the viewport width; the rotated footprint must still fit with a margin
  const dw = Math.max(w, d) / (OVERVIEW_FILL * 2 * tanH)
  const dfit = across / (OVERVIEW_FIT * 2 * tanH)
  const dv = vertical / (OVERVIEW_FILL_V * 2 * tanV)
  return Math.max(dw, dfit, dv, 1.2)
}

/** Overview pose for the framed bounds: target at the bounds centre, a little above ground when aircraft fly. */
export function overviewPose(b: Bounds, aspect: number, fovDeg = FOV_DEG): PerspPose {
  const c = boundsCenter(b)
  const tz = Math.min(1, b.maxZ * 0.4)
  return overviewPoseAt([c.x, c.y, tz], overviewDistance(b, aspect, fovDeg))
}

/** Overview direction and elevation looking at an ENU point from a given distance. */
export function overviewPoseAt(targetEnu: Vec3, distance: number): PerspPose {
  const dir = overviewDirection()
  const el = OVERVIEW_ELEVATION_DEG * DEG
  const h = distance * Math.cos(el)
  const camEnu: Vec3 = [targetEnu[0] + dir.e * h, targetEnu[1] + dir.n * h, targetEnu[2] + distance * Math.sin(el)]
  return { position: enuToScene(...camEnu), target: enuToScene(...targetEnu) }
}

export const CHASE_BACK = 1.2
export const CHASE_UP = 0.45
export const CHASE_AHEAD = 2

/** Chase: 1.2 m behind and 0.45 m above the aircraft, looking at a point 2 m ahead of the nose. Yaw only. */
export function chasePose(posEnu: Vec3, yaw: number): PerspPose {
  const fe = Math.sin(yaw)
  const fn = Math.cos(yaw)
  const cam: Vec3 = [posEnu[0] - fe * CHASE_BACK, posEnu[1] - fn * CHASE_BACK, posEnu[2] + CHASE_UP]
  const look: Vec3 = [posEnu[0] + fe * CHASE_AHEAD, posEnu[1] + fn * CHASE_AHEAD, posEnu[2]]
  return { position: enuToScene(...cam), target: enuToScene(...look) }
}

/** Top: straight down from 8 m, orthographic, north up; the framed bounds fill 60 % of the width. */
export function topPose(b: Bounds, aspect: number): TopPose {
  const c = boundsCenter(b)
  const w = b.maxX - b.minX
  const d = b.maxY - b.minY
  const halfH = Math.max(d / (2 * OVERVIEW_FILL_V), w / (2 * OVERVIEW_FILL * Math.max(0.2, aspect)), 1)
  return { center: enuToScene(c.x, c.y, 0), halfHeight: halfH }
}

/** Height at which a perspective camera sees the same ground extent as the orthographic top view. */
export function topEquivalentHeight(halfHeight: number, fovDeg = FOV_DEG): number {
  return halfHeight / Math.tan((fovDeg * DEG) / 2)
}

export type ViewMode = 'overview' | 'chase' | 'top'
export const VIEW_ORDER: readonly ViewMode[] = ['overview', 'chase', 'top']

/** Next view for the C key; chase is skipped while no aircraft can be chased. */
export function nextView(current: ViewMode, chaseAvailable: boolean): ViewMode {
  let i = VIEW_ORDER.indexOf(current)
  for (let k = 0; k < VIEW_ORDER.length; k++) {
    i = (i + 1) % VIEW_ORDER.length
    const v = VIEW_ORDER[i]
    if (v !== 'chase' || chaseAvailable) return v
  }
  return current
}
