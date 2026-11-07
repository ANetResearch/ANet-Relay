// The end-to-end scenario of 03 §14, step by step, against the real Electron shell, the real backend, simulators
// and two fake aircraft (plus a third fake bridge for the set-up wizard). Each step saves a screenshot to
// e2e/artifacts/scenario/.
import fs from 'node:fs'
import path from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import type { ChildProcess } from 'node:child_process'
import {
  ARTIFACTS, ROOT, freeTcpPortOn, freeUdpPort, launchApp, rest, startFake, startPython, stopProcess, until, waitForLine,
  type RelayApp,
} from './harness'

const SHOTS = path.join(ARTIFACTS, 'scenario')
const origin = (app: RelayApp) => app.apiBase.replace(/\/api\/v1$/, '')

interface V {
  id: string
  label: string
  armed: boolean
  controller: 'none' | 'human' | 'agent'
  possession: { input: string; pad: number | null } | null
  claim: { name: string; suspended: boolean } | null
  position: { z: number } | null
  mode: string | null
  sticks: { throttle: number } | null
  landed: string
  host: string | null
  kind: 'real' | 'sim'
  course: { state: string } | null
  record_session: number | null
}
interface F {
  vehicles: V[]
  scan: { running: boolean; found: number }
}

test.describe.configure({ mode: 'serial' })

