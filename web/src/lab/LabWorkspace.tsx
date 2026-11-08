// Lab workspace (06 §7, mod+4): programs and challenges on the left, the CodeMirror editor in the middle (save makes
// a revision; problems from POST /programs/{id}/validate show inline), and the Params / Tests / Run / Judge / Duel /
// History tabs on the right. Wide: 256 | editor | 400; below 1200 px: 216 | editor | 340.
import * as React from 'react'
import { RelayApiError } from '@/api'
import { useLang } from '@/i18n'
import { cn } from '@/lib/utils'
import { notifyError } from '@/shell/notify'
import { registerHotkey } from '@/shell/keyboard'
import { useLayoutMode } from '@/shell/layout'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/ui/components/ui/alert-dialog'
import { Button } from '@/ui/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/ui/components/ui/dropdown-menu'
import { Input } from '@/ui/components/ui/input'
import { ScrollArea } from '@/ui/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/ui/components/ui/tabs'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/ui/components/ui/tooltip'
import { Icon } from '@/ui/icons'
import { VoxelLoader } from '@/ui/voxel/VoxelLoader'
import { labApi } from './api'
import { challengeStatus, useChallenges } from './challenges'
import { CodeEditor, focusLine } from './CodeEditor'
import { useTL, type LabKey } from './copy'
import { programName, useAuthorLabel } from './parts'
import { isDirty, LAB_TABS, useLab, type LabTab } from './store'
import { ParamsTab } from './ParamsTab'
import { TestsTab } from './TestsTab'
import { RunTab } from './RunTab'
import { JudgeTab } from './JudgeTab'
import { DuelTab } from './DuelTab'
import { HistoryTab } from './HistoryTab'
import './lab.css'

const TAB_KEYS: Record<LabTab, LabKey> = {
  params: 'lab.tab.params',
  tests: 'lab.tab.tests',
  run: 'lab.tab.run',
  judge: 'lab.tab.judge',
  duel: 'lab.tab.duel',
  history: 'lab.tab.history',
}

// ---------------------------------------------------------------- dirty guard

interface Guard {
  ask(then: () => void): void
}
const GuardContext = React.createContext<Guard>({ ask: (then) => then() })

/** Asks before an action that would drop unsaved changes (opening another program, discarding). */
function useGuard(): Guard {
  return React.useContext(GuardContext)
}

function GuardProvider({ children }: { children: React.ReactNode }) {
  const tl = useTL()
  const lang = useLang()
  const [pending, setPending] = React.useState<(() => void) | null>(null)
  const name = useLab((s) => (s.program ? programName(s.program, lang) : ''))
  const guard = React.useMemo<Guard>(
    () => ({
      ask: (then) => {
        if (isDirty(useLab.getState())) setPending(() => then)
        else then()
      },
    }),
    [],
  )
  return (
    <GuardContext.Provider value={guard}>
      {children}
      <AlertDialog open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <AlertDialogContent data-lab-discard="">
          <AlertDialogHeader>
            <AlertDialogTitle>{tl('lab.discardTitle')}</AlertDialogTitle>
            <AlertDialogDescription>{tl('lab.discardBody', { name })}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tl('lab.keepEditing')}</AlertDialogCancel>
            <AlertDialogAction
              variant="outline"
              onClick={() => {
                const then = pending
                setPending(null)
                useLab.getState().revert()
                then?.()
              }}
            >
              {tl('lab.discard')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </GuardContext.Provider>
  )
}

// ---------------------------------------------------------------- left column

function ProgramRow({ id, name, rev, tests, builtin, selected, dirty }: { id: string; name: string; rev: number; tests: number; builtin: boolean; selected: boolean; dirty: boolean }) {
  const tl = useTL()
  const guard = useGuard()
  return (
    <button
      type="button"
      className="lab-row"
      data-selected={selected ? '' : undefined}
      data-lab-program={id}
      onClick={() => !selected && guard.ask(() => void useLab.getState().open(id))}
    >
      <span className="flex min-w-0 items-center gap-1.5">
        <span className="min-w-0 flex-1 truncate text-[12px] text-[var(--g100)]">{name}</span>
        {dirty ? <span className="lab-dot" aria-label={tl('lab.unsaved')} /> : null}
      </span>
      <span className="num truncate text-[11px] text-[var(--g500)]">
        {builtin ? tl('lab.example') : tl('lab.rev', { rev })} · {tl('lab.testCount', { n: tests })}
      </span>
    </button>
  )
}

