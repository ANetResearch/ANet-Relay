// Settings dialog (02 §16, 640 wide, categories on the left): General · Safety · Controllers · Network · Data ·
// About. Backend settings go through GET / PUT /settings (validated there); window preferences through prefs.ts.
// Controlled by the shell (`useUi().settings`); ⌘, opens it. Contract: CONTRACTS.md#settingsdialog.
import * as React from 'react'
import { api, env, RelayApiError } from '@/api'
import { setLang, useLang, useLangStore } from '@/i18n'
import { cn } from '@/lib/utils'
import { notify } from '@/shell/notify'
import { useHello } from '@/store/selectors'
import type { SettingsSection } from '@/store/ui'
import { Button } from '@/ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/ui/components/ui/dialog'
import { Input } from '@/ui/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/ui/components/ui/native-select'
import { Slider } from '@/ui/components/ui/slider'
import { Switch } from '@/ui/components/ui/switch'
import { ToggleGroup, ToggleGroupItem } from '@/ui/components/ui/toggle-group'
import { Icon } from '@/ui/icons'
import { Value } from '@/ui/relay'
import { t8, useT8, type AnyKey } from '@/fleet/copy'
import { CopyButton, openExternal } from '@/agents/CopyButton'
import { applyBackendUi, DEADZONE_RANGE, EXPO_RANGE, setPrefs, usePrefs, type StartView, type Units } from './prefs'
import './settings.css'

export interface SettingsDialogProps {
  open: boolean
  section: SettingsSection
  onOpenChange(open: boolean): void
}

interface BackendSettings {
  safety_volume: { x: number; y: number; z: number }
  cell_warn_v: number
  cell_crit_v: number
  possess_mode: 'auto' | 'stabilize' | 'althold' | string
  airborne_alert: boolean
  auto_params: boolean
  retention_days: number
  listen_port: number
  bridge_port: number
  stick_timeout_s: number
  restart_fields?: string[]
  data_dir?: string | null
  ui?: Record<string, unknown>
}

const SECTIONS: { id: SettingsSection; key: AnyKey; icon: Parameters<typeof Icon>[0]['icon'] }[] = [
  { id: 'general', key: 'set.general', icon: 'settings' },
  { id: 'safety', key: 'set.safety', icon: 'stop' },
  { id: 'controllers', key: 'set.controllers', icon: 'gamepad' },
  { id: 'network', key: 'set.network', icon: 'network' },
  { id: 'data', key: 'set.data', icon: 'database' },
  { id: 'about', key: 'set.about', icon: 'info' },
]

const REPO = 'https://github.com/ANetResearch/ANet-Relay'
const ANET4D = 'https://anet4d.agentnetwork.org.cn/'

function errText(err: unknown): string {
  const lang = useLangStore.getState().lang
  return err instanceof RelayApiError ? err.text(lang) : err instanceof Error ? err.message : String(err)
}

/** Loads backend settings when the backend is reachable; keeps the window preferences in sync. */
function useBackendSettings(open: boolean): [BackendSettings | null, (patch: Partial<BackendSettings>) => Promise<boolean>] {
  const hello = useHello()
  const [s, setS] = React.useState<BackendSettings | null>(null)
  React.useEffect(() => {
    if (!hello) return
    let alive = true
    api.settings().then(
      (data) => {
        if (!alive) return
        const b = data as unknown as BackendSettings
        setS(b)
        applyBackendUi(b.ui)
      },
      () => {
        /* offline: the dialog shows the not-connected state */
      },
    )
    return () => {
      alive = false
    }
  }, [hello, open])
  const save = React.useCallback(async (patch: Partial<BackendSettings>) => {
    try {
      const next = (await api.putSettings(patch as Record<string, unknown>)) as unknown as BackendSettings
      setS(next)
      return true
    } catch (err) {
      notify(t8('set.saveFailed'), { level: 'warn', description: errText(err) })
      return false
    }
  }, [])
  return [s, save]
}

// ------------------------------------------------------------------------------------------------ rows

function Row({ label, hint, children, wide = false }: { label: string; hint?: React.ReactNode; children: React.ReactNode; wide?: boolean }) {
  return (
    <div className={cn('set-row', wide && 'set-row-wide')}>
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="text-ed-value text-[var(--g100)]">{label}</span>
        {hint ? <span className="text-hud-sub text-[var(--g500)]">{hint}</span> : null}
      </div>
      <div className={cn('flex min-w-0 items-center gap-2', wide ? 'justify-start' : 'justify-end')}>{children}</div>
    </div>
  )
}

