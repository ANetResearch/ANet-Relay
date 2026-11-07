// Line-drawn DualSense, 48 x 32, 1.25 px g50 strokes (02 §7.2). With `draw` the strokes trace in and the two stick
// dots light up one after the other; under reduced motion the glyph simply appears (control.css).
import { cn } from '@/lib/utils'

export function DualSenseGlyph({ draw = true, className }: { draw?: boolean; className?: string }) {
  const line = { pathLength: 1, fill: 'none', stroke: 'currentColor', strokeWidth: 1.25, vectorEffect: 'non-scaling-stroke' as const, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }
  return (
    <svg viewBox="0 0 48 32" width={48} height={32} aria-hidden="true" className={cn('pad-glyph flex-none text-[var(--g50)]', className)} data-draw={draw ? '' : undefined}>
      {/* body and grips */}
      <path
        {...line}
        d="M9.2 6.2C12.2 4.6 16.6 4.9 18.8 6.6H29.2C31.4 4.9 35.8 4.6 38.8 6.2C42.9 8.4 45.4 16.2 45.9 22.9C46.3 28.1 43.4 30.6 40.4 29.6C37.6 28.6 35.2 24.6 33.1 21.8H14.9C12.8 24.6 10.4 28.6 7.6 29.6C4.6 30.6 1.7 28.1 2.1 22.9C2.6 16.2 5.1 8.4 9.2 6.2Z"
      />
      {/* touchpad */}
      <rect {...line} x="18.4" y="7.8" width="11.2" height="6.6" rx="1.4" />
      {/* d-pad */}
      <path {...line} d="M10.6 10.4V14.6M8.5 12.5H12.7" />
      {/* face buttons */}
      <path {...line} d="M37.4 10.3V10.4M37.4 14.6V14.7M35.3 12.5H35.4M39.5 12.5H39.6" />
      {/* sticks */}
      <circle {...line} className="pad-glyph-line" cx="17.2" cy="17.6" r="2.6" />
      <circle {...line} className="pad-glyph-line" cx="30.8" cy="17.6" r="2.6" />
      <circle className="pad-glyph-dot" cx="17.2" cy="17.6" r="1.1" fill="currentColor" />
      <circle className="pad-glyph-dot" cx="30.8" cy="17.6" r="1.1" fill="currentColor" />
    </svg>
  )
}
