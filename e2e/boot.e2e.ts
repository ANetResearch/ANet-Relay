// 05 §2.4: the director's cut can be skipped. Fixture mode (no backend wait) with the Full opening set through the
// setting's localStorage mirror; a key during act 1 must uncover a usable window within 6 s of the navigation.
import { expect, test } from '@playwright/test'
import { launchApp, type RelayApp } from './harness'

let r: RelayApp

test.afterAll(async () => {
  await r?.close()
})

test('opening: the full cut skips on a key and the window is usable within 6 s (fixture)', async () => {
  r = await launchApp({ lang: 'en', size: { width: 1440, height: 900 } })
  const { page } = r
  await page.evaluate(() => localStorage.setItem('relay.boot.mode', 'full'))
  const base = page.url().split('?')[0]
  const t0 = Date.now()
  await page.goto(`${base}?fixture=busy`)
  await expect(page.locator('#boot-mask[data-film="full"]')).toBeAttached({ timeout: 3000 })
  await expect(page.locator('#boot-voxels')).toBeAttached()
  await page.waitForTimeout(700)
  // Shift: a key with no binding of its own in the window
  await page.keyboard.press('Shift')
  await expect(page.locator('#boot-mask')).toHaveCount(0, { timeout: Math.max(1, 6000 - (Date.now() - t0)) })
  expect(Date.now() - t0).toBeLessThan(6000)
  await expect(page.locator('[data-fleet-panel]')).toBeVisible()
  await page.locator('[data-fleet-panel]').click({ trial: true, timeout: 1000 })
  // the decoders are released with the mask
  expect(await page.locator('video.boot-video').count()).toBe(0)
})

test('opening: reduced motion requests no film', async () => {
  await r?.close()
  r = await launchApp({ lang: 'en', size: { width: 1440, height: 900 }, reducedMotion: true })
  const { page } = r
  const requested: string[] = []
  page.on('request', (q) => {
    if (q.url().includes('/boot/')) requested.push(q.url())
  })
  const base = page.url().split('?')[0]
  await page.goto(`${base}?fixture=busy`)
  await expect(page.locator('#boot-mask')).toHaveCount(0, { timeout: 3000 })
  expect(requested).toEqual([])
})
