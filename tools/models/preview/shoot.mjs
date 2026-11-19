// Renders a model with the stage's light rig in headless Chromium (software WebGL) and saves PNG screenshots.
//
//   node tools/models/preview/shoot.mjs --model web/public/models/anet_du7.glb --out /tmp/shots [--views hero,side] [--prefix du7]
//
// --model takes a .glb or a ROS .stl (Z up, shown centred with its feet at y = 0); paths outside the repo are allowed.
// Views are the presets below; each writes <out>/<prefix>_<view>.png.
import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')

const VIEWS = {
  hero: { az: 22, el: 11, dist: 1.2, target: [0, 0.1, 0] }, // front three-quarter, like the poster
  front: { az: 0, el: 6, dist: 1.35, target: [0, 0.09, 0] },
  side: { az: 90, el: 4, dist: 1.35, target: [0, 0.1, 0] },
  rear: { az: 200, el: 22, dist: 1.35, target: [0, 0.09, 0] },
  top: { az: 0, el: 90, dist: 1.45, target: [0, 0.1, 0] },
  under: { az: 32, el: -16, dist: 1.0, target: [0, 0.06, 0.04], grid: false, shadow: false },
  gimbal: { az: 30, el: 6, dist: 0.5, target: [0, 0.06, 0.1], fov: 30 },
  nose: { az: 18, el: 18, dist: 0.62, target: [0, 0.12, 0.08] },
  posed: { az: 28, el: 6, dist: 0.62, target: [0, 0.055, 0.1], gimbalYaw: -55, gimbalPitch: 35, prop: 37, fov: 26 },
  posedtop: { az: 20, el: 55, dist: 1.3, target: [0, 0.1, 0], gimbalYaw: 35, gimbalPitch: 40, prop: 37 },
  low: { az: 145, el: 3, dist: 1.3, target: [0, 0.09, 0] },
  photo: { az: 0, el: 9.6, dist: 1.44, fov: 24.3, target: [0, 0.13, 0], width: 1280, height: 720, grid: false }, // camera of assets/tangyuan-u7.jpg
  far: { az: 35, el: 24, dist: 3.2, target: [0, 0.1, 0] }, // roughly how the stage frames an aircraft
  corner: { az: 70, el: 22, dist: 0.32, target: [0.27, 0.11, 0.27], grid: false },
  deck: { az: 150, el: 35, dist: 0.5, target: [0, 0.14, -0.02], grid: false },
  lidar: { az: 75, el: 15, dist: 0.38, target: [0, 0.13, 0.05], grid: false },
  belly: { az: 110, el: 2, dist: 0.6, target: [0, 0.05, 0], grid: false },
}

const args = Object.fromEntries(process.argv.slice(2).join(' ').split('--').filter(Boolean).map((s) => {
  const [k, ...v] = s.trim().split(/\s+/)
  return [k, v.join(' ')]
}))
const model = path.resolve(args.model ?? path.join(ROOT, 'web/public/models/anet_du7.glb'))
const out = path.resolve(args.out ?? '.')
const prefix = args.prefix ?? path.basename(model).replace(/\.\w+$/, '')
const views = (args.views ?? Object.keys(VIEWS).join(',')).split(',')
const extra = args.opts ? JSON.parse(args.opts) : {}
fs.mkdirSync(out, { recursive: true })

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.glb': 'model/gltf-binary', '.stl': 'model/stl' }
const server = http.createServer((req, res) => {
  const url = decodeURIComponent(new URL(req.url, 'http://x').pathname)
  const file = url.startsWith('/@model') ? model : path.join(ROOT, path.normalize(url))
  if (file !== model && !file.startsWith(ROOT + path.sep)) return res.writeHead(403).end()
  fs.readFile(file, (err, data) => {
    if (err) return res.writeHead(404).end()
    res.writeHead(200, { 'content-type': TYPES[path.extname(file)] ?? 'application/octet-stream' }).end(data)
  })
})
await new Promise((r) => server.listen(0, '127.0.0.1', r))
const port = server.address().port

const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] })
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
  page.on('console', (m) => { if (m.type() === 'error') console.error('page:', m.text()) })
  page.on('pageerror', (e) => console.error('page error:', e.message))
  await page.goto(`http://127.0.0.1:${port}/tools/models/preview/preview.html`)
  await page.waitForFunction(() => window.ready === true)
  for (const v of views) {
    const o = { ...VIEWS[v], model: `/@model${model.endsWith('.stl') ? '.stl' : ''}`, stlFit: model.endsWith('.stl'), ...extra }
    await page.setViewportSize({ width: o.width ?? 1280, height: o.height ?? 800 })
    const info = await page.evaluate((opts) => window.view(opts), o)
    const file = path.join(out, `${prefix}_${v}.png`)
    await page.locator('canvas').screenshot({ path: file })
    console.log(file, info.calls, 'draw calls,', info.triangles, 'triangles')
  }
} finally {
  await browser.close()
  server.close()
}
