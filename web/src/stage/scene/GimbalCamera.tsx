// The gimbal pod's picture (12 §5.5): the stage scene rendered from an aircraft's Gimbal_Camera with the pod's
// vertical field of view, into a small render target that is read back asynchronously and handed to the feed
// (gimbal/feed.ts) for the picture-in-picture and capture thumbnails. Renders only while a feed is open, at the
// feed's capped rate and size, between the stage's own frames; the stage's render loop and quality selection
// (gpu.ts) are untouched.
//
// The render target is flagged like an XR target with an sRGB texture on linear RGBA8 storage, so the renderer
// applies the stage's ACES tone mapping, exposure and sRGB encoding exactly as on screen and reuses the stage's
// compiled programs (no shader variants, no first-use hitch); the bytes read back are display-ready. The camera
// sees layer 0 (the stage's rings, trails and squares are on the mark layer) and its own ground on layer 2, which no
// stage camera sees; the carrying airframe is hidden for the pass and the shadow map of the stage's last frame is
// reused.
import * as React from 'react'
import * as THREE from 'three'
import { useFrame, useThree } from '@react-three/fiber'
import { useRelay } from '@/store/relay'
import { findVehicle } from '@/store/selectors'
import { feedHost, feedSlots, type FeedSlot } from '@/gimbal/feed'
import { fovDeg, isInfrared, paletteLut, toImage, verticalFov, type Lens, type Palette } from '@/gimbal/model'
import { token } from '../tokens'
import { handles } from './Aircraft'

/** Layer of objects only the pod camera sees (its ground). */
const POD_LAYER = 2

/** The ground as the pod's camera sees it: a matte plane a step lighter than the sky, fading out by 60 m. */
function podGround(): THREE.Mesh {
  const n = 64
  const alpha = new Uint8Array(n * n * 4)
  for (let y = 0; y < n; y++)
    for (let x = 0; x < n; x++) {
      const r = Math.hypot(x + 0.5 - n / 2, y + 0.5 - n / 2) / (n / 2)
      const a = Math.round(255 * Math.min(1, Math.max(0, (1 - r) / 0.7)))
      alpha.set([a, a, a, 255], (y * n + x) * 4)
    }
  const map = new THREE.DataTexture(alpha, n, n)
  map.needsUpdate = true
  map.magFilter = THREE.LinearFilter
  // not tone-mapped: the token's grey as it is (ACES would sink it into the sky)
  const material = new THREE.MeshBasicMaterial({ color: token('scene-pod-ground').color.clone(), alphaMap: map, transparent: true, depthWrite: true, toneMapped: false })
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(120, 120), material)
  mesh.rotation.x = -Math.PI / 2
  mesh.position.y = -0.003
  mesh.renderOrder = -3
  mesh.layers.set(POD_LAYER)
  mesh.raycast = () => {}
  mesh.name = 'relay-pod-ground'
  return mesh
}

const FLIP = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI)
const pos = new THREE.Vector3()
const quat = new THREE.Quaternion()
const scl = new THREE.Vector3()

interface Target {
  rt: THREE.WebGLRenderTarget
  buf: Uint8Array
}

const luts = new Map<Palette, Uint8Array>()
const lutOf = (p: Palette) => {
  let l = luts.get(p)
  if (!l) {
    l = paletteLut(p)
    luts.set(p, l)
  }
  return l
}

class GimbalRenderer {
  private readonly cam = new THREE.PerspectiveCamera(36, 16 / 9, 0.03, 400)
  private readonly targets = new Map<string, Target>()
  private readonly ground = podGround()

  constructor(
    private readonly gl: THREE.WebGLRenderer,
    private readonly scene: THREE.Scene,
    private readonly samples: number,
  ) {
    this.cam.layers.set(0)
    this.cam.layers.enable(POD_LAYER)
  }

  attach(): void {
    this.scene.add(this.ground)
  }

  private target(w: number, h: number): Target {
    const key = `${w}x${h}`
    let t = this.targets.get(key)
    if (!t) {
      const rt = new THREE.WebGLRenderTarget(w, h, { samples: this.samples, depthBuffer: true })
      rt.texture.colorSpace = THREE.SRGBColorSpace
      rt.texture.internalFormat = 'RGBA8'
      // XR-style output: tone mapping and the texture's colour space, as for the screen (see the header)
      ;(rt as unknown as { isXRRenderTarget: boolean }).isXRRenderTarget = true
      t = { rt, buf: new Uint8Array(w * h * 4) }
      this.targets.set(key, t)
    }
    return t
  }

