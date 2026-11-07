// One aircraft on the stage (02 §6.2-§6.4): the GoFilm 20 at real size with attitude, spinning propellers and a
// motion disc, the nose marker, ground rings for selection / possession, the screen-space label, the altitude line,
// the 20 s trail and, for simulators, the dashed base. Discrete facts (selection, controller, armed, link) come from
// narrow selectors; everything that moves is written by the shared frame loop in Fleet.tsx.
import * as React from 'react'
import * as THREE from 'three'
import { Html } from '@react-three/drei'
import type { ThreeEvent } from '@react-three/fiber'
import { useT } from '@/i18n'
import { useRelay } from '@/store/relay'
import { armedTone, criticalTone } from '@/store/red'
import { findVehicle, useRedOwner, useSelectedId } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { useShallow } from 'zustand/react/shallow'
import { useT8 } from '@/fleet/copy'
import { useHold } from '@/fleet/holds'
import { useReplay } from '@/records/replay'
import { HoldProgress, usePillSpec } from '@/ui/relay'
import { useStage } from '../store'
import { token } from '../tokens'
import { instantiate, type AirframeTemplate } from './model'

export const TRAIL_SECONDS = 20
export const TRAIL_HZ = 10
export const TRAIL_POINTS = TRAIL_SECONDS * TRAIL_HZ

/** Imperative handles the frame loop drives. */
export interface AircraftHandles {
  root: THREE.Group
  yaw: THREE.Group
  att: THREE.Group
  nose: THREE.Mesh
  props: THREE.Object3D[]
  discs: THREE.Mesh[]
  ground: THREE.Group
  pulse: THREE.LineLoop
  altLine: THREE.Line
  altLabel: { current: HTMLSpanElement | null }
  /** the screen label and its current declutter lift in px */
  label: { current: HTMLDivElement | null; lift: number }
  trail: THREE.Line
  trailCount: number
  trailLastAt: number
  /** smoothed throttle for the spin */
  throttle: number
}

export const handles = new Map<string, AircraftHandles>()

function circle(radius: number, color: THREE.Color, segments = 72): THREE.LineLoop {
  const pts: THREE.Vector3[] = []
  for (let i = 0; i < segments; i++) {
    const a = (i / segments) * Math.PI * 2
    pts.push(new THREE.Vector3(Math.cos(a) * radius, 0, Math.sin(a) * radius))
  }
  const geo = new THREE.BufferGeometry().setFromPoints(pts)
  const mat = new THREE.LineBasicMaterial({ color, toneMapped: false, transparent: true, depthWrite: false })
  const l = new THREE.LineLoop(geo, mat)
  l.raycast = () => {}
  return l
}

/** Dashed square of side `size` on the XZ plane. */
export function dashedSquare(size: number, color: THREE.Color, dash = 0.03, gap = 0.02): THREE.LineLoop {
  const h = size / 2
  const geo = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(-h, 0, -h), new THREE.Vector3(h, 0, -h), new THREE.Vector3(h, 0, h), new THREE.Vector3(-h, 0, h),
  ])
  const mat = new THREE.LineDashedMaterial({ color, dashSize: dash, gapSize: gap, toneMapped: false })
  const l = new THREE.LineLoop(geo, mat)
  l.computeLineDistances()
  l.raycast = () => {}
  return l
}

function noseMarker(color: THREE.Color, ahead: number, height: number): THREE.Mesh {
  // a 2 x 2 cm triangle lying flat, its apex pointing along the nose (-Z), 3 cm ahead of the nose tip
  const s = new THREE.Shape()
  s.moveTo(0, 0.01)
  s.lineTo(-0.01, -0.01)
  s.lineTo(0.01, -0.01)
  s.closePath()
  const geo = new THREE.ShapeGeometry(s)
  geo.rotateX(-Math.PI / 2)
  const mat = new THREE.MeshBasicMaterial({ color, toneMapped: false, side: THREE.DoubleSide })
  const m = new THREE.Mesh(geo, mat)
  m.position.set(0, height, -(ahead + 0.03 + 0.01))
  m.raycast = () => {}
  return m
}

