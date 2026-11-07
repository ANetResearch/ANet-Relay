// Scan tab (02 §8, R-02): the computer's interfaces and networks, a Scan button with progress, and the bridges
// found (address, bridge firmware, whether it was already in the fleet).
import * as React from 'react'
import { api } from '@/api'
import { notifyError } from '@/shell/notify'
import { useRelay } from '@/store/relay'
import { useScan } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { Button } from '@/ui/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/ui/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/ui/components/ui/tooltip'
import { Icon } from '@/ui/icons'
import { LinkStatus } from '@/ui/relay'
import { useT8, type AnyKey } from '@/fleet/copy'
import { useNetwork, type NetInterface } from './net'

function skipKey(n: NetInterface): AnyKey {
  if (n.skipped === 'loopback_or_link_local' || n.skipped === 'prefix_out_of_range') return `conn.skip.${n.skipped}`
  return 'conn.skip.other'
}

function Interfaces({ list }: { list: NetInterface[] }) {
  const t = useT8()
  const scannable = list.filter((n) => n.scannable)
  const other = list.filter((n) => !n.scannable)
  return (
    <div className="flex flex-col gap-1.5">
      <span className="cap">{t('conn.interfaces')}</span>
      <ul className="flex flex-col rounded-md border border-[var(--border)] bg-[var(--g900)]">
        {scannable.map((n) => (
          <li key={`${n.iface}-${n.ip}`} className="flex h-8 items-center gap-2 border-b border-dotted border-[var(--border)] px-3 last:border-b-0">
            <Icon icon="network" className="size-3.5 text-[var(--g400)]" />
            <span className="addr flex-1 truncate">{t('conn.iface', { iface: n.iface, ip: n.ip, prefix: n.prefix })}</span>
            <span className="num text-[11px] text-[var(--g400)]">{n.network}</span>
          </li>
        ))}
        {scannable.length === 0 ? <li className="px-3 py-2 text-hud-sub text-[var(--g400)]">{t('conn.noScannable')}</li> : null}
        {other.length ? (
          <li className="flex h-8 items-center gap-2 px-3">
            <Tooltip>
              <TooltipTrigger render={<span className="cursor-help text-hud-sub text-[var(--g500)]" />}>
                {other.length === 1 ? `${other[0].iface} · ${t(skipKey(other[0]))}` : `+${other.length} · ${t('conn.skip.other')}`}
              </TooltipTrigger>
              <TooltipContent className="max-w-sm whitespace-pre-line">
                {other.map((n) => `${n.iface} ${n.ip}/${n.prefix} · ${t(skipKey(n))}`).join('\n')}
              </TooltipContent>
            </Tooltip>
          </li>
        ) : null}
      </ul>
    </div>
  )
}

/** Real aircraft that a bridge probe identified (bridge version known), with the hosts present before the scan. */
function useResults(before: ReadonlySet<string> | null) {
  const json = useRelay((s) =>
    JSON.stringify(
      (s.fleet?.vehicles ?? [])
        .filter((v) => v.kind === 'real' && v.host && (v.bridge?.source === 'http' || v.bridge?.version))
        .map((v) => ({ id: v.id, host: v.host as string, version: v.bridge?.version ?? null, link: v.link.state, label: v.label })),
    ),
  )
  return React.useMemo(() => {
    const rows = JSON.parse(json) as { id: string; host: string; version: string | null; link: string; label: string }[]
    return rows.map((r) => ({ ...r, added: before !== null && !before.has(r.host) }))
  }, [json, before])
}

export function ScanTab({ onDone }: { onDone(): void }) {
  const t = useT8()
  const { info } = useNetwork(5000)
  const scan = useScan()
  const [before, setBefore] = React.useState<ReadonlySet<string> | null>(null)
  const results = useResults(before)
  const running = !!scan?.running
  const frac = scan && scan.total > 0 ? Math.min(1, scan.done / scan.total) : 0
  const canScan = !!info?.interfaces.some((n) => n.scannable)

  const start = () => {
    const hosts = new Set((useRelay.getState().fleet?.vehicles ?? []).map((v) => v.host).filter((h): h is string => !!h))
    setBefore(hosts)
    api.discover().catch(notifyError)
  }
  const show = (id: string) => {
    useUi.getState().setWorkspace('fly')
    useUi.getState().select(id)
    onDone()
  }

  return (
    <div className="flex flex-col gap-4" data-connect-scan="">
      <p className="text-hud-sub text-[var(--g400)]">{t('conn.scanHint')}</p>
      {info ? <Interfaces list={info.interfaces} /> : <div className="h-16 rounded-md border border-dashed border-[var(--g700)]" aria-busy="true" />}
      <div className="flex items-center gap-3">
        <Button onClick={start} disabled={running || !canScan} className="h-8 min-w-24 gap-1.5 text-[12px]">
          <Icon icon="scan" className="size-4" />
          {t('conn.scan')}
        </Button>
        <div className="flex min-w-0 flex-1 flex-col gap-1.5" role="status">
          {running ? (
            <>
              <span className="num truncate text-[11px] text-[var(--g200)]">
                {t('conn.scanning', { net: scan?.networks.join(', ') ?? '', done: scan?.done ?? 0, total: scan?.total ?? 0 })}
              </span>
              <span className="scan-line" aria-hidden="true">
                <i style={{ transform: `scaleX(${frac})` }} />
              </span>
            </>
          ) : scan?.error ? (
            <span className="text-hud-sub text-[var(--g50)]">{t('fleet.scanError', { reason: scan.error })}</span>
          ) : scan && scan.total > 0 ? (
            <span className="num text-[11px] text-[var(--g400)]">{t('fleet.scanDone', { found: scan.found, net: scan.networks.join(', ') })}</span>
          ) : null}
        </div>
      </div>
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <span className="cap">{t('conn.results')}</span>
          {results.length ? <span className="num text-hud-cap text-[var(--g500)]">{results.length}</span> : null}
        </div>
        {results.length === 0 ? (
          <p className="rounded-md border border-dashed border-[var(--g700)] px-3 py-3 text-hud-sub text-[var(--g400)]">{t('conn.noResults')}</p>
        ) : (
          <Table data-lf-table="" className="text-[12px]">
            <TableHeader>
              <TableRow>
                <TableHead className="h-7 text-hud-cap">{t('conn.col.address')}</TableHead>
                <TableHead className="h-7 text-hud-cap">{t('conn.col.bridge')}</TableHead>
                <TableHead className="h-7 text-hud-cap">{t('tele.link')}</TableHead>
                <TableHead className="h-7 text-right text-hud-cap">{t('conn.col.status')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {results.map((r) => (
                <TableRow key={r.id} className="h-8 cursor-default" onDoubleClick={() => show(r.id)}>
                  <TableCell className="addr">{r.host}</TableCell>
                  <TableCell className="num text-[11px] text-[var(--g300)]">{r.version ?? '—'}</TableCell>
                  <TableCell>
                    <LinkStatus link={{ state: r.link as never, heartbeat_age_s: null }} />
                  </TableCell>
                  <TableCell className="text-right">
                    <Button variant="ghost" size="sm" className="text-[var(--g200)]" onClick={() => show(r.id)}>
                      {r.added ? t('conn.added') : t('conn.inFleet')}
                      <Icon icon="chevron.right" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  )
}
