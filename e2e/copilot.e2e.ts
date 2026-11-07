// Copilot end to end (03 §16): settings stored by the shell, the agent in the main process talking to a scripted
// OpenAI-compatible endpoint, a sequence flown on a simulator, and the approval gate for a real aircraft.
import fs from 'node:fs'
import http from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { ARTIFACTS, freeTcpPortOn, freeUdpPort, launchApp, rest, startFake, stopProcess, until, type RelayApp } from './harness'

type Msg = { role: string; content?: unknown; tool_calls?: unknown[] }

/** A minimal Chat Completions endpoint: the first turn calls a tool chosen from the user text, the next one answers. */
function fakeLlm(): Promise<{ url: string; requests: Msg[][]; close(): Promise<void> }> {
  const requests: Msg[][] = []
  const server = http.createServer((req, res) => {
    let body = ''
    req.on('data', (c) => (body += c))
    req.on('end', () => {
      const parsed = JSON.parse(body || '{}') as { messages?: Msg[]; stream?: boolean }
      const messages = parsed.messages ?? []
      requests.push(messages)
      const last = messages[messages.length - 1]
      const text = typeof last?.content === 'string' ? last.content : JSON.stringify(last?.content ?? '')
      const vehicle = /\[Selected aircraft: ([^\]]+)\]/.exec(text)?.[1] ?? 'sim-1'
      let delta: Record<string, unknown>
      let finish: string
      if (last?.role === 'user' && /ready/.test(text)) {
        delta = { role: 'assistant', content: 'ready' }
        finish = 'stop'
      } else if (last?.role === 'user') {
        const call = /plan|sequence|序列|起飞/i.test(text)
          ? { name: 'relay_plan', arguments: JSON.stringify({ vehicle, steps: [{ action: 'takeoff', alt: 1 }, { action: 'hover', seconds: 1 }, { action: 'move', forward: 1 }, { action: 'yaw', deg: 45 }, { action: 'land' }] }) }
          : { name: 'relay_takeoff', arguments: JSON.stringify({ vehicle, alt: 1 }) }
        delta = { role: 'assistant', content: null, tool_calls: [{ index: 0, id: `call_${requests.length}`, type: 'function', function: call }] }
        finish = 'tool_calls'
      } else {
        delta = { role: 'assistant', content: 'Finished. The sequence was flown and the aircraft has landed.' }
        finish = 'stop'
      }
      const chunk = (d: Record<string, unknown>, f: string | null) =>
        `data: ${JSON.stringify({ id: 'cmpl', object: 'chat.completion.chunk', created: 0, model: 'fake', choices: [{ index: 0, delta: d, finish_reason: f }] })}\n\n`
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write(chunk(delta, null))
      res.write(chunk({}, finish))
      res.end('data: [DONE]\n\n')
    })
  })
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address() as { port: number }
      resolve({ url: `http://127.0.0.1:${port}/v1`, requests, close: () => new Promise((r) => server.close(() => r())) })
    })
  })
}

let r: RelayApp

/** The floating Copilot window (04 §6.1) is its own page; the main window stays `r.page`. */
async function copilotPage(app: RelayApp['app']): Promise<Page> {
  const page = await until(() => app.windows().find((w) => w.url().includes('window=copilot')), 'copilot window', 20000)
  await page.waitForLoadState('domcontentloaded')
  return page
}

const copilotWindowInfo = (app: RelayApp['app']) =>
  app.evaluate(({ BrowserWindow }) => {
    const w = BrowserWindow.getAllWindows().find((x) => x.webContents.getURL().includes('window=copilot'))
    if (!w) return null
    return { visible: w.isVisible(), onTop: w.isAlwaysOnTop(), modal: w.isModal(), hasParent: Boolean(w.getParentWindow()), bounds: w.getBounds() }
  })
let llm: Awaited<ReturnType<typeof fakeLlm>>
let fake: Awaited<ReturnType<typeof startFake>> | undefined

test.afterAll(async () => {
  await stopProcess(fake)
  await r?.close()
  await llm?.close()
})