test('03 §14 scenario: boot, possess, relay hand-back, gamepad, course, scan, bridge, stop all, Chinese, replay', async () => {
  fs.mkdirSync(SHOTS, { recursive: true })
  const children: ChildProcess[] = []
  let r: RelayApp | undefined
  let n = 0
  const shot = async (page: Page, name: string) => {
    n += 1
    await page.waitForTimeout(150)
    await page.screenshot({ path: path.join(SHOTS, `${String(n).padStart(2, '0')}-${name}.png`) })
  }

  try {
    // Fake aircraft are on loopback aliases and send to a GCS port nobody listens on, as a bridge that has not
    // met this ground station yet; the scan finds them over HTTP and the backend's first packet teaches them.
    const hosts = ['127.0.0.2', '127.0.0.3', '127.0.0.4', '127.0.0.5']
    const bridgePort = await freeUdpPort(hosts)
    const httpPort = await freeTcpPortOn(hosts)
    const deadGcs = await freeUdpPort(['127.0.0.1'])
    fs.mkdirSync(path.join(ROOT, 'test-results'), { recursive: true })
    const state = fs.mkdtempSync(path.join(ROOT, 'test-results', 'bridge-'))

    const fleet = async () => rest<F>(r!, 'GET', '/fleet', undefined, 'none')
    const vehicle = async (id: string) => (await fleet()).vehicles.find((v) => v.id === id)

    await test.step('boot sequence', async () => {
      r = await launchApp({ size: { width: 1440, height: 900 }, bridgePort, scan: `127.0.0.0/29:${httpPort}` })
      await shot(r.page, 'boot')
      await expect(r.page.locator('[data-fleet-panel]')).toBeVisible({ timeout: 30000 })
      await expect(r.page.getByText('No aircraft yet')).toBeVisible()
      await r.page.waitForTimeout(600)
      await shot(r.page, 'empty')
    })
    const { page } = r!
    const card = (id: string) => page.locator(`[data-fleet-card="${id}"]`)

    await test.step('add four simulators', async () => {
      await page.getByRole('button', { name: 'Add simulated aircraft' }).click()
      await expect(card('sim-1')).toBeVisible()
      for (let i = 0; i < 3; i++) await page.getByRole('button', { name: 'Add sim', exact: true }).click()
      await expect(card('sim-4')).toBeVisible()
      await until(async () => (await fleet()).vehicles.filter((v) => v.kind === 'sim').length === 4, '4 sims')
      await shot(page, 'four-sims')
    })

    await test.step('keyboard: possess sim-1, arm, climb, release', async () => {
      await card('sim-1').click()
      await page.getByRole('tab', { name: 'Control' }).click()
      await page.locator('[data-possess]').click()
      await until(async () => (await vehicle('sim-1'))?.possession?.input === 'keyboard', 'sim-1 possessed by keyboard')
      await shot(page, 'possessed-keyboard')
      // altitude hold: the throttle stick centre holds height (02 §7.5)
      await page.getByRole('combobox', { name: 'Mode' }).click()
      await page.getByRole('option', { name: 'ALT_HOLD' }).click()
      await until(async () => (await vehicle('sim-1'))?.mode === 'ALT_HOLD', 'sim-1 in ALT_HOLD')
      await page.getByRole('button', { name: 'Arm', exact: true }).click()
      await until(async () => (await vehicle('sim-1'))?.armed, 'sim-1 armed')
      // R raises the throttle while held and it stays where it is left (02 §3); F brings it back to the centre
      await page.locator('[data-stage]').click({ position: { x: 20, y: 200 } })
      await page.keyboard.down('r')
      await until(async () => ((await vehicle('sim-1'))?.position?.z ?? 0) > 0.6, 'sim-1 climbing', 15000, 100)
      await page.keyboard.up('r')
      for (let i = 0; i < 60; i++) {
        const thr = (await vehicle('sim-1'))?.sticks?.throttle ?? 0
        if (thr <= 0.54) break
        await page.keyboard.down('f')
        await page.waitForTimeout(40)
        await page.keyboard.up('f')
      }
      // throttle back near the centre: altitude hold keeps the height while the 50 Hz stick stream stays fresh
      expect((await vehicle('sim-1'))?.sticks?.throttle ?? 0.5).toBeLessThan(0.6)
      const held = (await vehicle('sim-1'))?.position?.z ?? 0
      expect(held).toBeGreaterThan(0.4)
      for (let i = 0; i < 15; i++) {
        await page.waitForTimeout(200)
        const v = await vehicle('sim-1')
        expect(v?.mode).toBe('ALT_HOLD')
        expect(v?.sticks?.age_s ?? 1).toBeLessThan(0.8)
        expect(Math.abs((v?.position?.z ?? 0) - held)).toBeLessThan(0.5)
      }
      await shot(page, 'climb')
      await page.keyboard.press('p')
      await until(async () => (await vehicle('sim-1'))?.controller === 'none', 'sim-1 released')
      await expect(page.getByText(/is airborne with no controller|Released SIM-1/).filter({ visible: true }).first()).toBeVisible()
      await shot(page, 'released-airborne')
    })

    let agent: ChildProcess | undefined
    let courseLabel = ''
    await test.step('SDK session claims sim-2 and hovers', async () => {
      agent = startPython(
        [
          'import os, sys',
          'from anet_relay_client import RelayClient',
          'c = RelayClient(os.environ["RELAY_BASE"])',
          'c.open_session("harness-a")',
          'c.claim("sim-2")',
          'c.arm("sim-2")',
          'c.takeoff("sim-2", alt=1.0)',
          'c.hold("sim-2")',
          'seq = c.last_seq()',
          'print("hovering", flush=True)',
          'ev = c.wait_event("relay_handback", vehicle="sim-2", after=seq, timeout=120)',
          'print("handback", ev["data"].get("session") or ev.get("session"), flush=True)',
          'c.land("sim-2")',
          'c.unclaim("sim-2")',
          'c.close()',
          'print("done", flush=True)',
        ].join('\n'),
        { RELAY_BASE: origin(r!) },
      )
      children.push(agent)
      await waitForLine(agent, /^hovering/, 60000)
      await card('sim-2').click()
      await expect(card('sim-2').getByText(/harness-a/).first()).toBeVisible()
      await shot(page, 'agent-hover')
    })

    await test.step('UI possesses sim-2 (claim suspended), then releases: SDK receives relay_handback', async () => {
      await expect(page.getByText('harness-a is flying this aircraft. Possessing suspends its claim.')).toBeVisible()
      await page.locator('[data-possess]').click()
      await until(async () => (await vehicle('sim-2'))?.claim?.suspended, 'claim suspended')
      await expect(card('sim-2').getByText(/YOU/).first()).toBeVisible()
      await page.waitForTimeout(500)
      await shot(page, 'possess-agent-aircraft')
      const handback = waitForLine(agent!, /^handback/, 30000)
      await page.locator('[data-release]').click()
      await handback
      await expect(page.getByText('SIM-2 handed back to harness-a')).toBeVisible()
      await page.waitForTimeout(500)
      await shot(page, 'handback')
    })

    await test.step('simulated gamepad connects: hint card', async () => {
      await page.evaluate(() => {
        const buttons = Array.from({ length: 17 }, () => ({ pressed: false, touched: false, value: 0 }))
        const pad = {
          id: 'DualSense Wireless Controller (STANDARD GAMEPAD Vendor: 054c Product: 0ce6)',
          index: 0, connected: true, mapping: 'standard', axes: [0, 0, 0, 0], buttons, timestamp: performance.now(),
        }
        Object.defineProperty(navigator, 'getGamepads', { configurable: true, value: () => [pad, null, null, null] })
        window.dispatchEvent(Object.assign(new Event('gamepadconnected'), { gamepad: pad }))
      })
      await expect(page.getByText('DualSense connected')).toBeVisible()
      await expect(page.getByText(/Controller 1 · flying SIM-/)).toBeVisible()
      await page.waitForTimeout(700)
      await shot(page, 'gamepad-hint')
      await page.getByRole('button', { name: 'Dismiss' }).click()
    })

    await test.step('ring course cage-loop flown by the SDK, then listed in Records', async () => {
      const bound = (await fleet()).vehicles.find((v) => v.possession?.input === 'gamepad')
      if (bound) await rest(r!, 'POST', `/vehicles/${bound.id}/release`)
      const free = (await fleet()).vehicles.find((v) => v.kind === 'sim' && v.controller === 'none' && !v.armed && v.id !== 'sim-1' && v.id !== 'sim-2')
      expect(free).toBeTruthy()
      await card(free!.id).click()
      courseLabel = free!.label
      const course = startPython(
        `import runpy, sys; sys.argv = ["course_sim.py", "--base", "${origin(r!)}", "--vehicle", "${free!.id}"]; sys.path.insert(0, "sdk/python/examples"); runpy.run_path("sdk/python/examples/course_sim.py", run_name="__main__")`,
        {},
      )
      children.push(course)
      await waitForLine(course, /airborne; course clock running/, 60000)
      await expect(page.locator('[data-course-hud]').first()).toBeVisible()
      await page.waitForTimeout(1500)
      await shot(page, 'course-running')
      await waitForLine(course, /course finished in/, 90000)
      await shot(page, 'course-finished')
      await waitForLine(course, /landed and released/, 60000)
      await page.getByRole('radio', { name: 'Records' }).click().catch(async () => page.keyboard.press('3'))
      await expect(page.locator('[data-workspace="records"]')).toBeVisible()
      await shot(page, 'records-list')
      await page.getByRole('radio', { name: 'Fly' }).click().catch(async () => page.keyboard.press('1'))
    })

    await test.step('scan finds two fake aircraft', async () => {
      children.push(await startFake({ autopilot: 'ardupilot', host: '127.0.0.2', udpPort: bridgePort, httpPort, gcs: `127.0.0.1:${deadGcs}`, sysid: 1 }))
      children.push(await startFake({ autopilot: 'px4', host: '127.0.0.3', udpPort: bridgePort, httpPort, gcs: `127.0.0.1:${deadGcs}`, sysid: 1, noPosition: true }))
      await page.getByRole('button', { name: 'Scan network' }).first().click()
      await until(async () => (await fleet()).vehicles.filter((v) => v.kind === 'real').length >= 2, 'two real aircraft', 40000)
      await expect(page.locator('[data-fleet-card]').filter({ hasText: '127.0.0.2' })).toBeVisible()
      await expect(page.locator('[data-fleet-card]').filter({ hasText: '127.0.0.3' })).toBeVisible()
      await page.waitForTimeout(1500)
      await shot(page, 'scan-found')
    })

    await test.step('bridge set-up wizard against a fake bridge', async () => {
      children.push(await startFake({
        autopilot: 'ardupilot', host: '127.0.0.4', staHost: '127.0.0.5', udpPort: bridgePort, httpPort,
        gcs: `127.0.0.1:${deadGcs}`, sysid: 3, stateFile: path.join(state, 'bridge.json'),
      }))
      await page.locator('[data-fleet-panel] header').getByRole('button').last().click()
      await page.getByRole('tab', { name: 'Set up a new bridge' }).click()
      const addr = page.getByLabel('Bridge address')
      await addr.fill(`127.0.0.4:${httpPort}`)
      await shot(page, 'wizard-connect')
      await page.getByRole('button', { name: /I have joined it|Next/ }).click()
      await expect(page.getByText('mavesp8266').or(page.getByText(/1\.2\.2/)).first()).toBeVisible({ timeout: 20000 })
      await shot(page, 'wizard-read')
      await page.getByRole('button', { name: /Next|Configure/ }).last().click()
      await page.getByLabel('Network name').fill('anet4d')
      await page.getByRole('textbox', { name: 'Password' }).fill('fake-bridge-pass')
      await shot(page, 'wizard-configure')
      await page.getByRole('button', { name: 'Write and restart bridge' }).click()
      await expect(page.getByText(/Found 127\.0\.0\.5/)).toBeVisible({ timeout: 60000 })
      await shot(page, 'wizard-rejoined')
      await page.getByRole('button', { name: 'Done' }).click()
    })

    await test.step('stop all', async () => {
      await rest(r!, 'POST', '/vehicles/sim-3/possess', { input: 'keyboard' }).catch(() => {})
      await page.getByRole('button', { name: 'Stop all' }).click()
      await until(async () => {
        const ev = await rest<{ events: { kind: string }[] }>(r!, 'GET', '/events?after=0&limit=1000', undefined, 'none')
        return ev.events.some((e) => e.kind === 'stop_all')
      }, 'stop_all event')
      await until(async () => (await fleet()).vehicles.every((v) => !v.armed), 'all disarmed', 30000)
      await shot(page, 'stop-all')
    })

    await test.step('switch to Chinese', async () => {
      await page.getByRole('radio', { name: '中' }).click().catch(async () => page.getByText('中', { exact: true }).click())
      await expect(page.getByText('全部急停')).toBeVisible()
      await shot(page, 'chinese')
    })

    await test.step('records replay', async () => {
      await page.keyboard.press('3')
      await expect(page.locator('[data-workspace="records"]')).toBeVisible()
      // the course aircraft's record carries the ring result
      await page.locator('[data-record]').filter({ hasText: courseLabel }).first().click()
      await page.waitForTimeout(800)
      await shot(page, 'records-session')
      await page.getByRole('button', { name: '播放' }).click()
      await expect(page.locator('[data-stage-replay]')).toBeVisible()
      await page.waitForTimeout(1500)
      await shot(page, 'records-replay')
    })
  } finally {
    for (const c of children.reverse()) await stopProcess(c)
    await r?.close()
  }
})
