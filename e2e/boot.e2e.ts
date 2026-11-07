// 05 §2.4: the director's cut can be skipped. Fixture mode (no backend wait) with the Full opening set through the
// setting's localStorage mirror. A key must take the opening straight to its reveal (act 4 / 5, never back into the
// film) and remove the mask within 6 s of the key reaching the page. The clock is the page's own, from the key event:
// under software GL (CI, xvfb) the renderer's first mount alone blocks the main thread for seconds with any opening,
// Minimal included, which delays input delivery and Playwright's polling alike.
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
  // the act sequence and the key as the page saw them (performance.now() of each change)
  await page.addInitScript(() => {
    const log: { t: number; what: string }[] = []
    ;(window as unknown as { __bootLog: typeof log }).__bootLog = log
    window.addEventListener('keydown', () => log.push({ t: performance.now(), what: 'key' }), true)
    // a mutation observer orders act changes exactly against the key (an animation-frame poll would not)
    let last = ''
    const note = () => {
      const m = document.getElementById('boot-mask')
      const act = m ? (m.dataset.act ?? '') : 'gone'
      if (act !== last) log.push({ t: performance.now(), what: (last = act) })
    }
    new MutationObserver(note).observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ['data-act'] })
  })
  const base = page.url().split('?')[0]
  await page.goto(`${base}?fixture=busy`)
  await expect(page.locator('#boot-mask[data-film="full"]')).toBeAttached({ timeout: 10000 })
  await expect(page.locator('#boot-voxels')).toBeAttached()
  // Shift: a key with no binding of its own in the window
  await page.keyboard.press('Shift')
  await expect(page.locator('#boot-mask')).toHaveCount(0, { timeout: 15000 })
  await expect(page.locator('[data-fleet-panel]')).toBeVisible()
  await page.locator('[data-fleet-panel]').click({ trial: true, timeout: 15000 })
  // the decoders are released with the mask
  expect(await page.locator('video.boot-video').count()).toBe(0)
  const log = await page.evaluate(() => (window as unknown as { __bootLog: { t: number; what: string }[] }).__bootLog)
  const key = log.findIndex((e) => e.what === 'key')
  expect(key).toBeGreaterThanOrEqual(0)
  const after = log.slice(key + 1).map((e) => e.what)
  // after the key: the waiting frame or the reveal, then gone; never another film act
  expect(after.filter((a) => ['cold', 'assemble', 'cut', 'film'].includes(a)), JSON.stringify(log)).toEqual([])
  expect(after.at(-1)).toBe('gone')
  // within 6 s of the key reaching the page, the mask is gone (its own clock: no input or polling latency)
  const gone = log.find((e, i) => i > key && e.what === 'gone')
  expect(gone!.t - log[key].t).toBeLessThan(6000)
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
  await expect(page.locator('html')).toHaveAttribute('data-motion', 'reduced')
  await expect(page.locator('#boot-mask')).toHaveCount(0, { timeout: 8000 })
  expect(requested).toEqual([])
})
