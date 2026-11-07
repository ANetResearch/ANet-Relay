// Body of the top-bar controller popover (02 §7.4, 320 wide; the shell owns trigger and surface): one row per
// controller (number, model, bound aircraft, live preview), dead zone 0.04-0.15, expo 0-0.6, mapping reference.
import * as React from 'react'
import { useShallow } from 'zustand/react/shallow'
import { useT } from '@/i18n'
import { cn } from '@/lib/utils'
import { useRelay, type PadInfo } from '@/store/relay'
import { useInputGate, usePads } from '@/store/selectors'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/ui/components/ui/collapsible'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/ui/components/ui/select'
import { Separator } from '@/ui/components/ui/separator'
import { Slider } from '@/ui/components/ui/slider'
import { Icon, StateIcon } from '@/ui/icons'
import { possessWith, releaseVehicle } from './actions'
import { RECONNECT_GRACE_MS } from './bindings'
import { DEADZONE_RANGE, EXPO_RANGE } from './mapping'
import { getPadPreview, useInputUi } from './store'
import { setPrefs, usePrefs } from '@/settings/prefs'
import { useCT, type ControlKey } from './strings'
import './control.css'

function MiniStick({ pad, side }: { pad: number; side: 'l' | 'r' }) {
  const dot = React.useRef<HTMLElement>(null)
  React.useEffect(() => {
    let raf = 0
    const loop = () => {
      const p = getPadPreview(pad)
      const x = p ? (side === 'l' ? p.lx : p.rx) : 0
      const y = p ? (side === 'l' ? p.ly : p.ry) : 0
      if (dot.current) dot.current.style.transform = `translate(${(x * 9).toFixed(2)}px, ${(-y * 9).toFixed(2)}px)`
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [pad, side])
  return (
    <span className="pad-mini" aria-hidden="true">
      <i ref={dot} />
    </span>
  )
}

function Countdown({ since }: { since: number }) {
  const ct = useCT()
  const left = (now: number) => Math.max(0, Math.ceil((RECONNECT_GRACE_MS - (now - since)) / 1000))
  const [s, setS] = React.useState(() => left(performance.now()))
  React.useEffect(() => {
    const id = setInterval(() => setS(left(performance.now())), 250)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [since])
  return <span className="num text-hud-sub text-[var(--g400)]">{ct('pad.disconnectedFor', { s })}</span>
}

function PadRow({ pad }: { pad: PadInfo }) {
  const t = useT()
  const ct = useCT()
  const vehicles = useRelay(useShallow((s) => (s.fleet?.vehicles ?? []).map((v) => `${v.id}\u0001${v.label}\u0001${v.kind}`)))
  const lostSince = useInputUi((s) => (pad.vehicle ? s.padLost[pad.vehicle]?.since : undefined))
  const model = ct(`pad.model.${pad.model}` as ControlKey)
  const items = [{ value: 'none', label: t('pad.panel.none') }, ...vehicles.map((x) => {
    const [id, label, kind] = x.split('\u0001')
    return { value: id, label: kind === 'sim' && !/sim/i.test(label) ? `${label} · ${t('kind.sim')}` : label }
  })]
  const onChange = (value: string | null) => {
    if (!value || value === (pad.vehicle ?? 'none')) return
    if (value === 'none') {
      if (pad.vehicle) void releaseVehicle(pad.vehicle).catch(() => undefined)
    } else void possessWith(value, pad.index).catch(() => undefined)
  }
  return (
    <div className="flex flex-col gap-2" data-pad-row={pad.index}>
      <div className="flex items-center gap-2">
        <StateIcon icon="gamepad" alt={!pad.connected} className={cn('size-4', pad.connected ? 'text-[var(--g100)]' : 'text-[var(--g500)]')} />
        <span className="text-ed-value font-[560] text-[var(--g100)]">{ct('pad.n', { n: pad.index })}</span>
        <span className="truncate text-hud-sub text-[var(--g400)]">{model}</span>
        {pad.battery !== null ? <span className="num text-hud-sub text-[var(--g400)]">{t('pad.panel.battery', { pct: Math.round(pad.battery * 100) })}</span> : null}
        <span className="flex-1" />
        {pad.connected ? (
          <span className="flex items-center gap-1.5" title={ct('pad.livePreview')}>
            <MiniStick pad={pad.index} side="l" />
            <MiniStick pad={pad.index} side="r" />
          </span>
        ) : lostSince !== undefined ? (
          <Countdown since={lostSince} />
        ) : null}
      </div>
      <div className="flex items-center gap-2 pl-6">
        <span className="w-16 flex-none text-hud-sub text-[var(--g400)]">{t('pad.panel.aircraft')}</span>
        <Select items={items} value={pad.vehicle ?? 'none'} onValueChange={(v) => onChange(v as string | null)} disabled={!pad.connected}>
          <SelectTrigger size="sm" className="h-7 min-w-0 flex-1" aria-label={t('pad.panel.aircraft')}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {items.map((it) => (
              <SelectItem key={it.value} value={it.value}>
                {it.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {pad.model === 'unknown' ? <p className="pl-6 text-hud-sub text-[var(--g500)]">{ct('pad.unrecognisedNote')}</p> : null}
    </div>
  )
}

function PrefSlider({ label, value, range, onChange }: { label: string; value: number; range: [number, number]; onChange(v: number): void }) {
  return (
    <div className="flex items-center gap-3">
      <span className="w-20 flex-none text-hud-sub text-[var(--g300)]">{label}</span>
      <Slider
        min={range[0]}
        max={range[1]}
        step={0.01}
        value={value}
        onValueChange={(v) => onChange(Array.isArray(v) ? (v[0] as number) : (v as number))}
        className="flex-1"
        aria-label={label}
      />
      <span className="num w-9 flex-none text-right text-[11px] text-[var(--g200)]">{value.toFixed(2)}</span>
    </div>
  )
}

const MAP_ROWS: [ControlKey, ControlKey][] = [
  ['map.leftY', 'map.leftYDo'],
  ['map.leftX', 'map.leftXDo'],
  ['map.right', 'map.rightDo'],
  ['map.options', 'map.optionsDo'],
  ['map.l1r1', 'map.l1r1Do'],
  ['map.l2r2', 'map.l2r2Do'],
  ['map.triangle', 'map.triangleDo'],
  ['map.square', 'map.squareDo'],
  ['map.circle', 'map.circleDo'],
  ['map.dpad', 'map.dpadDo'],
  ['map.touchpad', 'map.touchpadDo'],
  ['map.cross', 'map.crossDo'],
]

/** The DualSense map of 02 §7.5 as a lieflat table; also usable in Settings > Controllers. */
export function MappingReference({ className }: { className?: string }) {
  const ct = useCT()
  return (
    <table data-lf-table="" className={cn('w-full text-hud-sub', className)}>
      <caption className="sr-only">{ct('map.title')}</caption>
      <tbody>
        {MAP_ROWS.map(([k, d]) => (
          <tr key={k} className="border-t border-[var(--border)] first:border-t-0">
            <th scope="row" className="py-1 pr-3 text-left align-top font-[450] whitespace-nowrap text-[var(--g200)]">{ct(k)}</th>
            <td className="py-1 text-[var(--g400)]">{ct(d)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function PadsPopover() {
  const t = useT()
  const ct = useCT()
  const pads = usePads()
  const gate = useInputGate()
  const deadzone = usePrefs((p) => p.padDeadzone)
  const expo = usePrefs((p) => p.padExpo)
  const [mapOpen, setMapOpen] = React.useState(false)
  return (
    <div className="flex max-h-[min(640px,calc(100vh-96px))] flex-col gap-3 overflow-y-auto" data-pads-popover="">
      <div className="flex items-center justify-between">
        <h2 className="text-hud-title font-[560] text-[var(--g100)]">{t('pad.panel.title')}</h2>
        <span className="num text-hud-sub text-[var(--g500)]">{pads.filter((p) => p.connected).length} / 4</span>
      </div>
      {pads.length === 0 ? (
        <p className="text-hud-sub text-[var(--g400)]">{gate === 'pending' ? ct('pad.enableHint') : t('pad.panel.empty')}</p>
      ) : (
        <div className="flex flex-col gap-3">
          {pads.map((p) => (
            <PadRow key={p.index} pad={p} />
          ))}
          <p className="text-hud-sub text-[var(--g500)]">{ct('pad.bindHint')}</p>
        </div>
      )}
      <Separator />
      <div className="flex flex-col gap-2.5">
        <PrefSlider label={t('pad.panel.deadzone')} value={deadzone} range={DEADZONE_RANGE} onChange={(v) => setPrefs({ padDeadzone: v })} />
        <PrefSlider label={t('pad.panel.expo')} value={expo} range={EXPO_RANGE} onChange={(v) => setPrefs({ padExpo: v })} />
      </div>
      <Collapsible open={mapOpen} onOpenChange={setMapOpen}>
        <CollapsibleTrigger className="flex h-6 w-full items-center gap-2 rounded-sm text-left text-hud-sub text-[var(--g300)] outline-none hover:text-[var(--g100)] focus-visible:ring-1 focus-visible:ring-ring">
          <Icon icon="info" className="size-3.5" />
          <span className="flex-1">{t('pad.panel.mapping')}</span>
          <StateIcon icon="chevron.down" alt={mapOpen} className="size-3.5 text-[var(--g400)]" />
        </CollapsibleTrigger>
        <CollapsibleContent>
          <MappingReference className="mt-1" />
        </CollapsibleContent>
      </Collapsible>
      <p className="text-hud-sub text-[var(--g500)]">{ct('pad.auto')}</p>
    </div>
  )
}
