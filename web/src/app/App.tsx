// Window composition (02 §3): top bar, alert bar, workspace (Fly / Agents / Records), relay timeline, overlays.
import * as React from 'react'
import { useBoot } from './boot'
import { useUi } from '@/store/ui'
import { useT } from '@/i18n'
import { Button } from '@/ui/components/ui/button'
import { TooltipProvider } from '@/ui/components/ui/tooltip'
import { TopBar } from '@/shell/TopBar'
import { AlertBar } from '@/shell/AlertBar'
import { FlyWorkspace } from '@/shell/FlyWorkspace'
import { RelayTimeline } from '@/shell/timeline/RelayTimeline'
import { RelayToaster } from '@/shell/Toasts'
import { CommandPalette } from '@/shell/CommandPalette'
import { BootLayer } from '@/shell/BootLayer'
import { AgentsWorkspace } from '@/agents/AgentsWorkspace'
import { RecordsWorkspace } from '@/records/RecordsWorkspace'
import { ConnectDialog } from '@/connect/ConnectDialog'
import { SettingsDialog } from '@/settings/SettingsDialog'
import { GamepadHints } from '@/control/GamepadHint'
import { InputLayer } from '@/control/InputLayer'
import { AppNotices } from '@/appctl'
import { useCopilotShell } from '@/copilot/CopilotPanel'
import { VoxelWipe } from '@/ui/voxel/VoxelWipe'

const Gallery = React.lazy(() => import('@/dev/Gallery').then((m) => ({ default: m.Gallery })))
const SHOW_GALLERY = typeof location !== 'undefined' && new URLSearchParams(location.search).has('gallery')

/** Contains a render failure inside one workspace; the top bar (Stop all), timeline and dialogs stay usable. */
class ViewBoundary extends React.Component<{ children: React.ReactNode }, { failed: boolean }> {
  override state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  override componentDidCatch(error: unknown) {
    console.error('[relay] view failed', error)
  }
  override render() {
    if (!this.state.failed) return this.props.children
    return <ViewFailed onReload={() => this.setState({ failed: false })} />
  }
}

function ViewFailed({ onReload }: { onReload(): void }) {
  const t = useT()
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center" role="alert">
      <span className="text-hud-title font-[560] text-[var(--g100)]">{t('app.viewFailed')}</span>
      <span className="max-w-[420px] text-hud-sub text-[var(--g400)]">{t('app.viewFailedBody')}</span>
      <Button variant="outline" size="sm" className="mt-2" onClick={onReload}>
        {t('app.reloadView')}
      </Button>
    </div>
  )
}

function Workspace() {
  const ws = useUi((s) => s.workspace)
  const focus = useUi((s) => s.recordsFocus)
  if (SHOW_GALLERY) {
    return (
      <React.Suspense fallback={null}>
        <Gallery />
      </React.Suspense>
    )
  }
  if (ws === 'agents') return <AgentsWorkspace />
  if (ws === 'records') return <RecordsWorkspace focus={focus} />
  return <FlyWorkspace />
}

function Dialogs() {
  const connect = useUi((s) => s.connect)
  const settings = useUi((s) => s.settings)
  const closeConnect = useUi((s) => s.closeConnect)
  const closeSettings = useUi((s) => s.closeSettings)
  return (
    <>
      <ConnectDialog open={connect.open} tab={connect.tab} onOpenChange={(o) => !o && closeConnect()} />
      <SettingsDialog open={settings.open} section={settings.section} onOpenChange={(o) => !o && closeSettings()} />
    </>
  )
}

export function App() {
  const revealed = useBoot((s) => s.revealed)
  const workspace = useUi((s) => s.workspace)
  useCopilotShell()
  return (
    <TooltipProvider delay={400}>
      <RelayToaster>
        <div className="shell" data-revealed={revealed ? '' : undefined}>
          <TopBar />
          <AlertBar />
          <main className="shell-main pb-2">
            <ViewBoundary key={workspace}>
              <Workspace />
            </ViewBoundary>
            <VoxelWipe trigger={workspace} />
          </main>
          <RelayTimeline className={revealed ? 'enter' : ''} />
        </div>
        <CommandPalette />
        <Dialogs />
        <AppNotices />
        <GamepadHints />
        <InputLayer />
        <BootLayer />
      </RelayToaster>
    </TooltipProvider>
  )
}

export const AppMemo = React.memo(App)
