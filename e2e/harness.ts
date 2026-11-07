// End-to-end harness: free ports, fake aircraft processes and the Electron app with its real backend.
// Every run gets its own data directory and ports, so suites never touch the user's Relay or each other.
import { spawn, type ChildProcess } from 'node:child_process'
import dgram from 'node:dgram'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { _electron, type ElectronApplication, type Page } from '@playwright/test'

export const ROOT = path.resolve(import.meta.dirname, '..')
export const ARTIFACTS = path.join(ROOT, 'e2e', 'artifacts')
const PYTHON = path.join(ROOT, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')

/** A TCP port that is free on 127.0.0.1 right now. */
export function freeTcpPort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer()
    srv.once('error', reject)
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address() as net.AddressInfo
      srv.close(() => resolve(port))
    })
  })
}

/** A UDP port that is free on every address given (all loopback aliases share one port number). */
export async function freeUdpPort(hosts: string[] = ['0.0.0.0']): Promise<number> {
  for (let attempt = 0; attempt < 50; attempt++) {
    const port = 20000 + Math.floor(Math.random() * 30000)
    const ok = await Promise.all(hosts.map((h) => udpFree(h, port)))
    if (ok.every(Boolean)) return port
  }
  throw new Error('no free UDP port')
}

function udpFree(host: string, port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const s = dgram.createSocket('udp4')
    s.once('error', () => resolve(false))
    s.bind(port, host, () => s.close(() => resolve(true)))
  })
}

/** A TCP port free on every loopback alias given. */
export async function freeTcpPortOn(hosts: string[]): Promise<number> {
  for (let attempt = 0; attempt < 50; attempt++) {
    const port = 20000 + Math.floor(Math.random() * 30000)
    const ok = await Promise.all(hosts.map((h) => tcpFree(h, port)))
    if (ok.every(Boolean)) return port
  }
  throw new Error('no free TCP port')
}

function tcpFree(host: string, port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const srv = net.createServer()
    srv.once('error', () => resolve(false))
    srv.listen(port, host, () => srv.close(() => resolve(true)))
  })
}

export interface FakeOptions {
  autopilot: 'px4' | 'ardupilot'
  host: string
  udpPort: number
  httpPort: number
  gcs: string
  staHost?: string
  noPosition?: boolean
  sysid?: number
  stateFile?: string
}

/** One fake aircraft (mavesp8266 bridge + flight controller, tools/fakes/fake_vehicle.py). */
export async function startFake(o: FakeOptions): Promise<ChildProcess> {
  const args = [
    path.join(ROOT, 'tools', 'fakes', 'fake_vehicle.py'),
    '--autopilot', o.autopilot,
    '--bind', `${o.host}:${o.udpPort}`,
    '--http', `${o.host}:${o.httpPort}`,
    '--gcs', o.gcs,
    '--sysid', String(o.sysid ?? 1),
    '--reboot-s', '1.0',
  ]
  if (o.staHost) args.push('--sta-bind', o.staHost)
  if (o.noPosition) args.push('--no-position')
  if (o.stateFile) args.push('--state-file', o.stateFile)
  const child = spawn(PYTHON, args, { cwd: ROOT, stdio: ['ignore', 'ignore', 'pipe'] })
  await new Promise<void>((resolve, reject) => {
    let buf = ''
    const timer = setTimeout(() => reject(new Error(`fake ${o.host} did not start: ${buf}`)), 15000)
    child.stderr!.on('data', (d: Buffer) => {
      buf += d.toString()
      if (buf.includes('fake ')) {
        clearTimeout(timer)
        resolve()
      }
    })
    child.once('exit', (code) => {
      clearTimeout(timer)
      reject(new Error(`fake ${o.host} exited with ${code}: ${buf}`))
    })
  })
  return child
}

