// Settings → Autopilot packs (11 §3, §11): the runtime packs of this platform (firmware, version, git, platform,
// size, origin, license, usable or the problem, twins running on it), import by a local path with an optional
// sha256, and delete for imported packs. Importing and deleting are the operator's (they install executables);
// the controls stay visible and state why they are disabled.
import * as React from 'react'
import { api, RelayApiError, type AutopilotPacks } from '@/api'
import { useLang } from '@/i18n'
import { notify, notifyError } from '@/shell/notify'
import { useHello } from '@/store/selectors'
import { Button } from '@/ui/components/ui/button'
import { Input } from '@/ui/components/ui/input'
import { Spinner } from '@/ui/components/ui/spinner'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/ui/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/ui/components/ui/tooltip'
import { Icon } from '@/ui/icons'
import { firmwareTitle, formatBytes, importBlock, packDeleteBlock, sortPacks } from './model'
import { useTT } from './strings'

type RowComponent = (p: { label: string; hint?: React.ReactNode; children: React.ReactNode; wide?: boolean }) => React.ReactNode

function errText(err: unknown, lang: 'en' | 'zh'): string {
  return err instanceof RelayApiError ? err.text(lang) : err instanceof Error ? err.message : String(err)
}

function Disabled({ reason, children }: { reason: string | null; children: React.ReactElement }) {
  if (!reason) return children
  return (
    <Tooltip>
      <TooltipTrigger render={<span className="inline-flex" />}>{children}</TooltipTrigger>
      <TooltipContent className="max-w-[260px]">{reason}</TooltipContent>
    </Tooltip>
  )
}

