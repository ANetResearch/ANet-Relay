import { describe, expect, it } from 'vitest'
import { isIPv4, onHotspotNet, parseHostPort } from './address'

describe('address parsing', () => {
  it('accepts dotted IPv4 only', () => {
    expect(isIPv4('10.7.7.36')).toBe(true)
    expect(isIPv4('192.168.4.1')).toBe(true)
    for (const bad of ['10.7.7', '10.7.7.256', '010.7.7.1', 'a.b.c.d', '1.2.3.4.5', '']) expect(isIPv4(bad)).toBe(false)
  })
  it('parses an optional port', () => {
    expect(parseHostPort('10.7.7.36', 14555)).toEqual({ host: '10.7.7.36', port: 14555 })
    expect(parseHostPort('127.0.0.2:8188', 80)).toEqual({ host: '127.0.0.2', port: 8188 })
    expect(parseHostPort('127.0.0.2:0', 80)).toBeNull()
    expect(parseHostPort('127.0.0.2:70000', 80)).toBeNull()
    expect(parseHostPort('host:80', 80)).toBeNull()
  })
  it('recognises the bridge hotspot network', () => {
    expect(onHotspotNet('192.168.4.2')).toBe(true)
    expect(onHotspotNet('192.168.40.2')).toBe(false)
  })
})
