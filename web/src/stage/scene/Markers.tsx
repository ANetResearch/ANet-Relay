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
import { slotPosition } from '../placement'
import { useStage } from '../store'
import { token } from '../tokens'
import { dashedSquare } from './Aircraft'

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
    return { edges, floor }
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

function SpawnBox({ n, x, y }: { n: number; x: number; y: number }) {
  const obj = React.useMemo(() => {
    const l = dashedSquare(0.4, token('g700').color, 0.04, 0.03)
    return l
  }, [])
  const [sx, , sz] = enuToScene(x, y, 0)
  return (
    <group position={[sx, 0.0015, sz]}>
      <primitive object={obj} />
      <Html position={[0.2, 0, 0.2]} zIndexRange={[4, 0]} style={{ pointerEvents: 'none' }}>
        <span className="stage-tag stage-tag-corner">{n}</span>
      </Html>
    </group>
  )
}

export function SpawnPoints() {
  const key = useFrameKey(() =>
    [...live.spawns.entries()]
      .map(([id, [x, y]]) => `${id}:${x}:${y}`)
      .sort()
      .join('|'),
  )
  const spawns = React.useMemo(
    () =>
      key
        ? key.split('|').map((s) => {
            const [id, x, y] = s.split(':')
            const n = Number(/(\d+)$/.exec(id)?.[1] ?? 0)
            return { id, n, x: Number(x), y: Number(y) }
          })
        : [],
    [key],
  )
  return (
    <>
      {spawns.map((s) => (
        <SpawnBox key={s.id} n={s.n} x={s.x} y={s.y} />
      ))}
    </>
  )
}

function SlotBox({ x, y }: { x: number; y: number }) {
  const t = useT()
  const obj = React.useMemo(() => dashedSquare(0.4, token('g600').color, 0.035, 0.025), [])
  const [sx, , sz] = enuToScene(x, y, 0)
  return (
    <group position={[sx, 0.0015, sz]}>
      <primitive object={obj} />
      <Html position={[0, 0, 0.2]} zIndexRange={[4, 0]} style={{ pointerEvents: 'none' }}>
        <span className="stage-tag stage-tag-below">{t('stage.noPosition')}</span>
      </Html>
    </group>
  )
}

export function PadSlots() {
  const key = useFrameKey(() => {
    let n = 0
    for (const tg of live.targets.values()) if (tg.kind === 'slot') n++
    return `${n}:${live.row.y.toFixed(2)}:${live.row.cx.toFixed(2)}`
  })
  const [n, y, cx] = key.split(':').map(Number)
  const slots = Array.from({ length: n }, (_, i) => slotPosition({ y, cx }, i, n))
  return (
    <>
      {slots.map(([x, sy], i) => (
        <SlotBox key={i} x={x} y={sy} />
      ))}
    </>
  )
}
