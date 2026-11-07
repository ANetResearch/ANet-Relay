// Single-record replay (02 §13): summary, charts (voltage, altitude, roll / pitch, throttle) on a shared time axis,
// the event and control tracks, a scrubbable cursor with play / pause, the stage in replay mode, course results and
// CSV / JSON export.
import * as React from 'react'
import { api, RelayApiError } from '@/api'
import { useLang } from '@/i18n'
import { clock, courseTime, duration, radToDeg, split } from '@/lib/format'
import { cn } from '@/lib/utils'
import { notify } from '@/shell/notify'
import { backendNow } from '@/store/relay'
import { Button } from '@/ui/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/ui/components/ui/table'
import { ToggleGroup, ToggleGroupItem } from '@/ui/components/ui/toggle-group'
import { Icon, StateIcon } from '@/ui/icons'
import { Readout, SimTag } from '@/ui/relay'
import { useT8, type AnyKey } from '@/fleet/copy'
import { usePoll } from '@/inspector/usePoll'
import { useLength } from '@/settings/prefs'
import { ControlTrack, EventTrack, LineChart, makeScale, TimeAxis, useNearestEvent, useWidth } from './Charts'
import { indexAt, recordDomain, type RecordDetail, type RecordSummary } from './model'
import { exitReplay, setReplay, useReplay, type ReplayFrame } from './replayState'
import { StageView } from './StageView'

const SPEEDS = [1, 4, 16] as const
const COURSE_RINGS: Record<string, [AnyKey, number]> = { 'cage-loop': ['course.cageLoop', 4], slalom: ['course.slalom', 6], climb: ['course.climb', 5] }

function frameAt(d: RecordDetail, t: number): ReplayFrame | null {
  const s = d.samples
  const i = indexAt(s.t, t)
  if (i < 0) return null
  const ctl = s.controller[i]
  return {
    t,
    armed: !!s.armed[i],
    mode: s.mode[i],
    roll: s.roll[i],
    pitch: s.pitch[i],
    yaw: s.yaw[i],
    x: s.x[i],
    y: s.y[i],
    z: s.z[i],
    alt: s.alt[i],
    throttle: s.throttle[i],
    controller: ctl === 'human' || ctl === 'agent' || ctl === 'none' ? ctl : null,
  }
}

async function download(id: number, fmt: 'csv' | 'json', lang: 'en' | 'zh', t: (k: AnyKey, p?: Record<string, string | number>) => string) {
  try {
    const res = await fetch(api.historyExportUrl(id, fmt))
    if (!res.ok) throw new RelayApiError(res.status, `http_${res.status}`, res.statusText, res.statusText)
    const blob = await res.blob()
    const file = `anet-relay-record-${id}.${fmt}`
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = file
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 2000)
    notify(t('records.exported', { file }))
  } catch (err) {
    notify(t('records.exportFailed'), { level: 'warn', description: err instanceof RelayApiError ? err.text(lang) : String(err) })
  }
}

function Summary({ d }: { d: RecordDetail }) {
  const t = useT8()
  const len = useLength()
  const s = d.summary
  const v = (x: number | null) => (x === null ? '—' : `${x.toFixed(1)} V`)
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(112px,1fr))] gap-x-4 gap-y-2 px-3" data-record-summary="">
      <Readout label={t('records.duration')}>
        <span className="num">{duration(s.duration_s) ?? '—'}</span>
      </Readout>
      <Readout label={t('records.flightTime')}>
        <span className="num">{duration(s.armed_s) ?? '—'}</span>
      </Readout>
      <Readout label={t('records.voltage')}>
        <span className="num">
          {v(s.voltage_start)} → {v(s.voltage_end)}
        </span>
      </Readout>
      <Readout label={t('records.maxAlt')}>
        <span className="num">{len(s.max_alt_m) ?? '—'}</span>
      </Readout>
      <Readout label={t('records.possessions')}>
        <span className="num">{s.possessions}</span>
      </Readout>
      <Readout label={t('records.handbacks')}>
        <span className="num">{s.handbacks}</span>
      </Readout>
      <Readout label={t('records.bestCourse')}>
        <span className="num">{courseTime(s.course_best_s) ?? '—'}</span>
      </Readout>
    </div>
  )
}

