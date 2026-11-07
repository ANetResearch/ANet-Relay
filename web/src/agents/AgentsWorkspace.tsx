// Agents workspace (02 §12): sessions (360) | claim matrix and the selected session's recent calls | interface and
// documentation (320). Sessions and claims come from the snapshot; call logs and lease details from GET /sessions.
// Contract: CONTRACTS.md#agentsworkspace.
import * as React from 'react'
import { env, request, type Vehicle } from '@/api'
import { clock } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useRelay } from '@/store/relay'
import { useSessions } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { Button } from '@/ui/components/ui/button'
import { ScrollArea } from '@/ui/components/ui/scroll-area'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/ui/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/ui/components/ui/tooltip'
import { Icon } from '@/ui/icons'
import { SimTag, useAgo, useInputLabel } from '@/ui/relay'
import { useT8 } from '@/fleet/copy'
import { usePoll } from '@/inspector/usePoll'
import { CopyButton, openExternal } from './CopyButton'
import './agents.css'

interface Call {
  t: number
  method: string
  path: string
  status: number
  ms: number
}

interface SessionDetail {
  session_id: string
  name: string
  created_at: number
  expires_in_s: number
  lease_s: number
  last_call: number | null
  vehicles: string[]
  calls: Call[]
}

const GUIDE = 'https://github.com/ANetResearch/ANet-Relay/blob/main/docs/agent-api.md'

/** Origin of the API ("http://127.0.0.1:8765") from the /api/v1 base. */
export function apiOrigin(apiBase: string): string {
  return apiBase.replace(/\/api\/v1\/?$/, '')
}

export function quickStart(origin: string): string {
  return [
    'from anet_relay_client import RelayClient',
    `relay = RelayClient("${origin}")`,
    'relay.open_session("my-agent")',
    'relay.claim("sim-1"); relay.arm("sim-1")',
    'relay.takeoff("sim-1", alt=1.0); relay.land("sim-1")',
    'relay.close()',
  ].join('\n')
}

function useSessionDetails(): Map<string, SessionDetail> {
  const poll = usePoll(() => request<{ sessions: SessionDetail[] }>('GET', '/sessions'), 1000, 'sessions')
  return React.useMemo(() => new Map((poll.data?.sessions ?? []).map((s) => [s.session_id, s])), [poll.data])
}

// ------------------------------------------------------------------------------------------------ sessions

function SessionRow({ s, detail, selected, onSelect }: { s: ReturnType<typeof useSessions>[number]; detail?: SessionDetail; selected: boolean; onSelect(): void }) {
  const t = useT8()
  const ago = useAgo(detail?.last_call ?? null)
  const lease = Math.max(0, s.expires_in_s)
  const leaseTotal = detail?.lease_s ?? 15
  return (
    <button type="button" onClick={onSelect} className="agent-row" data-selected={selected ? '' : undefined} data-session={s.session_id}>
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate text-hud-title font-[560] text-[var(--g100)]">{s.name}</span>
        <span className="num truncate text-[10px] text-[var(--g500)]">{s.session_id}</span>
        <span className="flex-1" />
        <span className={cn('num shrink-0 text-[11px]', lease < 3 ? 'text-[var(--g50)]' : 'text-[var(--g300)]')}>
          {lease > 0 ? t('agents.lease', { s: Math.ceil(lease) }) : t('agents.expired')}
        </span>
      </div>
      <span className="lease-line" aria-hidden="true">
        <i style={{ transform: `scaleX(${Math.min(1, lease / leaseTotal)})` }} />
      </span>
      <div className="flex items-center gap-2 text-hud-sub text-[var(--g400)]">
        <span>{s.vehicles.length ? t('agents.aircraftCount', { n: s.vehicles.length }) : t('agents.noClaims')}</span>
        <span className="text-[var(--g600)]">·</span>
        <span className="truncate">{t('agents.lastCall', { t: detail?.last_call ? ago : '—' })}</span>
      </div>
    </button>
  )
}

