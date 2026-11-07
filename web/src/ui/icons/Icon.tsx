// Static icon: one <svg data-icon viewBox="0 0 24 24"> with the canonical morphicons path of a lucide IconNode,
// currentColor and a non-scaling 1.5 px stroke (styles/icons.css). With `label` the svg is role="img"; otherwise hidden.
import * as React from 'react'
import { canonicalD } from 'morphicons/dom'
import { cn } from '@/lib/utils'
import { ICONS, type IconKey } from './registry'
import type { IconNode } from './types'

export type IconSvgProps = Omit<React.SVGProps<SVGSVGElement>, 'ref' | 'pathLength'> & {
  node: IconNode
  size?: number | string
  label?: string
  pathLength?: number
  ref?: React.Ref<SVGSVGElement>
}

const dCache = new WeakMap<IconNode, string>()
export function iconD(node: IconNode): string {
  let d = dCache.get(node)
  if (d === undefined) {
    d = canonicalD(node as never)
    dCache.set(node, d)
  }
  return d
}

export const IconSvg = React.memo(function IconSvg({ node, size, label, className, ref, pathLength, ...rest }: IconSvgProps) {
  return (
    <svg
      ref={ref}
      data-icon=""
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      width={size}
      height={size}
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
      <path d={iconD(node)} pathLength={pathLength} />
    </svg>
  )
})

/** Registry lookup: unknown keys throw in development and fall back to `help` in production. */
export function resolveIcon(key: IconKey): IconNode {
  const node = (ICONS as Record<string, IconNode>)[key]
  if (node) return node
  if (import.meta.env.DEV) throw new Error(`icon key "${String(key)}" is not registered in ui/icons/registry.ts`)
  return ICONS.help
}

export type IconProps = Omit<IconSvgProps, 'node'> & { icon: IconKey }

export const Icon = React.memo(function Icon({ icon, ...rest }: IconProps) {
  return <IconSvg node={resolveIcon(icon)} {...rest} />
})
