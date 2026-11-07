// Renderer entry: motion tier, boot watchdog, connection (or dev fixture), keyboard layer, timeline feed, React.
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@/styles/index.css'
import { env } from '@/api'
import { App } from '@/app/App'
import { startBoot } from '@/app/boot'
import { startConnection } from '@/app/connection'
import { installKeyboard } from '@/shell/keyboard'
import { bindTimeline } from '@/shell/timeline/feed'
import { initMotionTier } from '@/ui/motion/tier'
import '@/i18n'

initMotionTier(location.search)
startBoot()
bindTimeline()
installKeyboard()

const fixture = env().fixture
if (fixture) {
  void import('@/dev/fixtures').then((m) => m.startFixture(fixture))
} else {
  startConnection()
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
