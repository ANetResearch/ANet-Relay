// Application control end to end (04 §3, §7): an external agent session switches the language, selects an aircraft,
// takes a screenshot and shows the Copilot window through POST /app/ui; the window runs the action, shows who did
// it, and the backend reports the window state.
import { expect, test } from '@playwright/test'
import { launchApp, rest, until, type RelayApp } from './harness'

let r: RelayApp

test.afterAll(async () => {
  await r?.close()
})

type Ui<T = Record<string, unknown>> = { ok: boolean; data: T }

test('app control: an agent session operates the window through /app/ui', async () => {
  r = await launchApp({ lang: 'en', size: { width: 1440, height: 900 } })
  const { page, app } = r
  await rest(r, 'POST', '/sims', { count: 2, autopilot: 'px4', position: false })
  await expect(page.locator('[data-fleet-card="sim-2"]')).toBeVisible({ timeout: 20000 })

  const session = await rest<{ token: string }>(r, 'POST', '/sessions', { name: 'mcp-app' }, 'none')
  const agent = { session: session.token }
  const ui = <T = Record<string, unknown>>(action: string, args: Record<string, unknown> = {}) => rest<Ui<T>>(r, 'POST', '/app/ui', { action, args }, agent)

  // the window reports its state to the backend
  const state0 = await until(async () => (await rest<{ state: { lang: string } | null }>(r, 'GET', '/app/ui', undefined, 'none')).state, 'ui state', 15000)
  expect(state0.lang).toBe('en')

  // language: the window switches, the native menu follows, and the agent's action is announced
  expect((await ui('set_language', { lang: 'zh' })).ok).toBe(true)
  await expect(page.locator('html')).toHaveAttribute('data-lang', 'zh')
  await expect(page.locator('[data-app-notice]')).toContainText('mcp-app')
  const labels = await app.evaluate(({ Menu }) => Menu.getApplicationMenu()?.items.map((i) => i.label) ?? [])
  expect(labels.some((l) => /[一-龥]/.test(l))).toBe(true)
  const afterLang = await rest<{ state: { lang: string } }>(r, 'GET', '/app/ui', undefined, 'none')
  expect(afterLang.state.lang).toBe('zh')

  // selection and workspace
  await ui('set_language', { lang: 'en' })
  await ui('select_vehicle', { vehicle: 'sim-2' })
  await expect(page.locator('[data-fleet-card="sim-2"][data-selected], [data-fleet-card="sim-2"][aria-selected="true"]').first()).toBeVisible()
  const ghost = await fetch(`${r.apiBase}/app/ui`, { method: 'POST', headers: { 'content-type': 'application/json', 'X-Relay-Session': session.token }, body: JSON.stringify({ action: 'select_vehicle', args: { vehicle: 'ghost' } }) })
  expect(ghost.status).toBe(409)
  const unknown = await fetch(`${r.apiBase}/app/ui`, { method: 'POST', headers: { 'content-type': 'application/json', 'X-Relay-Session': session.token }, body: JSON.stringify({ action: 'self_destruct', args: {} }) })
  expect(unknown.status).toBe(409)
  expect(JSON.stringify(await unknown.json())).toContain('unknown_action')

  // screenshot through capturePage
  const shot = await ui<{ png_base64: string; width: number; height: number }>('screenshot', { max_width: 800 })
  expect(shot.data.png_base64.startsWith('iVBOR')).toBe(true)
  expect(shot.data.width).toBeLessThanOrEqual(800)

  // window actions and the Copilot window
  const win = await ui<{ state: { fullscreen: boolean } }>('window', { action: 'focus' })
  expect(win.ok).toBe(true)
  expect((await ui<{ open: boolean }>('toggle_copilot', { open: true })).data.open).toBe(true)
  await until(() => app.windows().find((w) => w.url().includes('window=copilot')), 'copilot window', 20000)
  await expect(page.locator('[data-copilot-toggle][aria-pressed="true"]')).toBeVisible()
  expect((await ui<{ open: boolean }>('toggle_copilot', { open: false })).data.open).toBe(false)

  // an agent without permission is refused when the operator turned agent control off
  await rest(r, 'PUT', '/settings', { agents_app_control: false })
  const refused = await fetch(`${r.apiBase}/app/ui`, { method: 'POST', headers: { 'content-type': 'application/json', 'X-Relay-Session': session.token }, body: JSON.stringify({ action: 'get_state', args: {} }) })
  expect(refused.status).toBe(403)
  await rest(r, 'PUT', '/settings', { agents_app_control: true })
})
