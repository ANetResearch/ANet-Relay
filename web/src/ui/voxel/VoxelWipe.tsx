// VoxelWipe (05 §2.5): when `trigger` changes (workspace switch, a replay opening), a g850 cover of 16 px voxels is
// laid over the parent (which must be positioned) and flips away along the same diagonal wave as act 2 of the
// opening, in --voxel-wipe. Covers only its parent: the top bar and Stop all stay visible and usable. Off in reduced
// and off motion; nothing on the first render.
import * as React from 'react'
import { msToken, cssToken } from '@/ui/motion/tokens'
import { motionReduced } from '@/ui/motion/tier'
import { paintWave, surface, waveTiming } from './paint'
import { cellKeys, grid } from './wave'
import './voxel.css'

const CELL = 16
const DEFAULT_MS = 300

export function VoxelWipe({ trigger }: { trigger: unknown }) {
  const ref = React.useRef<HTMLCanvasElement>(null)
  const prev = React.useRef(trigger)
  const [active, setActive] = React.useState(false)

  // layout effect: the cover is drawn before the new content is painted for the first time
  React.useLayoutEffect(() => {
    if (Object.is(prev.current, trigger)) return
    prev.current = trigger
    const canvas = ref.current
    const host = canvas?.parentElement
    if (!canvas || !host || motionReduced()) return
    const w = host.clientWidth
    const h = host.clientHeight
    const surf = w > 0 && h > 0 ? surface(canvas, w, h) : null
    if (!surf) return
    const g = grid(w, h, CELL)
    const keys = cellKeys(g, w, h)
    const { spread, cell } = waveTiming(g, msToken('--voxel-wipe', DEFAULT_MS))
    const g850 = cssToken('--g850')
    const tones = { cover: g850, dark: g850, light: g850, clear: null }
    const t0 = performance.now()
    let raf = 0
    const draw = () => {
      const t = performance.now() - t0
      paintWave(surf, g, keys, t, 0, spread, cell, tones)
      if (t < spread + cell) raf = requestAnimationFrame(draw)
      else setActive(false)
    }
    setActive(true)
    draw()
    return () => cancelAnimationFrame(raf)
  }, [trigger])

  return <canvas ref={ref} className="voxel-wipe" aria-hidden="true" hidden={!active} />
}
