// Copilot run trace (08 §3, §4): a small red pixel quadcopter under each prompt that flies while Copilot works and
// shows the phase and the time; a click opens the trace of the run: every model call (time, tokens, stop reason, the
// thinking, the reply and the tool calls it asked for) and every tool execution (arguments and result). While a run
// is going the same chip also sits above the composer. Red is the owner's explicit exception for the craft itself;
// the trace panel stays graphite.
import * as React from 'react'
import { useLang } from '@/i18n'
import { cn } from '@/lib/utils'
import { Icon } from '@/ui/icons'
import { runEnded, useCopilot, type CopilotRun, type RunStep, type ToolStep, type TurnStep } from './store'
import { useCPT, type CopilotKey } from './strings'
import { toolLabel } from './toolLabels'

// ---------------------------------------------------------------- pixel craft

/** 7×7 top view of a quadcopter: 2×2 rotors in the corners (two diagonals each, so they can spin), 3×3 body and a
 * lighter nose pixel in front. */
const ROTORS: [number, number][] = [
  [0, 0],
  [5, 0],
  [0, 5],
  [5, 5],
]
const BODY: [number, number][] = [
  [2, 2],
  [3, 2],
  [4, 2],
  [2, 3],
  [3, 3],
  [4, 3],
  [2, 4],
  [3, 4],
  [4, 4],
]

export function PixelCraft({ active, size = 14, className }: { active: boolean; size?: number; className?: string }) {
  return (
    <svg
      viewBox="0 0 7 7"
      width={size}
      height={size}
      shapeRendering="crispEdges"
      aria-hidden="true"
      className={cn('cp-craft flex-none', className)}
      data-active={active ? '' : undefined}
    >
      <g className="cp-craft-body">
        {ROTORS.map(([x, y]) => (
          <React.Fragment key={`${x}-${y}`}>
            <rect className="cp-craft-a" x={x} y={y} width={1} height={1} />
            <rect className="cp-craft-a" x={x + 1} y={y + 1} width={1} height={1} />
            <rect className="cp-craft-b" x={x + 1} y={y} width={1} height={1} />
            <rect className="cp-craft-b" x={x} y={y + 1} width={1} height={1} />
          </React.Fragment>
        ))}
        {BODY.map(([x, y]) => (
          <rect key={`b${x}-${y}`} x={x} y={y} width={1} height={1} />
        ))}
        <rect className="cp-craft-nose" x={3} y={1} width={1} height={1} />
      </g>
    </svg>
  )
}

