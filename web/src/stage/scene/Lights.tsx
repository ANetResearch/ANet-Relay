// Lighting of 02 §6.1: hemisphere (sky g200, ground g950, 0.6), a directional key from the south-east above (1.4)
// whose 1024 shadow map covers only 4 m around the camera target, and drei contact shadows (blur 2.4, opacity
// 0.35) so every airframe sits on the ground.
import * as React from 'react'
import * as THREE from 'three'
import { useFrame } from '@react-three/fiber'
import { ContactShadows } from '@react-three/drei'
import { token } from '../tokens'
import { rig } from './rigState'

/** south-east, well above the horizon (scene: +X east, +Z south) */
const KEY_OFFSET = new THREE.Vector3(2.4, 5.2, 2.4)
const SHADOW_RADIUS = 4

export function Lights() {
  const key = React.useRef<THREE.DirectionalLight>(null)
  const sky = React.useMemo(() => token('g200').color.clone(), [])
  const ground = React.useMemo(() => token('g950').color.clone(), [])

  React.useLayoutEffect(() => {
    const l = key.current
    if (!l) return
    const cam = l.shadow.camera
    cam.left = -SHADOW_RADIUS
    cam.right = SHADOW_RADIUS
    cam.top = SHADOW_RADIUS
    cam.bottom = -SHADOW_RADIUS
    cam.near = 0.5
    cam.far = 16
    cam.updateProjectionMatrix()
    l.shadow.mapSize.set(1024, 1024)
    l.shadow.bias = -0.0004
    l.shadow.normalBias = 0.01
  }, [])

  useFrame(() => {
    const l = key.current
    if (!l) return
    l.position.copy(rig.target).add(KEY_OFFSET)
    l.target.position.copy(rig.target)
    l.target.updateMatrixWorld()
  })

  return (
    <>
      <hemisphereLight args={[sky, ground, 0.6]} />
      <directionalLight ref={key} intensity={1.4} castShadow />
    </>
  )
}

/** Contact shadows under the framed area; the plane follows the target and is resized only on large changes. */
export function GroundContact() {
  const group = React.useRef<THREE.Group>(null)
  const [scale, setScale] = React.useState(8)
  useFrame(() => {
    const g = group.current
    if (g) g.position.set(rig.target.x, 0.0005, rig.target.z)
    const want = Math.min(40, Math.max(4, Math.ceil(rig.extent * 2.6)))
    if (Math.abs(want - scale) / scale > 0.25) setScale(want)
  })
  return (
    <group ref={group}>
      <ContactShadows scale={scale} resolution={512} blur={2.4} opacity={0.35} far={1.2} frames={Infinity} />
    </group>
  )
}