function EmptySessions() {
  const t = useT8()
  const origin = apiOrigin(env().apiBase)
  const code = quickStart(origin)
  return (
    <div className="flex flex-col gap-3 px-3 pt-6" data-agents-empty="">
      <p className="text-hud-title font-[560] text-[var(--g100)]">{t('agents.empty')}</p>
      <p className="text-hud-sub text-[var(--g400)]">{t('agents.emptyBody')}</p>
      <div className="flex flex-col gap-1">
        <div className="flex items-center">
          <span className="cap">{t('agents.quickStart')}</span>
          <span className="flex-1" />
          <CopyButton text={code} label={t('agents.copyExample')} />
        </div>
        <pre className="code-block" data-selectable="">
          {code}
        </pre>
      </div>
      <div>
        <Button variant="outline" className="h-8 gap-1.5 text-[12px]" onClick={() => openExternal(`${origin}/docs`)}>
          <Icon icon="external" />
          {t('agents.openApiRef')}
        </Button>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------ matrix

type Cell = 'claimed' | 'suspended' | 'possessed' | null

function Mark({ cell, label }: { cell: Exclude<Cell, null>; label: string }) {
  return (
    <Tooltip>
      <TooltipTrigger render={<span className="matrix-mark" data-cell={cell} aria-label={label} />}>
        <i />
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

function Matrix({ sessions }: { sessions: ReturnType<typeof useSessions> }) {
  const t = useT8()
  const inputLabel = useInputLabel()
  const json = useRelay((s) =>
    JSON.stringify(
      (s.fleet?.vehicles ?? []).map((v) => ({ id: v.id, label: v.label, kind: v.kind, claim: v.claim, possession: v.possession, controller: v.controller })),
    ),
  )
  const rows = React.useMemo(() => JSON.parse(json) as Pick<Vehicle, 'id' | 'label' | 'kind' | 'claim' | 'possession' | 'controller'>[], [json])
  const select = (id: string) => {
    useUi.getState().setWorkspace('fly')
    useUi.getState().select(id)
  }
  if (!rows.length) return <p className="px-3 py-3 text-hud-sub text-[var(--g500)]">{t('agents.matrixEmpty')}</p>
  return (
    <div className="overflow-x-auto px-3">
      <table className="matrix w-full text-[12px]" data-lf-table="">
        <thead>
          <tr>
            <th className="cap h-8 text-left font-[560]">{t('fleet.title')}</th>
            {sessions.map((s) => (
              <th key={s.session_id} className="h-8 px-2 text-center font-[560]">
                <span className="num text-[11px] text-[var(--g200)]">{s.name}</span>
              </th>
            ))}
            <th className="cap h-8 px-2 text-center font-[560]">{t('agents.human')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((v) => {
            const human: Cell = v.possession ? 'possessed' : null
            return (
              <tr key={v.id} className="h-8" data-matrix-row={v.id}>
                <td className="pr-2">
                  <button type="button" onClick={() => select(v.id)} className="flex min-w-0 items-center gap-1.5 text-left hover:text-[var(--g50)]">
                    {v.kind === 'sim' ? <SimTag /> : null}
                    <span className="truncate text-[var(--g100)]">{v.label}</span>
                  </button>
                </td>
                {sessions.map((s) => {
                  const cell: Cell = v.claim?.session_id === s.session_id ? (v.claim.suspended || v.possession ? 'suspended' : 'claimed') : null
                  return (
                    <td key={s.session_id} className="px-2 text-center">
                      {cell ? <Mark cell={cell} label={`${s.name} · ${t(cell === 'claimed' ? 'agents.claimed' : 'agents.suspended')}`} /> : <span className="matrix-empty" aria-hidden="true" />}
                    </td>
                  )
                })}
                <td className="px-2 text-center">
                  {human && v.possession ? (
                    <Mark cell="possessed" label={`${t('agents.possessed')} · ${inputLabel(v.possession)}`} />
                  ) : (
                    <span className="matrix-empty" aria-hidden="true" />
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="flex items-center gap-4 pt-2 text-hud-sub text-[var(--g400)]">
        <span className="cap">{t('agents.legend')}</span>
        {(['claimed', 'suspended', 'possessed'] as const).map((c) => (
          <span key={c} className="flex items-center gap-1.5">
            <span className="matrix-mark" data-cell={c} aria-hidden="true">
              <i />
            </span>
            {t(`agents.${c}`)}
          </span>
        ))}
      </div>
    </div>
  )
}

function Calls({ detail, name }: { detail: SessionDetail | undefined; name: string | null }) {
  const t = useT8()
  if (!name) return <p className="px-3 py-3 text-hud-sub text-[var(--g500)]">{t('agents.selectSession')}</p>
  const calls = [...(detail?.calls ?? [])].reverse().slice(0, 50)
  if (!calls.length) return <p className="px-3 py-3 text-hud-sub text-[var(--g500)]">{t('agents.noCalls')}</p>
  return (
    <div className="px-3">
      <Table data-lf-table="" className="text-[11px]">
        <TableHeader>
          <TableRow>
            <TableHead className="cap h-7">{t('agents.col.time')}</TableHead>
            <TableHead className="cap h-7">{t('agents.col.method')}</TableHead>
            <TableHead className="cap h-7">{t('agents.col.path')}</TableHead>
            <TableHead className="cap h-7 text-right">{t('agents.col.result')}</TableHead>
            <TableHead className="cap h-7 text-right">{t('agents.col.duration')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {calls.map((c, i) => {
            const bad = c.status >= 400
            return (
              <TableRow key={`${c.t}-${i}`} className="h-7">
                <TableCell className="num py-1 text-[var(--g500)]">{clock(c.t)}</TableCell>
                <TableCell className="num py-1 text-[var(--g300)]">{c.method}</TableCell>
                <TableCell className="num max-w-[180px] truncate py-1 text-[var(--g200)] min-[1280px]:max-w-[280px]" title={c.path}>
                  {c.path}
                </TableCell>
                <TableCell data-num="" className={cn('num py-1', bad ? 'font-bold text-[var(--g50)]' : 'text-[var(--g300)]')}>
                  {c.status}
                </TableCell>
                <TableCell data-num="" className="num py-1 text-[var(--g400)]">
                  {c.ms.toFixed(1)} ms
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------ interface

function Endpoint({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="cap">{label}</span>
      <div className="flex items-center gap-1 rounded-md border border-[var(--border)] bg-[var(--g950)] py-0.5 pr-0.5 pl-2.5">
        <span className="addr min-w-0 flex-1 truncate" title={value} data-selectable="">
          {value}
        </span>
        <CopyButton text={value} />
      </div>
    </div>
  )
}

function InterfacePanel() {
  const t = useT8()
  const base = env().apiBase
  const origin = apiOrigin(base)
  const ws = `${base.replace(/^http/, 'ws')}/stream`
  const mcp = 'claude mcp add anet-relay -- python3 mcp/anet_relay_mcp.py --name claude-code'
  return (
    <div className="flex flex-col gap-4 p-3" data-agents-interface="">
      <Endpoint label={t('agents.api')} value={base} />
      <Endpoint label={t('agents.websocket')} value={ws} />
      <Endpoint label={t('agents.openapi')} value={`${origin}/openapi.json`} />
      <div className="flex flex-col gap-1">
        <span className="cap">{t('agents.mcp')}</span>
        <div className="flex items-start gap-1 rounded-md border border-[var(--border)] bg-[var(--g950)] py-1 pr-0.5 pl-2.5">
          <code className="addr min-w-0 flex-1 py-1 break-words" data-selectable="">
            {mcp}
          </code>
          <CopyButton text={mcp} label={t('agents.copyInstall')} />
        </div>
        <span className="text-hud-sub text-[var(--g500)]">{t('agents.mcpHint')}</span>
      </div>
      <div className="flex flex-col gap-1.5">
        <span className="cap">{t('agents.docs')}</span>
        <Button variant="outline" className="h-8 justify-start gap-2 text-[12px]" onClick={() => openExternal(`${origin}/docs`)}>
          <Icon icon="external" />
          {t('agents.reference')}
        </Button>
        <Button variant="outline" className="h-8 justify-start gap-2 text-[12px]" onClick={() => openExternal(GUIDE)}>
          <Icon icon="external" />
          {t('agents.guide')}
        </Button>
      </div>
      <div className="flex flex-col gap-1.5 border-t border-[var(--border)] pt-3">
        <p className="text-hud-sub text-[var(--g400)]">{t('agents.limits')}</p>
        <p className="text-hud-sub text-[var(--g400)]">{t('agents.leaseNote')}</p>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------------------------------------ workspace

export function AgentsWorkspace() {
  const t = useT8()
  const sessions = useSessions()
  const details = useSessionDetails()
  const [selected, setSelected] = React.useState<string | null>(null)
  const current = sessions.find((s) => s.session_id === selected) ?? sessions[0] ?? null
  const currentId = current?.session_id ?? null
  return (
    <div className="flex h-full min-w-0 gap-2" data-workspace="agents">
      <section className="l1 flex w-[360px] shrink-0 flex-col max-[1279px]:w-[260px] overflow-hidden rounded-[var(--radius)]" aria-label={t('agents.sessions')}>
        <header className="flex h-10 shrink-0 items-center gap-2 px-3">
          <h2 className="text-hud-title font-[560] text-[var(--g100)]">{t('agents.sessions')}</h2>
          {sessions.length ? <span className="num text-hud-sub text-[var(--g500)]">{sessions.length}</span> : null}
        </header>
        {sessions.length === 0 ? (
          <EmptySessions />
        ) : (
          <ScrollArea className="min-h-0 flex-1">
            <div className="flex flex-col gap-1 px-2 pb-2">
              {sessions.map((s) => (
                <SessionRow key={s.session_id} s={s} detail={details.get(s.session_id)} selected={s.session_id === currentId} onSelect={() => setSelected(s.session_id)} />
              ))}
            </div>
          </ScrollArea>
        )}
      </section>
      <section className="l1 flex min-w-0 flex-1 flex-col overflow-hidden rounded-[var(--radius)]">
        <ScrollArea className="min-h-0 flex-1">
          <header className="flex h-10 items-center px-3">
            <h2 className="text-hud-title font-[560] text-[var(--g100)]">{t('agents.matrix')}</h2>
          </header>
          <Matrix sessions={sessions} />
          <header className="mt-4 flex h-10 items-center border-t border-[var(--border)] px-3">
            <h2 className="text-hud-title font-[560] text-[var(--g100)]">{current ? t('agents.callsOf', { name: current.name }) : t('agents.calls')}</h2>
          </header>
          <Calls detail={currentId ? details.get(currentId) : undefined} name={current?.name ?? null} />
          <div className="h-3" />
        </ScrollArea>
      </section>
      <section className="l1 flex w-[320px] shrink-0 flex-col max-[1279px]:w-[280px] overflow-hidden rounded-[var(--radius)]" aria-label={t('agents.interface')}>
        <header className="flex h-10 shrink-0 items-center px-3">
          <h2 className="text-hud-title font-[560] text-[var(--g100)]">{t('agents.interface')}</h2>
        </header>
        <ScrollArea className="min-h-0 flex-1">
          <InterfacePanel />
        </ScrollArea>
      </section>
    </div>
  )
}