function build(template: AirframeTemplate, sim: boolean): AircraftHandles {
  const root = new THREE.Group()
  const yaw = new THREE.Group()
  const att = new THREE.Group()
  att.rotation.order = 'XYZ'
  root.add(yaw)
  yaw.add(att)
  const inst = instantiate(template)
  att.add(inst.root)
  const nose = noseMarker(token('g50').color, template.noseAhead, template.lift)
  att.add(nose)

  const discMat = new THREE.MeshBasicMaterial({ color: token('g300').color, transparent: true, opacity: 0, depthWrite: false, toneMapped: false, side: THREE.DoubleSide })
  const discGeo = new THREE.CircleGeometry(0.0245, 32)
  discGeo.rotateX(-Math.PI / 2)
  const discs: THREE.Mesh[] = []
  for (const p of inst.props) {
    const d = new THREE.Mesh(discGeo, discMat)
    d.position.y = -0.0047
    d.raycast = () => {}
    p.add(d)
    discs.push(d)
  }

  if (sim) {
    const base = dashedSquare(0.18, token('g500').color, 0.02, 0.014)
    base.position.y = 0.001
    yaw.add(base)
  }

  const ground = new THREE.Group()
  const sel = circle(0.24, token('g50').color)
  sel.name = 'sel'
  sel.position.y = 0.002
  const outer = circle(0.3, token('g50').color)
  outer.name = 'possess'
  outer.position.y = 0.002
  const pulse = circle(0.24, token('g50').color)
  pulse.position.y = 0.002
  pulse.visible = false
  ground.add(sel, outer, pulse)

  const altGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 1, 0)])
  const altLine = new THREE.Line(altGeo, new THREE.LineBasicMaterial({ color: token('g600').color, toneMapped: false }))
  altLine.raycast = () => {}
  altLine.frustumCulled = false

  const trailGeo = new THREE.BufferGeometry()
  trailGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(TRAIL_POINTS * 3), 3))
  trailGeo.setDrawRange(0, 0)
  const trail = new THREE.Line(trailGeo, new THREE.LineBasicMaterial({ color: token('trail-default').color, toneMapped: false, transparent: true, opacity: 0.8 }))
  trail.frustumCulled = false
  trail.raycast = () => {}

  return {
    root, yaw, att, nose, props: inst.props, discs, ground, pulse, altLine,
    altLabel: { current: null }, label: { current: null, lift: 0 }, trail, trailCount: 0, trailLastAt: 0, throttle: 0,
  }
}

/** Discrete visual state; module-level so the frame-owned objects are only mutated outside render. */
function applyFlags(h: AircraftHandles, f: { selected: boolean; possessed: boolean; armedRed: boolean }): void {
  const sel = h.ground.getObjectByName('sel')
  const outer = h.ground.getObjectByName('possess')
  if (sel) sel.visible = f.selected
  if (outer) outer.visible = f.possessed
  ;(h.trail.material as THREE.LineBasicMaterial).color.copy(token(f.selected ? 'trail-selected' : 'trail-default').color)
  ;(h.nose.material as THREE.MeshBasicMaterial).color.copy(token(f.armedRed ? 'r500' : 'g50').color)
}

function setTrailVisible(h: AircraftHandles, v: boolean): void {
  h.trail.visible = v
}

function bindAltLabel(h: AircraftHandles, el: HTMLSpanElement | null): void {
  h.altLabel.current = el
}

function bindLabel(h: AircraftHandles, el: HTMLDivElement | null): void {
  h.label.current = el
  h.label.lift = 0
}

function disposeHandles(h: AircraftHandles): void {
  h.trail.geometry.dispose()
  h.altLine.geometry.dispose()
}

interface Facts {
  label: string
  sim: boolean
  armed: boolean
  human: boolean
  lostS: number | null
  critical: boolean
  controller: 'none' | 'human' | 'agent'
  claim: { name: string; suspended: boolean } | null
  possession: { input: 'keyboard' | 'screen' | 'gamepad'; pad: number | null } | null
}

/** Narrow, discrete facts for the label and colours; changes at most once a second while the link is lost. */
function useFacts(id: string): Facts | null {
  const key = useRelay((s) => {
    const v = findVehicle(s.fleet?.vehicles, id)
    if (!v) return ''
    const lost = v.link.state === 'lost' || v.link.state === 'offline' ? Math.floor(v.link.heartbeat_age_s ?? 0) : null
    const critical = (s.fleet?.alerts ?? []).some((a) => a.vehicle === id && a.level === 'critical' && !a.acked && !s.localAcks.has(a.id))
    const f: Facts = {
      label: v.label, sim: v.kind === 'sim', armed: v.armed, human: v.controller === 'human', lostS: lost, critical,
      controller: v.controller,
      claim: v.claim ? { name: v.claim.name, suspended: v.claim.suspended } : null,
      possession: v.possession ? { input: v.possession.input, pad: v.possession.pad } : null,
    }
    return JSON.stringify(f)
  })
  return React.useMemo(() => (key ? (JSON.parse(key) as Facts) : null), [key])
}

