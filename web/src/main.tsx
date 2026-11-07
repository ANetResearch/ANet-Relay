// Renderer entry: motion tier, boot watchdog, connection (or dev fixture), keyboard layer, timeline feed, React.
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@/styles/index.css'
import { env } from '@/api'
import { App } from '@/app/App'
import { startBoot } from '@/app/boot'
import { startConnection } from '@/app/connection'
import { startUiReporter } from '@/appctl'
import { CopilotWindow } from '@/copilot/CopilotWindow'
import { installKeyboard } from '@/shell/keyboard'
import { bindTimeline } from '@/shell/timeline/feed'
import { initMotionTier } from '@/ui/motion/tier'
import '@/i18n'

initMotionTier(location.search)

// ?window=copilot: the floating Copilot window (04 §6.1) renders only the conversation; it has no boot sequence,
// stream connection or keyboard layer of its own.
const copilotWindow = new URLSearchParams(location.search).get('window') === 'copilot'

if (copilotWindow) {
  document.documentElement.dataset.window = 'copilot'
  document.getElementById('boot-mask')?.remove()
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <CopilotWindow />
    </StrictMode>,
  )
} else {
  startBoot()
  bindTimeline()
  installKeyboard()

  const fixture = env().fixture
  if (fixture) {
    void import('@/dev/fixtures').then((m) => m.startFixture(fixture))
  } else {
    startUiReporter()
    startConnection()
  }

  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}
