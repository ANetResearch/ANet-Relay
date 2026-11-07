// Settings > Controllers (02 §16 as amended by 05 §6, WP-C): dead zone, expo, control feel, stick mode, axis inversion,
// throttle detent, learned controller mappings and the mapping tables (controller, transmitter, keyboard). Plus the
// simulator rows of Settings > Safety (hover assist default, turbulence). The dialog passes its Row component.
import * as React from 'react'
import { useT8 } from '@/fleet/copy'
import { DEADZONE_RANGE, EXPO_RANGE, setControlFeel, setPrefs, usePrefs } from '@/settings/prefs'
import { Button } from '@/ui/components/ui/button'
import { Slider } from '@/ui/components/ui/slider'
import { Switch } from '@/ui/components/ui/switch'
import { Toggle } from '@/ui/components/ui/toggle'
import { ToggleGroup, ToggleGroupItem } from '@/ui/components/ui/toggle-group'
import { dropProfile } from './devices'
import { gamepadRows, HeadedTable, KEYBOARD_ROWS, TRANSMITTER_ROWS } from './MappingTables'
import { FEEL_ORDER, type AxisInvert, type ControlFeel } from './shaping'
import { useCT, type ControlKey } from './strings'

export type RowComponent = (p: { label: string; hint?: React.ReactNode; children: React.ReactNode; wide?: boolean }) => React.ReactNode

