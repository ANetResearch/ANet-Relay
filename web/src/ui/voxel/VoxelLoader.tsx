// VoxelLoader (05 §2.5): panel-level loading as a 3 x 3 x 3 isometric voxel cube that builds up voxel by voxel and
// tears down again (one --voxel-cycle). Pure CSS (transform + opacity) on an inline SVG; it holds one resident loop
// slot (ADR-029, ui/motion/budget.ts) and shows the static cube without one, and always in reduced motion.
// `scatter` plays the one-off exit of StageLoading: the voxels fly outward and fade (--voxel-scatter).
import * as React from 'react'
import { useT } from '@/i18n'
import { cn } from '@/lib/utils'
import { useLoopSlot } from '@/ui/motion/budget'
import { cubeModel } from './cube'
import './voxel.css'

export type VoxelSize = 16 | 24 | 40

const MODEL = cubeModel()

export interface VoxelLoaderProps {
  size?: VoxelSize
  /** accessible name; "Loading" by default */
  label?: string
  /** a visible line next to (or, at 40, under) the cube */
  caption?: React.ReactNode
  /** play the exit (StageLoading) instead of the loop */
  scatter?: boolean
  className?: string
}

export function VoxelLoader({ size = 24, label, caption, scatter = false, className }: VoxelLoaderProps) {
  const t = useT()
  const loop = useLoopSlot(!scatter)
  return (
    <span
      role="status"
      aria-label={label ?? t('voxel.loading')}
      className={cn('voxel-loader', className)}
      data-size={size}
      data-loop={loop ? 'on' : undefined}
      data-scatter={scatter ? '' : undefined}
    >
      <svg viewBox={MODEL.viewBox} width={size} height={size} aria-hidden="true" focusable="false">
        {MODEL.voxels.map((v) => (
          <g key={v.key} className="vx" style={{ '--d': (v.light / MODEL.voxels.length) * 0.4, '--sx': v.sx, '--sy': v.sy } as React.CSSProperties}>
            <polygon className="vx-top" points={v.top} />
            <polygon className="vx-left" points={v.left} />
            <polygon className="vx-right" points={v.right} />
          </g>
        ))}
      </svg>
      {caption ? <span className="voxel-caption">{caption}</span> : null}
    </span>
  )
}
