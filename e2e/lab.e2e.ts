// Lab end to end (06 §7, WP-5): the Lab workspace opens, a program is made from a built-in example, validated, its
// unit tests pass, it is deployed to a simulator and the stage label of that aircraft reads PROGRAM · name · state.
import path from 'node:path'
import { expect, test } from '@playwright/test'
import { ARTIFACTS, launchApp, rest, until, type RelayApp } from './harness'

type Run = { id: string; name: string; status: string; state: string; frames: number }
type V = { controller: string; program: Run | null }

let r: RelayApp

test.afterAll(async () => {
  await r?.close()
})

test('lab: example to program, validated, tested, deployed and labelled on the stage', async () => {
  r = await launchApp({ lang: 'en', size: { width: 1440, height: 900 } })
  const { page } = r
  const shots = path.join(ARTIFACTS, 'lab')
  await rest(r, 'POST', '/sims', { count: 1, autopilot: 'ardupilot', position: true })
  await expect(page.locator('[data-fleet-card="sim-1"]')).toBeVisible({ timeout: 15000 })

  // the fourth workspace, on the key 4 (06 §7)
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
  await page.keyboard.press('4')
  await expect(page.locator('[data-workspace="lab"]')).toBeVisible({ timeout: 15000 })
  await expect(page.locator('[data-lab-editor]')).toBeVisible({ timeout: 15000 })

  // a program of my own from the hover_trim example (read-only examples are duplicated)
  await page.locator('[data-lab-program="hover_trim"]').click()
  await expect(page.locator('[data-lab-editor-pane="hover_trim"]')).toBeVisible()
  await page.locator('[data-lab-editor-pane="hover_trim"] header').getByRole('button', { name: 'Duplicate' }).click()
  const pane = page.locator('[data-lab-editor-pane]:not([data-lab-editor-pane="hover_trim"])')
  await expect(pane).toBeVisible({ timeout: 10000 })
  const id = (await pane.getAttribute('data-lab-editor-pane'))!
  const listed = await rest<{ programs: { id: string; builtin: boolean }[] }>(r, 'GET', '/programs')
  expect(listed.programs.some((p) => p.id === id && !p.builtin)).toBe(true)

  // validated by the backend (the lint gutter and the header state come from POST /programs/{id}/validate)
  await expect(page.locator('[data-lab-valid]')).toBeVisible({ timeout: 10000 })

  // its unit tests run in the program host and all pass
  await page.locator('[data-lab-tab="tests"]').click()
  await expect(page.locator('[data-lab-tests]')).toBeVisible()
  await page.locator('[data-lab-run-tests]').click()
  const summary = page.locator('[data-lab-test-summary]')
  await expect(summary).toBeVisible({ timeout: 20000 })
  const m = /(\d+)\D+(\d+)/.exec((await summary.textContent()) ?? '')
  expect(m).not.toBeNull()
  expect(Number(m![1])).toBeGreaterThan(0)
  expect(m![1]).toBe(m![2])
  await page.screenshot({ path: path.join(shots, '01-tests.png') })

  // deployed to the simulator from the Run tab
  await page.locator('[data-lab-tab="run"]').click()
  await expect(page.locator('[data-lab-run]')).toBeVisible()
  await page.locator('[data-lab-deploy]').click()
  const v = await until(
    async () => {
      const x = await rest<V>(r, 'GET', '/vehicles/sim-1')
      return x.controller === 'program' && x.program?.status === 'running' && x.program.frames > 10 ? x : null
    },
    'program running on sim-1',
    20000,
  )
  expect(v.program?.id).toBe(id)
  await expect(page.locator('[data-program-run], [data-lab-log]').first()).toBeVisible({ timeout: 10000 })
  await page.screenshot({ path: path.join(shots, '02-run.png') })

  // the stage label of the aircraft names the program controller (Fly, key 1)
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
  await page.keyboard.press('1')
  const label = page.locator('.stage-label[data-vehicle="sim-1"] .stage-label-sub')
  await expect(label).toContainText('PROGRAM', { timeout: 15000 })
  await expect(label).toContainText(v.program!.name)
  await page.screenshot({ path: path.join(shots, '03-stage.png') })

  // stopping hands the aircraft back
  await rest(r, 'POST', '/vehicles/sim-1/program/stop')
  await until(async () => (await rest<V>(r, 'GET', '/vehicles/sim-1')).controller !== 'program', 'program stopped', 10000)
  await expect(label).not.toContainText('PROGRAM', { timeout: 10000 })
})
