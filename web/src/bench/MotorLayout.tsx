// Motor layout diagram (05 §4.2 S6): top view, nose up, one rotor per motor of the layout the backend reports
// (quad, hex, octo, tri, coaxial; never a fixed 1-4). The motor under test is outlined, a spinning one is filled red
// with a pulse ring (still under reduced motion), and each rotor carries its spin direction as an arc arrow.
import { cn } from '@/lib/utils'
import { motorPoint, type MotorLayoutData } from './model'
import { useTB } from './copy'

export interface MotorLayoutProps {
  layout: MotorLayoutData
  /** motor the prompt is about */
  target?: number | null
  /** motors whose outputs are above idle now */
  spinning?: readonly number[]
  /** motors already answered */
  done?: readonly number[]
  size?: number
  className?: string
}

/** Arc arrow around a rotor: clockwise or counter-clockwise seen from above. */
function DirArrow({ x, y, r, dir }: { x: number; y: number; r: number; dir: 'cw' | 'ccw' }) {
  // arc over the top of the rotor from 200 deg to 340 deg (screen angles), head at the end of the rotation
  const a0 = (-160 * Math.PI) / 180
  const a1 = (-20 * Math.PI) / 180
  const p0 = { x: x + r * Math.cos(a0), y: y + r * Math.sin(a0) }
  const p1 = { x: x + r * Math.cos(a1), y: y + r * Math.sin(a1) }
  const [from, to] = dir === 'cw' ? [p0, p1] : [p1, p0]
  const sweep = dir === 'cw' ? 1 : 0
  // head: small triangle tangent to the arc at `to`
  const tangent = dir === 'cw' ? a1 + Math.PI / 2 : a0 - Math.PI / 2
  const hx = Math.cos(tangent)
  const hy = Math.sin(tangent)
  const nx = -hy
  const ny = hx
  const s = 3
  const head = `${to.x + hx * s},${to.y + hy * s} ${to.x - hx * s + nx * s},${to.y - hy * s + ny * s} ${to.x - hx * s - nx * s},${to.y - hy * s - ny * s}`
  return (
    <>
      <path className="dir" d={`M ${from.x.toFixed(1)} ${from.y.toFixed(1)} A ${r} ${r} 0 0 ${sweep} ${to.x.toFixed(1)} ${to.y.toFixed(1)}`} />
      <polygon className="dir-head" points={head} />
    </>
  )
}

/** Placement sketch for S4 (05 §4.5): the aircraft level on a flat surface, side view, with a bubble level. */
export function LevelDiagram({ still = false, width = 168 }: { still?: boolean; width?: number }) {
  const t = useTB()
  const h = 56
  const label = t(still ? 'bench.diagram.still' : 'bench.diagram.level')
  return (
    <svg className="lf-svg bench-layout" width={width} height={h} viewBox={`0 0 ${width} ${h}`} role="img" aria-label={label} data-bench-diagram={still ? 'still' : 'level'}>
      <line className="arm" x1={8} y1={h - 8} x2={width - 8} y2={h - 8} />
      <rect className="body" x={width / 2 - 22} y={h - 22} width={44} height={10} rx={3} />
      <line className="arm" x1={width / 2 - 52} y1={h - 20} x2={width / 2 + 52} y2={h - 20} />
      <rect className="rotor" x={width / 2 - 62} y={h - 24} width={20} height={4} rx={2} />
      <rect className="rotor" x={width / 2 + 42} y={h - 24} width={20} height={4} rx={2} />
      <rect className="body" x={width / 2 - 24} y={8} width={48} height={10} rx={5} />
      <circle className="rotor" data-state="target" cx={width / 2} cy={13} r={3} />
      <text className="nose" x={width / 2} y={34} textAnchor="middle">
        {label}
      </text>
    </svg>
  )
}

export function MotorLayout({ layout, target = null, spinning = [], done = [], size = 168, className }: MotorLayoutProps) {
  const t = useTB()
  const c = size / 2
  const coaxial = layout.name === 'coaxial'
  const arm = size * 0.33
  const rotor = Math.max(9, Math.min(15, size / (layout.count > 6 ? 13 : 10)))
  const label = t('bench.motorLayout', { name: layout.name })
  return (
    <svg
      className={cn('lf-svg bench-layout', className)}
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={label}
      data-bench-layout={layout.name}
      data-motors={layout.count}
    >
      <text className="nose" x={c} y={10} textAnchor="middle">
        {t('bench.motorLayoutNose')}
      </text>
      {layout.motors.map((m, i) => {
        const p = coaxial ? { x: c + (i === 0 ? -rotor - 4 : rotor + 4), y: c } : motorPoint(m.angle_deg, arm, c, c + 4)
        return <line key={`arm-${m.n}`} className="arm" x1={c} y1={c + 4} x2={p.x} y2={p.y} />
      })}
      <rect className="body" x={c - 9} y={c - 7} width={18} height={22} rx={4} />
      <path className="dir-head" d={`M ${c} ${c - 4} l 4 6 h -8 z`} />
      {layout.motors.map((m, i) => {
        const p = coaxial ? { x: c + (i === 0 ? -rotor - 4 : rotor + 4), y: c } : motorPoint(m.angle_deg, arm, c, c + 4)
        const state = spinning.includes(m.n) ? 'spinning' : m.n === target ? 'target' : done.includes(m.n) ? 'done' : 'idle'
        return (
          <g key={m.n} data-motor={m.n} data-dir={m.dir} data-state={state}>
            <title>{`${t('bench.motor', { n: m.n })} · ${t(m.dir === 'cw' ? 'bench.motorDir.cw' : 'bench.motorDir.ccw')}`}</title>
            {state === 'spinning' ? <circle className="pulse" cx={p.x} cy={p.y} r={rotor} /> : null}
            <circle className="rotor" data-state={state} cx={p.x} cy={p.y} r={rotor} />
            <text className="num" x={p.x} y={p.y + 3.5} textAnchor="middle">
              {m.n}
            </text>
            <DirArrow x={p.x} y={p.y} r={rotor + 4} dir={m.dir} />
          </g>
        )
      })}
    </svg>
  )
}
