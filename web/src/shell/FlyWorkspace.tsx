// Fly workspace (02 §4.1): fleet panel | stage | inspector. Panels are resizable (fleet 248-360, inspector
// 320-440, double-click a gutter to reset) and collapsible to a 28 px strip (⌥⌘B / ⌥⌘I). Below 1200 px the inspector
// stays folded and opens as a sheet over the stage when an aircraft is selected.
import * as React from 'react'
import type { PanelImperativeHandle } from 'react-resizable-panels'
import { useT } from '@/i18n'
import { cn } from '@/lib/utils'
import { useBoot } from '@/app/boot'
import { useSelectedId } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/ui/components/ui/resizable'
import { Sheet, SheetContent, SheetTitle } from '@/ui/components/ui/sheet'
import { StateIcon } from '@/ui/icons'
import { FleetPanel } from '@/fleet/FleetPanel'
import { Inspector } from '@/inspector/Inspector'
import { Stage } from '@/stage/Stage'
import { CourseLayer } from '@/course/CourseLayer'
import { ArenaLayer } from '@/arena/ArenaLayer'
import { CourseFinishCard, CourseHud } from '@/course/CourseHud'
import { VideoPip } from '@/video/VideoPip'
import { TraceLayer } from '@/lab/TraceLayer' // WP-3
import { TraceReplayBar } from '@/lab/TraceReplayBar' // WP-3
import { useLayoutMode, type LayoutMode } from './layout'

const SIZES: Record<LayoutMode, { fleet: number; inspector: number }> = {
  wide: { fleet: 296, inspector: 360 },
  medium: { fleet: 264, inspector: 336 },
  compact: { fleet: 264, inspector: 336 },
}
const STRIP = 28

function Strip({ side, label, onOpen }: { side: 'left' | 'right'; label: string; onOpen(): void }) {
  return (
    <button type="button" className="strip" onClick={onOpen} aria-label={label} title={label}>
      <StateIcon icon={side === 'left' ? 'panel.left' : 'panel.right'} alt className="size-3.5" />
      <span className="strip-label">{label}</span>
    </button>
  )
}

function useEnter(i: number): { className: string; style: React.CSSProperties } {
  const revealed = useBoot((s) => s.revealed)
  return { className: revealed ? 'enter' : '', style: { '--enter-i': i } as React.CSSProperties }
}

/** Stage frame: L0, no border, rounded; HUD overlays from other work packages mount here. */
function StageFrame() {
  const selected = useSelectedId()
  return (
    <div className="relative h-full overflow-hidden rounded-[var(--radius)] bg-[var(--g950)]" data-stage-frame="">
      <Stage>
        <CourseLayer />
        <ArenaLayer />
        <TraceLayer /> {/* WP-3: evaluation trace replay (06 §7) */}
      </Stage>
      <div className="pointer-events-none absolute inset-0">
        <div className="pointer-events-auto absolute top-4 left-1/2 -translate-x-1/2">
          <CourseHud vehicleId={selected} />
        </div>
        <div className="pointer-events-auto absolute right-4 bottom-4">
          <VideoPip vehicleId={selected} />
        </div>
        <div className="pointer-events-auto absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2">
          <CourseFinishCard />
        </div>
        {/* WP-3: evaluation trace replay controls, bottom centre (the HUD strip is hidden while replaying) */}
        <div className="absolute inset-x-0 bottom-4 flex justify-center">
          <TraceReplayBar />
        </div>
      </div>
    </div>
  )
}

