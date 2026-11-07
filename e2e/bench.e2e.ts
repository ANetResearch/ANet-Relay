// Bench procedures (05 §4.5): the inspector's Bench tab on a simulator lists the procedures, follows a run started
// through the API, takes the operator's answers from its prompt buttons and ends in a saved report.
import path from 'node:path'
import { expect, test } from '@playwright/test'
import { ARTIFACTS, launchApp, rest, until, type RelayApp } from './harness'

type V = { controller: string }
type Run = { run_id: number; state: string; steps: { id: string; verdict: string | null }[] }

let r: RelayApp

test.afterAll(async () => {
  await r?.close()
})

test('bench tab: motor check on a simulator ends in a report', async () => {
  r = await launchApp({ lang: 'en', size: { width: 1440, height: 900 } })
  const { page } = r
  await rest(r, 'POST', '/sims', { count: 1, autopilot: 'ardupilot', position: true })
  await page.locator('[data-fleet-card="sim-1"]').click()
  await page.locator('[data-inspector-tab="control"]').click()
  await page.locator('[data-possess]').click()
  await until(async () => (await rest<V>(r, 'GET', '/vehicles/sim-1')).controller === 'human', 'sim-1 possessed')

  await page.locator('[data-inspector-tab="bench"]').click()
  await expect(page.locator('[data-bench-tab="sim-1"]')).toBeVisible()
  await expect(page.locator('[data-bench-plan="full_bench"] li')).toHaveCount(12)
  await expect(page.locator('[data-bench-action="start"]')).toBeEnabled()

  // started through the API (the select is a portal list); the tab picks the run up from the vehicle
  await rest(r, 'POST', '/vehicles/sim-1/procedure', { procedure: 'motor_check', options: { auto_answer: true } })
  await expect(page.locator('[data-bench-run]')).toBeVisible({ timeout: 10000 })

  const finished = await until(
    async () => {
      const run = await rest<Run>(r, 'GET', '/vehicles/sim-1/procedure')
      if (!['running', 'waiting'].includes(run.state)) return run
      const answer = page.locator('[data-bench-prompt] button[data-answer]').first()
      if (await answer.isVisible().catch(() => false)) await answer.click({ timeout: 2000 }).catch(() => {})
      return null
    },
    'motor check finished',
    90000,
    500,
  )
  expect(['passed', 'warned']).toContain(finished.state)
  await expect(page.locator('[data-bench-report]')).toBeVisible({ timeout: 10000 })
  const runs = await rest<{ runs: { run_id: number }[] }>(r, 'GET', '/test-runs?vehicle=sim-1')
  expect(runs.runs[0]?.run_id).toBe(finished.run_id)
  await page.screenshot({ path: path.join(ARTIFACTS, 'bench', 'report-en.png') })
})
