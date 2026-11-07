// Window preferences (02 §16 General / Controllers): persisted in the backend settings under `ui` and cached in
// localStorage so the first frame already uses them. Read by the stage (start view, chase on course) and the
// controller input (dead zone, expo); see CONTRACTS.md §5.
import { create } from 'zustand'
import { api } from '@/api'
import { DEFAULT_BOOT_MODE, parseBootMode, writeBootMode, type BootMode } from '@/app/bootFilm'

export type Units = 'm' | 'ft'
export type StartView = 'overview' | 'chase' | 'top'

export interface Prefs {
  units: Units
  startView: StartView
  chaseOnCourse: boolean
  padDeadzone: number
  padExpo: number
  /** WP-A: Settings > General > Opening sequence (05 §2.1) */
  bootMode: BootMode
}

export const DEFAULT_PREFS: Prefs = { units: 'm', startView: 'overview', chaseOnCourse: true, padDeadzone: 0.08, padExpo: 0.3, bootMode: DEFAULT_BOOT_MODE }
export const DEADZONE_RANGE = [0.04, 0.15] as const
export const EXPO_RANGE = [0, 0.6] as const

const KEY = 'relay.prefs'
const clamp = (v: number, [lo, hi]: readonly [number, number]) => Math.min(hi, Math.max(lo, v))

/** Accepts any stored object and returns valid preferences (unknown or invalid fields fall back to defaults). */
export function sanitizePrefs(raw: unknown): Prefs {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const num = (v: unknown, d: number) => (typeof v === 'number' && Number.isFinite(v) ? v : d)
  return {
    units: r.units === 'ft' ? 'ft' : 'm',
    startView: r.startView === 'chase' || r.startView === 'top' ? r.startView : 'overview',
    chaseOnCourse: typeof r.chaseOnCourse === 'boolean' ? r.chaseOnCourse : DEFAULT_PREFS.chaseOnCourse,
    padDeadzone: clamp(num(r.padDeadzone, DEFAULT_PREFS.padDeadzone), DEADZONE_RANGE),
    padExpo: clamp(num(r.padExpo, DEFAULT_PREFS.padExpo), EXPO_RANGE),
    bootMode: parseBootMode(r.bootMode) ?? DEFAULT_PREFS.bootMode,
  }
}

function loadLocal(): Prefs {
  try {
    const raw = localStorage.getItem(KEY)
    return sanitizePrefs(raw ? JSON.parse(raw) : null)
  } catch {
    return { ...DEFAULT_PREFS }
  }
}

function saveLocal(p: Prefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(p))
  } catch {
    /* storage unavailable: the backend copy still persists */
  }
}

export const usePrefs = create<Prefs>()(() => (typeof localStorage === 'undefined' ? { ...DEFAULT_PREFS } : loadLocal()))

// WP-A (05 §2.1): the opening runs before the backend answers, so the setting is mirrored into relay.boot.mode
usePrefs.subscribe((p, prev) => {
  if (p.bootMode !== prev.bootMode) writeBootMode(typeof localStorage === 'undefined' ? null : localStorage, p.bootMode)
})

/** Other keys of the backend `ui` object are kept when the window writes its preferences. */
let backendUi: Record<string, unknown> = {}
let timer: ReturnType<typeof setTimeout> | null = null

function flush(): void {
  timer = null
  const p = usePrefs.getState()
  const ui = { ...backendUi, ...p }
  backendUi = ui
  api.putSettings({ ui }).catch(() => {
    /* offline or read-only: the local cache keeps the value; the next write retries */
  })
}

export function setPrefs(patch: Partial<Prefs>): void {
  const next = sanitizePrefs({ ...usePrefs.getState(), ...patch })
  usePrefs.setState(next)
  saveLocal(next)
  if (timer) clearTimeout(timer)
  timer = setTimeout(flush, 400)
}

/** Applies the `ui` object of GET /settings (called once the backend is reachable). */
export function applyBackendUi(ui: unknown): void {
  backendUi = ui && typeof ui === 'object' ? { ...(ui as Record<string, unknown>) } : {}
  const keys = Object.keys(DEFAULT_PREFS)
  if (!keys.some((k) => k in backendUi)) return
  const next = sanitizePrefs({ ...usePrefs.getState(), ...backendUi })
  usePrefs.setState(next)
  saveLocal(next)
}

const FT = 3.28084

/** "1.26 m" or "4.1 ft"; null for unknown input. */
export function formatLength(m: number | null | undefined, units: Units, decimals = 2): string | null {
  if (typeof m !== 'number' || !Number.isFinite(m)) return null
  return units === 'ft' ? `${(m * FT).toFixed(Math.max(0, decimals - 1))} ft` : `${m.toFixed(decimals)} m`
}

export function useLength(): (m: number | null | undefined, decimals?: number) => string | null {
  const units = usePrefs((p) => p.units)
  return (m, decimals = 2) => formatLength(m, units, decimals)
}
