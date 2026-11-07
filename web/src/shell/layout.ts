// Window-size layout mode (02 §4.1): wide >= 1400 px, medium 1200-1399 (1280 x 800), compact < 1200 (1100 x 720).
import * as React from 'react'

export type LayoutMode = 'wide' | 'medium' | 'compact'

export function layoutModeFor(width: number): LayoutMode {
  if (width < 1200) return 'compact'
  if (width < 1400) return 'medium'
  return 'wide'
}

export function useLayoutMode(): LayoutMode {
  const [mode, setMode] = React.useState(() => layoutModeFor(window.innerWidth))
  React.useEffect(() => {
    const on = () => setMode(layoutModeFor(window.innerWidth))
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [])
  return mode
}
