// Ground of 02 §6.1: an infinite grid (0.5 m minor, 2.5 m major lines) fading out linearly over 30 m around the
// camera target, the 1 m origin cross and a shadow-catcher for the directional light. Line widths are constant in
// pixels (fwidth), so the grid stays crisp at any distance.
import * as React from 'react'
import * as THREE from 'three'
import { useFrame } from '@react-three/fiber'
import { token } from '../tokens'
import { rig } from './rigState'

const vertex = /* glsl */ `
  varying vec3 vWorld;
  void main() {
    vec4 w = modelMatrix * vec4(position, 1.0);
    vWorld = w.xyz;
    gl_Position = projectionMatrix * viewMatrix * w;
  }
`

const fragment = /* glsl */ `
  uniform vec3 uMinor;
  uniform vec3 uMajor;
  uniform float uMinorA;
  uniform float uMajorA;
  uniform vec2 uCenter;
  uniform float uRadius;
  varying vec3 vWorld;

  float gridLine(vec2 p, float spacing) {
    vec2 q = p / spacing;
    vec2 d = abs(fract(q - 0.5) - 0.5) / fwidth(q);
    return 1.0 - min(min(d.x, d.y), 1.0);
  }

  void main() {
    vec2 p = vWorld.xz;
    float minor = gridLine(p, 0.5);
    float major = gridLine(p, 2.5);
    float fade = clamp(1.0 - distance(p, uCenter) / uRadius, 0.0, 1.0);
    vec3 col = mix(uMinor, uMajor, major);
    float a = max(minor * uMinorA, major * uMajorA) * fade;
    if (a < 0.003) discard;
    gl_FragColor = vec4(col, a);
    #include <colorspace_fragment>
  }
`

export function Grid({ visible }: { visible: boolean }) {
  const mesh = React.useRef<THREE.Mesh>(null)
  const material = React.useMemo(() => {
    const minor = token('scene-grid-minor')
    const major = token('scene-grid-major')
    return new THREE.ShaderMaterial({
      vertexShader: vertex,
      fragmentShader: fragment,
      transparent: true,
      depthWrite: false,
      uniforms: {
        uMinor: { value: minor.color.clone() },
        uMajor: { value: major.color.clone() },
        uMinorA: { value: minor.alpha },
        uMajorA: { value: major.alpha },
        uCenter: { value: new THREE.Vector2() },
        uRadius: { value: 30 },
      },
    })
  }, [])
  React.useEffect(() => () => material.dispose(), [material])

  useFrame(() => {
    const m = mesh.current
    if (!m) return
    const c = rig.target
    // follow the camera target in whole major cells so the plane never runs out under the fade radius
    m.position.set(Math.round(c.x / 2.5) * 2.5, 0, Math.round(c.z / 2.5) * 2.5)
    ;(material.uniforms.uCenter.value as THREE.Vector2).set(c.x, c.z)
  })

  return (
    <mesh ref={mesh} rotation-x={-Math.PI / 2} material={material} visible={visible} renderOrder={-2} raycast={() => {}}>
      <planeGeometry args={[72, 72, 1, 1]} />
    </mesh>
  )
}

/** 1 m cross at the origin in g600. */
export function OriginCross({ visible }: { visible: boolean }) {
  const obj = React.useMemo(() => {
    const geo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(-0.5, 0.001, 0), new THREE.Vector3(0.5, 0.001, 0),
      new THREE.Vector3(0, 0.001, -0.5), new THREE.Vector3(0, 0.001, 0.5),
    ])
    const mat = new THREE.LineBasicMaterial({ color: token('g600').color, toneMapped: false })
    return new THREE.LineSegments(geo, mat)
  }, [])
  React.useEffect(() => () => {
    obj.geometry.dispose()
    ;(obj.material as THREE.Material).dispose()
  }, [obj])
  return <primitive object={obj} visible={visible} />
}

/** Receives the directional light's shadow (the ShadowMaterial draws only the shadow, nothing else). */
export function ShadowCatcher() {
  const mesh = React.useRef<THREE.Mesh>(null)
  useFrame(() => {
    const m = mesh.current
    if (m) m.position.set(rig.target.x, 0, rig.target.z)
  })
  return (
    <mesh ref={mesh} rotation-x={-Math.PI / 2} receiveShadow renderOrder={-1} raycast={() => {}}>
      <planeGeometry args={[10, 10]} />
      <shadowMaterial transparent opacity={0.28} depthWrite={false} />
    </mesh>
  )
}
