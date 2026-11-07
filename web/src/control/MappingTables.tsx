// Mapping reference tables (02 §7.5 as amended by 05 §6.3, WP-C): the standard controller in Mode 2 or Mode 1, the
// transmitter defaults and the keyboard. Lieflat tables; used by the controller popover and Settings > Controllers.
import { cn } from '@/lib/utils'
import { usePrefs } from '@/settings/prefs'
import { useCT, type ControlKey } from './strings'

type Rows = readonly (readonly [ControlKey, ControlKey])[]

/** Standard controller rows for a stick mode. */
export function gamepadRows(mode: 1 | 2): Rows {
  return [
    ['map.leftY', mode === 1 ? 'map.leftYDoM1' : 'map.leftYDo'],
    ['map.leftX', 'map.leftXDo'],
    ['map.right', mode === 1 ? 'map.rightDoM1' : 'map.rightDo'],
    ['map.options', 'map.optionsDo'],
    ['map.l1r1', 'map.l1r1Do'],
    ['map.l2r2', 'map.l2r2Do'],
    ['map.r1', 'map.r1Do'],
    ['map.triangle', 'map.triangleDo'],
    ['map.square', 'map.squareDo'],
    ['map.circle', 'map.circleDo'],
    ['map.dpad', 'map.dpadDo'],
    ['map.dpadUD', 'map.dpadUDDo'],
    ['map.touchpad', 'map.touchpadDo'],
    ['map.cross', 'map.crossDo'],
  ]
}

export const TRANSMITTER_ROWS: Rows = [
  ['map.tx.sticks', 'map.tx.sticksDo'],
  ['map.tx.switches', 'map.tx.switchesDo'],
  ['map.tx.h5', 'map.tx.h5Do'],
]

export const KEYBOARD_ROWS: Rows = [
  ['kb.wasd', 'kb.wasdDo'],
  ['kb.qe', 'kb.qeDo'],
  ['kb.rf', 'kb.rfDo'],
  ['kb.z', 'kb.zDo'],
  ['kb.space', 'kb.spaceDo'],
  ['kb.h', 'kb.hDo'],
  ['kb.x', 'kb.xDo'],
  ['kb.p', 'kb.pDo'],
]

/** Compact two-column table (the popover style). */
export function RowsTable({ rows, caption, className }: { rows: Rows; caption: string; className?: string }) {
  const ct = useCT()
  return (
    <table data-lf-table="" className={cn('w-full text-hud-sub', className)}>
      <caption className="sr-only">{caption}</caption>
      <tbody>
        {rows.map(([k, d]) => (
          <tr key={k} className="border-t border-[var(--border)] first:border-t-0">
            <th scope="row" className="py-1 pr-3 text-left align-top font-[450] whitespace-nowrap text-[var(--g200)]">{ct(k)}</th>
            <td className="py-1 text-[var(--g400)]">{ct(d)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** The controller map in the window's stick mode; also usable in Settings > Controllers. */
export function MappingReference({ className }: { className?: string }) {
  const ct = useCT()
  const mode = usePrefs((p) => p.stickMode)
  return <RowsTable rows={gamepadRows(mode)} caption={ct(mode === 1 ? 'map.titleM1' : 'map.title')} className={className} />
}

/** Headed table (the Settings style): input, action. */
export function HeadedTable({ rows, input, action }: { rows: Rows; input: string; action: string }) {
  const ct = useCT()
  return (
    <table className="w-full text-[12px]" data-lf-table="">
      <thead>
        <tr className="border-b border-[var(--foreground)] text-left">
          <th className="cap py-1 font-[560]">{input}</th>
          <th className="cap py-1 font-[560]">{action}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([k, d]) => (
          <tr key={k} className="border-b border-dotted border-[var(--border)]">
            <td className="py-1.5 pr-3 text-[var(--g200)]">{ct(k)}</td>
            <td className="py-1.5 text-[var(--g400)]">{ct(d)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
