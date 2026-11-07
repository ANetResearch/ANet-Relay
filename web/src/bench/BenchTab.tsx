// Inspector Bench tab (05 §4.5): start a bench procedure, follow it step by step and answer its prompts, then read
// the report. Layout: start panel (procedure, options) when idle; while a run is active a progress row, the step
// list and the step panel (prompt with type-to-confirm for dangerous steps, motor layout, live mini charts, the
// step's figures). The tab is a container: list and panel stack below 400 px and sit side by side above.
import * as React from 'react'
import { api, RelayApiError, type ProcedureEntry, type ProcedurePrompt, type ProcedureRun, type ProcedureStep, type Vehicle } from '@/api'
import { pickText, useLang } from '@/i18n'
import { cn } from '@/lib/utils'
import { useVehicle } from '@/store/selectors'
import { Button } from '@/ui/components/ui/button'
import { Progress } from '@/ui/components/ui/progress'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/ui/components/ui/select'
import { Switch } from '@/ui/components/ui/switch'
import { ToggleGroup, ToggleGroupItem } from '@/ui/components/ui/toggle-group'
import { Icon, StateIcon, type IconKey } from '@/ui/icons'
import { Group, TypeToConfirm } from '@/ui/relay'
import { LiveCharts } from './MiniChart'
import { LevelDiagram, MotorLayout } from './MotorLayout'
import { ReportView, VerdictWord } from './ReportView'
import { currentStep, formatValue, guardedAnswer, isActive, levelTone, progress, promptLayout, spinningMotors, stateTone, stepTone } from './model'
import { useCatalogue, useProcedure } from './useProcedure'
import { useTB, type KeyB } from './copy'
import './bench.css'

const HOVER_CHOICES = [10, 15, 20, 30] as const

/** State icon of a step in the list: pending, running / waiting, passed, warning or failed, skipped. */
function stepIcon(s: Pick<ProcedureStep, 'state' | 'verdict'>): IconKey {
  if (s.state === 'running' || s.state === 'waiting') return 'pending'
  if (s.verdict === 'pass') return 'check'
  if (s.verdict === 'warn') return 'alert'
  if (s.verdict === 'fail' || s.state === 'aborted') return 'alert.critical'
  if (s.state === 'skipped') return 'more'
  return 'chevron.right'
}

/** Why a procedure cannot start on this aircraft now, or null. */
export function startBlocker(v: Pick<Vehicle, 'armed' | 'link' | 'controller' | 'possession' | 'crashed'>): KeyB | null {
  if (v.link.state !== 'ok' && v.link.state !== 'delayed') return 'bench.unavailable.link'
  if (v.crashed) return 'bench.unavailable.crashed'
  if (v.armed) return 'bench.unavailable.armed'
  if (!(v.controller === 'human' && v.possession)) return 'bench.unavailable.control'
  return null
}

