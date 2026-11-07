// Address parsing for the connect dialog (02 §8): dotted IPv4 with an optional :port. Pure, unit-tested.

/** Strict dotted-quad IPv4 (no leading zeros beyond a single 0, each part 0..255). */
export function isIPv4(s: string): boolean {
  const parts = s.trim().split('.')
  if (parts.length !== 4) return false
  return parts.every((p) => /^(0|[1-9]\d{0,2})$/.test(p) && Number(p) <= 255)
}

export function isPort(n: number): boolean {
  return Number.isInteger(n) && n >= 1 && n <= 65535
}

/** "10.7.7.36" or "127.0.0.2:8188" -> { host, port }; null when invalid. */
export function parseHostPort(s: string, defaultPort: number): { host: string; port: number } | null {
  const v = s.trim()
  const m = v.match(/^([^:]+)(?::(\d{1,5}))?$/)
  if (!m || !isIPv4(m[1])) return null
  const port = m[2] === undefined ? defaultPort : Number(m[2])
  return isPort(port) ? { host: m[1], port } : null
}

/** True when the address lies in 192.168.4.0/24, the mavesp8266 hotspot network (03 §9). */
export function onHotspotNet(ip: string): boolean {
  return isIPv4(ip) && ip.startsWith('192.168.4.')
}
