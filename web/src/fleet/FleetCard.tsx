// Fleet card (02 §4.3): 76 high, three rows, left 2 px status bar, possession frame, dashed frame for sims, hold line
// on the bottom edge, context menu. Memoised by id; each card subscribes to its own vehicle only.
import * as React from 'react'
import { api } from '@/api'
import { pickText, useLang } from '@/i18n'
import { courseTime, duration } from '@/lib/format'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/shell/notify'
import { possess, release } from '@/shell/actions'
import { useRelay, backendNow } from '@/store/relay'
import { useVehicle } from '@/store/selectors'
import { useUi } from '@/store/ui'
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from '@/ui/components/ui/context-menu'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/ui/components/ui/tooltip'
import { Icon } from '@/ui/icons'
import { ArmedWord, BatteryReadout, ControllerPill, HoldProgress, LinkStatus, SimTag, useStatusBar, Value } from '@/ui/relay'
import { openInspector } from '@/inspector/tabs'
import { useT8 } from './copy'
import { useHold } from './holds'

function useOpenAlerts(id: string) {
  return useRelay((s) => {
    let critical = false
    let warn = false
    for (const a of s.fleet?.alerts ?? []) {
      if (a.vehicle !== id || a.acked || s.localAcks.has(a.id)) continue
      if (a.level === 'critical') critical = true
      else if (a.level === 'warn') warn = true
    }
    return critical ? 'critical' : warn ? 'warn' : null
  })
}

function AlertMark({ id, level }: { id: string; level: 'critical' | 'warn' }) {
  const lang = useLang()
  const texts = useRelay((s) =>
    (s.fleet?.alerts ?? [])
      .filter((a) => a.vehicle === id && !a.acked && !s.localAcks.has(a.id))
      .map((a) => pickText(lang, a.text_en, a.text_zh))
      .join('\n'),
  )
  return (
    <Tooltip>
      <TooltipTrigger render={<span className={cn('inline-flex shrink-0', level === 'critical' ? 'text-[var(--g50)]' : 'text-[var(--g300)]')} data-alert-mark={level} />}>
        <Icon icon={level === 'critical' ? 'alert.critical' : 'alert'} className="size-3.5" label={texts} />
      </TooltipTrigger>
      <TooltipContent className="whitespace-pre-line">{texts}</TooltipContent>
    </Tooltip>
  )
}

function RenameField({ initial, onDone }: { initial: string; onDone(label: string | null): void }) {
  const t = useT8()
  const [text, setText] = React.useState(initial)
  return (
    <input
      autoFocus
      value={text}
      maxLength={40}
      aria-label={t('action.rename')}
      placeholder={t('fleet.rename.placeholder')}
      onChange={(e) => setText(e.target.value)}
      onFocus={(e) => e.currentTarget.select()}
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        e.stopPropagation()
        if (e.key === 'Enter') {
          const v = text.trim()
          onDone(v && v !== initial ? v : null)
        } else if (e.key === 'Escape') onDone(null)
      }}
      // Esc first blurs the field (shell keyboard layer), so blur cancels and only Enter commits
      onBlur={() => onDone(null)}
      className="h-5 min-w-0 flex-1 rounded-sm border border-[var(--g600)] bg-[var(--g950)] px-1 text-hud-title font-[560] text-[var(--g50)] outline-none focus:border-[var(--g300)]"
    />
  )
}

