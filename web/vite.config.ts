// ANet Relay renderer build. base './' so the bundle loads from app://relay/ (Electron) and file:// alike (03 §12).
// relayBootPlugin inlines the first frame of 02 §15 into index.html: the ANet mark (public/brand/anet-logo.svg) and
// the primitive colour tokens read from src/styles/theme.css, so the boot mask paints before CSS or JS arrive and
// no colour value is written anywhere but theme.css.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig, type Plugin } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const root = import.meta.dirname
/** The renderer's version, for the boot's "first launch of this version" outside Electron (app/bootCinema.ts). */
const version = (JSON.parse(readFileSync(resolve(root, 'package.json'), 'utf8')) as { version: string }).version

/** `:root{--g950:#..;...}` from the primitive layer of theme.css */
export function bootTokens(themeCss: string): string {
  const decls = [...themeCss.matchAll(/--(g\d{2,3}|white|r\d00):\s*(#[0-9A-Fa-f]{6})/g)].map((m) => `--${m[1]}:${m[2]}`)
  if (!decls.some((d) => d.startsWith('--g950:'))) throw new Error('relay-boot: theme.css has no --g950')
  return `:root{${decls.join(';')}}`
}

/** The ANet mark for the boot mask, ids prefixed so they never collide with other inline copies */
export function bootLogo(svg: string): string {
  return svg
    .replace(/<\?xml[^>]*>/, '')
    .replace(/\s(width|height)="\d+"/g, '')
    .replace(/mask0_57_38/g, 'boot-logo-mask')
    .replace('<svg', '<svg aria-hidden="true" focusable="false"')
    .trim()
}

function relayBootPlugin(): Plugin {
  return {
    name: 'relay-boot',
    transformIndexHtml: {
      order: 'pre',
      handler(html) {
        const theme = readFileSync(resolve(root, 'src/styles/theme.css'), 'utf8')
        const logo = readFileSync(resolve(root, 'public/brand/anet-logo.svg'), 'utf8')
        return html.replace('/*BOOT_TOKENS*/', bootTokens(theme)).replace('<!--BOOT_LOGO-->', bootLogo(logo))
      },
    },
  }
}

export default defineConfig({
  base: './',
  define: { __RELAY_VERSION__: JSON.stringify(version) },
  plugins: [relayBootPlugin(), react(), tailwindcss()],
  resolve: { alias: { '@': resolve(root, 'src') } },
  server: { host: '127.0.0.1', port: 5173, strictPort: true },
  preview: { host: '127.0.0.1', port: 4291, strictPort: true },
  build: {
    target: 'es2023',
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: process.env.RELAY_SOURCEMAP === "1",
    minify: process.env.RELAY_SOURCEMAP !== "1",
    chunkSizeWarningLimit: 1600,
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
})
