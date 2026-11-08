// Top bar (02 §4.2, 44 high, L0): [traffic-light inset 76 | 12][word mark][Fly | Agents | Records][fleet summary]
// [input devices][EN / 中][Stop all]. The whole bar is the window drag region except its controls.
import * as React from 'react'
import { env } from '@/api'
import { setLang, useLang, useT } from '@/i18n'
import { cn } from '@/lib/utils'
import { armedTone } from '@/store/red'
import { useFleetSummary, useInputGate, usePads, useRedOwner } from '@/store/selectors'
import { useUi, type Workspace } from '@/store/ui'
import { Popover, PopoverContent, PopoverTrigger } from '@/ui/components/ui/popover'
import { ToggleGroup, ToggleGroupItem } from '@/ui/components/ui/toggle-group'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/ui/components/ui/tooltip'
import { Wordmark } from '@/ui/brand/Wordmark'
import { Icon, StateIcon } from '@/ui/icons'
import { PadsPopover } from '@/control/PadsPopover'
import { stopAll } from './actions'
import { AlertCenter } from './AlertCenter'
import { MOD } from './commands'
import { CopilotButton } from '@/copilot/CopilotPanel'
import { ArenaToggle } from '@/arena/ArenaToggle'

function WorkspaceSwitch() {
  const t = useT()
  const ws = useUi((s) => s.workspace)
  const setWs = useUi((s) => s.setWorkspace)
  const items: { id: Workspace; label: string }[] = [
    { id: 'fly', label: t('ws.fly') },
    { id: 'agents', label: t('ws.agents') },
    { id: 'records', label: t('ws.records') },
    { id: 'lab', label: t('ws.lab') }, // WP-3
  ]
  return (
    <ToggleGroup
      value={[ws]}
      onValueChange={(v: unknown[]) => v[0] && setWs(v[0] as Workspace)}
      spacing={0}
      aria-label={t('ws.label')}
      className="no-drag w-[320px] rounded-md bg-[var(--g900)] p-0.5 ring-1 ring-[var(--border)]"
    >
      {items.map((it) => (
        <ToggleGroupItem
          key={it.id}
          value={it.id}
          className="h-6 flex-1 rounded-[5px] text-[12px] font-[500] text-[var(--g400)] hover:bg-transparent hover:text-[var(--g100)] data-[pressed]:text-[var(--g50)]"
        >
          {it.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  )
}

function FleetSummary() {
  const t = useT()
  const s = useFleetSummary()
  const owner = useRedOwner()
  const open = useUi((u) => u.alertCenterOpen)
  const setOpen = useUi((u) => u.setAlertCenterOpen)
  const armedRed = armedTone(owner) === 'red'
  const sep = <span className="text-[var(--g600)]">·</span>
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className="no-drag num flex h-7 items-center gap-1.5 rounded-md px-2 text-[11px] leading-4 text-[var(--g400)] outline-none hover:bg-[var(--g900)] hover:text-[var(--g200)] focus-visible:ring-1 focus-visible:ring-ring"
        aria-label={t('top.summary.hint')}
      >
        <span>{t('top.summary.aircraft', { n: s.total })}</span>
        {s.armed > 0 ? (
          <>
            {sep}
            <span className={cn(armedRed ? 'text-[var(--r500)]' : 'font-bold text-[var(--g50)]')} data-armed-count="">
              {t('top.summary.armed', { n: s.armed })}
            </span>
          </>
        ) : null}
        {s.lost > 0 ? (
          <>
            {sep}
            <span className="text-[var(--g100)]">{t('top.summary.lost', { n: s.lost })}</span>
          </>
        ) : null}
      </PopoverTrigger>
      <PopoverContent align="end" sideOffset={6} className="l3 w-[400px] gap-0 p-0">
        <AlertCenter />
      </PopoverContent>
    </Popover>
  )
}

function InputDevices() {
  const t = useT()
  const pads = usePads()
  const gate = useInputGate()
  const open = useUi((u) => u.padsOpen)
  const setOpen = useUi((u) => u.setPadsOpen)
  const n = pads.filter((p) => p.connected).length
  const label = gate === 'pending' && n === 0 ? t('top.pads.enable') : n === 0 ? t('top.pads.none') : n === 1 ? t('top.pads.one') : t('top.pads.other', { n })
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className={cn(
          'no-drag flex h-7 items-center gap-1.5 rounded-md px-2 text-[11px] leading-4 outline-none hover:bg-[var(--g900)] focus-visible:ring-1 focus-visible:ring-ring',
          n > 0 ? 'text-[var(--g200)]' : gate === 'pending' ? 'text-[var(--g400)]' : 'text-[var(--g500)]',
        )}
        data-pads={n}
      >
        <StateIcon icon="gamepad" alt={n === 0} className="size-4" />
        <span className={cn('whitespace-nowrap', n === 0 && 'max-[1279px]:sr-only')}>{label}</span>
      </PopoverTrigger>
      <PopoverContent align="end" sideOffset={6} className="l3 w-[320px] p-3">
        <PadsPopover />
      </PopoverContent>
    </Popover>
  )
}

function LangSwitch() {
  const t = useT()
  const lang = useLang()
  return (
    <ToggleGroup
      value={[lang]}
      onValueChange={(v: unknown[]) => v[0] && setLang(v[0] as 'en' | 'zh')}
      spacing={0}
      aria-label={t('top.lang.label')}
      className="no-drag w-16 rounded-md bg-[var(--g900)] p-0.5 ring-1 ring-[var(--border)]"
    >
      <ToggleGroupItem value="en" className="h-6 flex-1 rounded-[5px] px-0 text-[11px] text-[var(--g400)] hover:bg-transparent data-[pressed]:text-[var(--g50)]">
        {t('top.lang.en')}
      </ToggleGroupItem>
      <ToggleGroupItem value="zh" className="h-6 flex-1 rounded-[5px] px-0 text-[11px] text-[var(--g400)] hover:bg-transparent data-[pressed]:text-[var(--g50)]">
        {t('top.lang.zh')}
      </ToggleGroupItem>
    </ToggleGroup>
  )
}

/** Stop all (02 §4.2): an action, not a state, so never red itself; a 1 px bar on its right edge while armed. */
export function StopAllButton() {
  const t = useT()
  const s = useFleetSummary()
  const owner = useRedOwner()
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <button
            type="button"
            onClick={stopAll}
            className="no-drag relative flex h-7 w-24 shrink-0 items-center justify-center gap-1.5 overflow-hidden rounded-md border border-[var(--g600)] bg-[var(--g850)] text-[12px] font-[560] text-[var(--g50)] outline-none transition-colors duration-quick hover:border-[var(--g300)] focus-visible:ring-2 focus-visible:ring-ring/40 active:bg-[var(--g800)]"
            data-stop-all=""
          />
        }
      >
        <Icon icon="stop" className="size-4" />
        <span>{t('action.stopAll')}</span>
        {s.armed > 0 ? (
          <i aria-hidden="true" className={cn('absolute top-1.5 right-0 bottom-1.5 w-px', armedTone(owner) === 'red' ? 'bg-[var(--r500)]' : 'bg-[var(--g50)]')} />
        ) : null}
      </TooltipTrigger>
      <TooltipContent side="bottom" align="end">
        {t('top.stopAll.hint').replace('⌘.', `${MOD}.`)}
      </TooltipContent>
    </Tooltip>
  )
}

export function TopBar() {
  const t = useT()
  const e = env()
  const mac = e.platform === 'darwin'
  return (
    <header
      className="drag grid h-11 grid-cols-[1fr_auto_1fr] items-center gap-3 pr-2"
      style={{ paddingLeft: mac && e.electron ? 'var(--traffic-light-w)' : 12 }}
    >
      <div className="flex min-w-0 items-center gap-3">
        <Wordmark />
        {e.fixture ? (
          <span className="no-drag sim-tag h-4 px-1 text-[var(--g500)]" title={t('boot.fixture')}>
            {t('top.fixture')}
          </span>
        ) : null}
      </div>
      <WorkspaceSwitch />
      <div className="flex min-w-max items-center justify-end gap-2">
        <FleetSummary />
        <span className="h-4 w-px bg-[var(--g800)]" aria-hidden="true" />
        <InputDevices />
        <ArenaToggle />
        <CopilotButton />
        <LangSwitch />
        <StopAllButton />
      </div>
    </header>
  )
}

export const TopBarMemo = React.memo(TopBar)