// ---------------------------------------------------------------- formatting

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(1)} s`
  const m = Math.floor(s / 60)
  return `${m} min ${Math.round(s - m * 60)} s`
}

export function fmtCount(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  if (n < 1000) return String(Math.round(n))
  if (n < 100_000) return `${(n / 1000).toFixed(1)}k`
  return `${Math.round(n / 1000)}k`
}

/** The chip's line for a run: its phase, with the tool or the characters written so far. */
export function phaseText(run: CopilotRun, t: (k: CopilotKey, p?: Record<string, string | number>) => string, lang: 'en' | 'zh'): string {
  const tool = run.tool ? toolLabel(run.tool, lang) : ''
  const chars = (n: number) => t('cp.run.chars', { n: fmtCount(n) })
  switch (run.phase) {
    case 'thinking':
      return t('cp.run.phase.thinking', { chars: chars(run.thinkingChars) })
    case 'drafting':
      return t('cp.run.phase.drafting', { tool, chars: chars(run.draftChars) })
    case 'tool':
      return t('cp.run.phase.tool', { tool })
    default:
      return t(`cp.run.phase.${run.phase}` as CopilotKey)
  }
}

/** Milliseconds since the run started, ticking every half second while it runs. */
function useElapsed(run: CopilotRun): number {
  const ended = runEnded(run)
  const [now, setNow] = React.useState(() => Date.now())
  React.useEffect(() => {
    if (ended) return
    const id = window.setInterval(() => setNow(Date.now()), 500)
    return () => window.clearInterval(id)
  }, [ended])
  return (run.endedAt ?? now) - run.startedAt
}

// ---------------------------------------------------------------- chip

export function RunChip({ run, live = false }: { run: CopilotRun; live?: boolean }) {
  const t = useCPT()
  const lang = useLang()
  const open = useCopilot((s) => s.openRun === run.id)
  const elapsed = useElapsed(run)
  const ended = runEnded(run)
  const bad = run.phase === 'error' || run.phase === 'empty' || run.phase === 'length'
  return (
    <button
      type="button"
      className={cn('cp-run-chip', live && 'cp-run-live')}
      onClick={() => useCopilot.getState().toggleRun(run.id)}
      aria-expanded={open}
      title={open ? t('cp.run.close') : t('cp.run.open')}
      data-run-chip={run.id}
      data-phase={run.phase}
    >
      <PixelCraft active={!ended} />
      <span className={cn('min-w-0 flex-1 truncate', bad ? 'text-[var(--g100)]' : 'text-[var(--g300)]')}>{phaseText(run, t, lang)}</span>
      <span className="num flex-none text-[var(--g500)]">{fmtMs(elapsed)}</span>
      <Icon icon={open ? 'chevron.up' : 'chevron.down'} className="size-3 flex-none text-[var(--g500)]" />
    </button>
  )
}

// ---------------------------------------------------------------- trace

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="cap text-[10px] text-[var(--g500)]">{title}</span>
      {children}
    </div>
  )
}

function TurnRow({ step }: { step: TurnStep }) {
  const t = useCPT()
  const [open, setOpen] = React.useState(false)
  const u = step.usage
  const stop = step.endedAt === null ? 'running' : step.stopReason
  const stopKey = `cp.run.stop.${stop}` as CopilotKey
  return (
    <li className="cp-run-step" data-step="turn" data-stop={stop ?? undefined}>
      <button type="button" className="cp-run-row" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <Icon icon={open ? 'chevron.down' : 'chevron.right'} className="size-3 flex-none text-[var(--g500)]" />
        <span className="flex-none font-[560] text-[var(--g100)]">{t('cp.run.turn', { n: step.turn })}</span>
        <span className="min-w-0 flex-1 truncate text-[var(--g400)]">
          {u ? t('cp.run.tokens', { input: fmtCount(u.input), output: fmtCount(u.output) }) : ''}
          {u?.reasoning ? ` · ${t('cp.run.reasoning', { n: fmtCount(u.reasoning) })}` : ''}
          {stop ? ` · ${t(stopKey) === stopKey ? stop : t(stopKey)}` : ''}
        </span>
        <span className="num flex-none text-[var(--g500)]">{fmtMs(step.endedAt === null ? null : step.endedAt - step.startedAt)}</span>
      </button>
      {open ? (
        <div className="cp-run-detail">
          <Section title={t('cp.run.section.thinking')}>
            {step.thinking ? <pre className="cp-pre cp-run-pre">{step.thinking}</pre> : <p className="text-[var(--g500)]">{t('cp.run.noThinking')}</p>}
          </Section>
          {step.text ? (
            <Section title={t('cp.run.section.reply')}>
              <pre className="cp-pre cp-run-pre">{step.text}</pre>
            </Section>
          ) : null}
          {step.toolCalls.length ? (
            <Section title={t('cp.run.section.calls')}>
              {step.toolCalls.map((c) => (
                <div key={c.id} className="flex flex-col gap-0.5">
                  <span className="num text-[var(--g200)]">{c.name}</span>
                  <pre className="cp-pre cp-run-pre">{c.args}</pre>
                </div>
              ))}
            </Section>
          ) : null}
          {step.error ? <p className="text-[var(--g200)]">{step.error}</p> : null}
        </div>
      ) : null}
    </li>
  )
}

function ToolRow({ step }: { step: ToolStep }) {
  const t = useCPT()
  const lang = useLang()
  const [open, setOpen] = React.useState(false)
  const vehicle = typeof step.args.vehicle === 'string' ? step.args.vehicle : null
  return (
    <li className="cp-run-step" data-step="tool" data-state={step.state}>
      <button type="button" className="cp-run-row" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <Icon icon={step.state === 'ok' ? 'check' : step.state === 'error' ? 'alert' : 'pending'} className={cn('size-3 flex-none', step.state === 'running' && 'cp-spin')} />
        <span className="flex-none font-[560] text-[var(--g100)]">{toolLabel(step.tool, lang)}</span>
        <span className="num min-w-0 flex-1 truncate text-[var(--g400)]">{vehicle ?? step.tool}</span>
        <span className="num flex-none text-[var(--g500)]">{fmtMs(step.endedAt === null ? null : step.endedAt - step.startedAt)}</span>
      </button>
      {open ? (
        <div className="cp-run-detail">
          <Section title={t('cp.run.section.args')}>
            <pre className="cp-pre cp-run-pre">{JSON.stringify(step.args, null, 1)}</pre>
          </Section>
          <Section title={t('cp.run.section.result')}>
            <pre className="cp-pre cp-run-pre">{step.result || t('cp.run.stop.running')}</pre>
          </Section>
        </div>
      ) : null}
    </li>
  )
}

export function RunTrace({ run }: { run: CopilotRun }) {
  const t = useCPT()
  const elapsed = useElapsed(run)
  const tokens = run.usage?.total ?? run.steps.reduce((n, st: RunStep) => n + (st.kind === 'turn' ? (st.usage?.total ?? 0) : 0), 0)
  return (
    <div className="cp-run-trace" data-run-trace={run.id}>
      <div className="flex items-center gap-2">
        <span className="cap text-[10px] text-[var(--g400)]">{t('cp.run.title')}</span>
        <span className="num min-w-0 flex-1 truncate text-right text-[10.5px] text-[var(--g500)]">{t('cp.run.model', { model: run.model || '—', thinking: run.thinking })}</span>
      </div>
      {run.steps.length ? (
        <ol className="flex flex-col">
          {run.steps.map((st) => (st.kind === 'turn' ? <TurnRow key={`t${st.turn}`} step={st} /> : <ToolRow key={st.id} step={st} />))}
        </ol>
      ) : (
        <p className="text-[var(--g500)]">{t('cp.run.empty')}</p>
      )}
      <p className="num text-[10.5px] text-[var(--g500)]" data-run-total="">
        {t('cp.run.total', { turns: run.turns, tools: run.tools, tokens: fmtCount(tokens), time: fmtMs(elapsed) })}
      </p>
    </div>
  )
}

/** The run of a user message: its chip and, when opened, its trace. */
export function RunBlock({ run }: { run: CopilotRun }) {
  const open = useCopilot((s) => s.openRun === run.id)
  return (
    <div className="flex flex-col gap-1.5" data-run={run.id}>
      <RunChip run={run} />
      {open ? <RunTrace run={run} /> : null}
    </div>
  )
}

/** Above the composer while a run is going and its chip under the prompt is scrolled out of view. */
export function LiveRunBar() {
  const run = useCopilot((s) => {
    const last = s.runs[s.runs.length - 1]
    return last && !runEnded(last) ? last : null
  })
  const id = run?.id ?? null
  // the run whose chip under the prompt is in view (keyed by run, so a new run starts visible here)
  const [inView, setInView] = React.useState<number | null>(null)
  React.useEffect(() => {
    if (id === null || typeof IntersectionObserver === 'undefined') return
    const el = document.querySelector(`[data-run="${id}"] [data-run-chip]`)
    if (!el) return
    const io = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting ? id : null), { threshold: 0.9 })
    io.observe(el)
    return () => io.disconnect()
  }, [id])
  if (!run || inView === run.id) return null
  return (
    <div data-live-run="">
      <RunChip run={run} live />
    </div>
  )
}
