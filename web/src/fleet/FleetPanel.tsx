// Fleet panel (02 §4.3): title row 40 with sort menu and add button, grouped cards (REAL above SIMULATED, 76 high,
// 4 apart), the empty state, and the bottom actions (92) with the scan progress. Contract: CONTRACTS.md#fleetpanel.
import * as React from 'react'
import { addSim, scanNetwork } from '@/shell/actions'
import { notify } from '@/shell/notify'
import { useRelay } from '@/store/relay'
import { useScan } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { Button } from '@/ui/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/ui/components/ui/dropdown-menu'
import { ScrollArea } from '@/ui/components/ui/scroll-area'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/ui/components/ui/tooltip'
import { Icon } from '@/ui/icons'
import { EmptyState } from '@/ui/relay'
import { t8, useT8 } from './copy'
import { FleetCard } from './FleetCard'
import { groupAndSort, SORT_KEYS, type SortKey } from './sort'
import './fleet.css'

const SORT_STORAGE = 'relay.fleet.sort'

function loadSort(): SortKey {
  try {
    const s = localStorage.getItem(SORT_STORAGE)
    return (SORT_KEYS as readonly string[]).includes(s ?? '') ? (s as SortKey) : 'name'
  } catch {
    return 'name'
  }
}

/** Sorted id groups; the panel re-renders only when membership or order changes. */
function useSortedGroups(key: SortKey): { real: string[]; sim: string[] } {
  const json = useRelay((s) => {
    const g = groupAndSort(s.fleet?.vehicles ?? [], key)
    return JSON.stringify([g.real.map((v) => v.id), g.sim.map((v) => v.id)])
  })
  return React.useMemo(() => {
    const [real, sim] = JSON.parse(json) as [string[], string[]]
    return { real, sim }
  }, [json])
}

