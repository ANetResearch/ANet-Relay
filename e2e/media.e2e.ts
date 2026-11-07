// README images (03 §13): docs/media/hero.png, relay.png and course.png at 1600 x 900, captured from the real app with
// simulators only (no host network details on screen). Skipped unless RELAY_MEDIA=1.
//
//   RELAY_MEDIA=1 npx playwright test --config e2e/playwright.config.ts media
import fs from 'node:fs'
import path from 'node:path'
import type { ChildProcess } from 'node:child_process'
import { expect, test, type Page } from '@playwright/test'
import { launchApp, rest, ROOT, startPython, stopProcess, until, waitForLine, type RelayApp } from './harness'

const MEDIA = path.join(ROOT, 'docs', 'media')

interface V {
  id: string
  controller: string
  armed: boolean
  skill: { id: number; state: string } | null
  position: { x: number; y: number; z: number } | null
  course: { state: string; next: number } | null
}

test('README media', async () => {
  test.skip(process.env.RELAY_MEDIA !== '1', 'set RELAY_MEDIA=1 to capture the README images')
  test.setTimeout(600_000)
  fs.mkdirSync(MEDIA, { recursive: true })
  const children: ChildProcess[] = []
  let r: RelayApp | undefined
  try {
    r = await launchApp({ size: { width: 1600, height: 900 } })
    const app = r
    // full-quality stage even on a software renderer (stage/scene/gpu.ts capture mode)
    await app.page.evaluate(() => localStorage.setItem('relay.stage.gl', 'hq'))
    await app.page.reload()
    const page = app.page
    await expect(page.locator('[data-fleet-panel]')).toBeVisible({ timeout: 30000 })
    const fleet = async () => (await rest<{ vehicles: V[] }>(app, 'GET', '/fleet', undefined, 'none')).vehicles
    const vehicle = async (id: string) => (await fleet()).find((v) => v.id === id)
    const card = (id: string) => page.locator(`[data-fleet-card="${id}"]`)
    const shot = async (p: Page, name: string, settle = 2500) => {
      await p.waitForTimeout(settle)
      await p
        .waitForFunction(() => document.getAnimations().every((a) => a.playState !== 'running' || a.effect?.getComputedTiming().endTime === Infinity), undefined, { timeout: 4000, polling: 100 })
        .catch(() => {})
      await p.screenshot({ path: path.join(MEDIA, `${name}.png`) })
    }

    // four simulators; two agents hold three of them, hovering
    await rest(app, 'POST', '/sims', { count: 4 })
    await until(async () => (await fleet()).length === 4, 'four sims', 30000)
    const base = app.apiBase.replace(/\/api\/v1$/, '')
    const hover = (name: string, ids: string[], alt: number) =>
      startPython(
        [
          'import os, time',
          'from anet_relay_client import RelayClient',
          'c = RelayClient(os.environ["RELAY_BASE"])',
          `c.open_session(${JSON.stringify(name)})`,
          ...ids.map((id) => `c.claim(${JSON.stringify(id)}); c.arm(${JSON.stringify(id)}); c.takeoff(${JSON.stringify(id)}, alt=${alt}); c.hold(${JSON.stringify(id)})`),
          'print("hovering", flush=True)',
          'time.sleep(3600)',
        ].join('\n'),
        { RELAY_BASE: base },
      )
    const a = hover('harness-a', ['sim-2', 'sim-3'], 1.1)
    children.push(a)
    const b = hover('harness-b', ['sim-4'], 0.8)
    children.push(b)
    await waitForLine(a, /^hovering/, 90000)
    await waitForLine(b, /^hovering/, 90000)

    // the operator possesses sim-1 with the keyboard and flies the cage loop (goto skills, as an operator may)
    await card('sim-1').click()
    await page.getByRole('tab', { name: 'Control' }).click()
    await page.locator('[data-possess]').click()
    await until(async () => (await vehicle('sim-1'))?.controller === 'human', 'sim-1 possessed')
    const started = (await rest<{ course: { rings: { c: [number, number, number]; yaw_deg: number }[]; origin: number[] } }>(app, 'POST', '/vehicles/sim-1/course/start', { course_id: 'cage-loop' })).course
    const skill = async (name: string, body: Record<string, number>, wait = true) => {
      const s = (await rest<{ skill: { id: number } }>(app, 'POST', `/vehicles/sim-1/skills/${name}`, body)).skill
      if (!wait) return
      await until(async () => {
        const v = await vehicle('sim-1')
        return v?.skill?.id === s.id && v.skill.state !== 'running'
      }, `${name} done`, 60000)
    }
    const [, , oz] = started.origin
    const along = (i: number, d: number) => {
      const ring = started.rings[i]
      const nx = Math.sin((ring.yaw_deg * Math.PI) / 180)
      const ny = Math.cos((ring.yaw_deg * Math.PI) / 180)
      return { x: ring.c[0] + d * nx, y: ring.c[1] + d * ny, z: ring.c[2], yaw: ring.yaw_deg }
    }
    await rest(app, 'POST', '/vehicles/sim-1/arm', { arm: true })
    await skill('takeoff', { alt: started.rings[0].c[2] - oz })
    for (const i of [0, 1]) {
      await skill('goto', along(i, -0.35))
      await skill('goto', along(i, 0.35))
    }
    // hero: mid-course towards ring 3, the overview of the whole fleet (starting the course switched to chase)
    await page.getByRole('button', { name: '1 min', exact: true }).click()
    await page.getByRole('button', { name: 'Overview' }).click()
    await skill('goto', along(2, -0.6))
    await shot(page, 'hero', 3500)

    // course: chase camera lined up behind ring 3, stage widened
    await page.getByRole('button', { name: 'Chase' }).click()
    await page.keyboard.press('Alt+Control+i')
    await page.keyboard.press('Alt+Control+t')
    await skill('goto', along(2, -0.9))
    await skill('goto', along(2, -0.15), false)
    await page.waitForTimeout(400)
    await shot(page, 'course', 1200)
    await skill('goto', along(2, 0.35))
    await until(async () => ((await vehicle('sim-1'))?.course?.next ?? 0) >= 3, 'ring 3 passed', 30000)
    await skill('goto', along(3, -0.35))
    await skill('goto', along(3, 0.35))
    await until(async () => (await vehicle('sim-1'))?.course?.state === 'finished', 'course finished', 30000)
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: 'Overview' }).click()
    await page.keyboard.press('Alt+Control+i')
    await page.keyboard.press('Alt+Control+t')
    // land and release sim-1 so the relay moment shows only the handback (an input flies one aircraft at a time)
    await skill('land', {}, false)
    await until(async () => !(await vehicle('sim-1'))?.armed, 'sim-1 landed', 60000)
    await rest(app, 'POST', '/vehicles/sim-1/release')
    await page.waitForTimeout(4000)

    // relay: the operator takes sim-2 from harness-a for a few seconds and hands it back
    await card('sim-2').click()
    await page.getByRole('tab', { name: 'Control' }).click()
    await page.locator('[data-possess]').click()
    await until(async () => (await vehicle('sim-2'))?.controller === 'human', 'sim-2 possessed')
    await page.waitForTimeout(9000)
    await page.locator('[data-release]').click()
    await until(async () => (await vehicle('sim-2'))?.controller === 'agent', 'sim-2 handed back')
    await expect(page.getByText('SIM-2 handed back to harness-a').filter({ visible: true }).first()).toBeVisible()
    await shot(page, 'relay', 200)
  } finally {
    for (const c of children.reverse()) await stopProcess(c)
    await r?.close()
  }
})