export async function stopProcess(child: ChildProcess | undefined): Promise<void> {
  if (!child || child.exitCode !== null || child.signalCode !== null) return
  const done = new Promise<void>((resolve) => child.once('exit', () => resolve()))
  child.kill('SIGTERM')
  const timer = setTimeout(() => child.kill('SIGKILL'), 2000)
  await done
  clearTimeout(timer)
}

export interface AppOptions {
  lang?: 'en' | 'zh'
  size?: { width: number; height: number }
  listenPort?: number
  bridgePort?: number
  scan?: string
  dataDir?: string
  reducedMotion?: boolean
  /** RELAY_BACKEND_CMD: a stand-in backend command (boot failure states); skips the health wait */
  backendCmd?: string[]
  /** RELAY_HTTP_PORT; defaults to a free port. null leaves it unset (the shell scans 8765-8775). */
  httpPort?: number | null
  /** extra query for the window (for example `fixture=alerts`) */
  query?: string
}

export interface RelayApp {
  app: ElectronApplication
  page: Page
  dataDir: string
  apiBase: string
  token: string
  listenPort: number
  close(): Promise<void>
}

/** Launch the Electron shell (dev mode, real backend from .venv) and wait for the main window. */
export async function launchApp(o: AppOptions = {}): Promise<RelayApp> {
  const dataDir = o.dataDir ?? fs.mkdtempSync(path.join(os.tmpdir(), 'relay-e2e-'))
  const listenPort = o.listenPort ?? (await freeUdpPort())
  const httpPort = o.httpPort === null ? null : (o.httpPort ?? (await freeTcpPort()))
  const settingsPath = path.join(dataDir, 'settings.json')
  if (!fs.existsSync(settingsPath)) {
    const ui: Record<string, unknown> = {}
    fs.writeFileSync(settingsPath, JSON.stringify({ listen_port: listenPort, bridge_port: o.bridgePort ?? 14555, ui }))
  }
  const env: Record<string, string> = {
    ...(process.env as Record<string, string>),
    RELAY_USER_DATA_DIR: dataDir,
    RELAY_LISTEN_PORT: String(listenPort),
    RELAY_ALLOW_LOOPBACK: '1',
    ELECTRON_DISABLE_SANDBOX: '1',
  }
  if (httpPort !== null) env.RELAY_HTTP_PORT = String(httpPort)
  else delete env.RELAY_HTTP_PORT
  if (o.scan) env.RELAY_SCAN_NETWORKS = o.scan
  if (o.backendCmd) env.RELAY_BACKEND_CMD = JSON.stringify(o.backendCmd)
  delete env.RELAY_WEB_URL
  delete env.RELAY_OPERATOR_TOKEN
  // Software WebGL (SwiftShader) so the 3D stage renders on CI machines without a GPU.
  const args = [ROOT, '--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist']
  if (o.lang) args.push(`--lang=${o.lang === 'zh' ? 'zh-CN' : 'en-US'}`)
  if (o.reducedMotion) args.push('--force-prefers-reduced-motion')
  const app = await _electron.launch({ args, cwd: ROOT, env, timeout: 60000 })
  const page = await app.firstWindow({ timeout: 60000 })
  if (o.size) await setWindowSize(app, o.size.width, o.size.height)
  const boot = await page.evaluate(() => {
    const r = (window as unknown as { relay?: { apiBase: string | null; operatorToken: string } }).relay
    return r ? { apiBase: r.apiBase, token: r.operatorToken } : null
  })
  if (!boot) throw new Error('window.relay is missing')
  const apiBase = `${(boot.apiBase ?? `http://127.0.0.1:${httpPort ?? 8765}`).replace(/\/+$/, '').replace(/\/api\/v1$/, '')}/api/v1`
  if (!o.backendCmd && o.httpPort === undefined) await until(async () => (await fetch(`${apiBase}/health`)).ok, 'backend health', 30000)
  return {
    app,
    page,
    dataDir,
    apiBase,
    token: boot.token,
    listenPort,
    async close() {
      await app.close().catch(() => {})
      if (!o.dataDir) fs.rmSync(dataDir, { recursive: true, force: true })
    },
  }
}