/** Number field that commits on Enter or blur; invalid input reverts. */
function NumberField({ value, onCommit, step = 1, min, max, unit, width = 76, label }: {
  value: number
  onCommit(v: number): void
  step?: number
  min?: number
  max?: number
  unit?: string
  width?: number
  label: string
}) {
  const [text, setText] = React.useState(String(value))
  const [seen, setSeen] = React.useState(value)
  if (seen !== value) {
    setSeen(value)
    setText(String(value))
  }
  const commit = () => {
    const n = Number(text)
    if (!Number.isFinite(n) || text.trim() === '' || (min !== undefined && n < min) || (max !== undefined && n > max)) {
      setText(String(value))
      return
    }
    if (n !== value) onCommit(n)
  }
  return (
    <span className="flex items-center gap-1.5">
      <Input
        value={text}
        inputMode="decimal"
        aria-label={label}
        step={step}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.currentTarget as HTMLInputElement).blur()
        }}
        className="h-7 text-right font-mono text-[12px]"
        style={{ width }}
      />
      {unit ? <span className="num min-w-5 text-[11px] text-[var(--g500)]">{unit}</span> : null}
    </span>
  )
}

function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange(v: T): void; label: string }) {
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

function ReadOnly({ value, copy }: { value: string; copy?: boolean }) {
  const t = useT8()
  return (
    <span className="flex items-center gap-1">
      <span className="num text-[12px] text-[var(--g200)]">{value}</span>
      <span className="text-hud-sub text-[var(--g500)]">· {t('set.readOnly')}</span>
      {copy ? <CopyButton text={value} /> : null}
    </span>
  )
}

// ------------------------------------------------------------------------------------------------ sections

function General() {
  const t = useT8()
  const lang = useLang()
  const units = usePrefs((p) => p.units)
  const startView = usePrefs((p) => p.startView)
  const chase = usePrefs((p) => p.chaseOnCourse)
  return (
    <>
      <Row label={t('set.language')}>
        <Segmented value={lang} onChange={(v) => setLang(v)} label={t('set.language')} options={[{ value: 'en', label: 'English' }, { value: 'zh', label: '中文' }]} />
      </Row>
      <Row label={t('set.units')}>
        <Segmented<Units> value={units} onChange={(v) => setPrefs({ units: v })} label={t('set.units')} options={[{ value: 'm', label: t('set.metres') }, { value: 'ft', label: t('set.feet') }]} />
      </Row>
      <Row label={t('set.startView')}>
        <Segmented<StartView>
          value={startView}
          onChange={(v) => setPrefs({ startView: v })}
          label={t('set.startView')}
          options={[
            { value: 'overview', label: t('stage.view.overview') },
            { value: 'chase', label: t('stage.view.chase') },
            { value: 'top', label: t('stage.view.top') },
          ]}
        />
      </Row>
      <Row label={t('set.chaseOnCourse')}>
        <Switch checked={chase} onCheckedChange={(v) => setPrefs({ chaseOnCourse: v })} aria-label={t('set.chaseOnCourse')} />
      </Row>
    </>
  )
}

function Safety({ s, save }: { s: BackendSettings; save(p: Partial<BackendSettings>): Promise<boolean> }) {
  const t = useT8()
  const sv = s.safety_volume
  const cells = 3
  return (
    <>
      <Row label={t('set.safetyVolume')} hint={t('set.safetyHint')} wide>
        <NumberField label={t('set.width')} value={sv.x} step={0.5} min={1} max={50} unit="m" width={60} onCommit={(x) => void save({ safety_volume: { ...sv, x } })} />
        <span className="text-[var(--g600)]">×</span>
        <NumberField label={t('set.length')} value={sv.y} step={0.5} min={1} max={50} unit="m" width={60} onCommit={(y) => void save({ safety_volume: { ...sv, y } })} />
        <span className="text-[var(--g600)]">×</span>
        <NumberField label={t('set.height')} value={sv.z} step={0.5} min={1} max={20} unit="m" width={60} onCommit={(z) => void save({ safety_volume: { ...sv, z } })} />
      </Row>
      <Row label={t('set.cellWarn')} hint={t('set.cellHint', { warn: (s.cell_warn_v * cells).toFixed(2), crit: (s.cell_crit_v * cells).toFixed(2) })}>
        <NumberField label={t('set.cellWarn')} value={s.cell_warn_v} step={0.01} min={3} max={4.2} unit="V" onCommit={(v) => void save({ cell_warn_v: v })} />
      </Row>
      <Row label={t('set.cellCritical')}>
        <NumberField label={t('set.cellCritical')} value={s.cell_crit_v} step={0.01} min={3} max={4.2} unit="V" onCommit={(v) => void save({ cell_crit_v: v })} />
      </Row>
      <Row label={t('set.possessMode')} hint={t('set.possessModeHint')}>
        <NativeSelect value={s.possess_mode} onChange={(e) => void save({ possess_mode: e.target.value })} aria-label={t('set.possessMode')} className="w-44 [&_select]:h-7 [&_select]:text-[12px]">
          <NativeSelectOption value="auto">{t('set.possessMode.auto')}</NativeSelectOption>
          <NativeSelectOption value="althold">{t('set.possessMode.althold')}</NativeSelectOption>
          <NativeSelectOption value="stabilize">{t('set.possessMode.stabilize')}</NativeSelectOption>
        </NativeSelect>
      </Row>
      <Row label={t('set.airborneAlert')}>
        <Switch checked={s.airborne_alert} onCheckedChange={(v) => void save({ airborne_alert: v })} aria-label={t('set.airborneAlert')} />
      </Row>
      <Row label={t('set.stickTimeout')} hint={t('set.stickTimeoutHint')}>
        <ReadOnly value={`${s.stick_timeout_s.toFixed(1)} s`} />
      </Row>
    </>
  )
}

const MAPPING: [AnyKey, AnyKey][] = [
  ['map.leftV', 'map.leftV.do'],
  ['map.leftH', 'map.leftH.do'],
  ['map.right', 'map.right.do'],
  ['map.options', 'map.options.do'],
  ['map.l1r1', 'map.l1r1.do'],
  ['map.l2r2', 'map.l2r2.do'],
  ['map.triangle', 'map.triangle.do'],
  ['map.square', 'map.square.do'],
  ['map.circle', 'map.circle.do'],
  ['map.dpad', 'map.dpad.do'],
  ['map.touchpad', 'map.touchpad.do'],
]

function Controllers() {
  const t = useT8()
  const dz = usePrefs((p) => p.padDeadzone)
  const expo = usePrefs((p) => p.padExpo)
  return (
    <>
      <Row label={t('pad.panel.deadzone')}>
        <div className="w-40 shrink-0"><Slider value={[dz]} min={DEADZONE_RANGE[0]} max={DEADZONE_RANGE[1]} step={0.01} onValueChange={(v) => setPrefs({ padDeadzone: Array.isArray(v) ? v[0] : (v as number) })} aria-label={t('pad.panel.deadzone')} /></div>
        <span className="num w-10 text-right text-[12px] text-[var(--g200)]">{dz.toFixed(2)}</span>
      </Row>
      <Row label={t('pad.panel.expo')}>
        <div className="w-40 shrink-0"><Slider value={[expo]} min={EXPO_RANGE[0]} max={EXPO_RANGE[1]} step={0.05} onValueChange={(v) => setPrefs({ padExpo: Array.isArray(v) ? v[0] : (v as number) })} aria-label={t('pad.panel.expo')} /></div>
        <span className="num w-10 text-right text-[12px] text-[var(--g200)]">{expo.toFixed(2)}</span>
      </Row>
      <div className="flex flex-col gap-1.5 pt-3">
        <span className="cap">{t('set.mapping')}</span>
        <table className="w-full text-[12px]" data-lf-table="">
          <thead>
            <tr className="border-b border-[var(--foreground)] text-left">
              <th className="cap py-1 font-[560]">{t('set.mapping.input')}</th>
              <th className="cap py-1 font-[560]">{t('set.mapping.action')}</th>
            </tr>
          </thead>
          <tbody>
            {MAPPING.map(([k, d]) => (
              <tr key={k} className="border-b border-dotted border-[var(--border)]">
                <td className="py-1.5 pr-3 text-[var(--g200)]">{t(k)}</td>
                <td className="py-1.5 text-[var(--g400)]">{t(d)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function Network({ s, save }: { s: BackendSettings; save(p: Partial<BackendSettings>): Promise<boolean> }) {
  const t = useT8()
  const apiBase = env().apiBase
  const port = (() => {
    try {
      return new URL(apiBase).port || '80'
    } catch {
      return '—'
    }
  })()
  const restart = (s.restart_fields ?? []).includes('listen_port')
  return (
    <>
      <Row label={t('set.listenPort')} hint={restart ? t('set.restartNeeded') : undefined}>
        <NumberField label={t('set.listenPort')} value={s.listen_port} min={1024} max={65535} onCommit={(v) => void save({ listen_port: v })} />
      </Row>
      <Row label={t('set.bridgePort')}>
        <NumberField label={t('set.bridgePort')} value={s.bridge_port} min={1} max={65535} onCommit={(v) => void save({ bridge_port: v })} />
      </Row>
      <Row label={t('set.apiPort')}>
        <ReadOnly value={port} copy />
      </Row>
      <Row label={t('set.autoParams')}>
        <Switch checked={s.auto_params} onCheckedChange={(v) => void save({ auto_params: v })} aria-label={t('set.autoParams')} />
      </Row>
    </>
  )
}

function Data({ s, save }: { s: BackendSettings; save(p: Partial<BackendSettings>): Promise<boolean> }) {
  const t = useT8()
  const canOpen = !!window.relay?.revealLogs
  return (
    <>
      <Row label={t('set.dataDir')}>
        <Value value={s.data_dir ?? null} reason={t('set.dataDirUnknown')} className="min-w-0 truncate font-mono text-[11px] text-[var(--g300)]" />
        <Button variant="outline" size="sm" className="gap-1.5" disabled={!canOpen} title={canOpen ? undefined : t('set.desktopOnly')} onClick={() => void window.relay?.revealLogs()}>
          <Icon icon="folder" />
          {t('set.openDir')}
        </Button>
      </Row>
      <Row label={t('set.retention')} hint={t('set.retentionHint')}>
        <NumberField label={t('set.retention')} value={s.retention_days} min={1} max={3650} unit={t('set.daysUnit')} onCommit={(v) => void save({ retention_days: Math.round(v) })} />
      </Row>
    </>
  )
}

function About() {
  const t = useT8()
  const hello = useHello()
  return (
    <>
      <div className="flex items-center gap-3 pb-3">
        <img src="./brand/anet-logo.svg" alt="" className="h-4 opacity-90" />
        <span className="text-[13px] font-[560] text-[var(--g100)]">{t('brand.product')}</span>
        <span className="text-hud-sub text-[var(--g500)]">{t('brand.tagline')}</span>
      </div>
      <Row label={t('set.appVersion')}>
        <Value value={window.relay?.version ?? null} reason={t('set.desktopOnly')} className="text-[12px]" />
      </Row>
      <Row label={t('set.backendVersion')}>
        <Value value={hello?.version ?? null} reason={t('set.notConnected')} className="text-[12px]" />
      </Row>
      <Row label={t('set.license')}>
        <span className="text-ed-value text-[var(--g200)]">{t('set.licenseValue')}</span>
      </Row>
      <Row label={t('set.links')}>
        <Button variant="ghost" size="sm" className="gap-1.5 text-[var(--g200)]" onClick={() => openExternal(REPO)}>
          <Icon icon="external" />
          {t('set.github')}
        </Button>
        <Button variant="ghost" size="sm" className="gap-1.5 text-[var(--g200)]" onClick={() => openExternal(ANET4D)}>
          <Icon icon="external" />
          {t('set.anet4d')}
        </Button>
      </Row>
    </>
  )
}

export function SettingsDialog({ open, section, onOpenChange }: SettingsDialogProps) {
  const t = useT8()
  const [current, setCurrent] = React.useState<SettingsSection>(section)
  const [seen, setSeen] = React.useState({ open, section })
  if (seen.open !== open || seen.section !== section) {
    setSeen({ open, section })
    if (open) setCurrent(section)
  }
  const [s, save] = useBackendSettings(open)
  const needsBackend = current === 'safety' || current === 'network' || current === 'data'

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="l3 flex h-[min(560px,calc(100vh-48px))] flex-col gap-0 overflow-hidden p-0 sm:max-w-[640px]" data-settings-dialog="">
        <DialogHeader className="shrink-0 gap-1 border-b border-[var(--border)] px-5 pt-5 pb-3">
          <DialogTitle className="text-[15px] font-[560] text-[var(--g50)]">{t('set.title')}</DialogTitle>
          <DialogDescription className="text-hud-sub text-[var(--g400)]">{t('set.description')}</DialogDescription>
        </DialogHeader>
        <div className="flex min-h-0 flex-1">
          <nav className="flex w-[168px] shrink-0 flex-col gap-0.5 border-r border-[var(--border)] p-2" aria-label={t('set.title')}>
            {SECTIONS.map((sec) => (
              <button
                key={sec.id}
                type="button"
                onClick={() => setCurrent(sec.id)}
                className="set-nav"
                data-active={current === sec.id ? '' : undefined}
                aria-current={current === sec.id ? 'page' : undefined}
                data-settings-section={sec.id}
              >
                <Icon icon={sec.icon} className="size-3.5" />
                {t(sec.key)}
              </button>
            ))}
          </nav>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3">
            <h3 className="pb-2 text-hud-title font-[560] text-[var(--g100)]">{t(SECTIONS.find((x) => x.id === current)!.key)}</h3>
            {needsBackend && !s ? (
              <p className="text-hud-sub text-[var(--g500)]">{t('set.notConnected')}</p>
            ) : current === 'general' ? (
              <General />
            ) : current === 'safety' && s ? (
              <Safety s={s} save={save} />
            ) : current === 'controllers' ? (
              <Controllers />
            ) : current === 'network' && s ? (
              <Network s={s} save={save} />
            ) : current === 'data' && s ? (
              <Data s={s} save={save} />
            ) : (
              <About />
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