export function FlyWorkspace() {
  const t = useT()
  const mode = useLayoutMode()
  const size = SIZES[mode]
  const fleetCollapsed = useUi((s) => s.fleetCollapsed)
  const inspectorCollapsed = useUi((s) => s.inspectorCollapsed)
  const toggleFleet = useUi((s) => s.toggleFleet)
  const toggleInspector = useUi((s) => s.toggleInspector)
  const sheetOpen = useUi((s) => s.inspectorSheetOpen)
  const setSheet = useUi((s) => s.setInspectorSheet)
  const selected = useSelectedId()
  const fleetRef = React.useRef<PanelImperativeHandle | null>(null)
  const inspRef = React.useRef<PanelImperativeHandle | null>(null)
  const compact = mode === 'compact'
  const enterFleet = useEnter(0)
  const enterStage = useEnter(1)
  const enterInsp = useEnter(2)

  // store -> panels
  React.useEffect(() => {
    const p = fleetRef.current
    if (!p) return
    if (fleetCollapsed && !p.isCollapsed()) p.collapse()
    if (!fleetCollapsed && p.isCollapsed()) p.expand()
  }, [fleetCollapsed])
  React.useEffect(() => {
    const p = inspRef.current
    if (!p) return
    const want = compact || inspectorCollapsed
    if (want && !p.isCollapsed()) p.collapse()
    if (!want && p.isCollapsed()) p.expand()
  }, [inspectorCollapsed, compact])

  // compact: selecting an aircraft opens the inspector sheet (02 §4.1)
  const prevSel = React.useRef(selected)
  React.useEffect(() => {
    if (compact && selected && selected !== prevSel.current) setSheet(true)
    prevSel.current = selected
  }, [selected, compact, setSheet])
  React.useEffect(() => {
    if (!compact && sheetOpen) setSheet(false)
  }, [compact, sheetOpen, setSheet])

  // panels -> store (dragging a panel below its minimum folds it)
  const onFleetResize = React.useCallback((s: { inPixels: number }) => {
    const folded = s.inPixels <= STRIP + 1
    if (folded !== useUi.getState().fleetCollapsed) useUi.getState().toggleFleet(folded)
  }, [])
  const onInspResize = React.useCallback(
    (s: { inPixels: number }) => {
      if (compact) return
      const folded = s.inPixels <= STRIP + 1
      if (folded !== useUi.getState().inspectorCollapsed) useUi.getState().toggleInspector(folded)
    },
    [compact],
  )

  return (
    <>
      <ResizablePanelGroup orientation="horizontal" className="h-full" id="fly">
        <ResizablePanel
          id="fleet"
          panelRef={fleetRef}
          defaultSize={fleetCollapsed ? STRIP : size.fleet}
          minSize={248}
          maxSize={360}
          collapsible
          collapsedSize={STRIP}
          onResize={onFleetResize}
          className={cn('min-w-0', enterFleet.className)}
          style={enterFleet.style}
        >
          {fleetCollapsed ? (
            <Strip side="left" label={t('fleet.title')} onOpen={() => toggleFleet(false)} />
          ) : (
            <div className="l1 h-full overflow-hidden rounded-[var(--radius)]" data-panel="fleet">
              <FleetPanel />
            </div>
          )}
        </ResizablePanel>
        <ResizableHandle className="gutter bg-transparent" />
        <ResizablePanel id="stage" minSize={360} className={cn('min-w-0', enterStage.className)} style={enterStage.style}>
          <StageFrame />
        </ResizablePanel>
        <ResizableHandle className="gutter bg-transparent" disabled={compact} />
        <ResizablePanel
          id="inspector"
          panelRef={inspRef}
          defaultSize={compact || inspectorCollapsed ? STRIP : size.inspector}
          minSize={320}
          maxSize={440}
          collapsible
          collapsedSize={STRIP}
          onResize={onInspResize}
          className={cn('min-w-0', enterInsp.className)}
          style={enterInsp.style}
        >
          {compact || inspectorCollapsed ? (
            <Strip side="right" label={t('insp.title')} onOpen={() => (compact ? setSheet(true) : toggleInspector(false))} />
          ) : (
            <div className="l1 h-full overflow-hidden rounded-[var(--radius)]" data-panel="inspector">
              <Inspector vehicleId={selected} variant="panel" />
            </div>
          )}
        </ResizablePanel>
      </ResizablePanelGroup>
      {compact ? (
        <Sheet open={sheetOpen} onOpenChange={setSheet}>
          <SheetContent side="right" className="l3 w-[360px] max-w-[calc(100vw-48px)] p-0 sm:max-w-[360px]">
            <SheetTitle className="sr-only">{t('insp.title')}</SheetTitle>
            <Inspector vehicleId={selected} variant="sheet" />
          </SheetContent>
        </Sheet>
      ) : null}
    </>
  )
}
