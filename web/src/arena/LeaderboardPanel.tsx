// Leaderboard panel of the Fly stage (07 §4, §5): top right under the view tools while the arena is on, collapsible.
// shadcn Card + Tabs + Table under the lieflat table rules: tabs Downs / Survival / Accuracy / Rams; each row the
// rank, aircraft, controller and the tab's primary and secondary metric (./leaderboard); this terminal's rows in
// bold with the selected-row bar; the match countdown in the header; "End match" while a dogfight scenario runs.
// The board is refetched only when the arena view's board_rev or match changes and when the tab changes (./board);
// survival times run on locally between two refetches. A backend without a leaderboard (404) gets the v0.4 terminal
// scoreboard instead. Graphite only: nothing here paints red.
import * as React from 'react'
import { useShallow } from 'zustand/react/shallow'
import { duration } from '@/lib/format'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/shell/notify'
import { backendNow } from '@/store/relay'
import { Button } from '@/ui/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/ui/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/ui/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/ui/components/ui/tabs'
import { Icon, StateIcon } from '@/ui/icons'
import { arenaApi } from './api'
import { refetchBoard, refetchScenario, useBoard } from './board'
import { useTA, type ArenaKey } from './copy'
import { BOARD_TABS, matchLeft, metricCells, ranks, rowLabel, sortRows, type BoardBy, type ControllerKind, type LeaderboardRow } from './leaderboard'
import { useBoardSignal } from './store'
import './arena.css'

/** The survival columns and the countdown move once a second. */
const TICK_MS = 1000

const TAB_KEY: Record<BoardBy, ArenaKey> = { downs: 'board.tab.downs', survival: 'board.tab.survival', accuracy: 'board.tab.accuracy', rams: 'board.tab.rams' }
const COLS: Record<BoardBy, [ArenaKey, ArenaKey]> = {
  downs: ['board.col.downs', 'board.col.lost'],
  survival: ['board.col.best', 'board.col.now'],
  accuracy: ['board.col.accuracy', 'board.col.hits'],
  rams: ['board.col.rams', 'board.col.crashes'],
}
const CTL_KEY: Record<ControllerKind, ArenaKey> = { program: 'board.ctl.program', agent: 'board.ctl.agent', human: 'board.ctl.human', none: 'board.ctl.none' }

/** performance.now() of the last tick, once a second while `on` (a board newer than the tick runs on from zero). */
function useTick(on: boolean): number {
  const [now, setNow] = React.useState(() => performance.now())
  React.useEffect(() => {
    if (!on) return
    const id = window.setInterval(() => setNow(performance.now()), TICK_MS)
    return () => window.clearInterval(id)
  }, [on])
  return now
}

/** Rows with survival times run on from the moment the board arrived (alive rows only). */
export function liveRows(rows: readonly LeaderboardRow[], elapsedS: number): LeaderboardRow[] {
  if (!(elapsedS > 0)) return [...rows]
  return rows.map((r) => {
    if (r.alive_s <= 0) return r
    const alive = r.alive_s + elapsedS
    return { ...r, alive_s: alive, best_alive_s: Math.max(r.best_alive_s, alive) }
  })
}

function EndMatch() {
  const t = useTA()
  const [busy, setBusy] = React.useState(false)
  return (
    <Button
      variant="outline"
      size="sm"
      disabled={busy}
      data-board-end=""
      onClick={() => {
        setBusy(true)
        void arenaApi
          .dogfightStop()
          .then(() => notify(t('board.ended')))
          .catch((e: unknown) => notifyError(e))
          .finally(() => {
            setBusy(false)
            void refetchScenario()
            void refetchBoard()
          })
      }}
    >
      {t('board.endMatch')}
    </Button>
  )
}