function StartPanel({ v, onStarted }: { v: Vehicle; onStarted(run: ProcedureRun): void }) {
  const t = useTB()
  const lang = useLang()
  const cat = useCatalogue()
  const [pid, setPid] = React.useState<string>('full_bench')
  const [hover, setHover] = React.useState<number>(15)
  const [auto, setAuto] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const entry: ProcedureEntry | undefined = cat?.procedures.find((p) => p.id === pid)
  const flies = entry?.steps.some((s) => s.id === 'S9') ?? false
  const spinsOne = entry?.steps.some((s) => s.id === 'S6') ?? false
  const blocker = startBlocker(v)
  const items = (cat?.procedures ?? []).map((p) => ({ value: p.id, label: pickText(lang, p.title_en, p.title_zh) }))
  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      const run = await api.procedureStart(v.id, pid, { ...(flies ? { hover_s: hover } : {}), ...(auto && v.kind === 'sim' && spinsOne ? { auto_answer: true } : {}) })
      onStarted(run)
    } catch (e) {
      setError(e instanceof RelayApiError ? e.text(lang) : String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Group label={t('bench.procedure')} right={entry ? <span className="num text-hud-sub text-[var(--g400)]">{t('bench.estimate', { min: Math.max(1, Math.round(entry.est_s / 60)) })}</span> : null}>
      <div className="flex flex-col gap-3 px-3" data-bench-start="">
        <Select items={items} value={pid} onValueChange={(x) => x && setPid(String(x))} disabled={!cat}>
          <SelectTrigger size="sm" className="h-7 w-full text-[12px]" aria-label={t('bench.procedure')}>
            <SelectValue placeholder="—" />
          </SelectTrigger>
          <SelectContent>
            {items.map((it) => (
              <SelectItem key={it.value} value={it.value} className="text-[12px]">
                {it.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {entry ? (
          <ol className="flex flex-wrap gap-x-3 gap-y-1 text-hud-sub text-[var(--g400)]" data-bench-plan={entry.id}>
            {entry.steps.map((s) => (
              <li key={s.id} title={t(`bench.kind.${s.kind}` as KeyB)}>
                <span className="num text-[var(--g500)]">{s.id}</span> {pickText(lang, s.title_en, s.title_zh)}
              </li>
            ))}
          </ol>
        ) : null}
        {flies ? (
          <div className="flex items-center gap-3">
            <span className="w-24 flex-none text-hud-sub text-[var(--g300)]">{t('bench.hover')}</span>
            <ToggleGroup
              value={[String(hover)]}
              onValueChange={(x: unknown[]) => x[0] && setHover(Number(x[0]))}
              spacing={0}
              aria-label={t('bench.hover')}
              className="rounded-md bg-[var(--g900)] p-0.5 ring-1 ring-[var(--border)]"
            >
              {HOVER_CHOICES.map((s) => (
                <ToggleGroupItem key={s} value={String(s)} className="num h-6 px-2 rounded-[5px] text-[11px] text-[var(--g400)] data-[pressed]:text-[var(--g50)]">
                  {t('bench.hoverValue', { s })}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
          </div>
        ) : null}
        {v.kind === 'sim' && spinsOne ? (
          <label className="flex items-start gap-2">
            <Switch checked={auto} onCheckedChange={setAuto} size="sm" aria-label={t('bench.autoAnswer')} />
            <span className="flex flex-col">
              <span className="text-ed-value text-[var(--g200)]">{t('bench.autoAnswer')}</span>
              <span className="text-hud-sub text-[var(--g500)]">{t('bench.autoAnswerHint')}</span>
            </span>
          </label>
        ) : null}
        {blocker ? <p className="text-hud-sub text-[var(--g400)]">{t(blocker)}</p> : null}
        {error ? (
          <p className="text-hud-sub text-[var(--g50)]" role="alert">
            {error}
          </p>
        ) : null}
        <Button className="self-start" disabled={Boolean(blocker) || busy || !entry} onClick={() => void start()} data-bench-action="start">
          <Icon icon="tl.play" />
          {busy ? t('bench.starting') : t('bench.start')}
        </Button>
      </div>
    </Group>
  )
}

function StepList({ run, selected, onSelect }: { run: ProcedureRun; selected: string | null; onSelect(id: string): void }) {
  const t = useTB()
  const lang = useLang()
  return (
    <ol className="bench-steps" aria-label={t('bench.steps')} data-bench-steps="">
      {run.steps.map((s) => {
        const tone = stepTone(s)
        const icon = stepIcon(s)
        return (
          <li key={s.id}>
            <button type="button" className="bench-step w-full" data-tone={tone} data-selected={s.id === selected ? '' : undefined} data-step={s.id} data-state={s.state} onClick={() => onSelect(s.id)}>
              <StateIcon icon={icon} spring="smooth" className="bench-step-mark size-3 flex-none" />
              <span className="num w-6 flex-none text-[11px] text-[var(--g500)]">{s.id}</span>
              <span className="min-w-0 flex-1 truncate text-hud-sub">{pickText(lang, s.title_en, s.title_zh)}</span>
              <span className="sr-only">{t(`bench.stepState.${s.state}` as KeyB)}</span>
            </button>
          </li>
        )
      })}
    </ol>
  )
}

function PromptCard({ v, run, step, prompt, onRun }: { v: Vehicle; run: ProcedureRun; step: ProcedureStep; prompt: ProcedurePrompt; onRun(run: ProcedureRun): void }) {
  const t = useTB()
  const lang = useLang()
  const [confirming, setConfirming] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const guarded = guardedAnswer(prompt)
  const layout = promptLayout(prompt)
  const data = prompt.data as { motor?: number; index?: number; count?: number; blocking?: string[] }
  const detail = prompt.detail_en ? pickText(lang, prompt.detail_en, prompt.detail_zh ?? '') : null
  const send = async (answer: string, confirm?: string) => {
    setBusy(true)
    setError(null)
    try {
      onRun(await api.procedureAnswer(v.id, step.id, answer, confirm))
      setConfirming(null)
    } catch (e) {
      setError(t('bench.answerFailed', { reason: e instanceof RelayApiError ? e.text(lang) : String(e) }))
    } finally {
      setBusy(false)
    }
  }
  const done = typeof data.index === 'number' ? Array.from({ length: data.index }, (_, i) => layout?.motors[i]?.n ?? i + 1) : []
  return (
    <div className="bench-prompt" data-bench-prompt={prompt.id} role="group" aria-label={pickText(lang, prompt.text_en, prompt.text_zh)}>
      <p className="text-ed-value text-[var(--g50)]">{pickText(lang, prompt.text_en, prompt.text_zh)}</p>
      {detail ? <p className="text-hud-sub text-[var(--g400)]">{detail}</p> : null}
      {data.blocking && data.blocking.length ? <p className="text-hud-sub text-[var(--g50)]">{t('bench.blocking', { items: data.blocking.join(', ') })}</p> : null}
      {prompt.id === 'calibrate_level' || prompt.id === 'calibrate_gyro' ? <LevelDiagram still={prompt.id === 'calibrate_gyro'} /> : null}
      {layout ? (
        <div className="flex items-center gap-3">
          <MotorLayout layout={layout} target={data.motor ?? null} spinning={spinningMotors(v.servo, layout.count)} done={done} size={148} />
          {typeof data.count === 'number' ? <span className="num text-hud-sub text-[var(--g400)]">{t('bench.motorChecked', { n: data.index ?? 0, total: data.count })}</span> : null}
        </div>
      ) : null}
      {run.paused ? <p className="text-hud-sub text-[var(--g400)]">{t('bench.paused')}</p> : null}
      {prompt.operator_only ? (
        <p className="flex items-center gap-1.5 text-hud-sub text-[var(--g400)]">
          <Icon icon="lock" className="size-3" />
          {t('bench.operatorOnly')}
        </p>
      ) : null}
      {confirming && prompt.confirm_word ? (
        <TypeToConfirm
          word={prompt.confirm_word}
          prompt={t('bench.confirmPrompt', { word: prompt.confirm_word })}
          actionLabel={pickText(lang, prompt.answers.find((a) => a.id === confirming)?.label_en ?? '', prompt.answers.find((a) => a.id === confirming)?.label_zh ?? '')}
          onConfirm={() => send(confirming, prompt.confirm_word ?? undefined)}
          onCancel={() => setConfirming(null)}
          busy={busy}
          error={error}
        />
      ) : prompt.answers.length ? (
        <div className="flex flex-wrap gap-2">
          {prompt.answers.map((a) => (
            <Button
              key={a.id}
              size="sm"
              variant={a.tone === 'primary' ? 'default' : a.tone === 'danger' ? 'destructive' : 'outline'}
              disabled={busy}
              onClick={() => (a.id === guarded ? setConfirming(a.id) : void send(a.id))}
              data-answer={a.id}
            >
              {pickText(lang, a.label_en, a.label_zh)}
            </Button>
          ))}
        </div>
      ) : null}
      {error && !confirming ? (
        <p className="text-hud-sub text-[var(--g50)]" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  )
}

function StepPanel({ v, run, step, onRun }: { v: Vehicle; run: ProcedureRun; step: ProcedureStep; onRun(run: ProcedureRun): void }) {
  const t = useTB()
  const lang = useLang()
  const activity = step.activity_en ? pickText(lang, step.activity_en, step.activity_zh ?? '') : null
  const live = step.state === 'running' || step.state === 'waiting'
  return (
    <div className="flex min-w-0 flex-col gap-3" data-bench-panel={step.id}>
      <div className="flex flex-col gap-0.5">
        <div className="flex items-center gap-2">
          <span className="num text-[11px] text-[var(--g500)]">{step.id}</span>
          <h4 className="min-w-0 flex-1 truncate text-hud-title font-[560] text-[var(--g100)]">{pickText(lang, step.title_en, step.title_zh)}</h4>
          <VerdictWord step={step} />
        </div>
        <p className="text-hud-sub text-[var(--g400)]">{pickText(lang, step.description_en, step.description_zh)}</p>
      </div>
      {activity && live ? (
        <p className="flex items-center gap-2 text-ed-value text-[var(--g200)]" aria-live="polite">
          <StateIcon icon="pending" className="size-3.5 flex-none text-[var(--g400)]" spring="smooth" />
          {activity}
        </p>
      ) : null}
      {step.prompt && step.state === 'waiting' ? <PromptCard key={`${step.id}-${step.prompt.id}-${JSON.stringify(step.prompt.data)}`} v={v} run={run} step={step} prompt={step.prompt} onRun={onRun} /> : null}
      {live ? <LiveCharts step={step} /> : null}
      {step.metrics.length ? (
        <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1" data-bench-metrics={step.id}>
          {step.metrics.map((m) => (
            <React.Fragment key={m.key}>
              <dt className="truncate text-hud-sub text-[var(--g400)]">{pickText(lang, m.label_en, m.label_zh)}</dt>
              <dd className={cn('num text-right text-hud-sub', `bench-tone-${levelTone(m.level)}`)}>{formatValue(m.value, m.unit)}</dd>
            </React.Fragment>
          ))}
        </dl>
      ) : null}
      {step.findings.length ? (
        <ul className="flex flex-col gap-1">
          {step.findings.map((f, i) => (
            <li key={`${f.code}-${i}`} className="flex items-start gap-2 text-hud-sub">
              <Icon icon={f.level === 'info' ? 'info' : 'alert'} className={cn('mt-px size-3.5 flex-none', f.level === 'fail' ? 'text-[var(--r400)]' : 'text-[var(--g400)]')} />
              <span className={f.level === 'info' ? 'text-[var(--g400)]' : 'text-[var(--g100)]'}>{pickText(lang, f.text_en, f.text_zh)}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {!live && step.state === 'pending' ? <p className="text-hud-sub text-[var(--g500)]">{t('bench.stepState.pending')}</p> : null}
    </div>
  )
}

function RunView({ v, run, onRun }: { v: Vehicle; run: ProcedureRun; onRun(run: ProcedureRun): void }) {
  const t = useTB()
  const lang = useLang()
  const cur = currentStep(run)
  const [picked, setPicked] = React.useState<string | null>(null)
  // follow the current step until the operator picks another one; a new current step resets the pick
  const [followed, setFollowed] = React.useState(cur?.id ?? null)
  if ((cur?.id ?? null) !== followed) {
    setFollowed(cur?.id ?? null)
    setPicked(null)
  }
  const shown = run.steps.find((s) => s.id === (picked ?? cur?.id)) ?? cur ?? run.steps[0]
  const [aborting, setAborting] = React.useState(false)
  const abort = async () => {
    setAborting(true)
    try {
      onRun(await api.procedureAbort(v.id))
    } catch {
      /* the next poll shows the state */
    } finally {
      setAborting(false)
    }
  }
  const tone = stateTone(run.state)
  return (
    <div className="flex flex-col gap-3" data-bench-run={run.run_id} data-run-state={run.state}>
      <div className="flex flex-col gap-2 px-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-hud-title font-[560] text-[var(--g100)]">{pickText(lang, run.title_en, run.title_zh)}</span>
          <span className={cn('text-hud-sub', `bench-tone-${tone}`)}>{t(`bench.state.${run.state}` as KeyB)}</span>
        </div>
        <Progress value={Math.round(progress(run) * 100)} aria-label={t('bench.progress', { index: run.index + 1, total: run.total })} />
        <div className="flex items-center gap-2">
          <span className="num text-hud-sub text-[var(--g400)]">{t('bench.progress', { index: Math.min(run.index + 1, run.total), total: run.total })}</span>
          <span className="truncate text-hud-sub text-[var(--g500)]">· {run.by.kind === 'session' ? t('bench.agentRun', { name: run.by.name ?? '—' }) : t('bench.operatorRun')}</span>
          <span className="flex-1" />
          <Button size="sm" variant="outline" disabled={aborting} onClick={() => void abort()} title={t('bench.abortHint')} data-bench-action="abort">
            <Icon icon="stop" />
            {aborting ? t('bench.aborting') : t('bench.abort')}
          </Button>
        </div>
      </div>
      <div className="bench-split px-3">
        <StepList run={run} selected={shown?.id ?? null} onSelect={setPicked} />
        {shown ? <StepPanel v={v} run={run} step={shown} onRun={onRun} /> : null}
      </div>
    </div>
  )
}

export function BenchTab({ vehicleId }: { vehicleId: string }) {
  const t = useTB()
  const v = useVehicle(vehicleId)
  const { run, set } = useProcedure(vehicleId)
  const [fresh, setFresh] = React.useState(false)
  if (!v) return null
  const active = run !== null && isActive(run.state)
  return (
    <div className="bench" data-bench-tab={vehicleId}>
      {active ? (
        <RunView v={v} run={run} onRun={set} />
      ) : (
        <>
          {run && !fresh ? (
            <div className="flex items-center gap-2 px-3">
              <span className="cap flex-1">{t('bench.report')}</span>
              <Button size="sm" variant="outline" onClick={() => setFresh(true)} data-bench-action="new">
                <Icon icon="refresh" />
                {t('bench.newRun')}
              </Button>
            </div>
          ) : (
            <StartPanel
              v={v}
              onStarted={(r) => {
                setFresh(false)
                set(r)
              }}
            />
          )}
          {run && !fresh ? <ReportView run={run} compact /> : null}
        </>
      )}
    </div>
  )
}
