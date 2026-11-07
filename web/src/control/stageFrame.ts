// Locates the Fly workspace's stage frame (shell/FlyWorkspace.tsx marks it with data-stage-frame) so WP7 overlays
// (input notices, video picture-in-picture) can portal into it and position themselves against the stage.
import * as React from 'react'
import { useUi } from '@/store/ui'

/** The stage frame element while the Fly workspace shows it, else null. Re-checked on workspace change and 2 Hz. */
export function useStageFrame(): HTMLElement | null {
  const ws = useUi((s) => s.workspace)
  const [el, setEl] = React.useState<HTMLElement | null>(null)
  React.useEffect(() => {
    const check = () => {
      const node = document.querySelector<HTMLElement>('[data-stage-frame]')
      setEl((cur) => (cur === node && (!cur || cur.isConnected) ? cur : node))
    }
    check()
    const raf = requestAnimationFrame(check)
    const id = setInterval(check, 500)
    return () => {
      cancelAnimationFrame(raf)
      clearInterval(id)
    }
  }, [ws])
  return el
}
