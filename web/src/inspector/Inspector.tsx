// Inspector (02 §11): header 64 (name with double-click rename, kind tag, controller pill; address · firmware ·
// sysid), tabs 36 (Overview · Control · Telemetry · Parameters · Log). Rendered in the right panel or inside the
// compact sheet below 1200 px. The Control tab body is WP7's ControlTab. Contract: CONTRACTS.md#inspector.
import * as React from 'react'
import { api } from '@/api'
import { cn } from '@/lib/utils'
import { notifyError } from '@/shell/notify'
import { useVehicle } from '@/store/selectors'
import { Kbd } from '@/ui/components/ui/kbd'
import { ScrollArea } from '@/ui/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/ui/components/ui/tabs'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/ui/components/ui/tooltip'
import { ArmedWord, ControllerPill, SimTag } from '@/ui/relay'
import { ControlTab } from '@/control/ControlTab'
import { useT8 } from '@/fleet/copy'
import { INSPECTOR_TABS, useInspectorTab, type InspectorTab } from './tabs'
import { OverviewTab } from './OverviewTab'
import { TelemetryTab } from './TelemetryTab'
import { ParametersTab } from './ParametersTab'
import { LogTab } from './LogTab'
import './inspector.css'

export interface InspectorProps {
  vehicleId: string | null
  variant: 'panel' | 'sheet'
}

function NameField({ id, label }: { id: string; label: string }) {
  const t = useT8()
  const [editing, setEditing] = React.useState(false)
  const [text, setText] = React.useState(label)
  if (editing) {
    return (
      <input
        autoFocus
        value={text}
        maxLength={40}
        aria-label={t('action.rename')}
        onFocus={(e) => e.currentTarget.select()}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          e.stopPropagation()
          if (e.key === 'Enter') {
            const v = text.trim()
            setEditing(false)
            if (v && v !== label) api.label(id, v).catch(notifyError)
          } else if (e.key === 'Escape') setEditing(false)
        }}
        // Esc blurs the field first (shell keyboard layer); blur cancels, Enter commits
        onBlur={() => setEditing(false)}
        className="h-6 min-w-0 flex-1 rounded-sm border border-[var(--g600)] bg-[var(--g950)] px-1.5 text-[14px] font-[560] text-[var(--g50)] outline-none focus:border-[var(--g300)]"
      />
    )
  }
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <h2
            className="min-w-0 cursor-text truncate text-[14px] leading-6 font-[560] text-[var(--g50)]"
            onDoubleClick={() => {
              setText(label)
              setEditing(true)
            }}
          />
        }
      >
        {label}
      </TooltipTrigger>
      <TooltipContent side="bottom" align="start">
        {t('insp.renameHint')}
      </TooltipContent>
    </Tooltip>
  )
}

function Header({ id, sheet = false }: { id: string; sheet?: boolean }) {
  const t = useT8()
  const v = useVehicle(id)
  if (!v) return null
  const meta = [v.kind === 'sim' ? v.id : (v.host ?? t('fleet.noHost')), v.firmware ? `${v.autopilot ?? ''} ${v.firmware}`.trim() : v.autopilot, v.sysid !== null ? t('insp.sysid', { n: v.sysid }) : null]
    .filter(Boolean)
    .join(' · ')
  return (
    // in the sheet the close button sits top right: keep the header row clear of it
    <header className={cn('flex h-16 shrink-0 flex-col justify-center gap-1 border-b border-[var(--border)] px-3', sheet && 'pr-11')} data-inspector-header="">
      <div className="flex min-w-0 items-center gap-2">
        {v.kind === 'sim' ? <SimTag /> : null}
        <NameField key={`${id}:${v.label}`} id={id} label={v.label} />
        <span className="flex-1" />
        {v.armed ? <ArmedWord armed className="shrink-0" /> : null}
        <ControllerPill vehicle={v} showWaiting compactWaiting className="min-w-0 shrink justify-end" />
      </div>
      <p className="addr truncate">{meta}</p>
    </header>
  )
}

function NoSelection({ gone }: { gone: boolean }) {
  const t = useT8()
  return (
    <div className="flex h-full flex-col px-5 pt-[22%]" data-inspector-empty="">
      <div className="flex w-[240px] flex-col gap-2">
        <p className="text-hud-title font-[560] text-[var(--g100)]">{t('insp.select')}</p>
        <p className="text-hud-sub text-[var(--g400)]">{gone ? t('insp.gone') : t('insp.selectHint')}</p>
        <div className="mt-2 flex flex-col gap-1.5 text-hud-sub text-[var(--g400)]">
          <span className="flex items-center gap-2">
            <Kbd>↑</Kbd>
            <Kbd>↓</Kbd>
            <span>{t('fleet.title')}</span>
          </span>
          <span className="flex items-center gap-2">
            <Kbd>Enter</Kbd>
            <span>{t('insp.title')}</span>
          </span>
          <span className="flex items-center gap-2">
            <Kbd>P</Kbd>
            <span>{t('action.possess')}</span>
          </span>
        </div>
      </div>
    </div>
  )
}

const TAB_KEYS: Record<InspectorTab, 'insp.tab.overview' | 'insp.tab.control' | 'insp.tab.telemetry' | 'insp.tab.parameters' | 'insp.tab.log'> = {
  overview: 'insp.tab.overview',
  control: 'insp.tab.control',
  telemetry: 'insp.tab.telemetry',
  parameters: 'insp.tab.parameters',
  log: 'insp.tab.log',
}

function Body({ id }: { id: string }) {
  const t = useT8()
  const tab = useInspectorTab((s) => s.tab)
  const setTab = useInspectorTab((s) => s.setTab)
  // Parameters and Log manage their own scrolling (virtual list); the other tabs scroll as a whole
  const own = tab === 'parameters'
  return (
    <Tabs value={tab} onValueChange={(v) => setTab(v as InspectorTab)} className="min-h-0 flex-1 gap-0">
      <TabsList variant="line" className="h-9 w-full shrink-0 justify-between gap-2 rounded-none border-b border-[var(--border)] px-3">
        {INSPECTOR_TABS.map((k) => (
          <TabsTrigger key={k} value={k} className="line-tab flex-none px-0" data-inspector-tab={k}>
            {t(TAB_KEYS[k])}
          </TabsTrigger>
        ))}
      </TabsList>
      {own ? (
        <TabsContent value="parameters" className="min-h-0 flex-1">
          <ParametersTab vehicleId={id} />
        </TabsContent>
      ) : (
        <ScrollArea className="insp-scroll min-h-0 flex-1">
          <TabsContent value="overview" className="py-3">
            <OverviewTab vehicleId={id} />
          </TabsContent>
          <TabsContent value="control" className="py-3">
            <ControlTab vehicleId={id} />
          </TabsContent>
          <TabsContent value="telemetry" className="py-3">
            <TelemetryTab vehicleId={id} />
          </TabsContent>
          <TabsContent value="log" className="py-1">
            <LogTab vehicleId={id} />
          </TabsContent>
        </ScrollArea>
      )}
    </Tabs>
  )
}

export function Inspector({ vehicleId, variant }: InspectorProps) {
  const exists = useVehicle(vehicleId) !== null
  return (
    <div className={cn('flex h-full min-w-0 flex-col', variant === 'sheet' && 'bg-[var(--g900)]')} data-inspector={variant}>
      {vehicleId && exists ? (
        <>
          <Header id={vehicleId} sheet={variant === 'sheet'} />
          <Body id={vehicleId} />
        </>
      ) : (
        <NoSelection gone={!!vehicleId && !exists} />
      )}
    </div>
  )
}