export function LeaderboardPanel({ top, fallback }: { top: number; fallback: React.ReactNode }) {
  const t = useTA()
  const signal = useBoardSignal()
  const { by, board, at, status, scenario, collapsed } = useBoard(
    useShallow((s) => ({ by: s.by, board: s.board, at: s.at, status: s.status, scenario: s.scenario, collapsed: s.collapsed })),
  )
  const matchSig = signal.split('|')[2] ?? ''
  React.useEffect(() => {
    void refetchBoard()
  }, [signal])
  React.useEffect(() => {
    void refetchScenario()
  }, [matchSig])
  const match = board?.match ?? null
  const now = useTick(!collapsed && (Boolean(match?.running) || by === 'survival'))
  if (status === 'unavailable') return <>{fallback}</>

  const left = matchLeft(match, backendNow())
  const caption = left !== null ? t('board.match', { t: duration(left) ?? '—' }) : match && !match.running ? t('board.matchEnded') : t('board.totals')
  const rows = sortRows(liveRows(board?.rows ?? [], at > 0 ? Math.max(0, now - at) / 1000 : 0), by)
  const place = ranks(rows, by)
  const [c1, c2] = COLS[by]
  const num = 'num py-1 text-right'

  return (
    <Card
      size="sm"
      className="stage-hud arena-leaderboard gap-2 py-2 ring-0"
      style={{ top, maxHeight: `calc(100% - ${top + 96}px)` }}
      data-arena-leaderboard={collapsed ? 'collapsed' : 'open'}
      aria-label={t('board.title')}
    >
      <CardHeader className="flex items-center gap-2 px-2.5">
        <Icon icon="judge" className="size-3.5 shrink-0 text-[var(--g400)]" />
        <CardTitle className="cap min-w-0 flex-1 truncate text-[var(--g200)]">{t('board.title')}</CardTitle>
        <span className="num shrink-0 text-[11px] text-[var(--g300)]" title={left !== null ? t('board.matchLeft', { t: duration(left) ?? '—' }) : undefined} data-board-clock="">
          {caption}
        </span>
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label={collapsed ? t('board.expand') : t('board.collapse')}
          aria-expanded={!collapsed}
          className="text-[var(--g400)] hover:text-[var(--g100)]"
          onClick={() => useBoard.getState().setCollapsed(!collapsed)}
        >
          <StateIcon icon={collapsed ? 'chevron.down' : 'chevron.up'} className="size-3.5" />
        </Button>
      </CardHeader>
      {collapsed ? null : (
        <>
          <Tabs value={by} onValueChange={(v) => useBoard.getState().setBy(v as BoardBy)} className="gap-0 px-2.5">
            <TabsList variant="line" aria-label={t('board.tabs')} className="h-7 w-full justify-start gap-3 rounded-none border-b border-[var(--border)] p-0">
              {BOARD_TABS.map((k) => (
                <TabsTrigger key={k} value={k} className="arena-tab h-7 px-0 text-[11px]" data-board-tab={k}>
                  {t(TAB_KEY[k])}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <CardContent className="min-h-0 overflow-y-auto px-2.5">
            {rows.length ? (
              <Table data-lf-table="" className="table-fixed text-[11px]">
                <colgroup>
                  <col style={{ width: 22 }} />
                  <col />
                  <col style={{ width: '29%' }} />
                  <col style={{ width: 54 }} />
                  <col style={{ width: 48 }} />
                </colgroup>
                <TableHeader>
                  <TableRow>
                    <TableHead className="cap h-6 px-1 text-right">{t('board.col.rank')}</TableHead>
                    <TableHead className="cap h-6 px-1">{t('board.col.aircraft')}</TableHead>
                    <TableHead className="cap h-6 px-1">{t('board.col.controller')}</TableHead>
                    <TableHead className="cap h-6 px-1 text-right">{t(c1)}</TableHead>
                    <TableHead className="cap h-6 px-1 text-right">{t(c2)}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((r, i) => {
                    const [m1, m2] = metricCells(r, by)
                    const ctl = r.controller.name || t(CTL_KEY[r.controller.kind])
                    return (
                      <TableRow key={r.key} className={cn('h-6', r.local && 'font-semibold')} data-selected={r.local ? '' : undefined} data-board-row={r.key}>
                        <TableCell data-num="" className={cn(num, 'px-1 text-[var(--g400)]')}>{place[i]}</TableCell>
                        <TableCell className="truncate px-1 py-1 text-[var(--g100)]" title={r.local ? `${rowLabel(r)} · ${t('board.own')}` : rowLabel(r)}>
                          {rowLabel(r)}
                        </TableCell>
                        <TableCell className="truncate px-1 py-1 text-[var(--g300)]" title={`${t(CTL_KEY[r.controller.kind])}${r.controller.name ? ` · ${r.controller.name}` : ''}`}>
                          {ctl}
                        </TableCell>
                        <TableCell data-num="" className={cn(num, 'px-1 text-[var(--g50)]')}>{m1 ?? '—'}</TableCell>
                        <TableCell data-num="" className={cn(num, 'px-1 text-[var(--g400)]')}>{m2 ?? '—'}</TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            ) : (
              <p className="arena-board-empty" data-board-empty="">
                {status === 'error' ? t('board.error') : status === 'loading' || status === 'idle' ? t('board.loading') : t('board.empty')}
              </p>
            )}
          </CardContent>
          {scenario?.running ? (
            <CardFooter className="justify-end px-2.5">
              <EndMatch />
            </CardFooter>
          ) : null}
        </>
      )}
    </Card>
  )
}
