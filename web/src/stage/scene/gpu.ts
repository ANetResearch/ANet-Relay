// Software WebGL detection (SwiftShader, llvmpipe and similar). On those renderers every frame is rasterised on
// the CPU in the GPU process; when frames queue up the renderer thread blocks in its compositor commit, and that
// also stalls the 50 Hz stick timer past the 0.8 s stick timeout (03 §6.1 H2). The stage then renders a lighter
// scene at a capped frame rate so the input path keeps its timing.
const SOFTWARE = /swiftshader|llvmpipe|softpipe|software|basic render|lavapipe/i

let cached: boolean | null = null
let mode: StageQuality | null = null

/**
 * How the stage renders. hardware: full quality, free-running. software: lighter scene (no MSAA, no shadows) at
 * SOFTWARE_FPS. capture: full quality on a software renderer at CAPTURE_FPS, for screenshots only; selected with
 * localStorage relay.stage.gl = "hq" (the frame rate is far too low to fly by).
 */
export interface StageQuality {
  light: boolean
  /** frame cap, or null for the normal display-synced loop */
  fps: number | null
}

/** Frame cap for software renderers (frames per second). */
export const SOFTWARE_FPS = 12
/** Frame cap of the capture mode: one full-quality software frame costs about half a second. */
export const CAPTURE_FPS = 1.5

function override(): string | null {
  try {
    return localStorage.getItem('relay.stage.gl')
  } catch {
    return null
  }
}

export function stageQuality(): StageQuality {
  if (!mode) {
    const o = override()
    if (!isSoftwareGL()) mode = { light: false, fps: null }
    else if (o === 'hq') mode = { light: false, fps: CAPTURE_FPS }
    else mode = { light: true, fps: SOFTWARE_FPS }
  }
  return mode
}

/** The renderer string of a throwaway WebGL context, or null when none can be created. */
function rendererName(): string | null {
  try {
    const canvas = document.createElement('canvas')
    const gl = (canvas.getContext('webgl2') ?? canvas.getContext('webgl')) as WebGLRenderingContext | null
    if (!gl) return null
    const info = gl.getExtension('WEBGL_debug_renderer_info')
    const name = String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER) ?? '')
    gl.getExtension('WEBGL_lose_context')?.loseContext()
    return name
  } catch {
    return null
  }
}

export function isSoftwareGL(): boolean {
  if (cached === null) {
    const forced = override()
    cached = forced === 'soft' ? true : SOFTWARE.test(rendererName() ?? '')
  }
  return cached
}