export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange(v: T): void; label: string }) {
  return (
    <ToggleGroup value={[value]} onValueChange={(v: unknown[]) => v[0] && onChange(v[0] as T)} spacing={0} aria-label={label} className="rounded-md bg-[var(--g950)] p-0.5 ring-1 ring-[var(--border)]">
      {options.map((o) => (
        <ToggleGroupItem key={o.value} value={o.value} className="h-6 min-w-14 rounded-[5px] px-2 text-[11px] text-[var(--g400)] hover:bg-transparent data-[pressed]:text-[var(--g50)]">
          {o.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  )
}

/** Gentle / Standard / Sport (05 §6.1); also in the controller popover. */
export function FeelSelector() {
  const ct = useCT()
  const feel = usePrefs((p) => p.controlFeel)
  return (
    <Segmented<ControlFeel>
      value={feel}
      onChange={setControlFeel}
      label={ct('feel.title')}
      options={FEEL_ORDER.map((f) => ({ value: f, label: ct(`feel.${f}` as ControlKey) }))}
    />
  )
}

const AXES: (keyof AxisInvert)[] = ['roll', 'pitch', 'yaw', 'throttle']

export function ControllerSettings({ Row }: { Row: RowComponent }) {
  const t = useT8()
  const ct = useCT()
  const dz = usePrefs((p) => p.padDeadzone)
  const expo = usePrefs((p) => p.padExpo)
  const mode = usePrefs((p) => p.stickMode)
  const invert = usePrefs((p) => p.invert)
  const detentOn = usePrefs((p) => p.throttleDetent)
  const profiles = usePrefs((p) => p.padProfiles)
  return (
    <>
      <Row label={ct('feel.title')} hint={ct('feel.hint')}>
        <FeelSelector />
      </Row>
      <Row label={t('pad.panel.deadzone')}>
        <div className="w-40 shrink-0"><Slider value={[dz]} min={DEADZONE_RANGE[0]} max={DEADZONE_RANGE[1]} step={0.01} onValueChange={(v) => setPrefs({ padDeadzone: Array.isArray(v) ? v[0] : (v as number) })} aria-label={t('pad.panel.deadzone')} /></div>
        <span className="num w-10 text-right text-[12px] text-[var(--g200)]">{dz.toFixed(2)}</span>
      </Row>
      <Row label={t('pad.panel.expo')}>
        <div className="w-40 shrink-0"><Slider value={[expo]} min={EXPO_RANGE[0]} max={EXPO_RANGE[1]} step={0.05} onValueChange={(v) => setPrefs({ padExpo: Array.isArray(v) ? v[0] : (v as number) })} aria-label={t('pad.panel.expo')} /></div>
        <span className="num w-10 text-right text-[12px] text-[var(--g200)]">{expo.toFixed(2)}</span>
      </Row>
      <Row label={ct('stickMode.title')} hint={ct('stickMode.hint')}>
        <Segmented<'1' | '2'>
          value={String(mode) as '1' | '2'}
          onChange={(v) => setPrefs({ stickMode: v === '1' ? 1 : 2 })}
          label={ct('stickMode.title')}
          options={[
            { value: '2', label: ct('stickMode.m2') },
            { value: '1', label: ct('stickMode.m1') },
          ]}
        />
      </Row>
      <Row label={ct('invert.title')}>
        <div className="flex items-center gap-1" data-invert-axes="">
          {AXES.map((a) => (
            <Toggle
              key={a}
              size="sm"
              variant="outline"
              pressed={invert[a]}
              onPressedChange={(on: boolean) => setPrefs({ invert: { ...invert, [a]: on } })}
              className="h-6 px-2 text-[11px]"
              aria-label={ct(`invert.${a}` as ControlKey)}
            >
              {ct(`invert.${a}` as ControlKey)}
            </Toggle>
          ))}
        </div>
      </Row>
      <Row label={ct('detent.title')} hint={ct('detent.hint')}>
        <Switch checked={detentOn} onCheckedChange={(v) => setPrefs({ throttleDetent: v })} aria-label={ct('detent.title')} />
      </Row>
      <Row label={ct('set.profiles')} hint={Object.keys(profiles).length ? undefined : ct('set.profilesEmpty')} wide>
        {Object.keys(profiles).length ? (
          <ul className="flex w-full flex-col gap-1" data-pad-profiles="">
            {Object.values(profiles).map((p) => (
              <li key={p.id} className="flex items-center gap-2 text-[12px]">
                <span className="min-w-0 flex-1 truncate text-[var(--g200)]" title={p.id}>{p.id}</span>
                <span className="text-hud-sub text-[var(--g500)]">{ct(p.kind === 'transmitter' ? 'pad.kind.transmitter' : 'pad.kind.generic')}</span>
                <Button variant="ghost" size="sm" className="h-6 px-2" onClick={() => setPrefs({ padProfiles: dropProfile(profiles, p.id) })}>
                  {ct('set.profileRemove')}
                </Button>
              </li>
            ))}
          </ul>
        ) : null}
      </Row>
      <MappingGroup title={t('set.mapping')} rows={gamepadRows(mode)} />
      <MappingGroup title={ct('set.txTitle')} rows={TRANSMITTER_ROWS} />
      <MappingGroup title={ct('kb.title')} rows={KEYBOARD_ROWS} />
    </>
  )
}

function MappingGroup({ title, rows }: { title: string; rows: Parameters<typeof HeadedTable>[0]['rows'] }) {
  const t = useT8()
  return (
    <div className="flex flex-col gap-1.5 pt-3">
      <span className="cap">{title}</span>
      <HeadedTable rows={rows} input={t('set.mapping.input')} action={t('set.mapping.action')} />
    </div>
  )
}

export interface SimSettingsValues {
  sim_hover_assist?: boolean
  sim_turbulence?: number
}

/** Settings > Safety: hover assist default for new simulators and turbulence (05 §5.2). */
export function SimSettings({ s, save, Row }: { s: SimSettingsValues; save(p: SimSettingsValues): Promise<boolean>; Row: RowComponent }) {
  const ct = useCT()
  // the slider shows the value being dragged, then the saved one
  const [dragging, setDragging] = React.useState<number | null>(null)
  const draft = dragging ?? s.sim_turbulence ?? 0.3
  return (
    <>
      <Row label={ct('set.simAssist')} hint={ct('set.simAssistHint')}>
        <Switch checked={s.sim_hover_assist ?? true} onCheckedChange={(v) => void save({ sim_hover_assist: v })} aria-label={ct('set.simAssist')} data-sim-hover-assist="" />
      </Row>
      <Row label={ct('set.simTurbulence')} hint={ct('set.simTurbulenceHint')}>
        <div className="w-40 shrink-0">
          <Slider
            value={[draft]}
            min={0}
            max={1}
            step={0.05}
            onValueChange={(v) => setDragging(Array.isArray(v) ? v[0] : (v as number))}
            onValueCommitted={(v) => {
              setDragging(null)
              void save({ sim_turbulence: Array.isArray(v) ? v[0] : (v as number) })
            }}
            aria-label={ct('set.simTurbulence')}
          />
        </div>
        <span className="num w-10 text-right text-[12px] text-[var(--g200)]">{draft.toFixed(2)}</span>
      </Row>
    </>
  )
}