export const FleetCard = React.memo(function FleetCard({ id }: { id: string }) {
  const t = useT8()
  const v = useVehicle(id)
  const selected = useUi((s) => s.selectedId === id)
  const alertLevel = useOpenAlerts(id)
  const bar = useStatusBar(v, alertLevel === 'critical')
  const hold = useHold(id)
  const [renaming, setRenaming] = React.useState(false)
  const ref = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    if (selected) ref.current?.scrollIntoView({ block: 'nearest' })
  }, [selected])

  if (!v) return null
  const sim = v.kind === 'sim'
  const possessed = v.controller === 'human' && !!v.possession
  const running = v.course && (v.course.state === 'running' || v.course.state === 'waiting_takeoff') ? v.course : null

  const commitRename = (label: string | null) => {
    setRenaming(false)
    if (label) api.label(id, label).catch(notifyError)
  }
  const remove = () => {
    const label = v.label
    api
      .remove(id)
      .then(() => notify(t(sim ? 'fleet.deleted' : 'fleet.removed', { aircraft: label })))
      .catch(notifyError)
  }

  const address = sim ? v.id : (v.host ?? t('fleet.noHost'))
  const modeReason = v.link.state === 'waiting' ? t('fleet.waitingHeartbeat') : t('fleet.reason.noMode')

  return (
    <ContextMenu>
      <ContextMenuTrigger
        render={
          <div
            ref={ref}
            role="button"
            tabIndex={0}
            aria-pressed={selected}
            aria-label={v.label}
            data-fleet-card={id}
            data-kind={v.kind}
            data-selected={selected ? '' : undefined}
            data-possessed={possessed ? '' : undefined}
            data-bar={bar}
            className="fleet-card"
            onClick={() => useUi.getState().select(id)}
            onDoubleClick={() => openInspector(id)}
          />
        }
      >
        <i className="fleet-card-bar" aria-hidden="true" />
        {/* row 1: name · link · armed */}
        <div className="flex h-4 items-center gap-2">
          <div className="flex min-w-0 flex-1 items-center gap-1.5">
            {sim ? <SimTag /> : null}
            {renaming ? (
              <RenameField initial={v.label} onDone={commitRename} />
            ) : (
              <span className="truncate text-hud-title font-[560] text-[var(--g100)]">{v.label}</span>
            )}
            {alertLevel ? <AlertMark id={id} level={alertLevel} /> : null}
          </div>
          <LinkStatus link={v.link} critical={alertLevel === 'critical'} className="shrink-0" />
          {v.crashed ? (
            <span className="shrink-0 text-hud-sub font-[560] text-[var(--g50)]">{t('status.crashed')}</span>
          ) : (
            <ArmedWord armed={v.armed} className="w-[52px] shrink-0 text-right" />
          )}
        </div>
        {/* row 2: address · firmware · mode · flight time */}
        <div className="flex h-4 items-center gap-2">
          <span className="addr min-w-0 flex-1 truncate">
            {address}
            {v.autopilot ? <span className="text-[var(--g500)]"> · {v.autopilot}</span> : null}
          </span>
          <Value value={v.mode} reason={modeReason} className="max-w-[96px] shrink-0 truncate text-[11px] leading-4 text-[var(--g300)]" />
          <span className="num w-[44px] shrink-0 text-right text-[11px] leading-4 text-[var(--g400)]" title={t('fleet.flightTime')}>
            {duration(v.flight_time_s)}
          </span>
        </div>
        {/* row 3: battery · course progress · controller */}
        <div className="flex h-[18px] items-center gap-2">
          {running ? (
            // the course progress takes the battery's place (02 §10.4); the battery stays in the inspector and HUD
            <span className="flex min-w-0 flex-1 items-center gap-2">
              <span className="num truncate text-[11px] leading-4 text-[var(--g200)]">
                {t('fleet.ringProgress', { n: Math.min(running.next + 1, running.total), total: running.total, t: courseTime(running.elapsed_s) ?? '—' })}
              </span>
            </span>
          ) : (
            <BatteryReadout battery={v.battery} className="h-4 min-w-0 flex-1 flex-wrap overflow-hidden whitespace-nowrap" />
          )}
          <ControllerPill vehicle={v} className="max-w-[60%] shrink-0 justify-end" />
        </div>
        <HoldProgress active={!!hold} durationMs={hold?.durationMs ?? 1000} className="fleet-card-hold" />
      </ContextMenuTrigger>
      <ContextMenuContent className="l3 min-w-48">
        {possessed ? (
          <ContextMenuItem onClick={() => release(id)}>
            <Icon icon="possess" />
            {t('action.release')}
          </ContextMenuItem>
        ) : (
          <ContextMenuItem onClick={() => possess(id)} disabled={v.link.state !== 'ok' && v.link.state !== 'delayed'}>
            <Icon icon="possess" />
            {t('action.possess')}
          </ContextMenuItem>
        )}
        <ContextMenuItem onClick={() => setRenaming(true)}>
          <Icon icon="sliders" />
          {t('action.rename')}
        </ContextMenuItem>
        <ContextMenuItem onClick={() => openInspector(id, 'control', 'calibration')}>
          <Icon icon="crosshair" />
          {t('fleet.menu.calibrate')}
        </ContextMenuItem>
        <ContextMenuItem onClick={() => useUi.getState().focusRecords(id, backendNow())}>
          <Icon icon="ws.records" />
          {t('fleet.menu.openRecords')}
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem onClick={remove} disabled={v.armed}>
          <Icon icon="trash" />
          {t(sim ? 'action.delete' : 'action.remove')}
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  )
})