test('copilot: settings, a sequence on a simulator, approval for a real aircraft', async () => {
  llm = await fakeLlm()
  const listen = await freeUdpPort(['0.0.0.0'])
  r = await launchApp({ lang: 'en', size: { width: 1440, height: 900 }, listenPort: listen })
  const { page } = r
  const shots = path.join(ARTIFACTS, 'copilot')

  // settings: endpoint, model and key; the key never comes back to the renderer
  await page.locator('[data-copilot-toggle]').click()
  const cp = await copilotPage(r.app)
  await expect(page.locator('[data-copilot-toggle][aria-pressed="true"]')).toBeVisible()
  await cp.locator('[data-copilot-setup]').click() // the floating window asks the main window for Settings > Copilot
  await page.locator('[data-copilot-base]').fill(llm.url)
  await page.locator('[data-copilot-model]').fill('fake-model')
  await page.locator('[data-copilot-key]').fill('sk-test-0123456789abcd')
  await page.locator('[data-copilot-test]').click()
  await expect(page.locator('[data-copilot-result]')).toContainText('Connected', { timeout: 15000 })
  const cfg = await page.evaluate(() => (window as unknown as { relay: { copilot: { getConfig(): Promise<Record<string, unknown>> } } }).relay.copilot.getConfig())
  expect(cfg).toMatchObject({ baseUrl: llm.url, model: 'fake-model', hasKey: true, keyHint: 'abcd' })
  expect(JSON.stringify(cfg)).not.toContain('sk-test')
  await page.screenshot({ path: path.join(shots, '01-settings.png') })
  await page.keyboard.press('Escape')

  // a sequence on a simulator, flown by the copilot session
  await rest(r, 'POST', '/sims', { count: 1, autopilot: 'px4', position: false })
  await page.locator('[data-fleet-card="sim-1"]').click()
  await expect(cp.getByText('Selected: SIM-1')).toBeVisible({ timeout: 10000 })
  await cp.locator('[data-copilot-input]').fill('Take off, hover, forward 1 m, turn 45 degrees and land as one sequence')
  await cp.keyboard.press('Enter')
  await expect(cp.locator('[data-tool="relay_plan"]')).toBeVisible({ timeout: 15000 })
  await cp.screenshot({ path: path.join(shots, '02-running.png') })
  await expect(cp.locator('[data-tool="relay_plan"][data-state="ok"]')).toBeVisible({ timeout: 90000 })
  await expect(cp.getByText('Finished. The sequence was flown')).toBeVisible({ timeout: 15000 })
  const events = await rest<{ events: { kind: string; data?: Record<string, unknown> }[] }>(r, 'GET', '/events?after=0&limit=1000&tail=true')
  expect(events.events.some((e) => e.kind === 'plan_finished')).toBe(true)
  expect(events.events.some((e) => e.kind === 'claim_started' && e.data?.name === 'copilot')).toBe(true)
  await page.screenshot({ path: path.join(shots, '03-done.png') })

  // a real (fake) aircraft: Copilot asks before it moves; declining blocks the command
  const host = '127.0.0.31'
  const udp = await freeUdpPort([host])
  const httpPort = await freeTcpPortOn([host])
  fake = await startFake({ autopilot: 'px4', host, udpPort: udp, httpPort, gcs: `127.0.0.1:${listen}`, noPosition: true })
  const vid = 'real-127-0-0-31'
  await rest(r, 'POST', '/vehicles', { host, port: udp })
  await until(async () => (await rest<{ link: { state: string } }>(r, 'GET', `/vehicles/${vid}`)).link.state === 'ok', 'fake aircraft link', 30000)
  await page.locator(`[data-fleet-card="${vid}"]`).click()
  await expect(cp.getByText('Selected: ')).toBeVisible({ timeout: 10000 })
  // Copilot reads the selection from the window's report (GET /app/ui, rate-limited): wait until it carries the aircraft
  const selected = async () => (await rest<{ state: { selected?: string | null } | null }>(r, 'GET', '/app/ui', undefined, 'none')).state?.selected
  await until(async () => (await selected()) === vid, 'the window reports the selection', 10000)
  await cp.locator('[data-copilot-input]').fill('Take off')
  await cp.keyboard.press('Enter')
  await expect(cp.locator('[data-approval]')).toBeVisible({ timeout: 15000 })
  await cp.screenshot({ path: path.join(shots, '04-approval.png') })
  await cp.locator('[data-decline]').click()
  await expect(cp.locator('[data-tool="relay_takeoff"][data-state="error"]')).toBeVisible({ timeout: 15000 })
  const v = await rest<{ armed: boolean }>(r, 'GET', `/vehicles/${vid}`)
  expect(v.armed).toBe(false)
})

test('copilot window: floating, placed anywhere, remembered, main window stays usable', async () => {
  await r?.close()
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'relay-e2e-cpw-'))
  r = await launchApp({ lang: 'en', size: { width: 1440, height: 900 }, dataDir })
  const { page, app } = r
  try {
    await page.locator('[data-copilot-toggle]').click()
    const cp = await copilotPage(app)
    await expect(cp.locator('[data-copilot-drag]')).toBeVisible()
    // the page renders before its first frame is ready; the window is shown on ready-to-show
    const info = await until(async () => {
      const i = await copilotWindowInfo(app)
      return i?.visible ? i : null
    }, 'copilot window shown', 10000)
    expect(info).toMatchObject({ visible: true, onTop: true, modal: false, hasParent: false })

    // drag it to the left of the main window; the main window still takes clicks while it is shown
    await app.evaluate(({ BrowserWindow }) => {
      const w = BrowserWindow.getAllWindows().find((x) => x.webContents.getURL().includes('window=copilot'))!
      w.setBounds({ x: 24, y: 80, width: 420, height: 560 })
    })
    await page.getByText('Records', { exact: true }).first().click()
    await expect(page.locator('[data-workspace="records"]')).toBeVisible()
    expect((await copilotWindowInfo(app))?.visible).toBe(true)

    // hide through the window's own button, then the top bar reflects it and the position is on disk
    await cp.locator('[data-copilot-hide]').click()
    await expect(page.locator('[data-copilot-toggle][aria-pressed="false"]')).toBeVisible()
    expect((await copilotWindowInfo(app))?.visible).toBe(false)
    const file = path.join(dataDir, 'copilot-window.json')
    const saved = await until(() => (fs.existsSync(file) ? (JSON.parse(fs.readFileSync(file, 'utf8')) as { x: number; y: number; width: number; height: number }) : null), 'saved bounds', 5000)
    expect(saved).toMatchObject({ x: 24, y: 80 })

    // Ctrl+J shows it again, in the same place
    await page.keyboard.press(process.platform === 'darwin' ? 'Meta+J' : 'Control+J')
    await until(async () => (await copilotWindowInfo(app))?.visible, 'copilot window shown again', 5000)
    expect((await copilotWindowInfo(app))?.bounds).toMatchObject({ x: 24, y: 80 })
  } finally {
    await r.close()
    fs.rmSync(dataDir, { recursive: true, force: true })
  }
})
