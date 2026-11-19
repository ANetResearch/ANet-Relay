// Ground markers: the safety volume (02 §6.1), simulator spawn points (02 §6.3: dashed square and number on the
// 2 m grid) and pad slots for aircraft without a position (dashed 0.4 x 0.4 m square, "No position").
import * as React from 'react'
import * as THREE from 'three'
import { Html } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { useT } from '@/i18n'
import { useUi } from '@/store/ui'
import { anyPositioned, live, safetyOriginFor } from '../live'
import { enuToScene } from '../math'
import { slotPosition, SLOT_BOX } from '../placement'
import { metrics } from '../models'
import { useStage } from '../store'
import { token } from '../tokens'
import { dashedSquare } from './Aircraft'
import { markLayer } from './rigState'

/** Re-renders only when the string key computed in the frame loop changes. */
function useFrameKey(compute: () => string): string {
  const [key, setKey] = React.useState(compute)
  useFrame(() => {
    const k = compute()
    if (k !== key) setKey(k)
  })
  return key
}

export function SafetyVolume() {
  const mode = useStage((s) => s.layers.safety)
  const group = React.useRef<THREE.Group>(null)
  const parts = React.useMemo(() => {
    const box = new THREE.BoxGeometry(1, 1, 1)
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(box), new THREE.LineBasicMaterial({ color: token('g700').color, toneMapped: false }))
    edges.raycast = () => {}
    box.dispose()
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(1, 1).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: token('g800').color, toneMapped: false, transparent: true, opacity: 0.55, depthWrite: false }),
    )
    floor.position.y = 0.0008
    floor.renderOrder = -1
    floor.raycast = () => {}
    return { edges: markLayer(edges), floor: markLayer(floor) }
  }, [])

  useFrame(() => {
    const g = group.current
    if (!g) return
    const show = mode === 'on' || (mode === 'auto' && anyPositioned())
    g.visible = show
    if (!show) return
    const sel = useUi.getState().selectedId
    let focus = sel && live.targets.get(sel)?.kind !== 'slot' ? sel : null
    if (!focus) for (const t of live.targets.values()) if (t.kind !== 'slot') { focus = t.id; break }
    const [ox, oy, oz] = safetyOriginFor(focus)
    const { x, y, z } = live.safety
    const [sx, , sz] = enuToScene(ox, oy, oz)
    g.position.set(sx, 0, sz)
    parts.edges.scale.set(x, z, y)
    parts.edges.position.set(0, z / 2, 0)
    parts.floor.scale.set(x, 1, y)
  })

  return (
    <group ref={group}>
      <primitive object={parts.edges} />
      <primitive object={parts.floor} />
    </group>
  )
}

function SpawnBox({ n, x, y, size }: { n: number; x: number; y: number; size: number }) {
  const obj = React.useMemo(() => {
    const k = size / 0.4
    return dashedSquare(size, token('g700').color, 0.04 * k, 0.03 * k)
  }, [size])
  const [sx, , sz] = enuToScene(x, y, 0)
  return (
    <group position={[sx, 0.0015, sz]}>
      <primitive object={obj} />
      <Html position={[size / 2, 0, size / 2]} zIndexRange={[4, 0]} style={{ pointerEvents: 'none' }}>
        <span className="stage-tag stage-tag-corner">{n}</span>
      </Html>
    </group>
  )
}

export function SpawnPoints() {
  // the square fits the simulator that spawned there (12 §2.2: an ANetDU7 needs a larger one)
  const key = useFrameKey(() =>
    [...live.spawns.entries()]
      .map(([id, [x, y]]) => `${id}:${x}:${y}:${metrics(live.targets.get(id)?.model ?? 'micro').spawnBox.toFixed(3)}`)
      .sort()
      .join('|'),
  )
  const spawns = React.useMemo(
    () =>
      key
        ? key.split('|').map((s) => {
            const [id, x, y, size] = s.split(':')
            const n = Number(/(\d+)$/.exec(id)?.[1] ?? 0)
            return { id, n, x: Number(x), y: Number(y), size: Number(size) }
          })
        : [],
    [key],
  )
  return (
    <>
      {spawns.map((s) => (
        <SpawnBox key={s.id} n={s.n} x={s.x} y={s.y} size={s.size} />
      ))}
    </>
  )
}

function SlotBox({ x, y, size }: { x: number; y: number; size: number }) {
  const t = useT()
  const obj = React.useMemo(() => {
    const k = size / SLOT_BOX
    return dashedSquare(size, token('g600').color, 0.035 * k, 0.025 * k)
  }, [size])
  const [sx, , sz] = enuToScene(x, y, 0)
  return (
    <group position={[sx, 0.0015, sz]}>
      <primitive object={obj} />
      <Html position={[0, 0, size / 2]} zIndexRange={[4, 0]} style={{ pointerEvents: 'none' }}>
        <span className="stage-tag stage-tag-below">{t('stage.noPosition')}</span>
      </Html>
    </group>
  )
}

export function PadSlots() {
  const key = useFrameKey(() => {
    let n = 0
    for (const tg of live.targets.values()) if (tg.kind === 'slot') n++
    return `${n}:${live.row.y.toFixed(2)}:${live.row.cx.toFixed(2)}:${(live.row.pitch ?? 0).toFixed(3)}:${(live.row.box ?? SLOT_BOX).toFixed(3)}`
  })
  const [n, y, cx, pitch, box] = key.split(':').map(Number)
  const slots = Array.from({ length: n }, (_, i) => slotPosition({ y, cx, pitch: pitch || undefined }, i, n))
  return (
    <>
      {slots.map(([x, sy], i) => (
        <SlotBox key={i} x={x} y={sy} size={box} />
      ))}
    </>
  )
}
