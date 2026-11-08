// All aircraft and the one frame loop that moves them. Targets come from ./live (a store subscription outside
// React); here they are smoothed for display only (02 §6.3: 120 ms exponential smoothing of position and attitude,
// with a short dead-reckoning step so 10 Hz snapshots glide instead of stepping).
import * as React from 'react'
import * as THREE from 'three'
import { useFrame } from '@react-three/fiber'
import { useShallow } from 'zustand/react/shallow'
import { useRelay } from '@/store/relay'
import { useUi } from '@/store/ui'
import { motionReduced } from '@/ui/motion/tier'
import { useReplay } from '@/records/replayState'
import { live } from '../live'
import { enuToScene, expAlpha, smoothAngle } from '../math'
import { useStage } from '../store'
import { Aircraft, handles, TRAIL_HZ, TRAIL_POINTS, type AircraftHandles } from './Aircraft'
import { trailJump } from './trail'
import type { AirframeTemplate } from './model'

const SMOOTH_TAU = 0.12
const LEAD_MAX_S = 0.15
const SNAP_DISTANCE = 3
const PULSE_MS = 350

const tmp = new THREE.Vector3()
const proj = new THREE.Vector3()
const LABEL_LIFT = 0.12

interface LabelBox {
  h: AircraftHandles
  x: number
  y: number
  w: number
  ht: number
  depth: number
  selected: boolean
}

/**
 * Screen-space label declutter: labels are placed nearest first (the selected aircraft's always first) and lifted
 * just enough to clear the ones already placed, so a row of pad slots or a tight formation stays legible. Lifts
 * ease towards their goal to avoid jitter.
 */
function declutter(camera: THREE.Camera, size: { width: number; height: number }, dt: number): void {
  // frame-rate independent easing: 0.35 per frame at 60 Hz, faster per frame on slow (software GL) hosts
  const k = expAlpha(dt, 0.038)
  const sel = useUi.getState().selectedId
  const boxes: LabelBox[] = []
  for (const [id, h] of handles) {
    const el = h.label.current
    if (!el || !h.root.visible) continue
    proj.copy(h.root.position)
    proj.y += LABEL_LIFT
    proj.project(camera)
    if (proj.z > 1) continue
    boxes.push({
      h,
      x: (proj.x * 0.5 + 0.5) * size.width,
      y: (-proj.y * 0.5 + 0.5) * size.height,
      w: el.offsetWidth,
      ht: el.offsetHeight,
      depth: proj.z,
      selected: id === sel,
    })
  }
  boxes.sort((a, b) => (a.selected === b.selected ? a.depth - b.depth : a.selected ? -1 : 1))
  const placed: { l: number; r: number; t: number; b: number }[] = []
  for (const bx of boxes) {
    const l = bx.x - bx.w / 2 - 2
    const r = bx.x + bx.w / 2 + 2
    let bottom = bx.y - 4
    for (let k = 0; k < 8; k++) {
      const top = bottom - bx.ht
      const hit = placed.find((p) => l < p.r && r > p.l && top < p.b && bottom > p.t)
      if (!hit) break
      bottom = hit.t - 2
    }
    placed.push({ l, r, t: bottom - bx.ht, b: bottom })
    const goal = bx.y - 4 - bottom
    const lift = bx.h.label.lift + (goal - bx.h.label.lift) * k
    const next = Math.abs(goal - lift) < 0.5 ? goal : lift
    if (Math.abs(next - bx.h.label.lift) > 0.05) {
      bx.h.label.lift = next
      bx.h.label.current!.style.translate = `0 ${(-next).toFixed(1)}px`
    }
  }
}

/** Live fleet ids, or only the replayed aircraft while Records replays a flight. */
function useIds(): { id: string; label: string; sim: boolean }[] {
  const liveIds = useRelay(useShallow((s) => (s.fleet?.vehicles ?? []).map((v) => v.id)))
  const replay = useReplay(useShallow((r) => (r.active && r.vehicleId ? [r.vehicleId, r.label, r.kind] : null)))
  // WP-3: the aircraft of a multi-aircraft trace, keyed by id and label only (frames change every tick)
  const crafts = useReplay(useShallow((r) => (r.active && r.crafts ? r.crafts.map((c) => `${c.id}\u0000${c.label}`) : null)))
  return React.useMemo(() => {
    if (crafts) return crafts.map((k) => { const [id, label] = k.split('\u0000'); return { id, label, sim: true } })
    if (replay) return [{ id: replay[0], label: replay[1], sim: replay[2] === 'sim' }]
    return liveIds.map((id) => ({ id, label: id, sim: id.startsWith('sim-') }))
  }, [replay, crafts, liveIds])
}