function ProgramList() {
  const tl = useTL()
  const lang = useLang()
  const guard = useGuard()
  const programs = useLab((s) => s.programs)
  const listError = useLab((s) => s.listError)
  const selectedId = useLab((s) => s.selectedId)
  const dirty = useLab((s) => isDirty(s))
  const program = useLab((s) => s.program)
  const [confirmDelete, setConfirmDelete] = React.useState(false)
  const mine = (programs ?? []).filter((p) => !p.builtin)
  const examples = (programs ?? []).filter((p) => p.builtin)
  const row = (p: (typeof mine)[number]) => (
    <ProgramRow key={p.id} id={p.id} name={programName(p, lang)} rev={p.rev} tests={p.test_count} builtin={p.builtin} selected={p.id === selectedId} dirty={p.id === selectedId && dirty} />
  )
  return (
    <section className="flex min-h-0 flex-col" aria-label={tl('lab.programs')}>
      <header className="flex h-10 shrink-0 items-center gap-1 pr-1.5 pl-3">
        <h2 className="min-w-0 flex-1 truncate text-hud-title font-[560] text-[var(--g100)]">{tl('lab.programs')}</h2>
        <Tooltip>
          <TooltipTrigger render={<Button variant="ghost" size="icon-sm" aria-label={tl('lab.new')} onClick={() => guard.ask(() => void useLab.getState().createNew())} data-lab-new="" />}>
            <Icon icon="plus" />
          </TooltipTrigger>
          <TooltipContent>{tl('lab.new')}</TooltipContent>
        </Tooltip>
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label={tl('lab.more')} disabled={!program} />}>
            <Icon icon="more" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-40">
            <DropdownMenuItem onClick={() => guard.ask(() => void useLab.getState().duplicate())}>
              <Icon icon="duplicate" />
              {tl('lab.duplicate')}
            </DropdownMenuItem>
            <DropdownMenuItem disabled={!program || program.builtin} onClick={() => setConfirmDelete(true)}>
              <Icon icon="trash" />
              {tl('lab.delete')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </header>
      {programs === null ? (
        <div className="px-3 py-2 text-hud-sub text-[var(--g500)]">
          {listError ? tl('lab.listFailed', { reason: listError instanceof RelayApiError ? listError.text(lang) : String(listError) }) : <VoxelLoader size={16} label={tl('lab.loading')} caption={tl('lab.loading')} />}
        </div>
      ) : (
        <div className="flex flex-col gap-0.5 px-1.5 pb-2">
          <span className="cap px-1.5 pt-1 pb-1">{tl('lab.mine')}</span>
          {mine.length ? mine.map(row) : <p className="px-1.5 pb-1 text-hud-sub text-[var(--g500)]">{tl('lab.mineEmpty')}</p>}
          <span className="cap px-1.5 pt-3 pb-1">{tl('lab.examples')}</span>
          {examples.map(row)}
        </div>
      )}
      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{tl('lab.deleteTitle', { name: program ? programName(program, lang) : '' })}</AlertDialogTitle>
            <AlertDialogDescription>{tl('lab.deleteBody')}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tl('lab.cancel')}</AlertDialogCancel>
            <AlertDialogAction variant="outline" onClick={() => void useLab.getState().remove()}>
              {tl('lab.delete')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  )
}

function ChallengeList() {
  const tl = useTL()
  const lang = useLang()
  const list = useChallenges((s) => s.list)
  const unavailable = useChallenges((s) => s.unavailable)
  const current = useLab((s) => s.challengeId)
  const tab = useLab((s) => s.tab)
  React.useEffect(() => {
    void useChallenges.getState().load()
  }, [])
  return (
    <section className="flex flex-col border-t border-[var(--border)]" aria-label={tl('lab.challenges')}>
      <header className="flex h-10 shrink-0 items-center px-3">
        <h2 className="text-hud-title font-[560] text-[var(--g100)]">{tl('lab.challenges')}</h2>
      </header>
      {unavailable || (list && !list.length) ? (
        <p className="px-3 pb-3 text-hud-sub text-[var(--g500)]">{tl('lab.ch.unavailable')}</p>
      ) : (
        <div className="flex flex-col gap-0.5 px-1.5 pb-2">
          {(list ?? []).map((c) => {
            const st = challengeStatus(c)
            const selected = c.id === current && tab === 'judge'
            return (
              <button
                key={c.id}
                type="button"
                className="lab-row lab-row-inline"
                data-selected={selected ? '' : undefined}
                data-lab-challenge={c.id}
                data-status={st}
                onClick={() => {
                  useLab.getState().setChallenge(c.id)
                  useLab.getState().setTab('judge')
                }}
              >
                <span className={cn('lab-mark', `lab-mark-${st}`)} aria-hidden />
                <span className="min-w-0 flex-1 truncate text-[12px] text-[var(--g200)]">{lang === 'zh' ? c.name_zh : c.name}</span>
                <span className="sr-only">{tl(st === 'passed' ? 'lab.ch.passed' : st === 'failed' ? 'lab.ch.failed' : 'lab.ch.new')}</span>
                <span className={cn('num w-8 shrink-0 text-right text-[11px]', st === 'passed' ? 'font-bold text-[var(--g50)]' : 'text-[var(--g400)]')}>{c.best ? Math.round(c.best.score) : '—'}</span>
              </button>
            )
          })}
        </div>
      )}
    </section>
  )
}

