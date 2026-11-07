// LAN arena HUD of the stage (05 §7.5): the 1 px g50 crosshair at the stage centre in the chase view while an
// aircraft of this window is possessed, whose four corners jump out when one of our rounds hits; the g50 arc at
// the stage edge that points towards the shooter when one of our aircraft is hit; and the scoreboard (lieflat
// table, top right, under the view tools). Values that change with every arena message re-render only when the
// derived row text changes. Nothing here paints red. Reduced motion shows the marks without movement; every mark
// unmounts after its token duration on a timer, so it never outlives the effect when no animation runs.
import * as React from 'react'
import * as THREE from 'three'
import { cn } from '@/lib/utils'
import { useReplay } from '@/records/replayState'
import { useRelay } from '@/store/relay'
import { enuToScene } from '@/stage/math'
import { rig } from '@/stage/scene/rigState'
import { useStage } from '@/stage/store'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/ui/components/ui/table'
import { useTA } from './copy'
import { useArena, watchArenaEvents, type ArenaMark } from './store'
import { ARENA_TOKENS, markRemainingMs, tokenMs } from './timing'
import './arena.css'

/** Arena overlays are shown: arena on, layer on, not replaying. */
function useArenaShown(): boolean {
  const enabled = useArena((s) => Boolean(s.view?.enabled))
  const layer = useStage((s) => s.layers.arena)
  const replaying = useReplay((r) => r.active)
  return enabled && layer && !replaying
}

/** One of this window's inputs flies a simulator (crosshair and hit direction only make sense then). */
function usePossessedSim(): string | null {
  return useRelay((s) => s.fleet?.vehicles.find((v) => v.kind === 'sim' && v.possession && v.controller === 'human')?.id ?? null)
}

/** Latest mark of a kind for a set of aircraft, as React state (re-renders once per mark), cleared after `hold`. */
function useLatestMark(kind: ArenaMark['kind'], vehicle: string | null, hold: string): ArenaMark | null {
  const [mark, setMark] = React.useState<ArenaMark | null>(null)
  React.useEffect(() => watchArenaEvents(), [])
  React.useEffect(() => {
    if (!mark) return
    const id = window.setTimeout(() => setMark((m) => (m === mark ? null : m)), markRemainingMs(mark.at, performance.now(), tokenMs(hold)))
    return () => window.clearTimeout(id)
  }, [mark, hold])
  React.useEffect(() => {
    let last = useArena.getState().marks.at(-1)?.seq ?? 0
    return useArena.subscribe((s) => {
      const fresh = s.marks.filter((m) => m.seq > last)
      if (!fresh.length) return
      last = fresh[fresh.length - 1].seq
      const mine = fresh.filter((m) => m.kind === kind && (vehicle === null || m.vehicle === vehicle))
      if (mine.length) setMark(mine[mine.length - 1])
    })
  }, [kind, vehicle])
  return mark
}

/**
 * Screen angle (degrees clockwise from the top) of the direction a round came from, seen through `camera`.
 * `dir` is the round's travel in local ENU; the shooter lies the other way.
 */
export function incomingAngle(dir: readonly number[], camera: THREE.Camera): number {
  const [x, y, z] = enuToScene(-dir[0], -dir[1], -dir[2])
  const v = new THREE.Vector3(x, y, z).applyQuaternion(camera.quaternion.clone().invert())
  return (Math.atan2(v.x, v.y) * 180) / Math.PI
}

function Crosshair() {
  const t = useTA()
  const vehicle = usePossessedSim()
  const chase = useStage((s) => s.view === 'chase')
  const hit = useLatestMark('hit', vehicle, ARENA_TOKENS.hitMark)
  if (!vehicle || !chase) return null
  return (
    <div className="arena-crosshair" aria-hidden="true" data-arena-crosshair="">
      <i className="arena-cross-h" />
      <i className="arena-cross-v" />
      {hit ? (
        <span key={hit.seq} className="arena-hit-corners" data-arena-hit="">
          <i data-c="tl" />
          <i data-c="tr" />
          <i data-c="bl" />
          <i data-c="br" />
        </span>
      ) : null}
      <span className="sr-only">{hit ? t('arena.hitConfirmed') : ''}</span>
    </div>
  )
}