function Label({ id, facts, fallback, bind }: { id: string; facts: Facts | null; fallback: string; bind(el: HTMLDivElement | null): void }) {
  const t = useT()
  const t8 = useT8()
  const owner = useRedOwner()
  const spec = usePillSpec(
    facts
      ? { controller: facts.controller, claim: facts.claim ? { session_id: '', since: 0, ...facts.claim } : null, possession: facts.possession ? { ...facts.possession, since: 0 } : null }
      : { controller: 'none', claim: null, possession: null },
  )
  const hold = useHold(id)
  // Records replay: the label states the recorded armed state and controller, not the live ones
  const replay = useReplay(useShallow((r) => (r.active && r.vehicleId === id && r.frame ? { armed: r.frame.armed, controller: r.frame.controller ?? 'none' } : null)))
  const armed = replay ? replay.armed : facts?.armed
  const lost = replay ? null : (facts?.lostS ?? null)
  const critical = replay ? false : facts?.critical
  const armedRed = armed && armedTone(owner) === 'red'
  const critRed = critical && criticalTone(owner) === 'red'
  return (
    <div className="stage-label" ref={bind} data-lost={lost !== null ? '' : undefined} data-vehicle={id}>
      <div className="stage-label-name">
        {armed ? <span className={armedRed ? 'stage-armed-red' : 'stage-armed-strong'}>{t('status.armed')}</span> : null}
        {facts?.sim ? <span className="stage-label-sim">{t('kind.sim')}</span> : null}
        <span className={critRed ? 'stage-crit-red' : critical ? 'stage-crit-strong' : undefined}>{facts?.label ?? fallback}</span>
      </div>
      <div className="stage-label-sub">
        {lost !== null ? <span>{t('status.lostFor', { s: lost })}</span> : <span>{replay ? t8(`records.ctl.${replay.controller}`) : spec.label}</span>}
      </div>
      {hold ? <HoldProgress active durationMs={hold.durationMs} className="stage-label-hold" /> : null}
    </div>
  )
}

export interface AircraftProps {
  id: string
  template: AirframeTemplate
  /** label to show when the vehicle is not in the live fleet (replay) */
  fallbackLabel?: string
  /** simulator flag when the live fleet does not know the vehicle (replay) */
  fallbackSim?: boolean
}

export const Aircraft = React.memo(function Aircraft({ id, template, fallbackLabel, fallbackSim }: AircraftProps) {
  const facts = useFacts(id)
  const sim = facts?.sim ?? fallbackSim ?? false
  const h = React.useMemo(() => build(template, sim), [template, sim])
  const selected = useSelectedId() === id
  const owner = useRedOwner()
  const labels = useStage((s) => s.layers.labels)
  const trails = useStage((s) => s.layers.trails)

  React.useEffect(() => {
    handles.set(id, h)
    return () => {
      if (handles.get(id) === h) handles.delete(id)
    }
  }, [id, h])

  // discrete colours
  const armedRed = !!facts?.armed && armedTone(owner) === 'red'
  React.useEffect(() => applyFlags(h, { selected, possessed: !!facts?.human, armedRed }), [h, selected, facts?.human, armedRed])
  React.useEffect(() => setTrailVisible(h, trails), [h, trails])

  React.useEffect(() => () => disposeHandles(h), [h])

  const onClick = React.useCallback(
    (e: ThreeEvent<MouseEvent>) => {
      e.stopPropagation()
      useUi.getState().select(id)
    },
    [id],
  )
  const onDouble = React.useCallback(
    (e: ThreeEvent<MouseEvent>) => {
      e.stopPropagation()
      useUi.getState().select(id)
      useStage.getState().requestFlyTo(id)
    },
    [id],
  )
  const over = React.useCallback(() => {
    document.body.style.cursor = 'pointer'
  }, [])
  const out = React.useCallback(() => {
    document.body.style.cursor = ''
  }, [])

  return (
    <>
      <primitive object={h.root}>
        <mesh onClick={onClick} onDoubleClick={onDouble} onPointerOver={over} onPointerOut={out} position={[0, template.lift, 0]}>
          <sphereGeometry args={[0.11, 12, 8]} />
          <meshBasicMaterial visible={false} />
        </mesh>
        {labels ? (
          <Html position={[0, 0.12, 0]} zIndexRange={[12, 0]} style={{ pointerEvents: 'none' }}>
            <Label id={id} facts={facts} fallback={fallbackLabel ?? id} bind={(el) => bindLabel(h, el)} />
          </Html>
        ) : null}
      </primitive>
      <primitive object={h.ground}>
        {labels ? (
          <Html position={[0.3, 0, 0]} zIndexRange={[11, 0]} style={{ pointerEvents: 'none' }}>
            <span className="stage-alt" ref={(el: HTMLSpanElement | null) => bindAltLabel(h, el)} />
          </Html>
        ) : null}
      </primitive>
      <primitive object={h.altLine} />
      <primitive object={h.trail} />
    </>
  )
})
