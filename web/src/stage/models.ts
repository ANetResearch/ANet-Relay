// Aircraft models of the stage (12 §2.2, §5.5). The table mirrors GET /capabilities -> models (span and height of
// the airframe, cage included); everything the stage sizes per aircraft (rings, nose marker, propeller discs, the
// simulator base, the click target, label height, pad slots, camera distances) derives from it here, so a mixed
// fleet stays consistent and nothing else carries a per-model constant. Pure: no three.js, no React.
//
// Sizing rule: things that frame an airframe on the ground (base square, click target) grow with its span;
// things that mark it for the eye (rings, nose marker, label gap, camera distances) grow more slowly, by
// presence() = (span / micro span)^0.6, so an ANetDU7 does not swamp the 2 m spawn grid while it still reads as the
// bigger aircraft. The micro values are exactly the pre-0.6 constants of the GoFilm 20 stage.

export type ModelId = 'anet-du7' | 'micro'
export const MODEL_IDS: readonly ModelId[] = ['anet-du7', 'micro']

export interface ModelSpec {
  title: string
  /** outer span in metres (cage or propeller tips), the capabilities' span_m */
  span: number
  /** height in metres from the feet, the capabilities' height_m */
  height: number
  /** carries a gimbal pod (Gimbal_Yaw / Gimbal_Pitch / Gimbal_Camera nodes) */
  gimbal: boolean
  /** the GLB under public/models */
  file: string
  /** propeller disc radius (m) and its height relative to the propeller pivot */
  disc: { r: number; y: number }
}

/** Mirrors GET /capabilities -> models of backend 0.6.0 (relay/api/routes_fleet.py); stage.test.ts checks the micro. */
export const MODELS: Readonly<Record<ModelId, ModelSpec>> = {
  'anet-du7': { title: 'ANetDU7', span: 0.684, height: 0.211, gimbal: true, file: 'anet_du7.glb', disc: { r: 0.127, y: 0 } },
  micro: { title: 'Micro 120', span: 0.166, height: 0.045, gimbal: false, file: 'caddxfpv_gofilm20.glb', disc: { r: 0.0245, y: -0.0047 } },
}

/** The model of a vehicle-like object; anything unknown (records, traces, older backends) is the micro. */
export function modelOf(v: { model?: unknown } | null | undefined): ModelId {
  return v?.model === 'anet-du7' ? 'anet-du7' : 'micro'
}

export function modelUrl(model: ModelId, base = import.meta.env?.BASE_URL ?? '/'): string {
  return `${base}models/${MODELS[model].file}`
}

const MICRO_SPAN = 0.166

/** How much more presence an airframe of `span` gets than the micro (1 for the micro). */
export function presence(span: number): number {
  return Math.pow(Math.max(0.05, span) / MICRO_SPAN, 0.6)
}

export interface ModelMetrics {
  model: ModelId
  span: number
  height: number
  /** selection ring radius */
  ring: number
  /** possession (outer) ring radius */
  possess: number
  /** radius the one-shot pulse grows to */
  pulseTo: number
  /** dashed base square of a simulator, side */
  base: number
  /** invisible click target (sphere) radius */
  hit: number
  /** screen label anchor height above the feet */
  label: number
  /** altitude label offset east of the ground marker */
  altLabel: number
  /** nose marker: triangle size and the gap ahead of the nose tip */
  nose: { size: number; gap: number }
  disc: { r: number; y: number }
  /** dashed spawn square of a simulator spawn point, side */
  spawnBox: number
  /** pad slot square, side, and the slot pitch in the pad row */
  slotBox: number
  slotPitch: number
  /** camera: fly-to distance, chase pose, the overview footprint of a lone aircraft and the closest overview */
  flyTo: number
  chase: { back: number; up: number; ahead: number }
  single: number
  minDistance: number
}

const cache = new Map<ModelId, ModelMetrics>()

/** Every per-aircraft size of the stage for one model. */
export function metrics(model: ModelId): ModelMetrics {
  let m = cache.get(model)
  if (m) return m
  const spec = MODELS[model] ?? MODELS.micro
  const linear = spec.span / MICRO_SPAN
  const k = presence(spec.span)
  const ring = 0.24 * k
  m = {
    model,
    span: spec.span,
    height: spec.height,
    ring,
    possess: 0.3 * k,
    pulseTo: 0.5 * k,
    base: 0.18 * linear,
    hit: 0.11 * linear,
    label: spec.height + 0.075 * k,
    altLabel: ring + 0.06 * k,
    nose: { size: 0.02 * k, gap: 0.03 * k },
    disc: spec.disc,
    spawnBox: 0.4 * k,
    slotBox: 0.4 * k,
    slotPitch: Math.max(0.6 * k, 0.4 * k + 0.2),
    flyTo: 1.6 * k,
    chase: { back: 1.2 * k, up: 0.45 * k, ahead: 2 * k },
    single: 0.8 * k,
    minDistance: 1.2 * k,
  }
  cache.set(model, m)
  return m
}

/** The largest metrics among models (pad row pitch, overview footprint of a mixed fleet). */
export function largest(models: Iterable<ModelId>): ModelMetrics {
  let best = metrics('micro')
  for (const id of models) {
    const m = metrics(id)
    if (m.span > best.span) best = m
  }
  return best
}
