// Stick visualisation and on-screen sticks (02 §5.3): left box yaw / throttle, right box roll / pitch, 96 x 96, and
// an 8 x 96 throttle bar with the hover tick. For the keyboard / screen source the boxes are draggable with pointer
// capture: the right stick springs back to centre on release, the left stick keeps its throttle. Positions are
// written to the DOM from a rAF loop reading getLiveSticks(); React does not re-render per frame.
import * as React from 'react'
import { cn } from '@/lib/utils'
import { clamp, stickForThrottle } from './mapping'
import { screenStick, windowThrottleY } from './InputLayer'
import { getLiveSticks } from './store'
import { useCT } from './strings'
import './control.css'

const R = 44 // dot travel from centre, px (96 / 2 minus the dot radius)

function useStickLoop(vehicleId: string, refs: {
  left: React.RefObject<HTMLElement | null>
  right: React.RefObject<HTMLElement | null>
  bar: React.RefObject<HTMLElement | null>
  tick: React.RefObject<HTMLElement | null>
  boxes: React.RefObject<HTMLElement | null>
}): void {
  React.useEffect(() => {
    let raf = 0
    const loop = () => {
      const s = getLiveSticks(vehicleId)
      const f = s?.frame ?? { roll: 0, pitch: 0, yaw: 0, throttle: 0 }
      const centre = s?.centre ?? 0.5
      const ty = stickForThrottle(f.throttle, centre)
      if (refs.left.current) refs.left.current.style.transform = `translate(${(f.yaw * R).toFixed(1)}px, ${(-ty * R).toFixed(1)}px)`
      if (refs.right.current) refs.right.current.style.transform = `translate(${(f.roll * R).toFixed(1)}px, ${(-f.pitch * R).toFixed(1)}px)`
      if (refs.bar.current) refs.bar.current.style.transform = `scaleY(${clamp(f.throttle, 0, 1).toFixed(3)})`
      if (refs.tick.current) refs.tick.current.style.bottom = `${(centre * 100).toFixed(1)}%`
      if (refs.boxes.current) refs.boxes.current.toggleAttribute('data-idle', !s?.sending)
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [vehicleId, refs])
}

function StickBox({ side, interactive, dotRef, label }: { side: 'left' | 'right'; interactive: boolean; dotRef: React.RefObject<HTMLElement | null>; label: string }) {
  const [dragging, setDragging] = React.useState(false)
  const box = React.useRef<HTMLDivElement>(null)
  const posFrom = (e: React.PointerEvent) => {
    const r = box.current!.getBoundingClientRect()
    const x = clamp((e.clientX - (r.left + r.width / 2)) / R, -1, 1)
    const y = clamp(-(e.clientY - (r.top + r.height / 2)) / R, -1, 1)
    return { x, y }
  }
  const onDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!interactive || e.button !== 0) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    setDragging(true)
    const p = posFrom(e)
    // the left stick's throttle continues from where it is; a click does not jump it
    screenStick(side, p.x, side === 'left' ? (Math.abs(p.y - windowThrottleY()) < 0.15 ? windowThrottleY() : p.y) : p.y, true)
  }
  const onMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging) return
    const p = posFrom(e)
    screenStick(side, p.x, p.y, true)
  }
  const end = () => {
    if (!dragging) return
    setDragging(false)
    screenStick(side, 0, 0, false)
  }
  return (
    <div
      ref={box}
      className="stick-box"
      role="group"
      aria-label={label}
      data-interactive={interactive ? '' : undefined}
      data-dragging={dragging ? '' : undefined}
      data-stick={side}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={end}
      onPointerCancel={end}
      onLostPointerCapture={end}
    >
      <i ref={dotRef as React.RefObject<HTMLElement>} className="stick-dot" />
    </div>
  )
}

export function Sticks({ vehicleId, interactive, className }: { vehicleId: string; interactive: boolean; className?: string }) {
  const ct = useCT()
  const left = React.useRef<HTMLElement>(null)
  const right = React.useRef<HTMLElement>(null)
  const bar = React.useRef<HTMLElement>(null)
  const tick = React.useRef<HTMLElement>(null)
  const boxes = React.useRef<HTMLDivElement>(null)
  const refs = React.useMemo(() => ({ left, right, bar, tick, boxes }), [])
  useStickLoop(vehicleId, refs)
  return (
    <div ref={boxes} className={cn('flex items-center gap-2', className)} data-sticks="">
      <div className="throttle-bar" aria-hidden="true">
        <i ref={bar as React.RefObject<HTMLElement>} />
        <b ref={tick as React.RefObject<HTMLElement>} />
      </div>
      <StickBox side="left" interactive={interactive} dotRef={left} label={ct('ctl.sticks')} />
      <span className="w-1" />
      <StickBox side="right" interactive={interactive} dotRef={right} label={ct('ctl.sticks')} />
    </div>
  )
}