function CourseRuns({ d }: { d: RecordDetail }) {
  const t = useT8()
  const runs = d.course_runs
  return (
    <section className="flex flex-col gap-1.5 px-3" data-course-runs="">
      <span className="cap">{t('records.courseRuns')}</span>
      {runs.length === 0 ? (
        <p className="text-hud-sub text-[var(--g500)]">{t('records.noRuns')}</p>
      ) : (
        <Table data-lf-table="" className="text-[11px]">
          <TableHeader>
            <TableRow>
              <TableHead className="cap h-7">{t('records.run.course')}</TableHead>
              <TableHead className="cap h-7">{t('records.run.state')}</TableHead>
              <TableHead className="cap h-7 text-right">{t('records.run.total')}</TableHead>
              <TableHead className="cap h-7">{t('records.run.splits')}</TableHead>
              <TableHead className="cap h-7">{t('records.run.controller')}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.map((r) => {
              const meta = COURSE_RINGS[r.course_id]
              const best = r.state === 'finished' && r.total_s !== null && r.total_s === d.summary.course_best_s
              const stateKey: AnyKey = r.state === 'finished' ? 'records.run.state.finished' : r.state === 'aborted' ? 'records.run.state.aborted' : 'records.run.state.running'
              return (
                <TableRow key={r.id} className="h-7">
                  <TableCell className="py-1 text-[var(--g100)]">{meta ? t(meta[0], { n: meta[1] }) : r.course_id}</TableCell>
                  <TableCell className="py-1 text-[var(--g300)]">{t(stateKey)}</TableCell>
                  <TableCell data-num="" className={cn('num py-1', best ? 'font-bold text-[var(--g50)]' : 'text-[var(--g200)]')}>
                    {courseTime(r.total_s) ?? '—'}
                  </TableCell>
                  <TableCell className="num py-1 text-[var(--g400)]">{r.splits.map((s) => split(s)).join('  ') || '—'}</TableCell>
                  <TableCell className="py-1 text-[var(--g300)]">
                    {r.controller === 'agent' ? t('records.ctl.agent') : r.controller === 'human' ? t('records.ctl.human') : '—'}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
    </section>
  )
}

export function Replay({ record, focusT }: { record: RecordSummary; focusT: number | null }) {
  const t = useT8()
  const lang = useLang()
  const poll = usePoll(() => api.historySession(record.id) as Promise<RecordDetail>, record.live ? 2000 : 0, record.id)
  const d = poll.data
  const [cursor, setCursor] = React.useState<number | null>(focusT)
  const [playing, setPlaying] = React.useState(false)
  const [speed, setSpeed] = React.useState<(typeof SPEEDS)[number]>(1)
  const [onStage, setOnStage] = React.useState(true)
  const [chartsRef, width] = useWidth<HTMLDivElement>()

  // a new focus moment from the relay timeline moves the cursor
  const [seenFocus, setSeenFocus] = React.useState(focusT)
  if (seenFocus !== focusT) {
    setSeenFocus(focusT)
    if (focusT !== null) setCursor(focusT)
  }

  const domain = d ? recordDomain(d, backendNow()) : null
  const scale = domain ? makeScale(domain[0], domain[1], width) : null
  const cur = cursor === null || !domain ? (domain ? domain[0] : null) : Math.min(Math.max(cursor, domain[0]), domain[1])
  const cursorRef = React.useRef<number | null>(cur)
  React.useLayoutEffect(() => {
    cursorRef.current = cur
  }, [cur])

  // play: advance the cursor in real time x speed; stop at the end
  const d0 = domain?.[0] ?? null
  const d1 = domain?.[1] ?? null
  React.useEffect(() => {
    if (!playing || d0 === null || d1 === null) return
    let raf = 0
    let last = performance.now()
    const step = (now: number) => {
      const dt = ((now - last) / 1000) * speed
      last = now
      const next = Math.max(d0, cursorRef.current ?? d0) + dt
      cursorRef.current = Math.min(next, d1)
      setCursor(cursorRef.current)
      if (next >= d1) setPlaying(false)
      else raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [playing, speed, d0, d1])

  // publish the replay state for the stage (CONTRACTS.md §5)
  React.useEffect(() => {
    if (!d || cur === null || !onStage) {
      if (useReplay.getState().active) exitReplay()
      return
    }
    setReplay({ active: true, recordId: record.id, vehicleId: record.vehicle_id, label: record.label, kind: record.kind, t: cur, frame: frameAt(d, cur), playing })
  }, [d, cur, onStage, playing, record.id, record.vehicle_id, record.label, record.kind])
  React.useEffect(() => () => exitReplay(), [])

  const nearest = useNearestEvent(d?.events ?? [], cur, domain ? Math.max(0.5, (domain[1] - domain[0]) / 200) : 1)

  if (!d) {
    const err = poll.error
    return (
      <div className="px-4 py-6 text-hud-sub text-[var(--g400)]">
        {err ? t('records.loadFailed', { reason: err instanceof RelayApiError ? err.text(lang) : String(err) }) : t('records.loading')}
      </div>
    )
  }

  const s = d.samples
  const deg = (r: (number | null)[]) => r.map((v) => (typeof v === 'number' ? radToDeg(v) : null))
  const i = cur === null ? -1 : indexAt(s.t, cur)
  const elapsed = cur !== null && domain ? cur - domain[0] : 0
  const total = domain ? domain[1] - domain[0] : 0
  const ctlAt = i >= 0 ? s.controller[i] : null
  const onScrub = (tt: number) => {
    setPlaying(false)
    setCursor(tt)
  }

  return (
    <div className="flex h-full min-h-0 flex-col" data-replay={record.id}>
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-[var(--border)] px-3">
        {record.kind === 'sim' ? <SimTag /> : null}
        <h2 className="truncate text-[14px] font-[560] text-[var(--g50)]">{record.label}</h2>
        <span className="addr truncate">
          {clock(record.started_at)} – {record.ended_at ? clock(record.ended_at) : t('records.recording')}
          {record.autopilot ? ` · ${record.autopilot}` : ''} · #{record.id}
        </span>
        <span className="flex-1" />
        <Button variant="outline" size="sm" className="gap-1.5" onClick={() => void download(record.id, 'csv', lang, t)}>
          <Icon icon="folder" />
          {t('records.exportCsv')}
        </Button>
        <Button variant="outline" size="sm" className="gap-1.5" onClick={() => void download(record.id, 'json', lang, t)}>
          <Icon icon="folder" />
          {t('records.exportJson')}
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto py-3">
        <Summary d={d} />
        {s.t.length === 0 ? (
          <p className="px-3 pt-6 text-hud-sub text-[var(--g500)]">{t('records.noSamples')}</p>
        ) : (
          <div className={cn('mt-4 grid gap-3 px-3', onStage ? 'grid-cols-[minmax(0,1fr)_minmax(280px,38%)]' : 'grid-cols-1')}>
            <div className="flex min-w-0 flex-col gap-3">
              <div ref={chartsRef} className="flex min-w-0 flex-col gap-2" data-replay-charts="">
                {scale ? (
                  <>
                    <LineChart title={t('records.chart.voltage')} series={[{ values: s.voltage, tone: 'primary' }]} ts={s.t} scale={scale} cursor={cur} onScrub={onScrub} fmt={(x) => `${x.toFixed(2)} V`} minSpan={0.2} />
                    <LineChart title={t('records.chart.altitude')} series={[{ values: s.alt, tone: 'primary' }]} ts={s.t} scale={scale} cursor={cur} onScrub={onScrub} fmt={(x) => `${x.toFixed(2)} m`} minSpan={0.5} />
                    <LineChart
                      title={t('records.chart.attitude')}
                      series={[
                        { values: deg(s.roll), tone: 'primary', label: t('axis.roll') },
                        { values: deg(s.pitch), tone: 'secondary', label: t('axis.pitch') },
                      ]}
                      ts={s.t}
                      scale={scale}
                      cursor={cur}
                      onScrub={onScrub}
                      fmt={(x) => `${x.toFixed(1)}°`}
                      minSpan={4}
                    />
                    <LineChart title={t('records.chart.throttle')} series={[{ values: s.throttle, tone: 'primary' }]} ts={s.t} scale={scale} cursor={cur} onScrub={onScrub} fmt={(x) => `${Math.round(x)} %`} minSpan={10} bounds={[0, 100]} />
                    <EventTrack events={d.events} samples={s} scale={scale} cursor={cur} onScrub={onScrub} />
                    <ControlTrack segments={d.control_segments} scale={scale} end={domain![1]} cursor={cur} onScrub={onScrub} />
                    <TimeAxis scale={scale} />
                  </>
                ) : null}
              </div>
              {/* pinned to the bottom of the scrolling pane so play / pause and the cursor time stay in view */}
              <div className="sticky -bottom-3 z-[2] flex flex-wrap items-center gap-2 border-t border-[var(--border)] bg-[var(--g900)] pt-2 pb-5" data-replay-controls="">
                <Button variant="outline" size="icon" aria-label={playing ? t('records.pause') : t('records.play')} onClick={() => {
                  if (!playing && domain && cur !== null && cur >= domain[1]) setCursor(domain[0])
                  setPlaying((p) => !p)
                }}>
                  <StateIcon icon="tl.play" alt={playing} />
                </Button>
                <ToggleGroup value={[String(speed)]} onValueChange={(v: unknown[]) => v[0] && setSpeed(Number(v[0]) as (typeof SPEEDS)[number])} spacing={0} className="rounded-md bg-[var(--g950)] p-0.5 ring-1 ring-[var(--border)]">
                  {SPEEDS.map((x) => (
                    <ToggleGroupItem key={x} value={String(x)} className="num h-6 min-w-9 rounded-[5px] px-1.5 text-[11px] text-[var(--g400)] hover:bg-transparent data-[pressed]:text-[var(--g50)]">
                      {t('records.speed', { n: x })}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
                <span className="num text-[11px] text-[var(--g200)]">
                  {clock(cur)} · {duration(elapsed)} / {duration(total)}
                </span>
                <span className="min-w-0 flex-1 truncate text-hud-sub text-[var(--g400)]">
                  {[s.mode[i], ctlAt ? t(ctlAt === 'agent' ? 'records.ctl.agent' : ctlAt === 'human' ? 'records.ctl.human' : 'records.ctl.none') : null, nearest].filter(Boolean).join(' · ')}
                </span>
                {!onStage ? (
                  <Button variant="outline" size="sm" className="gap-1.5" onClick={() => setOnStage(true)}>
                    <Icon icon="ws.fly" />
                    {t('records.onStage')}
                  </Button>
                ) : null}
              </div>
            </div>
            {onStage ? (
              <div className="flex min-h-[300px] flex-col gap-2" data-replay-viewport="">
                <div className="flex h-5 items-center gap-2">
                  <span className="flex-1" />
                  <Button variant="ghost" size="sm" className="gap-1 text-[var(--g300)]" onClick={() => setOnStage(false)}>
                    <Icon icon="close" />
                    {t('records.exit')}
                  </Button>
                </div>
                <div className="min-h-0 flex-1">
                  <StageView />
                </div>
              </div>
            ) : null}
          </div>
        )}
        <div className="mt-5">
          <CourseRuns d={d} />
        </div>
      </div>
    </div>
  )
}
