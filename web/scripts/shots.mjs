// Review screenshots of the shell with the dev fixture (02 §20 item 1-2): 1440x900, 1280x800, 1100x720 in EN and
// ZH, plus the alert centre, the command palette, the empty fleet and the boot frames. Builds are served by
// `vite preview`; output goes to $SHOTS_DIR (default /tmp/relay-shots). PW_CHROME may point at a Chromium binary.
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, readdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join, resolve } from 'node:path'
import { chromium } from '@playwright/test'

const root = resolve(import.meta.dirname, '..')
const out = process.env.SHOTS_DIR ?? '/tmp/relay-shots'
mkdirSync(out, { recursive: true })

function findChrome() {
  if (process.env.PW_CHROME) return process.env.PW_CHROME
  const base = join(homedir(), '.cache/ms-playwright')
  if (!existsSync(base)) return undefined
  const dirs = readdirSync(base).filter((d) => /^chromium-\d+$/.test(d)).sort().reverse()
  for (const d of dirs) {
    const p = join(base, d, 'chrome-linux64/chrome')
    if (existsSync(p)) return p
  }
  return undefined
}

const PORT = Number(process.env.SHOTS_PORT ?? 4291)
const server = spawn('npx', ['vite', 'preview', '--port', String(PORT), '--strictPort'], { cwd: root, stdio: 'ignore' })
const url = (q) => `http://127.0.0.1:${PORT}/?${q}`

async function waitServer() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(url(''))
      if (r.ok && (await r.text()).includes('<title>ANet Relay</title>')) return
    } catch {
      /* not yet */
    }
    await new Promise((r) => setTimeout(r, 250))
  }
  throw new Error('vite preview did not start')
}

const only = process.argv.slice(2)
const want = (name) => only.length === 0 || only.some((o) => name.includes(o))

try {
  await waitServer()
  const browser = await chromium.launch({ executablePath: findChrome(), args: ['--force-color-profile=srgb'] })
  const shoot = async (name, { w, h, q, after, waitBoot = true, delay = 500 }) => {
    if (!want(name)) return
    const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 1 })
    await page.goto(url(q))
    if (waitBoot) await page.waitForSelector('#boot-mask', { state: 'detached', timeout: 30000 })
    if (after) await after(page)
    await page.waitForTimeout(delay)
    await page.screenshot({ path: join(out, `${name}.png`) })
    await page.close()
    console.log(name)
  }
  const sizes = [
    [1440, 900],
    [1280, 800],
    [1100, 720],
  ]
  for (const [w, h] of sizes) {
    for (const lang of ['en', 'zh']) {
      await shoot(`foundation-${w}x${h}-${lang}`, { w, h, q: `fixture=busy&lang=${lang}&motion=off` })
    }
  }
  for (const lang of ['en', 'zh']) {
    await shoot(`foundation-alerts-${lang}`, {
      w: 1440, h: 900, q: `fixture=alerts&lang=${lang}&motion=off`,
      after: (p) => p.click('[aria-label="Open the alert centre"], [aria-label="打开告警中心"]'),
    })
    await shoot(`foundation-palette-${lang}`, {
      w: 1440, h: 900, q: `fixture=busy&lang=${lang}&motion=off`,
      after: (p) => p.keyboard.press('Control+K'),
    })
    await shoot(`foundation-empty-${lang}`, { w: 1440, h: 900, q: `fixture=empty&lang=${lang}&motion=off` })
  }
  await shoot('foundation-timeline-expanded-1280x800-en', {
    w: 1280, h: 800, q: 'fixture=busy&lang=en&motion=off',
    after: (p) => p.click('[aria-label="Expand timeline"]'),
  })
  await shoot('foundation-primitives', {
    w: 1440, h: 900, q: 'fixture=busy&lang=en&motion=off&gallery=1',
    after: async (p) => {
      await p.getByRole('button', { name: 'Release' }).first().click()
      await p.waitForTimeout(400)
    },
  })
  await shoot('foundation-compact-sheet-en', {
    w: 1100, h: 720, q: 'fixture=busy&lang=en&motion=off',
    after: (p) => p.keyboard.press('ArrowDown'),
  })
  await shoot('foundation-boot-0300ms', { w: 1440, h: 900, q: 'fixture=busy&lang=en', waitBoot: false, delay: 300 })
  await shoot('foundation-boot-0900ms', { w: 1440, h: 900, q: 'fixture=busy&lang=en', waitBoot: false, delay: 900 })
  await shoot('foundation-boot-failed', { w: 1440, h: 900, q: 'api=http://127.0.0.1:9&lang=en', waitBoot: false, delay: 21000 })
  await shoot('foundation-boot-slow-zh', { w: 1440, h: 900, q: 'api=http://127.0.0.1:9&lang=zh', waitBoot: false, delay: 8600 })
  await browser.close()
} finally {
  server.kill()
}