/** Resize the window's content area (the shell's minimum is 1100 x 720). */
export async function setWindowSize(app: ElectronApplication, width: number, height: number): Promise<void> {
  await app.evaluate(({ BrowserWindow }, s) => {
    const w = BrowserWindow.getAllWindows()[0]
    w.setContentSize(s.width, s.height)
  }, { width, height })
  const page = app.windows()[0]
  await page.waitForFunction((s) => window.innerWidth === s.width && window.innerHeight === s.height, { width, height }, { timeout: 5000 }).catch(() => {})
}

/** Calls the backend REST API directly (operator token from the shell when `operator` is set). */
export async function rest<T = unknown>(r: Pick<RelayApp, 'apiBase' | 'token'>, method: string, route: string, body?: unknown, auth: 'operator' | { session: string } | 'none' = 'operator'): Promise<T> {
  const headers: Record<string, string> = { 'content-type': 'application/json' }
  if (auth === 'operator') headers['X-Relay-Operator'] = r.token
  else if (auth !== 'none') headers['X-Relay-Session'] = auth.session
  const res = await fetch(`${r.apiBase}${route}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  const text = await res.text()
  const data = text ? JSON.parse(text) : null
  if (!res.ok) throw new Error(`${method} ${route} -> ${res.status} ${text}`)
  return data as T
}

/** Polls `fn` until it returns a truthy value. */
export async function until<T>(fn: () => Promise<T> | T, what: string, timeoutMs = 20000, everyMs = 200): Promise<NonNullable<T>> {
  const end = Date.now() + timeoutMs
  let last: unknown
  while (Date.now() < end) {
    try {
      const v = await fn()
      if (v) return v as NonNullable<T>
    } catch (err) {
      last = err
    }
    await new Promise((r) => setTimeout(r, everyMs))
  }
  throw new Error(`timed out waiting for ${what}${last ? `: ${String(last)}` : ''}`)
}

/** Output collected from a child process since it started (waitForLine scans all of it). */
const output = new WeakMap<ChildProcess, { out: string; err: string; code: number | null; exited: boolean }>()

function collect(child: ChildProcess): void {
  const rec = { out: '', err: '', code: null as number | null, exited: false }
  output.set(child, rec)
  child.stdout?.on('data', (d: Buffer) => (rec.out += d.toString()))
  child.stderr?.on('data', (d: Buffer) => (rec.err += d.toString()))
  child.once('exit', (code) => {
    rec.code = code
    rec.exited = true
  })
}

/** Run a Python SDK snippet against the backend (stdlib-only client in sdk/python). */
export function startPython(script: string, env: Record<string, string>): ChildProcess {
  const child = spawn(PYTHON, ['-u', '-c', script], {
    cwd: ROOT,
    env: { ...(process.env as Record<string, string>), PYTHONPATH: path.join(ROOT, 'sdk', 'python'), ...env },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  collect(child)
  return child
}

/** Resolves with the first stdout line (since the process started) that matches. */
export async function waitForLine(child: ChildProcess, pattern: RegExp, timeoutMs = 30000): Promise<string> {
  const rec = output.get(child)
  if (!rec) throw new Error('waitForLine needs a process started by startPython')
  const end = Date.now() + timeoutMs
  for (;;) {
    const line = rec.out.split('\n').find((l) => pattern.test(l))
    if (line) return line
    if (rec.exited) throw new Error(`process exited with ${rec.code} before ${pattern}\nstdout:\n${rec.out}\nstderr:\n${rec.err}`)
    if (Date.now() > end) throw new Error(`no line matching ${pattern} in ${timeoutMs} ms\nstdout:\n${rec.out}\nstderr:\n${rec.err}`)
    await new Promise((r) => setTimeout(r, 100))
  }
}