export function PacksSection({ Row }: { Row: RowComponent }) {
  const t = useTT()
  const lang = useLang()
  const operator = Boolean(useHello()?.operator)
  const [data, setData] = React.useState<AutopilotPacks | null>(null)
  const [error, setError] = React.useState<unknown>(null)
  const [nonce, setNonce] = React.useState(0)
  const [path, setPath] = React.useState('')
  const [sha, setSha] = React.useState('')
  const [tried, setTried] = React.useState(false)
  const [busy, setBusy] = React.useState<string | null>(null)
  const [importError, setImportError] = React.useState<string | null>(null)

  React.useEffect(() => {
    let alive = true
    api.autopilots().then(
      (d) => alive && (setData(d), setError(null)),
      (e: unknown) => alive && setError(e),
    )
    return () => {
      alive = false
    }
  }, [nonce])
  const reload = () => setNonce((n) => n + 1)

  const block = importBlock(path, sha, operator)
  const shownBlock = block && (tried || block.key === 'packs.import.operator' || block.key === 'packs.import.badSha') ? block : null
  const doImport = () => {
    setTried(true)
    setImportError(null)
    if (block || busy) return
    setBusy('import')
    api
      .importAutopilot(path.trim(), sha.trim() || undefined)
      .then((p) => {
        notify(t('packs.import.done', { pack: p.id }))
        setPath('')
        setSha('')
        setTried(false)
        reload()
      })
      .catch((e: unknown) => setImportError(errText(e, lang)))
      .finally(() => setBusy(null))
  }
  const remove = (id: string) => {
    setBusy(id)
    api
      .deleteAutopilot(id)
      .then(() => {
        notify(t('packs.deleted', { pack: id }))
        reload()
      })
      .catch(notifyError)
      .finally(() => setBusy(null))
  }

  const packs = data ? sortPacks(data.packs) : []
  return (
    <div className="flex flex-col" data-packs-section="">
      <p className="pb-2 text-hud-sub text-[var(--g400)]">{t('packs.hint')}</p>
      {data ? (
        <>
          <Row label={t('packs.platform')}>
            <span className="num text-[12px] text-[var(--g200)]">{data.platform ?? '—'}</span>
          </Row>
          <Row label={t('packs.bundledDir')}>
            <span className="min-w-0 truncate font-mono text-[11px] text-[var(--g300)]" title={data.bundled_dir}>
              {data.bundled_dir}
            </span>
          </Row>
          {data.imported_dir ? (
            <Row label={t('packs.importedDir')}>
              <span className="min-w-0 truncate font-mono text-[11px] text-[var(--g300)]" title={data.imported_dir}>
                {data.imported_dir}
              </span>
            </Row>
          ) : null}
        </>
      ) : null}
      <div className="flex items-center gap-2 pt-3 pb-1.5">
        <span className="cap">{t('packs.section')}</span>
        {data ? <span className="num text-hud-cap text-[var(--g500)]">{packs.length}</span> : null}
        <span className="flex-1" />
        <Button variant="ghost" size="sm" className="gap-1.5 text-[var(--g300)]" onClick={reload} data-packs-refresh="">
          <Icon icon="refresh" className="size-3.5" />
          {t('packs.refresh')}
        </Button>
      </div>
      {!data ? (
        <p className="text-hud-sub text-[var(--g500)]">{error ? t('packs.error', { msg: errText(error, lang) }) : t('packs.loading')}</p>
      ) : packs.length === 0 ? (
        <p className="rounded-md border border-dashed border-[var(--g700)] px-3 py-3 text-hud-sub text-[var(--g400)]">{t('packs.empty')}</p>
      ) : (
        <Table data-lf-table="" className="table-fixed text-[12px]" data-packs-table="">
          <TableHeader>
            <TableRow>
              <TableHead className="h-7 w-[36%] text-hud-cap">{t('packs.col.firmware')}</TableHead>
              <TableHead className="h-7 w-[19%] text-hud-cap">{t('packs.col.platform')}</TableHead>
              <TableHead className="h-7 w-[17%] text-hud-cap">{t('packs.col.origin')}</TableHead>
              <TableHead className="h-7 text-hud-cap">{t('packs.col.status')}</TableHead>
              <TableHead className="h-7 w-8 px-0" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {packs.map((p) => {
              const del = packDeleteBlock(p, operator)
              const problem = lang === 'zh' ? (p.problem_zh ?? p.problem) : p.problem
              return (
                <TableRow key={p.id} className="h-11 align-middle" data-pack={p.id} data-usable={p.usable ? '' : undefined}>
                  <TableCell className="py-1">
                    <div className="flex min-w-0 flex-col [&>span]:truncate">
                      <span className="text-[var(--g100)]">{firmwareTitle(p.family, p.version, p.vehicle)}</span>
                      <span className="num text-[10px] text-[var(--g500)]" title={p.sha256 ?? undefined}>
                        {p.git.slice(0, 8)} · {p.built}
                      </span>
                    </div>
                  </TableCell>
                  <TableCell className="py-1">
                    <div className="flex min-w-0 flex-col [&>span]:truncate">
                      <span className="num text-[11px] text-[var(--g200)]">{p.platform}</span>
                      <span className="num text-[10px] text-[var(--g500)]">{formatBytes(p.size_bytes)}</span>
                    </div>
                  </TableCell>
                  <TableCell className="py-1">
                    <div className="flex min-w-0 flex-col [&>span]:truncate">
                      <span className="text-[var(--g200)]">{t(p.origin === 'bundled' ? 'packs.origin.bundled' : 'packs.origin.imported')}</span>
                      <span className="text-[10px] text-[var(--g500)]">{p.license}</span>
                    </div>
                  </TableCell>
                  <TableCell className="max-w-[120px] py-1" data-twins={p.twins}>
                    {p.usable ? (
                      <div className="flex min-w-0 flex-col [&>span]:truncate">
                        <span className="text-[var(--g200)]">{t('packs.usable')}</span>
                        {p.twins > 0 ? <span className="text-[10px] text-[var(--g400)]">{t('packs.twinsValue', { n: p.twins })}</span> : null}
                      </div>
                    ) : (
                      <Tooltip>
                        <TooltipTrigger render={<span className="flex min-w-0 flex-col" />}>
                          <span className="font-[560] text-[var(--g50)]">{t('packs.problem')}</span>
                          <span className="truncate text-[10px] text-[var(--g400)]">{problem}</span>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-[300px]">{problem}</TooltipContent>
                      </Tooltip>
                    )}
                  </TableCell>
                  <TableCell className="px-0 py-1 text-right">
                    <Disabled reason={del ? t(del.key, del.params) : null}>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={t('packs.delete')}
                        disabled={Boolean(del) || busy !== null}
                        onClick={() => remove(p.id)}
                        className="text-[var(--g400)] hover:text-[var(--g100)]"
                        data-pack-delete={p.id}
                      >
                        {busy === p.id ? <Spinner /> : <Icon icon="trash" />}
                      </Button>
                    </Disabled>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}

      <form
        className="mt-4 flex flex-col gap-2 border-t border-[var(--border)] pt-3"
        onSubmit={(e) => {
          e.preventDefault()
          doImport()
        }}
        data-packs-import=""
      >
        <span className="cap">{t('packs.import')}</span>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-[var(--g300)]">{t('packs.import.path')}</span>
          <Input
            value={path}
            spellCheck={false}
            autoComplete="off"
            placeholder="ardupilot-copter-4.4.4-linux-x64.tar.gz"
            onChange={(e) => setPath(e.target.value)}
            onKeyDown={(e) => e.stopPropagation()}
            disabled={!operator}
            className="h-7 font-mono text-[11px]"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-[var(--g300)]">{t('packs.import.sha')}</span>
          <Input
            value={sha}
            spellCheck={false}
            autoComplete="off"
            onChange={(e) => setSha(e.target.value)}
            onKeyDown={(e) => e.stopPropagation()}
            disabled={!operator}
            aria-invalid={sha.trim() !== '' && block?.key === 'packs.import.badSha' ? true : undefined}
            className="h-7 font-mono text-[11px]"
          />
          <span className="text-hud-sub text-[var(--g500)]">{t('packs.import.shaHint')}</span>
        </label>
        <div className="flex items-center gap-3">
          <span className="min-w-0 flex-1 text-hud-sub text-[var(--g50)]" role={shownBlock || importError ? 'alert' : undefined}>
            {importError ?? (shownBlock ? t(shownBlock.key, shownBlock.params) : '')}
          </span>
          <Button type="submit" variant="outline" size="sm" className="gap-1.5" disabled={!operator || busy !== null} data-packs-import-submit="">
            {busy === 'import' ? <Spinner /> : <Icon icon="pack.import" />}
            {t('packs.import.action')}
          </Button>
        </div>
      </form>
    </div>
  )
}
