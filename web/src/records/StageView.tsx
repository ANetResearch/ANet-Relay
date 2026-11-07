// Replay viewport (02 §13): mounts the stage while the replay store is active. The stage reads useReplay in its frame
// loop (CONTRACTS.md §5); an error inside the 3D scene is contained here so the charts keep working.
import * as React from 'react'
import { Stage } from '@/stage/Stage'
import { t8 } from '@/fleet/copy'

class Contain extends React.Component<{ children: React.ReactNode }, { failed: boolean }> {
  override state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  override render() {
    if (this.state.failed) {
      return <div className="flex h-full items-center justify-center px-4 text-center text-hud-sub text-[var(--g500)]">{t8('value.reason.noData')}</div>
    }
    return this.props.children
  }
}

export function StageView() {
  return (
    <div className="relative h-full overflow-hidden rounded-[var(--radius)] bg-[var(--g950)]" data-replay-stage="">
      <Contain>
        <Stage />
      </Contain>
    </div>
  )
}
