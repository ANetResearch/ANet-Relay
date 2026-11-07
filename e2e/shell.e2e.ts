// Shell checks against the real app (03 §12, §13): operator token, live updates over WS, application menu
// (labels follow the interface language, actions reach the renderer) and the media permission policy.
import { expect, test, type ElectronApplication } from '@playwright/test'
import { launchApp, rest, until, type RelayApp } from './harness'

let r: RelayApp

test.afterAll(async () => {
  await r?.close()
})

/** Top-level menu labels, read in the main process. */
const menuLabels = (app: ElectronApplication) => app.evaluate(({ Menu }) => Menu.getApplicationMenu()?.items.map((i) => i.label) ?? [])

/** Clicks the menu item whose label matches, as a user would from the menu bar. */
const clickMenu = (app: ElectronApplication, top: number, label: string) =>
  app.evaluate(({ Menu, BrowserWindow }, a) => {
    const item = Menu.getApplicationMenu()?.items[a.top]?.submenu?.items.find((i) => i.label === a.label)
    if (!item) throw new Error(`menu item ${a.label} not found`)
    const win = BrowserWindow.getAllWindows()[0]
    item.click(undefined, win, win.webContents)
  }, { top, label })

test('shell: operator token, WS, menus, media permissions', async () => {
  r = await launchApp({ lang: 'en', size: { width: 1440, height: 900 } })
  const { app, page } = r

  // operator token: the shell holds it, the backend refuses operator calls without it
  expect(r.token.length).toBeGreaterThanOrEqual(32)
  const anon = await fetch(`${r.apiBase}/sims`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{"count":1}' })
  expect([401, 403]).toContain(anon.status)
  const forged = await fetch(`${r.apiBase}/sims`, { method: 'POST', headers: { 'content-type': 'application/json', 'X-Relay-Operator': 'x'.repeat(43) }, body: '{"count":1}' })
  expect([401, 403]).toContain(forged.status)

  // WS: a REST change made outside the window shows up live
  await rest(r, 'POST', '/sims', { count: 1 })
  await expect(page.locator('[data-fleet-card="sim-1"]')).toBeVisible({ timeout: 20000 })

  // menu in English
  const view = (await menuLabels(app)).indexOf('View')
  const fly = (await menuLabels(app)).indexOf('Fly')
  expect(view).toBeGreaterThan(0)
  expect(fly).toBeGreaterThan(0)

  // View > Toggle Fleet hides and restores the fleet panel
  await clickMenu(app, view, 'Toggle Fleet')
  await expect(page.locator('[data-fleet-panel]')).toBeHidden()
  await clickMenu(app, view, 'Toggle Fleet')
  await expect(page.locator('[data-fleet-panel]')).toBeVisible()

  // App > Settings opens the dialog
  await clickMenu(app, 0, 'Settings…')
  await expect(page.getByRole('dialog', { name: 'Settings' })).toBeVisible()
  await page.keyboard.press('Escape')

  // Fly > Stop All: the shell issues the request itself and the renderer only reports it
  await clickMenu(app, fly, 'Stop All')
  await expect(page.getByText('Stop sent to all aircraft').first()).toBeVisible({ timeout: 10000 })
  await until(async () => (await rest<{ events: { kind: string }[] }>(r, 'GET', '/events?after=0&limit=1000')).events.some((e) => e.kind === 'stop_all'), 'stop_all event', 10000)

  // switching the interface to Chinese relabels the native menu
  await page.getByRole('button', { name: '中', exact: true }).click()
  await until(async () => (await menuLabels(app)).includes('视图'), 'Chinese menu', 10000)
  expect(await menuLabels(app)).toContain('飞行')
  await page.getByRole('button', { name: 'EN', exact: true }).click()
  await until(async () => (await menuLabels(app)).includes('View'), 'English menu', 10000)

  // media: video capture (UVC capture cards) is allowed, the microphone is not. This host has no devices, so the
  // check handler is read through the Permissions API and getUserMedia(video) must not be refused outright.
  const media = await page.evaluate(async () => {
    const state = async (name: string) => (await navigator.permissions.query({ name: name as PermissionName })).state
    let video = 'ok'
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: true })
      s.getTracks().forEach((t) => t.stop())
    } catch (err) {
      video = (err as DOMException).name
    }
    return { camera: await state('camera'), microphone: await state('microphone'), video }
  })
  expect(media.camera).toBe('granted')
  expect(media.microphone).toBe('denied')
  expect(media.video).not.toBe('NotAllowedError')
  const geo = await page.evaluate(async () => (await navigator.permissions.query({ name: 'geolocation' })).state)
  expect(geo).not.toBe('granted')
})
