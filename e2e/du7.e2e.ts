// ANetDU7 and its gimbal pod (12 §2.2, §3, §5.5): a simulator added without a frame is an ANetDU7 on PX4 with a
// simulated pod; the stage draws it, the picture-in-picture shows the pod's rendered picture with its HUD, the
// inspector's Gimbal block moves and zooms it, switches the lens, takes a photo; the Control tab offers the built-in
// box test; Telemetry shows the pose in the control frame; Settings has the Aircraft section.
import path from 'node:path'
import { expect, test } from '@playwright/test'
import { ARTIFACTS, launchApp, rest, until, type RelayApp } from './harness'

type G = { kind: string; lens: string; zoom: number; photos: number; yaw_deg: number }
type V = { model: string; autopilot: string; gimbal: G | null; nav: { frame: string } | null }

let r: RelayApp

test.afterAll(async () => {
  await r?.close()
})

test('ANetDU7 simulator: stage model, gimbal picture, Gimbal block, box test, control frame', async () => {
  r = await launchApp({ lang: 'en', size: { width: 1440, height: 900 } })
  const { page } = r
  const shots = path.join(ARTIFACTS, 'du7')
  const vehicle = () => rest<V>(r, 'GET', '/vehicles/sim-1')

  await rest(r, 'POST', '/sims', { count: 1 })
  const v = await until(async () => {
    const x = await vehicle().catch(() => null)
    return x?.gimbal ? x : null
  }, 'sim-1 with its pod', 20000)
  expect(v.model).toBe('anet-du7')
  expect(v.autopilot).toBe('PX4')
  expect(v.gimbal?.kind).toBe('sim')

  // select it: the PiP shows the pod's picture (rendered by the stage) with the HUD
  await page.locator('[data-fleet-card="sim-1"]').click()
  const pip = page.locator('[data-video-pip="sim-1"]')
  await expect(pip).toBeVisible()
  await expect(pip).toHaveAttribute('data-pip-shows', 'gimbal')
  await expect(pip.locator('[data-video-feed="gimbal"]')).toBeVisible({ timeout: 20000 })
  await expect(pip.locator('[data-gimbal-lens="visible1"]')).toHaveText(/EO 1\.0×/)

  // the Gimbal block: zoom in, then the infrared lens, then a photo
  const block = page.locator('[data-gimbal="sim"]')
  await block.scrollIntoViewIfNeeded()
  await expect(page.locator('[data-model="anet-du7"]').first()).toBeVisible()
  await block.locator('[data-gimbal-zoom-in]').click()
  await until(async () => ((await vehicle()).gimbal?.zoom ?? 1) > 1.5, 'zoomed in', 15000)
  await block.locator('[data-gimbal-lens-switch]').getByRole('button', { name: 'IR', exact: true }).click()
  await until(async () => (await vehicle()).gimbal?.lens === 'ir1', 'infrared lens', 15000)
  await expect(pip.locator('[data-gimbal-lens="ir1"]')).toBeVisible()
  await block.locator('[data-gimbal-photo]').click()
  await until(async () => ((await vehicle()).gimbal?.photos ?? 0) >= 1, 'a photo', 15000)
  await expect(block.locator('[data-gimbal-capture]').first()).toBeVisible({ timeout: 15000 })
  // the rate pad turns the pod while held and stops on release
  const right = block.locator('[data-gimbal-pad="right"]')
  const box = await right.boundingBox()
  if (!box) throw new Error('no pad')
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await until(async () => ((await vehicle()).gimbal?.yaw_deg ?? 0) > 3, 'panned right', 15000, 100)
  await page.mouse.up()
  await page.screenshot({ path: path.join(shots, 'gimbal.png') })

  // the built-in box test for the ANetDU7 (not started: it would arm)
  await page.locator('[data-inspector-tab="control"]').click()
  await expect(page.locator('[data-builtin-plan="du7_box"]')).toBeVisible()
  await expect(page.locator('[data-builtin-plan="du7_box"]')).toContainText('ANetDU7 box test')

  // the pose in the control frame
  await page.locator('[data-inspector-tab="telemetry"]').click()
  await expect(page.locator('[data-nav-frame="flu"]')).toBeVisible()
  expect((await vehicle()).nav?.frame).toBe('flu')

  // Settings: the Aircraft section
  await page.keyboard.press('Control+Comma')
  await page.getByRole('button', { name: 'Aircraft', exact: true }).click()
  await expect(page.locator('[data-sim-frame-default]')).toHaveValue('anet-du7')
  await page.screenshot({ path: path.join(shots, 'settings.png') })
})
