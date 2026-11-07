// The three.js scene of the stage (02 §6.1): WebGLRenderer from the same three package as the GLTF loader,
// antialiased, pixel ratio capped at 2 (1.5 from 8 aircraft), ACES tone mapping, sRGB output, scene-clear
// background. Children are scene layers from other modules (CourseLayer).
import * as React from 'react'
import * as THREE from 'three'
import { Canvas, useThree } from '@react-three/fiber'
import { useGLTF } from '@react-three/drei'
import { useRelay } from '@/store/relay'
import { useT6 } from '../copy'
import { useStage } from '../store'
import { token } from '../tokens'
import { CameraRig } from './CameraRig'
import { Fleet } from './Fleet'
import { Grid, OriginCross, ShadowCatcher } from './Ground'
import { GroundContact, Lights } from './Lights'
import { PadSlots, SafetyVolume, SpawnPoints } from './Markers'
import { isSoftwareGL, stageQuality } from './gpu'
import { fallbackTemplate, MODEL_URL, prepareTemplate } from './model'
import { markStageModelReady } from '../loading'

/** Exposure that keeps the GoFilm 20's grey plastics readable on g950 under the 02 §6.1 light levels. */
const EXPOSURE = 1.35

/**
 * The window's CSP allows blob: images but not blob: fetches, and GLTFLoader's default ImageBitmapLoader fetches
 * embedded textures; the image-element TextureLoader (the Safari / Firefox path of GLTFLoader) loads them instead.
 */
const imageTextures: NonNullable<Parameters<typeof useGLTF>[3]> = (loader) => {
  loader.register((parser) => {
    const p = parser as unknown as { textureLoader: THREE.Loader; options: { manager: THREE.LoadingManager; crossOrigin?: string } }
    const tl = new THREE.TextureLoader(p.options.manager)
    tl.setCrossOrigin(p.options.crossOrigin ?? 'anonymous')
    p.textureLoader = tl
    return { name: 'relay_image_textures' } as unknown as ReturnType<Parameters<typeof loader.register>[0]>
  })
}

function Model() {
  const gltf = useGLTF(MODEL_URL, false, false, imageTextures)
  const template = React.useMemo(() => prepareTemplate(gltf.scene), [gltf.scene])
  React.useEffect(() => markStageModelReady(), [])
  return <Fleet template={template} />
}

function FallbackModel() {
  const template = React.useMemo(() => fallbackTemplate(token('g300').color), [])
  React.useEffect(() => markStageModelReady(), [])
  return <Fleet template={template} />
}

class ModelBoundary extends React.Component<{ children: React.ReactNode; onError(): void }, { failed: boolean }> {
  override state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  override componentDidCatch() {
    this.props.onError()
  }
  override render() {
    return this.state.failed ? <FallbackModel /> : this.props.children
  }
}

/** WebGL may be unavailable (no GPU, blocked driver): keep the rest of the window working and say so. */
class CanvasBoundary extends React.Component<{ children: React.ReactNode; fallback: React.ReactNode }, { failed: boolean }> {
  override state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  override render() {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}

function NoWebGL() {
  const t = useT6()
  React.useEffect(() => markStageModelReady(), [])
  return (
    <div className="stage-nogl" role="status" data-stage-nogl="">
      <span className="text-hud-title font-[560] text-[var(--g100)]">{t('stage.noWebgl')}</span>
      <span className="text-hud-sub text-[var(--g400)]">{t('stage.noWebglHint')}</span>
    </div>
  )
}

/** Drives the demand frame loop at a fixed rate (software renderers only). */
function FrameCap({ fps }: { fps: number }) {
  const invalidate = useThree((s) => s.invalidate)
  React.useEffect(() => {
    const id = window.setInterval(() => invalidate(), 1000 / fps)
    return () => window.clearInterval(id)
  }, [fps, invalidate])
  return null
}

export function StageCanvas({ children, onModelError }: { children?: React.ReactNode; onModelError(): void }) {
  const many = useRelay((s) => (s.fleet?.vehicles.length ?? 0) >= 8)
  const grid = useStage((s) => s.layers.grid)
  const background = React.useMemo(() => token('scene-clear').color.clone(), [])
  const quality = React.useMemo(() => stageQuality(), [])
  const soft = quality.light
  return (
    <div className="stage-canvas" data-stage-gl={isSoftwareGL() ? (soft ? 'software' : 'capture') : 'hardware'}>
      <CanvasBoundary fallback={<NoWebGL />}>
        <Canvas
          dpr={soft ? 1 : [1, many ? 1.5 : 2]}
          shadows={soft ? false : 'percentage'}
          frameloop={quality.fps ? 'demand' : 'always'}
          gl={{ antialias: !soft, powerPreference: 'high-performance' }}
          onCreated={({ gl }) => {
            gl.toneMapping = THREE.ACESFilmicToneMapping
            gl.toneMappingExposure = EXPOSURE
            gl.outputColorSpace = THREE.SRGBColorSpace
            // Program status queries make the renderer thread wait for the GPU process to finish compiling; with
            // software WebGL a first-use shader blocks the window (and the 50 Hz stick timer) for over a second.
            gl.debug.checkShaderErrors = import.meta.env.DEV
          }}
        >
          <color attach="background" args={[background]} />
          {quality.fps ? <FrameCap fps={quality.fps} /> : null}
          <CameraRig />
          <Lights />
          <Grid visible={grid} />
          <OriginCross visible={grid} />
          {soft ? null : <ShadowCatcher />}
          {soft ? null : <GroundContact />}
          <SafetyVolume />
          <SpawnPoints />
          <PadSlots />
          <ModelBoundary onError={onModelError}>
            <React.Suspense fallback={null}>
              <Model />
            </React.Suspense>
          </ModelBoundary>
          {children}
        </Canvas>
      </CanvasBoundary>
    </div>
  )
}
