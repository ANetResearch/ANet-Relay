// Value formatting (02 §2.2, §4.4). Pure functions, no React. Every formatter returns null for unknown input so the
// caller can show "—" with a reason (02 §1.5); numbers use a true minus sign and fixed widths for tabular display.

export const DASH = '—'
const MINUS = '−'

const finite = (v: number | null | undefined): v is number => typeof v === 'number' && Number.isFinite(v)

/** Signed, zero-padded: signed(1.4, 2, 1) = "+01.4", signed(-0.8, 2, 1) = "−00.8". */
export function signed(v: number | null | undefined, intDigits: number, decimals: number): string | null {
  if (!finite(v)) return null
  const r = Number(v.toFixed(decimals))
  const sign = r > 0 ? '+' : r < 0 ? MINUS : '+'
  const [i, f] = Math.abs(r).toFixed(decimals).split('.')
  return `${sign}${i.padStart(intDigits, '0')}${f !== undefined ? `.${f}` : ''}`
}

export const radToDeg = (r: number) => (r * 180) / Math.PI

/** Roll / pitch from radians: "+01.4°". */
export function angle(rad: number | null | undefined): string | null {
  const s = signed(finite(rad) ? radToDeg(rad) : null, 2, 1)
  return s === null ? null : `${s}°`
}

/** Heading or yaw in degrees 0..359: "184°", "007°". */
export function heading(deg: number | null | undefined): string | null {
  if (!finite(deg)) return null
  const d = ((Math.round(deg) % 360) + 360) % 360
  return `${String(d).padStart(3, '0')}°`
}

/** Yaw from radians as a heading. */
export function yaw(rad: number | null | undefined): string | null {
  return heading(finite(rad) ? radToDeg(rad) : null)
}

export function metres(v: number | null | undefined, decimals = 2): string | null {
  return finite(v) ? `${v.toFixed(decimals)} m` : null
}

export function climb(v: number | null | undefined): string | null {
  const s = signed(v, 1, 1)
  return s === null ? null : `${s} m/s`
}

export function speed(v: number | null | undefined): string | null {
  return finite(v) ? `${v.toFixed(1)} m/s` : null
}

export function volts(v: number | null | undefined): string | null {
  return finite(v) ? `${v.toFixed(1)} V` : null
}

export function amps(v: number | null | undefined): string | null {
  return finite(v) ? `${v.toFixed(1)} A` : null
}

export function percent(v: number | null | undefined): string | null {
  return finite(v) ? `${Math.round(v)} %` : null
}

export function hertz(v: number | null | undefined): string | null {
  return finite(v) ? `${v >= 10 ? Math.round(v) : v.toFixed(1)} Hz` : null
}

export function millis(v: number | null | undefined): string | null {
  return finite(v) ? `${Math.round(v)} ms` : null
}

/** Seconds with one decimal: "3.4 s". */
export function seconds1(v: number | null | undefined): string | null {
  return finite(v) ? `${v.toFixed(1)} s` : null
}

/** Whole seconds for link ages in words ("Lost 7 s"). */
export function wholeSeconds(v: number | null | undefined): number | null {
  return finite(v) ? Math.max(0, Math.floor(v)) : null
}

/** Flight time: "02:14", "1:02:14". */
export function duration(s: number | null | undefined): string | null {
  if (!finite(s) || s < 0) return null
  const total = Math.floor(s)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const sec = total % 60
  const mm = String(m).padStart(2, '0')
  const ss = String(sec).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

/** Course clock: "00:12.48". */
export function courseTime(s: number | null | undefined): string | null {
  if (!finite(s) || s < 0) return null
  const cs = Math.round(s * 100)
  const m = Math.floor(cs / 6000)
  const rest = (cs % 6000) / 100
  return `${String(m).padStart(2, '0')}:${rest.toFixed(2).padStart(5, '0')}`
}

/** Course split: "+3.42". */
export function split(s: number | null | undefined): string | null {
  return finite(s) ? `+${s.toFixed(2)}` : null
}

/** Wall-clock time of a backend timestamp (unix seconds): "14:02:31". */
export function clock(t: number | null | undefined): string | null {
  if (!finite(t)) return null
  const d = new Date(t * 1000)
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':')
}

/** Relative age bucket for "{n} s ago" style copy. */
export function ageParts(nowS: number, thenS: number): { unit: 'now' | 's' | 'min' | 'h'; n: number } {
  const d = Math.max(0, nowS - thenS)
  if (d < 2) return { unit: 'now', n: 0 }
  if (d < 60) return { unit: 's', n: Math.floor(d) }
  if (d < 3600) return { unit: 'min', n: Math.floor(d / 60) }
  return { unit: 'h', n: Math.floor(d / 3600) }
}

/** Battery fill in 0..5 cells (02 §4.3): from remaining %, else from voltage per cell (3.3 .. 4.2 V). */
export function batteryCells(b: { remaining_pct: number | null; voltage: number | null; cells: number | null }): number | null {
  let frac: number | null = null
  if (finite(b.remaining_pct)) frac = b.remaining_pct / 100
  else if (finite(b.voltage) && finite(b.cells) && b.cells > 0) frac = (b.voltage / b.cells - 3.3) / 0.9
  if (frac === null) return null
  return Math.max(0, Math.min(5, Math.ceil(Math.max(0, Math.min(1, frac)) * 5 - 1e-9)))
}
