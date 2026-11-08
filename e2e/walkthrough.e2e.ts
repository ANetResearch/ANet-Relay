// Visual walkthrough (02 §20): every surface and state at 1440 x 900, 1280 x 800 and 1100 x 720 in English and Chinese,
// against the real app, backend, simulators and fake aircraft. Screenshots go to e2e/artifacts/walkthrough/<size>-<lang>/
// together with audit.json, which lists clipped text and anything outside the window for each shot.
//
//   RELAY_WALKTHROUGH=1 npx playwright test --config e2e/playwright.config.ts walkthrough   (npm run test:walkthrough)
//   RELAY_WALKTHROUGH=1440-en,1100-zh ...                                                     (a subset)
//   RELAY_WALKTHROUGH_DIR=final RELAY_STAGE_GL=hq ...   (release screenshots: e2e/artifacts/final/<size>-<lang>/, with the
//                                                          full-quality stage even on a software WebGL renderer)
import dgram from 'node:dgram'
import fs from 'node:fs'
import net from 'node:net'
import path from 'node:path'
import { execFileSync, type ChildProcess } from 'node:child_process'
import { expect, test, type Page } from '@playwright/test'
import {
  ARTIFACTS, freeTcpPort, freeTcpPortOn, freeUdpPort, launchApp, rest, startFake, startPython, stopProcess, until, waitForLine,
  type RelayApp,
} from './harness'

const SIZES = [
  { w: 1440, h: 900 },
  { w: 1280, h: 800 },
  { w: 1100, h: 720 },
] as const
const LANGS = ['en', 'zh'] as const
const wanted = (process.env.RELAY_WALKTHROUGH ?? '').trim()
const enabled = wanted !== ''
const pick = (key: string) => wanted === '1' || wanted === 'all' || wanted.split(',').includes(key)
const OUT = (process.env.RELAY_WALKTHROUGH_DIR ?? 'walkthrough').replace(/[^\w-]/g, '') || 'walkthrough'
const STAGE_GL = process.env.RELAY_STAGE_GL ?? ''

type Lang = (typeof LANGS)[number]
const L = (lang: Lang, en: string | RegExp, zh: string | RegExp) => (lang === 'en' ? en : zh)

interface Audit {
  shot: string
  hscroll: boolean
  clipped: { path: string; text: string; over: number }[]
  outside: { path: string; text: string }[]
}

/** Clipped leaf text (scrollWidth beyond the box without an ellipsis) and boxes outside the window. */
async function audit(page: Page, shot: string): Promise<Audit> {
  const res = await page.evaluate(() => {
    const pathOf = (el: Element) => {
      const parts: string[] = []
      let e: Element | null = el
      while (e && parts.length < 4 && e !== document.body) {
        const data = [...e.attributes].find((a) => a.name.startsWith('data-') && !a.name.startsWith('data-slot') && a.name !== 'data-state')
        parts.unshift(`${e.tagName.toLowerCase()}${data ? `[${data.name}${data.value ? `=${data.value.slice(0, 16)}` : ''}]` : ''}`)
        e = e.parentElement
      }
      return parts.join(' > ')
    }
    const clipped: { path: string; text: string; over: number }[] = []
    const outside: { path: string; text: string }[] = []
    for (const el of document.querySelectorAll<HTMLElement>('body *')) {
      if (el.closest('canvas, svg, [aria-hidden="true"], .invisible')) continue
      const cs = getComputedStyle(el)
      if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) continue
      const r = el.getBoundingClientRect()
      if (r.width <= 1 || r.height <= 1) continue
      const text = (el.innerText || '').trim()
      const leaf = el.childElementCount === 0 && text.length > 0
      if (leaf && el.scrollWidth > el.clientWidth + 1 && cs.textOverflow !== 'ellipsis' && (cs.overflowX === 'hidden' || cs.overflowX === 'clip')) {
        clipped.push({ path: pathOf(el), text: text.slice(0, 60), over: el.scrollWidth - el.clientWidth })
      }
      if (leaf && (r.right > innerWidth + 1 || r.bottom > innerHeight + 1 || r.left < -1 || r.top < -1)) {
        // inside a scroll container the box may legitimately sit outside the window
        let p: HTMLElement | null = el.parentElement
        let scrolled = false
        while (p) {
          const s = getComputedStyle(p)
          if (/(auto|scroll)/.test(s.overflowY + s.overflowX) || p.hasAttribute('data-radix-scroll-area-viewport') || p.getAttribute('data-slot') === 'scroll-area-viewport') {
            scrolled = true
            break
          }
          p = p.parentElement
        }
        if (!scrolled) outside.push({ path: pathOf(el), text: text.slice(0, 60) })
      }
    }
    return { hscroll: document.documentElement.scrollWidth > innerWidth + 1, clipped, outside }
  })
  return { shot, ...res }
}

