// State icon (02 §2.5): one <svg> whose path is driven by morphicons. `alt` shows the partner geometry of the key
// (possess <-> release, layer visible <-> hidden ...). Registered pairs morph with a spring; when the motion tier is
// reduced the change is applied instantly. Business code only passes semantic keys from ./registry.
import * as React from 'react'
import { createMorph, type Morph } from 'morphicons/dom'
import { cn } from '@/lib/utils'
import { useMotionTier, getMotionTier } from '@/ui/motion/tier'
import { iconD, resolveIcon } from './Icon'
import { PARTNERS, type IconKey } from './registry'
import type { IconNode } from './types'

export type StateIconProps = Omit<React.SVGProps<SVGSVGElement>, 'ref'> & {
  icon: IconKey
  alt?: boolean
  /** snappy for user actions, smooth for state processes */
  spring?: 'snappy' | 'smooth'
  label?: string
}

const SPRINGS = {
  snappy: { stiffness: 420, damping: 32 },
  smooth: { stiffness: 220, damping: 26 },
} as const

export const StateIcon = React.memo(function StateIcon({ icon, alt = false, spring = 'snappy', label, className, ...rest }: StateIconProps) {
  const node: IconNode = (alt ? PARTNERS[icon] : undefined) ?? resolveIcon(icon)
  const [initialD] = React.useState(() => iconD(node))
  const main = React.useRef<SVGPathElement>(null)
  const drv = React.useRef<Morph | null>(null)
  const last = React.useRef<IconNode>(node)
  useMotionTier()

  React.useLayoutEffect(() => {
    drv.current = createMorph(main.current!, last.current as never, { reducedMotion: 'user' })
    return () => {
      drv.current?.destroy()
      drv.current = null
    }
  }, [])

  React.useLayoutEffect(() => {
    const m = drv.current
    if (!m || last.current === node) return
    last.current = node
    if (getMotionTier() !== 'full') m.set(node as never)
    else m.morphTo(node as never, SPRINGS[spring])
  }, [node, spring])

  return (
    <svg
      data-icon=""
      data-state-icon=""
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? 'img' : undefined}
      aria-hidden={label ? undefined : true}
      className={cn('icon', className)}
      {...rest}
    >
      {label ? <title>{label}</title> : null}
      <path ref={main} d={initialD} />
    </svg>
  )
})
