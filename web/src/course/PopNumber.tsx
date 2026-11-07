// Digit pop-in (02 §18 "数字变化", motion recipe "Number pop-in"): when the text changes, only the characters that
// differ from the previous text (aligned from the right) rise in with a short blur, staggered. Reduced motion
// replaces the text directly.
import * as React from 'react'
import { cn } from '@/lib/utils'
import { motionReduced } from '@/ui/motion/tier'

function recipe(): { dur: number; distance: number; stagger: number; blur: number; ease: string } {
  const cs = getComputedStyle(document.documentElement)
  const ms = (v: string, d: number) => {
    const s = v.trim()
    if (!s) return d
    const n = parseFloat(s)
    return s.endsWith('ms') ? n : s.endsWith('s') ? n * 1000 : d
  }
  const px = (v: string, d: number) => (v.trim() ? parseFloat(v) : d)
  return {
    dur: ms(cs.getPropertyValue('--digit-dur'), 500),
    distance: px(cs.getPropertyValue('--digit-distance'), 8),
    stagger: ms(cs.getPropertyValue('--digit-stagger'), 70),
    blur: px(cs.getPropertyValue('--digit-blur'), 2),
    ease: cs.getPropertyValue('--digit-ease').trim() || 'ease-out',
  }
}

export function PopNumber({ text, className }: { text: string; className?: string }) {
  const ref = React.useRef<HTMLSpanElement>(null)
  const prev = React.useRef<string | null>(null)

  React.useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const before = prev.current
    prev.current = text
    el.textContent = ''
    const chars = [...text]
    const old = before === null ? null : [...before]
    const animate = old !== null && !motionReduced() && typeof el.animate === 'function'
    const r = animate ? recipe() : null
    let k = 0
    chars.forEach((ch, i) => {
      const span = document.createElement('span')
      span.className = 't-digit'
      span.textContent = ch
      el.appendChild(span)
      if (!r || !old) return
      const j = old.length - (chars.length - i)
      if (j >= 0 && old[j] === ch) return
      span.animate(
        [
          { opacity: 0, transform: `translateY(${r.distance}px)`, filter: `blur(${r.blur}px)` },
          { opacity: 1, transform: 'translateY(0)', filter: 'blur(0)' },
        ],
        { duration: r.dur, easing: r.ease, delay: k * r.stagger, fill: 'backwards' },
      )
      k++
    })
  }, [text])

  return <span ref={ref} className={cn('t-number', className)} aria-label={text} />
}