  /** Renders the scene from the pod camera of `id` into a target of w x h; false when it cannot. */
  private render(id: string, lens: Lens, vfov: number, t: Target): boolean {
    const h = handles.get(id)
    const g = h?.gimbal
    if (!h || !g || !h.root.visible) return false
    g.camera.updateWorldMatrix(true, false)
    g.camera.matrixWorld.decompose(pos, quat, scl)
    const cam = this.cam
    cam.position.copy(pos)
    // the pod camera looks along its local +Z; a three.js camera along its local -Z
    cam.quaternion.copy(quat).multiply(FLIP)
    cam.fov = vfov
    cam.aspect = 16 / 9
    cam.near = lens === 'visible1' ? 0.05 : 0.03
    cam.updateProjectionMatrix()
    cam.updateMatrixWorld(true)
    this.ground.position.set(Math.round(pos.x), -0.003, Math.round(pos.z))
    this.ground.updateMatrixWorld()
    const gl = this.gl
    const shadows = gl.shadowMap.autoUpdate
    const prev = gl.getRenderTarget()
    h.root.visible = false
    gl.shadowMap.autoUpdate = false
    try {
      gl.setRenderTarget(t.rt)
      gl.render(this.scene, cam)
    } finally {
      gl.setRenderTarget(prev)
      gl.shadowMap.autoUpdate = shadows
      h.root.visible = true
    }
    return true
  }

  private async read(t: Target, w: number, hgt: number, lut: Uint8Array | null): Promise<ImageData> {
    try {
      await this.gl.readRenderTargetPixelsAsync(t.rt, 0, 0, w, hgt, t.buf)
    } catch {
      this.gl.readRenderTargetPixels(t.rt, 0, 0, w, hgt, t.buf)
    }
    const img = new ImageData(w, hgt)
    toImage(t.buf, w, hgt, img.data, lut)
    return img
  }

  frame(): void {
    const slots = feedSlots()
    if (!slots.size) return
    const now = performance.now()
    const fleet = useRelay.getState().fleet
    for (const s of slots) {
      if (s.busy || now - s.lastAt < 1000 / s.fps) continue
      const v = findVehicle(fleet?.vehicles, s.vehicleId)
      const g = v?.gimbal
      if (!g) continue
      const main = this.target(s.width, s.height)
      if (!this.render(s.vehicleId, g.lens, verticalFov(g), main)) continue
      s.lastAt = now
      s.n++
      // the pod's small picture: a quarter-size render through its own lens, every other frame
      let small: Target | null = null
      const pipLens = g.pip
      const pw = Math.max(64, Math.round(s.width / 4 / 16) * 16)
      const ph = Math.round((pw * 9) / 16)
      if (pipLens && !s.once && (s.n % 2 === 0 || !this.lastPip.has(s))) {
        small = this.target(pw, ph)
        const zoom = pipLens === g.lens ? g.zoom : 1
        if (!this.render(s.vehicleId, pipLens, fovDeg(pipLens, zoom)[1], small)) small = null
      }
      s.busy = true
      const lut = isInfrared(g.lens) ? lutOf(g.palette) : null
      const pipLut = pipLens && isInfrared(pipLens) ? lutOf(g.palette) : null
      void Promise.all([this.read(main, s.width, s.height, lut), small ? this.read(small, pw, ph, pipLut) : Promise.resolve(pipLens ? (this.lastPip.get(s) ?? null) : null)])
        .then(([image, pip]) => {
          if (pip && small) this.lastPip.set(s, pip)
          if (!pipLens) this.lastPip.delete(s)
          if (slots.has(s)) s.draw({ image, pip, at: now })
        })
        .catch(() => undefined)
        .finally(() => {
          s.busy = false
        })
    }
    for (const k of this.lastPip.keys()) if (!slots.has(k)) this.lastPip.delete(k)
  }

  private readonly lastPip = new Map<FeedSlot, ImageData>()

  dispose(): void {
    for (const t of this.targets.values()) t.rt.dispose()
    this.targets.clear()
    this.scene.remove(this.ground)
    this.ground.geometry.dispose()
    const m = this.ground.material as THREE.MeshBasicMaterial
    m.alphaMap?.dispose()
    m.dispose()
  }

  /** Drops render targets of sizes no feed uses any more. */
  prune(): void {
    const used = new Set<string>()
    for (const s of feedSlots()) {
      used.add(`${s.width}x${s.height}`)
      const pw = Math.max(64, Math.round(s.width / 4 / 16) * 16)
      used.add(`${pw}x${Math.round((pw * 9) / 16)}`)
    }
    for (const [k, t] of this.targets) {
      if (used.has(k)) continue
      t.rt.dispose()
      this.targets.delete(k)
    }
  }
}

export function GimbalCamera({ light }: { light: boolean }) {
  const gl = useThree((s) => s.gl)
  const scene = useThree((s) => s.scene)
  const invalidate = useThree((s) => s.invalidate)
  const r = React.useMemo(() => new GimbalRenderer(gl, scene, light || !gl.capabilities.isWebGL2 ? 0 : 4), [gl, scene, light])
  React.useEffect(() => {
    feedHost.mounted = true
    feedHost.software = light
    r.attach()
    const prune = window.setInterval(() => r.prune(), 5000)
    return () => {
      feedHost.mounted = false
      window.clearInterval(prune)
      r.dispose()
    }
  }, [r, light])
  // a demand-driven loop (software GL) still needs frames while a snapshot waits
  React.useEffect(() => {
    const id = window.setInterval(() => {
      for (const s of feedSlots()) if (s.once) invalidate()
    }, 100)
    return () => window.clearInterval(id)
  }, [invalidate])
  useFrame(() => r.frame())
  return null
}