function SortMenu({ value, onChange }: { value: SortKey; onChange(k: SortKey): void }) {
  const t = useT8()
  return (
    <DropdownMenu>
      <Tooltip>
        <TooltipTrigger
          render={
            <DropdownMenuTrigger
              render={<Button variant="ghost" size="icon-sm" aria-label={t('fleet.sort')} className="text-[var(--g400)] hover:text-[var(--g100)]" />}
            />
          }
        >
          <Icon icon="sort" />
        </TooltipTrigger>
        <TooltipContent>{t('fleet.sortBy')}</TooltipContent>
      </Tooltip>
      <DropdownMenuContent align="end" className="l3 w-44">
        <DropdownMenuGroup>
          <DropdownMenuLabel>{t('fleet.sortBy')}</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={value} onValueChange={(v) => onChange(v as SortKey)}>
            {SORT_KEYS.map((k) => (
              <DropdownMenuRadioItem key={k} value={k}>
                {t(`fleet.sort.${k}`)}
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function GroupLabel({ label, count }: { label: string; count: number }) {
  return (
    <div className="flex h-6 items-end gap-2 px-1 pb-1">
      <span className="cap">{label}</span>
      <span className="num text-hud-cap text-[var(--g500)]">{count}</span>
    </div>
  )
}

/** Toasts the outcome when a scan ends (any scan: footer, dialog, command palette). */
function useScanOutcome() {
  const scan = useScan()
  const was = React.useRef(scan?.running ?? false)
  React.useEffect(() => {
    if (!scan) return
    if (was.current && !scan.running) {
      if (scan.error) notify(t8('fleet.scanError', { reason: scan.error }), { level: 'warn' })
      else notify(t8('fleet.scanDone', { found: scan.found, net: scan.networks.join(', ') || '—' }))
    }
    was.current = scan.running
  }, [scan])
}

function ScanFooter() {
  const t = useT8()
  const scan = useScan()
  const openConnect = useUi((s) => s.openConnect)
  const running = !!scan?.running
  const net = scan ? (scan.networks.length > 1 ? `${scan.networks[0]} +${scan.networks.length - 1}` : (scan.networks[0] ?? '')) : ''
  const frac = scan && scan.total > 0 ? Math.min(1, scan.done / scan.total) : 0
  return (
    <footer className="flex h-[92px] shrink-0 flex-col gap-2 border-t border-[var(--border)] p-3">
      {running ? (
        <div className="relative flex h-8 flex-col justify-center gap-1.5 rounded-md border border-[var(--border)] bg-[var(--g850)] px-2.5" role="status" data-scan-progress="">
          <span className="num truncate text-[11px] leading-4 text-[var(--g200)]">
            {t('fleet.scanning', { net, done: scan?.done ?? 0, total: scan?.total ?? 0 })}
          </span>
          <span className="scan-line" aria-hidden="true">
            <i style={{ transform: `scaleX(${frac})` }} />
          </span>
        </div>
      ) : (
        <Button variant="outline" className="h-8 w-full justify-center gap-1.5 text-[12px]" onClick={scanNetwork} title={scan?.error ? t('fleet.scanError', { reason: scan.error }) : undefined}>
          <Icon icon="scan" className="size-4" />
          {t('fleet.scanNetwork')}
        </Button>
      )}
      <div className="grid h-7 grid-cols-2 gap-2">
        <Button variant="ghost" className="h-7 border border-[var(--border)] text-[12px] text-[var(--g200)]" onClick={() => openConnect('address')}>
          <Icon icon="connect" />
          {t('fleet.connectShort')}
        </Button>
        <Button variant="ghost" className="h-7 border border-[var(--border)] text-[12px] text-[var(--g200)]" onClick={addSim}>
          <Icon icon="sim" />
          {t('fleet.addSimShort')}
        </Button>
      </div>
    </footer>
  )
}

export function FleetPanel() {
  const t = useT8()
  const [sort, setSort] = React.useState<SortKey>(loadSort)
  const groups = useSortedGroups(sort)
  const openConnect = useUi((s) => s.openConnect)
  const hasFleet = useRelay((s) => s.fleet !== null)
  const listenPort = useRelay((s) => s.fleet?.listen.port ?? 14550)
  useScanOutcome()

  const changeSort = (k: SortKey) => {
    setSort(k)
    try {
      localStorage.setItem(SORT_STORAGE, k)
    } catch {
      /* storage unavailable */
    }
  }
  const empty = groups.real.length + groups.sim.length === 0

  return (
    <div className="flex h-full min-w-0 flex-col" data-fleet-panel="">
      <header className="flex h-10 shrink-0 items-center gap-1 pr-2 pl-3">
        <h2 className="text-hud-title font-[560] text-[var(--g100)]">{t('fleet.title')}</h2>
        <span className="num pl-1 text-hud-sub text-[var(--g500)]">{empty ? '' : groups.real.length + groups.sim.length}</span>
        <span className="flex-1" />
        <SortMenu value={sort} onChange={changeSort} />
        <Tooltip>
          <TooltipTrigger
            render={<Button variant="ghost" size="icon-sm" aria-label={t('fleet.add')} className="text-[var(--g400)] hover:text-[var(--g100)]" onClick={() => openConnect('scan')} />}
          >
            <Icon icon="plus" />
          </TooltipTrigger>
          <TooltipContent>{t('fleet.add')}</TooltipContent>
        </Tooltip>
      </header>
      {empty ? (
        <div className="min-h-0 flex-1">
          {hasFleet ? (
            <EmptyState
              title={t('fleet.empty.title')}
              body={t('fleet.empty.body', { port: listenPort })}
              actions={
                <>
                  <Button variant="outline" className="h-8 justify-start gap-2 text-[12px]" onClick={scanNetwork}>
                    <Icon icon="scan" className="size-4" />
                    {t('fleet.scanNetwork')}
                  </Button>
                  <Button variant="outline" className="h-8 justify-start gap-2 text-[12px]" onClick={() => openConnect('address')}>
                    <Icon icon="connect" className="size-4" />
                    {t('fleet.connectByAddress')}
                  </Button>
                  <Button variant="outline" className="h-8 justify-start gap-2 text-[12px]" onClick={addSim}>
                    <Icon icon="sim" className="size-4" />
                    {t('fleet.addSimulated')}
                  </Button>
                </>
              }
            />
          ) : null}
        </div>
      ) : (
        <ScrollArea className="fleet-scroll min-h-0 flex-1">
          <div className="flex flex-col gap-1 px-2 pb-2">
            {groups.real.length ? (
              <>
                <GroupLabel label={t('fleet.group.real')} count={groups.real.length} />
                {groups.real.map((id) => (
                  <FleetCard key={id} id={id} />
                ))}
              </>
            ) : null}
            {groups.sim.length ? (
              <>
                <div className={groups.real.length ? 'pt-2' : ''}>
                  <GroupLabel label={t('fleet.group.sim')} count={groups.sim.length} />
                </div>
                {groups.sim.map((id) => (
                  <FleetCard key={id} id={id} />
                ))}
              </>
            ) : null}
          </div>
        </ScrollArea>
      )}
      <ScanFooter />
    </div>
  )
}