// ---------------------------------------------------------------- editor

function NameField() {
  const lang = useLang()
  const program = useLab((s) => s.program)
  const [text, setText] = React.useState<string | null>(null)
  if (!program) return null
  const name = programName(program, lang)
  if (program.builtin) return <h2 className="min-w-0 truncate text-[13px] font-[560] text-[var(--g50)]">{name}</h2>
  const commit = async () => {
    const next = (text ?? '').trim()
    setText(null)
    if (!next || next === program.name) return
    try {
      await labApi.update(program.id, { name: next })
      const lab = useLab.getState()
      if (lab.program?.id === program.id) useLab.setState({ program: { ...lab.program, name: next, name_zh: next } })
      void lab.refreshList()
    } catch (e) {
      notifyError(e)
    }
  }
  return (
    <Input
      value={text ?? name}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => void commit()}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
      }}
      maxLength={48}
      aria-label={name}
      className="h-7 max-w-[260px] min-w-0 flex-1 border-transparent bg-transparent px-1.5 text-[13px] font-[560] text-[var(--g50)] hover:border-[var(--border)] focus-visible:border-[var(--ring)]"
      data-lab-name=""
    />
  )
}

function ValidationState() {
  const tl = useTL()
  const v = useLab((s) => s.validation)
  const stale = useLab((s) => s.validatedSource !== s.source)
  if (!v || stale) return <span className="text-hud-sub text-[var(--g500)]">{tl('lab.validating')}</span>
  if (v.ok) return <span className="text-hud-sub text-[var(--g400)]" data-lab-valid="">{tl('lab.valid')}</span>
  const n = v.errors.length
  return (
    <span className="text-hud-sub font-bold text-[var(--g50)]" data-lab-problems={n}>
      {n === 1 ? tl('lab.problem') : tl('lab.problems', { n })}
    </span>
  )
}

function EditorPane() {
  const tl = useTL()
  const lang = useLang()
  const author = useAuthorLabel()
  const program = useLab((s) => s.program)
  const source = useLab((s) => s.source)
  const loadSeq = useLab((s) => s.loadSeq)
  const loading = useLab((s) => s.loading)
  const saving = useLab((s) => s.saving)
  const dirty = useLab((s) => isDirty(s))
  const validation = useLab((s) => (s.validatedSource === s.source ? s.validation : null))
  const [cursor, setCursor] = React.useState<[number, number]>([1, 1])
  const root = React.useRef<HTMLDivElement>(null)
  const guard = useGuard()
  const readOnly = !program || program.builtin
  const problems = validation?.errors ?? []
  const first = problems[0]
  const save = React.useCallback(() => void useLab.getState().save(), [])

  // validate the draft 350 ms after the last keystroke (AST only; nothing runs)
  const id = program?.id ?? null
  React.useEffect(() => {
    if (!id) return
    let alive = true
    const timer = setTimeout(() => {
      labApi.validate(id, source).then(
        (v) => alive && useLab.getState().setValidation(source, v),
        () => alive && useLab.getState().setValidation(source, null),
      )
    }, 350)
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [id, source])

  // mod+s outside the editor too (inside it the editor's own keymap saves)
  React.useEffect(() => registerHotkey({ combo: 'mod+s', workspace: 'lab', inText: true, handler: () => void useLab.getState().save() }), [])

  if (!program) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-hud-sub text-[var(--g500)]">
        {loading ? <VoxelLoader size={24} label={tl('lab.loading')} caption={tl('lab.loading')} /> : tl('lab.noProgram')}
      </div>
    )
  }
  return (
    <div className="flex h-full min-h-0 flex-col" ref={root} data-lab-editor-pane={program.id}>
      <header className="flex h-10 shrink-0 items-center gap-2 border-b border-[var(--border)] pr-2 pl-2">
        <NameField />
        <span className="num shrink-0 truncate text-[11px] text-[var(--g500)]">
          {program.builtin ? tl('lab.example') : `${tl('lab.rev', { rev: program.rev })} · ${author(program.author)}`}
        </span>
        <span className="min-w-2 flex-1" />
        <ValidationState />
        {dirty ? (
          <>
            <span className="shrink-0 text-hud-sub text-[var(--g200)]" data-lab-dirty="">
              {tl('lab.unsaved')}
            </span>
            <Button variant="ghost" size="sm" className="text-[var(--g300)]" onClick={() => guard.ask(() => useLab.getState().revert())}>
              {tl('lab.revert')}
            </Button>
          </>
        ) : null}
        {program.builtin ? (
          <Button variant="outline" size="sm" className="gap-1.5" onClick={() => void useLab.getState().duplicate()}>
            <Icon icon="duplicate" />
            {tl('lab.duplicate')}
          </Button>
        ) : (
          <Button variant="outline" size="sm" className="gap-1.5" disabled={!dirty || saving} onClick={save} data-lab-save="">
            <Icon icon="save" />
            {saving ? tl('lab.saving') : tl('lab.save')}
          </Button>
        )}
      </header>
      {program.builtin ? <p className="shrink-0 border-b border-[var(--border)] px-3 py-1.5 text-hud-sub text-[var(--g400)]">{tl('lab.readOnly')}</p> : null}
      <div className="min-h-0 flex-1 overflow-hidden">
        <CodeEditor
          value={source}
          loadSeq={loadSeq}
          readOnly={readOnly}
          problems={problems}
          lang={lang}
          onChange={(s) => useLab.getState().setSource(s)}
          onSave={save}
          onCursor={(l, c) => setCursor([l, c])}
          label={tl('lab.editorLabel')}
        />
      </div>
      <footer className="flex h-7 shrink-0 items-center gap-3 border-t border-[var(--border)] px-3 text-[11px]">
        {first ? (
          <button type="button" className="min-w-0 flex-1 truncate text-left text-[var(--g100)] hover:text-[var(--g50)]" onClick={() => focusLine(root.current, first.line, first.col)} data-lab-first-problem="">
            <span className="num text-[var(--g400)]">{first.line}:{first.col}</span> {lang === 'zh' ? first.message_zh || first.message_en : first.message_en}
          </button>
        ) : (
          <span className="flex-1" />
        )}
        <span className="num shrink-0 text-[var(--g500)]">{tl('lab.cursor', { line: cursor[0], col: cursor[1] })}</span>
      </footer>
    </div>
  )
}