function HitDirection() {
  const vehicle = usePossessedSim()
  const taken = useLatestMark('taken', vehicle, ARENA_TOKENS.hitDir)
  if (!vehicle || !taken || !taken.dir || !rig.camera) return null
  const angle = incomingAngle(taken.dir, rig.camera)
  return (
    <div key={taken.seq} className="arena-incoming" style={{ rotate: `${angle.toFixed(1)}deg` }} aria-hidden="true" data-arena-incoming="">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="arena-incoming-arc">
        <path d="M 38 2 A 48 48 0 0 1 62 2" />
      </svg>
    </div>
  )
}

interface BoardRow {
  key: string
  callsign: string
  you: boolean
  aircraft: number
  hits: number
  downs: number
  deaths: number
  rtt: number | null
  stale: boolean
}

function useBoardRows(): BoardRow[] {
  const key = useArena((s) => {
    const v = s.view
    if (!v?.enabled) return '[]'
    const rows: BoardRow[] = [
      {
        key: v.instance,
        callsign: v.callsign,
        you: true,
        aircraft: Object.keys(v.local.hp).length,
        ...v.local.score,
        rtt: null,
        stale: false,
      },
      ...v.peers.map((p) => ({
        key: p.id,
        callsign: p.callsign,
        you: false,
        aircraft: p.vehicles.length,
        ...p.score,
        rtt: p.rtt_ms === null ? null : Math.round(p.rtt_ms),
        stale: p.stale,
      })),
    ]
    return JSON.stringify(rows)
  })
  return React.useMemo(() => JSON.parse(key) as BoardRow[], [key])
}

export function Scoreboard({ top }: { top: number }) {
  const t = useTA()
  const rows = useBoardRows()
  if (!rows.length) return null
  const num = 'num py-1 text-right'
  return (
    <div className="stage-hud arena-board" style={{ top }} data-arena-board="" aria-label={t('arena.board')}>
      <Table data-lf-table="" className="text-[11px]">
        <TableHeader>
          <TableRow>
            <TableHead className="cap h-6">{t('arena.col.callsign')}</TableHead>
            <TableHead className="cap h-6 text-right">{t('arena.col.aircraft')}</TableHead>
            <TableHead className="cap h-6 text-right">{t('arena.col.hits')}</TableHead>
            <TableHead className="cap h-6 text-right">{t('arena.col.downs')}</TableHead>
            <TableHead className="cap h-6 text-right">{t('arena.col.deaths')}</TableHead>
            <TableHead className="cap h-6 text-right">{t('arena.col.rtt')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.key} className="h-6" data-stale={r.stale ? '' : undefined} data-selected={r.you ? '' : undefined}>
              <TableCell className={cn('max-w-28 truncate py-1', r.stale ? 'text-[var(--g500)]' : 'text-[var(--g100)]')} title={r.you ? t('arena.you') : r.callsign}>
                {r.callsign}
              </TableCell>
              <TableCell data-num="" className={cn(num, 'text-[var(--g300)]')}>{r.aircraft}</TableCell>
              <TableCell data-num="" className={cn(num, 'text-[var(--g100)]')}>{r.hits}</TableCell>
              <TableCell data-num="" className={cn(num, 'text-[var(--g100)]')}>{r.downs}</TableCell>
              <TableCell data-num="" className={cn(num, 'text-[var(--g300)]')}>{r.deaths}</TableCell>
              <TableCell data-num="" className={cn(num, 'text-[var(--g400)]')}>
                {r.stale ? t('arena.stale') : r.rtt === null ? '—' : `${r.rtt} ms`}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {rows.length === 1 ? <p className="arena-board-empty">{t('arena.empty')}</p> : null}
    </div>
  )
}

/** Stage overlays of the arena; `boardTop` places the scoreboard under the view tools (Stage layout). */
export function ArenaHud({ boardTop }: { boardTop: number }) {
  const shown = useArenaShown()
  if (!shown) return null
  return (
    <>
      <Crosshair />
      <HitDirection />
      <Scoreboard top={boardTop} />
    </>
  )
}
