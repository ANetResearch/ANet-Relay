// Flight health (03 §5.9): the inspector shows the rolling diagnostics while an operator hovers a simulator.
import path from 'node:path'
import { expect, test } from '@playwright/test'
import { ARTIFACTS, launchApp, rest, until, type RelayApp } from './harness'

type V = { controller: string; landed: string; skill: { id: number; state: string } | null; health: { state: string; airborne_samples: number } }

let r: RelayApp

test.afterAll(async () => {
  await r?.close()
})

for (const lang of ['en', 'zh'] as const) {
  test(`flight health in the inspector (${lang})`, async () => {
    r = await launchApp({ lang, size: { width: 1440, height: 900 } })
    const { page } = r
    if (lang === 'zh') {
      await page.evaluate(() => localStorage.setItem('relay.lang', 'zh'))
      await page.reload()
    }
    const vehicle = async () => rest<V>(r, 'GET', '/vehicles/sim-1')
    await rest(r, 'POST', '/sims', { count: 1, autopilot: 'px4', position: false })
    await page.locator('[data-fleet-card="sim-1"]').click()
    await expect(page.locator('[data-health="idle"]')).toBeVisible({ timeout: 15000 })

    await page.locator('[data-inspector-tab="control"]').click()
    await page.locator('[data-possess]').click()
    await until(async () => (await vehicle()).controller === 'human', 'sim-1 possessed')
    const s = (await rest<{ skill: { id: number } }>(r, 'POST', '/vehicles/sim-1/skills/takeoff', { alt: 1 })).skill
    await until(async () => (await vehicle()).skill?.id === s.id && (await vehicle()).skill?.state === 'done', 'takeoff', 40000)
    await until(async () => (await vehicle()).health.airborne_samples >= 30, 'health samples', 20000)

    await page.locator('[data-inspector-tab="overview"]').click()
    await expect(page.locator('[data-health="ok"]')).toBeVisible({ timeout: 10000 })
    await expect(page.locator('[data-health-item]')).toHaveCount(10)
    await expect(page.locator('[data-health-item="oscillation"][data-level="ok"]')).toBeVisible()
    await expect(page.locator('[data-health="ok"]')).toHaveText(lang === 'zh' ? '正常' : 'Normal')
    await page.locator('[data-health]').scrollIntoViewIfNeeded()
    await page.screenshot({ path: path.join(ARTIFACTS, 'health', `overview-${lang}.png`) })
    await r.close()
  })
}
