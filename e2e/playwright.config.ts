// Playwright drives the Electron app (_electron) with its real backend and fake aircraft (03 §14).
// Run with `npm run test:e2e` (under xvfb-run on Linux without a display). Build the renderer first.
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: '.',
  testMatch: /.*\.e2e\.ts$/,
  outputDir: '../test-results',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 300_000,
  expect: { timeout: 15_000 },
  reporter: [['list']],
  use: { trace: 'off' },
})