function killBackend(httpPort: number): void {
  // the shell's backend: the process listening on its HTTP port
  const out = execFileSync('sh', ['-c', `ss -ltnpH 'sport = :${httpPort}' || true`], { encoding: 'utf8' })
  const m = out.match(/pid=(\d+)/)
  if (m) process.kill(Number(m[1]), 'SIGKILL')
}

for (const size of SIZES) {
  for (const lang of LANGS) {
    const key = `${size.w}-${lang}`
    test(`walkthrough ${size.w} x ${size.h} ${lang}`, async () => {
      test.skip(!enabled || !pick(key), 'set RELAY_WALKTHROUGH=1 (or a list such as 1440-en) to capture the walkthrough')
      const dir = path.join(ARTIFACTS, OUT, key)
      fs.rmSync(dir, { recursive: true, force: true })
      fs.mkdirSync(dir, { recursive: true })
      const audits: Audit[] = []
      let n = 0
      const shot = async (page: Page, name: string, settle = 1000) => {
        n += 1
        await page.waitForTimeout(settle)
        // the software-rendered stage starves the main thread: let finite CSS transitions and animations land first
        await page
          .waitForFunction(() => document.getAnimations().every((a) => a.playState !== 'running' || a.effect?.getComputedTiming().endTime === Infinity), undefined, { timeout: 4000, polling: 100 })
          .catch(() => {})
        const file = `${String(n).padStart(2, '0')}-${name}.png`
        await page.screenshot({ path: path.join(dir, file) })
        audits.push(await audit(page, file))
      }
      const children: ChildProcess[] = []
      const apps: RelayApp[] = []
      const launch = async (o: Parameters<typeof launchApp>[0] = {}) => {
        const r = await launchApp({ size: { width: size.w, height: size.h }, ...o })
        apps.push(r)
        if (lang === 'zh' || STAGE_GL) {
          await r.page.evaluate(({ zh, gl }) => {
            if (zh) localStorage.setItem('relay.lang', 'zh')
            if (gl) localStorage.setItem('relay.stage.gl', gl)
          }, { zh: lang === 'zh', gl: STAGE_GL })
          await r.page.reload()
        }
        return r
      }
      const closeAll = async () => {
        for (const c of children.splice(0).reverse()) await stopProcess(c)
        for (const a of apps.splice(0)) await a.close()
      }

      try {
        // ---------------------------------------------------------------- boot states with a stand-in backend
        {
          const r = await launch({ backendCmd: ['/bin/sleep', '60'] })
          await shot(r.page, 'boot-starting', 600)
          await r.page.waitForTimeout(8200)
          await shot(r.page, 'boot-slow', 0)
          await r.page.waitForTimeout(12500)
          await shot(r.page, 'boot-failed', 300)
          await closeAll()
        }
        {
          // every free port of the shell's 8765-8775 range is held, RELAY_HTTP_PORT unset
          const blockers: net.Server[] = []
          for (let p = 8765; p <= 8775; p++) {
            const srv = net.createServer()
            const ok = await new Promise<boolean>((res) => {
              srv.once('error', () => res(false))
              srv.listen(p, '127.0.0.1', () => res(true))
            })
            if (ok) blockers.push(srv)
          }
          try {
            const r = await launch({ httpPort: null })
            await shot(r.page, 'boot-ports-busy', 3000)
          } finally {
            await closeAll()
            for (const b of blockers) b.close()
          }
        }
        {
          // an explicit RELAY_HTTP_PORT that another program holds
          const port = await freeTcpPort()
          const blocker = net.createServer()
          await new Promise<void>((res) => blocker.listen(port, '127.0.0.1', () => res()))
          try {
            const r = await launch({ httpPort: port })
            await expect(r.page.getByText(L(lang, `Port ${port} is in use`, `端口 ${port} 已被占用`))).toBeVisible({ timeout: 15000 })
            await shot(r.page, 'boot-port-busy', 300)
          } finally {
            await closeAll()
            blocker.close()
          }
        }

        // ---------------------------------------------------------------- live app
        const hosts = ['127.0.0.2', '127.0.0.3']
        const bridgePort = await freeUdpPort(hosts)
        const httpPort = await freeTcpPortOn(hosts)
        const listenPort = await freeUdpPort()
        const appHttp = await freeTcpPort()
        const r = await launch({ bridgePort, listenPort, httpPort: appHttp, scan: `127.0.0.0/30:${httpPort}` })
        await until(async () => (await fetch(`${r.apiBase}/health`)).ok, 'backend health', 30000)
        // these scripted agents fly with skills: agent flight level full (06 §5; the default sticks refuses them)
        await rest(r, 'PUT', '/settings', { agent_flight_level: 'full' })
        const { page } = r
        const card = (id: string) => page.locator(`[data-fleet-card="${id}"]`)
        const fleet = async () => rest<{ vehicles: { id: string; label: string; armed: boolean; controller: string; host: string | null; skill: { id: number; state: string } | null; position: { x: number; y: number; z: number } | null }[]; safety_volume: { x: number; y: number; z: number } }>(r, 'GET', '/fleet', undefined, 'none')
        // below 1200 px the inspector is a sheet over the workspace (02 §20): close it before reaching the fleet
        const sheet = page.locator('[data-inspector="sheet"]')
        const closeSheet = async () => {
          if (size.w >= 1200 || !(await sheet.isVisible().catch(() => false))) return
          await page.keyboard.press('Escape')
          await expect(sheet).toBeHidden()
        }
        const select = async (id: string) => {
          await closeSheet()
          await card(id).click()
          if (size.w < 1200) {
            if (!(await sheet.isVisible().catch(() => false))) await page.keyboard.press('Enter')
            await expect(sheet).toBeVisible()
          }
        }
        const tab = (i: number) => page.locator('[data-inspector-header] ~ * [role="tab"], [data-inspector-header] + * [role="tab"]').nth(i)
        await expect(page.locator('[data-fleet-panel]')).toBeVisible({ timeout: 30000 })
        await shot(page, 'fly-empty', 2500)

        // connect dialog
        await page.locator('[data-fleet-panel] header button').last().click()
        await shot(page, 'connect-scan')
        await page.getByRole('tab', { name: L(lang, 'Address', '地址') }).click()
        const addr = page.locator('[role="dialog"] input').first()
        await addr.fill('10.7.7')
        await page.getByRole('button', { name: L(lang, 'Connect', '连接') }).last().click().catch(() => {})
        await shot(page, 'connect-address-invalid')
        await page.getByRole('tab', { name: L(lang, 'Set up a new bridge', '配置新数传') }).click()
        await shot(page, 'connect-bridge-step1')
        await page.keyboard.press('Escape')

        // fleet: four simulators, two fake aircraft (one without a position estimate), one agent
        await rest(r, 'POST', '/sims', { count: 4 })
        children.push(await startFake({ autopilot: 'ardupilot', host: '127.0.0.2', udpPort: bridgePort, httpPort, gcs: `127.0.0.1:${listenPort}` }))
        children.push(await startFake({ autopilot: 'px4', host: '127.0.0.3', udpPort: bridgePort, httpPort, gcs: `127.0.0.1:${listenPort}`, noPosition: true }))
        const agent = startPython(
          [
            'import os, time',
            'from anet_relay_client import RelayClient',
            'c = RelayClient(os.environ["RELAY_BASE"])',
            'c.open_session("harness-a")',
            'c.claim("sim-2"); c.arm("sim-2"); c.takeoff("sim-2", alt=1.0); c.hold("sim-2")',
            'print("hovering", flush=True)',
            'time.sleep(3600)',
          ].join('\n'),
          { RELAY_BASE: r.apiBase.replace(/\/api\/v1$/, '') },
        )
        children.push(agent)
        await until(async () => (await fleet()).vehicles.filter((v) => v.host?.startsWith('127.0.0.')).length === 2, 'two fake aircraft', 30000)
        await waitForLine(agent, /^hovering/, 60000)
        const real = (await fleet()).vehicles.filter((v) => v.host?.startsWith('127.0.0.')).sort((a, b) => a.host!.localeCompare(b.host!))
        await select('sim-2')
        await shot(page, 'fly-fleet-agent-selected', 1200)

        // inspector tabs on sim-1
        await select('sim-1')
        await shot(page, 'inspector-overview')
        await tab(2).click()
        await shot(page, 'inspector-telemetry')
        await page.locator('[data-inspector-telemetry] button').nth(1).click()
        await shot(page, 'inspector-raw-mavlink', 1500)
        await tab(3).click()
        await shot(page, 'inspector-parameters', 2500)
        await tab(4).click()
        await shot(page, 'inspector-log')
        await tab(1).click()
        await shot(page, 'inspector-control')

        // keyboard possession, arm, climb
        await page.locator('[data-possess]').click()
        await page.getByRole('combobox', { name: L(lang, 'Mode', '模式') }).click()
        await page.getByRole('option', { name: 'ALT_HOLD' }).click()
        await page.getByRole('button', { name: L(lang, 'Arm', '解锁'), exact: true }).click()
        await until(async () => (await fleet()).vehicles.find((v) => v.id === 'sim-1')?.armed, 'sim-1 armed')
        await closeSheet()
        await page.locator('[data-stage]').click({ position: { x: 30, y: 160 } })
        await page.keyboard.down('r')
        await until(async () => ((await fleet()).vehicles.find((v) => v.id === 'sim-1')?.position?.z ?? 0) > 0.6, 'sim-1 climbing', 15000, 100)
        await page.keyboard.up('r')
        // back to the stick centre: altitude hold
        for (let i = 0; i < 60; i++) {
          const thr = (await rest<{ vehicles: { id: string; sticks: { throttle: number } | null }[] }>(r, 'GET', '/fleet', undefined, 'none')).vehicles.find((v) => v.id === 'sim-1')?.sticks?.throttle ?? 0
          if (thr <= 0.54) break
          await page.keyboard.down('f')
          await page.waitForTimeout(40)
          await page.keyboard.up('f')
        }
        await shot(page, 'fly-possessed-keyboard')

        // agent aircraft: possess (claim suspended), then hand back
        await select('sim-2')
        await tab(1).click()
        await shot(page, 'control-agent-flying')
        await page.locator('[data-possess]').click()
        await until(async () => (await fleet()).vehicles.find((v) => v.id === 'sim-2')?.controller === 'human', 'sim-2 possessed')
        await shot(page, 'control-claim-suspended', 900)
        await page.locator('[data-release]').click()
        await until(async () => (await fleet()).vehicles.find((v) => v.id === 'sim-2')?.controller === 'agent', 'handed back')
        await shot(page, 'handback-toast', 900)
        await closeSheet()

        // real aircraft: preflight and type-to-arm; no-position aircraft on its pad slot
        await select(real[0].id)
        await tab(1).click()
        await page.locator('[data-possess]').click()
        await shot(page, 'control-real-preflight-blocked')
        await rest(r, 'POST', `/vehicles/${real[0].id}/confirm`, { item: 'signs' })
        await rest(r, 'POST', `/vehicles/${real[0].id}/confirm`, { item: 'props' })
        await page.getByRole('button', { name: L(lang, 'Arm', '解锁'), exact: true }).click()
        await shot(page, 'control-real-type-arm')
        await page.locator('[data-release]').click()
        await closeSheet()
        await select(real[1].id)
        await tab(0).click()
        await shot(page, 'overview-real-no-position')
        await closeSheet()

        // context menu, command palette, alert centre
        await card('sim-3').click({ button: 'right' })
        await shot(page, 'fleet-context-menu')
        await page.keyboard.press('Escape')
        await page.keyboard.press('Control+k')
        await shot(page, 'command-palette')
        await page.keyboard.press('Escape')
        await page.getByRole('button', { name: L(lang, 'Open the alert centre', '打开告警中心') }).click()
        await shot(page, 'alert-centre')
        await page.keyboard.press('Escape')

        // gamepad: hint card, pads popover
        await page.evaluate(() => {
          const buttons = Array.from({ length: 17 }, () => ({ pressed: false, touched: false, value: 0 }))
          const pad = { id: 'DualSense Wireless Controller (STANDARD GAMEPAD Vendor: 054c Product: 0ce6)', index: 0, connected: true, mapping: 'standard', axes: [0, 0, 0, 0], buttons, timestamp: performance.now() }
          Object.defineProperty(navigator, 'getGamepads', { configurable: true, value: () => [pad, null, null, null] })
          window.dispatchEvent(Object.assign(new Event('gamepadconnected'), { gamepad: pad }))
        })
        await expect(page.locator('[data-pad-hints]')).toBeVisible()
        await shot(page, 'gamepad-hint', 900)
        await page.locator('[data-pads]').click()
        // the first click can land while the hint card is still entering; retry once
        if (!(await page.locator('[data-pads-popover]').isVisible().catch(() => false))) {
          await page.waitForTimeout(500)
          if (!(await page.locator('[data-pads-popover]').isVisible().catch(() => false))) await page.locator('[data-pads]').click()
        }
        await expect(page.locator('[data-pads-popover]')).toBeVisible()
        await shot(page, 'pads-popover')
        await page.keyboard.press('Escape')
        for (const v of (await fleet()).vehicles) if (v.controller === 'human') await rest(r, 'POST', `/vehicles/${v.id}/release`)

        // ring course flown by the operator (goto skills while possessing): HUD, then the completion card
        await rest(r, 'POST', '/vehicles/sim-4/possess', { input: 'keyboard' })
        await select('sim-4')
        await closeSheet()
        const started = (await rest<{ course: { rings: { c: [number, number, number]; yaw_deg: number }[]; origin: number[] } }>(r, 'POST', '/vehicles/sim-4/course/start', { course_id: 'cage-loop' })).course
        const vol = (await fleet()).safety_volume
        const [ox, oy, oz] = started.origin
        const clamp = (x: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, x))
        const inside = (x: number, y: number, z: number) => ({
          x: clamp(x, ox - vol.x / 2 + 0.1, ox + vol.x / 2 - 0.1), y: clamp(y, oy - vol.y / 2 + 0.1, oy + vol.y / 2 - 0.1), z: clamp(z, oz + 0.3, oz + vol.z - 0.1),
        })
        const skill = async (name: string, body: Record<string, number>) => {
          const s = (await rest<{ skill: { id: number } }>(r, 'POST', `/vehicles/sim-4/skills/${name}`, body)).skill
          await until(async () => {
            const v = (await fleet()).vehicles.find((x) => x.id === 'sim-4')
            return v?.skill?.id === s.id && v.skill.state !== 'running'
          }, `${name} done`, 60000)
        }
        await rest(r, 'POST', '/vehicles/sim-4/arm', { arm: true })
        await skill('takeoff', { alt: started.rings[0].c[2] - oz })
        for (const [i, ring] of started.rings.entries()) {
          const nx = Math.sin((ring.yaw_deg * Math.PI) / 180)
          const ny = Math.cos((ring.yaw_deg * Math.PI) / 180)
          const a = inside(ring.c[0] - 0.35 * nx, ring.c[1] - 0.35 * ny, ring.c[2])
          const b = inside(ring.c[0] + 0.35 * nx, ring.c[1] + 0.35 * ny, ring.c[2])
          await skill('goto', { ...a, yaw: ring.yaw_deg })
          await skill('goto', { ...b, yaw: ring.yaw_deg })
          if (i === 1) await shot(page, 'course-hud', 200)
        }
        await expect(page.locator('[role="dialog"], [data-course-finish]').first()).toBeVisible({ timeout: 10000 }).catch(() => {})
        await shot(page, 'course-finish-card', 900)
        await page.keyboard.press('Escape')
        await rest(r, 'POST', '/vehicles/sim-4/skills/land', {})
        await rest(r, 'POST', '/vehicles/sim-4/release')

        // timeline collapsed
        await page.keyboard.press('Alt+Control+t')
        await shot(page, 'timeline-collapsed')
        await page.keyboard.press('Alt+Control+t')

        // Agents and Records
        await page.keyboard.press('2')
        await shot(page, 'agents', 900)
        await page.keyboard.press('3')
        await shot(page, 'records-list', 900)
        await page.locator('[data-record]').filter({ hasText: 'SIM-4' }).first().click()
        await shot(page, 'records-session', 1500)
        await page.getByRole('button', { name: L(lang, 'Play', '播放'), exact: true }).click()
        await shot(page, 'records-replay', 1500)
        await page.keyboard.press('1')

        // settings, every section
        await page.keyboard.press('Control+,')
        const sections = page.locator('[role="dialog"] nav button')
        const count = await sections.count()
        for (let i = 0; i < count; i++) {
          await sections.nth(i).click()
          await shot(page, `settings-${i + 1}`)
        }
        await page.keyboard.press('Escape')

        // stop all from inside a dialog (02 §20 item 5)
        await page.locator('[data-fleet-panel] header button').last().click()
        await page.keyboard.press('Control+.')
        await shot(page, 'stop-all-in-dialog', 900)
        await page.keyboard.press('Escape')

        // backend lost while running
        killBackend(appHttp)
        await shot(page, 'backend-lost', 2500)
        await closeAll()

        // critical station alert (the UDP listen port is held by another program) and a full fleet of 8 sims
        {
          const holder = dgram.createSocket('udp4')
          const port = await freeUdpPort()
          await new Promise<void>((res) => holder.bind(port, '0.0.0.0', () => res()))
          try {
            const f = await launch({ listenPort: port })
            await rest(f, 'POST', '/sims', { count: 8 })
            await until(async () => (await rest<{ vehicles: unknown[] }>(f, 'GET', '/fleet')).vehicles.length === 8, 'eight sims', 30000)
            await expect(f.page.locator('[data-alert-bar]')).toBeVisible({ timeout: 30000 })
            await expect(f.page.locator('[data-fleet-card]')).toHaveCount(8, { timeout: 30000 })
            await shot(f.page, 'alert-bar-critical', 1500)
            await f.page.getByRole('button', { name: L(lang, 'Open the alert centre', '打开告警中心') }).click()
            await shot(f.page, 'alert-centre-critical')
            await f.page.keyboard.press('Escape')
            await f.page.locator('[data-fleet-panel] [data-fleet-card]').last().scrollIntoViewIfNeeded()
            await shot(f.page, 'fleet-full')
          } finally {
            await closeAll()
            holder.close()
          }
        }
      } finally {
        await closeAll()
        fs.writeFileSync(path.join(dir, 'audit.json'), JSON.stringify(audits, null, 1))
      }
    })
  }
}