// ---------------------------------------------------------------- right tabs

function RightTabs() {
  const tl = useTL()
  const tab = useLab((s) => s.tab)
  const program = useLab((s) => s.program)
  return (
    <Tabs value={tab} onValueChange={(v) => useLab.getState().setTab(v as LabTab)} className="h-full min-h-0 gap-0">
      <TabsList variant="line" className="h-9 w-full shrink-0 justify-between gap-2 overflow-x-auto rounded-none border-b border-[var(--border)] px-3">
        {LAB_TABS.map((k) => (
          <TabsTrigger key={k} value={k} className="line-tab flex-none px-0" data-lab-tab={k}>
            {tl(TAB_KEYS[k])}
          </TabsTrigger>
        ))}
      </TabsList>
      {program ? (
        <ScrollArea className="min-h-0 flex-1">
          <TabsContent value="params" className="py-3">
            <ParamsTab />
          </TabsContent>
          <TabsContent value="tests" className="py-3">
            <TestsTab />
          </TabsContent>
          <TabsContent value="run" className="py-3">
            <RunTab />
          </TabsContent>
          <TabsContent value="judge" className="py-3">
            <JudgeTab />
          </TabsContent>
          <TabsContent value="duel" className="py-3">
            <DuelTab />
          </TabsContent>
          <TabsContent value="history" className="py-3">
            <HistoryTab />
          </TabsContent>
        </ScrollArea>
      ) : null}
    </Tabs>
  )
}

export function LabWorkspace() {
  const mode = useLayoutMode()
  const compact = mode === 'compact'
  React.useEffect(() => {
    void useLab.getState().refresh()
  }, [])
  return (
    <GuardProvider>
      <div className="lab flex h-full min-w-0 gap-2" data-workspace="lab">
        <aside className={cn('l1 flex shrink-0 flex-col overflow-hidden rounded-[var(--radius)]', compact ? 'w-[216px]' : 'w-[256px]')}>
          <ScrollArea className="min-h-0 flex-1">
            <ProgramList />
            <ChallengeList />
          </ScrollArea>
        </aside>
        <section className="l1 flex min-w-0 flex-1 flex-col overflow-hidden rounded-[var(--radius)]">
          <EditorPane />
        </section>
        <aside className={cn('l1 flex shrink-0 flex-col overflow-hidden rounded-[var(--radius)]', compact ? 'w-[340px]' : 'w-[400px]')} data-lab-tabs="">
          <RightTabs />
        </aside>
      </div>
    </GuardProvider>
  )
}