export function Fleet({ template }: { template: AirframeTemplate }) {
  const ids = useIds()

  useFrame((state, rawDt) => {
    const dt = Math.min(0.1, rawDt)
    const now = performance.now()
    const a = expAlpha(dt, SMOOTH_TAU)
    const pulses = useStage.getState().pulses
    const reduced = motionReduced()
    for (const [id, h] of handles) {
      const t = live.targets.get(id)
      if (!t) {
        h.root.visible = false
        h.ground.visible = false
        h.altLine.visible = false
        continue
      }
      h.root.visible = true
      h.ground.visible = true
      let st = live.state.get(id)
      const lead = t.kind === 'position' ? Math.min(LEAD_MAX_S, (now - t.at) / 1000) : 0
      const [sx, sy, sz] = enuToScene(t.x + t.vx * lead, t.y + t.vy * lead, Math.max(0, t.z + t.vz * lead))
      tmp.set(sx, sy, sz)
      if (!st) {
        st = { pos: tmp.clone(), yaw: t.yaw ?? 0, kind: t.kind, ready: true }
        live.state.set(id, st)
        h.att.rotation.set(t.pitch ?? 0, 0, -(t.roll ?? 0))
      }
      st.kind = t.kind
      if (st.pos.distanceTo(tmp) > SNAP_DISTANCE) st.pos.copy(tmp)
      else st.pos.lerp(tmp, a)
      st.yaw = smoothAngle(st.yaw, t.yaw ?? st.yaw, a)
      const pitch = t.pitch ?? 0
      const roll = t.roll ?? 0
      h.att.rotation.x = smoothAngle(h.att.rotation.x, pitch, a)
      h.att.rotation.z = smoothAngle(h.att.rotation.z, -roll, a)

      h.root.position.copy(st.pos)
      h.yaw.rotation.y = -st.yaw
      h.ground.position.set(st.pos.x, 0, st.pos.z)

      // propellers: still while disarmed, 8 + 40 x throttle rev/s while armed, plus a faint motion disc
      h.throttle += ((t.armed ? t.throttle : 0) - h.throttle) * expAlpha(dt, 0.15)
      const rps = t.armed ? 8 + 40 * h.throttle : 0
      if (rps > 0) for (const p of h.props) p.rotation.y += (p.userData.relayProp as number) * rps * 2 * Math.PI * dt
      const discOpacity = t.armed ? 0.08 * h.throttle : 0
      if (h.discs.length) (h.discs[0].material as THREE.MeshBasicMaterial).opacity = discOpacity

      // altitude line above 0.2 m with the height written at its foot
      const height = st.pos.y
      const showAlt = t.kind !== 'slot' && height > 0.2
      h.altLine.visible = showAlt
      if (showAlt) {
        const pos = h.altLine.geometry.getAttribute('position') as THREE.BufferAttribute
        pos.setXYZ(0, st.pos.x, 0, st.pos.z)
        pos.setXYZ(1, st.pos.x, height, st.pos.z)
        pos.needsUpdate = true
      }
      const label = h.altLabel.current
      if (label) {
        const text = showAlt ? `${height.toFixed(2)} m` : ''
        if (label.textContent !== text) label.textContent = text
      }

      // trail: one sample per 100 ms, the last 20 s
      if (t.kind !== 'slot' && now - h.trailLastAt >= 1000 / TRAIL_HZ) {
        h.trailLastAt = now
        const attr = h.trail.geometry.getAttribute('position') as THREE.BufferAttribute
        const arr = attr.array as Float32Array
        if (h.trailCount && trailJump(arr, (h.trailCount - 1) * 3, st.pos)) h.trailCount = 0 // a respawn, not a flight
        if (h.trailCount >= TRAIL_POINTS) arr.copyWithin(0, 3)
        else h.trailCount++
        const i = (h.trailCount - 1) * 3
        arr[i] = st.pos.x
        arr[i + 1] = st.pos.y
        arr[i + 2] = st.pos.z
        attr.needsUpdate = true
        h.trail.geometry.setDrawRange(0, h.trailCount)
      } else if (t.kind === 'slot' && h.trailCount) {
        h.trailCount = 0
        h.trail.geometry.setDrawRange(0, 0)
      }

      // one-shot ripple of the selection ring (0.24 -> 0.5 m, opacity 0.8 -> 0, medium 350 ms)
      const p = pulses.length ? pulses.findLast((x) => x.id === id) : undefined
      const age = p ? now - p.at : Infinity
      if (!reduced && age < PULSE_MS) {
        const k = age / PULSE_MS
        const e = 1 - (1 - k) * (1 - k)
        const r = (0.24 + 0.26 * e) / 0.24
        h.pulse.visible = true
        h.pulse.scale.set(r, 1, r)
        ;(h.pulse.material as THREE.LineBasicMaterial).opacity = 0.8 * (1 - e)
      } else if (h.pulse.visible) {
        h.pulse.visible = false
      }
    }
    declutter(state.camera, state.size, dt)
  })

  return (
    <>
      {ids.map((v) => (
        <Aircraft key={v.id} id={v.id} template={template} fallbackLabel={v.label} fallbackSim={v.sim} />
      ))}
    </>
  )
}
