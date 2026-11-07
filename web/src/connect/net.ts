// Typed access to GET /network and the bridge routes with an explicit HTTP port (03 §9, §10.1). The shared api
// helpers address port 80 only; development fakes and bridges behind port forwarding need `?port=`.
import * as React from 'react'
import { api, request, type Ok } from '@/api'

export interface NetInterface {
  iface: string
  ip: string
  prefix: number
  network: string
  broadcast: string
  scannable: boolean
  skipped: string | null
  bridge_ap: boolean
}

export interface NetworkInfo {
  interfaces: NetInterface[]
  on_bridge_ap: boolean
  listen: { port: number; ok: boolean; error: string | null }
  bridge_port: number
  api_port: number
  allow_loopback?: boolean
}

export interface BridgeSettings {
  version: string | null
  mode: 'ap' | 'station' | string
  ip: string | null
  ap_ssid: string | null
  channel: number | null
  sta_ssid: string | null
  sta_ip: string | null
  sta_gateway: string | null
  sta_subnet: string | null
  dhcp: boolean
  host_port: number | null
  client_port: number | null
  baud: number | null
  has_ap_password: boolean
  has_sta_password: boolean
}

export interface BridgeRecord {
  host: string
  http_port: number
  kind: 'mavesp8266' | 'unknown' | string
  settings: BridgeSettings | null
  version: string | null
  form: unknown
  status?: Record<string, number> | null
  on_bridge_ap?: boolean
  last_network_ssid?: string | null
}

export interface BridgeConfig {
  mode: 'station' | 'ap'
  ssid?: string
  password?: string
  ip?: string
  gateway?: string
  subnet?: string
  host_port?: number
  client_port?: number
  baud?: number
}

const enc = encodeURIComponent
const portQuery = (port: number) => (port === 80 ? '' : `?port=${port}`)

export const net = {
  network: () => api.network() as Promise<NetworkInfo>,
  readBridge: (host: string, port: number) => request<BridgeRecord>('GET', `/bridges/${enc(host)}${portQuery(port)}`, undefined, 'operator'),
  configureBridge: (host: string, port: number, body: BridgeConfig) =>
    request<Ok & { submitted?: string[] }>('POST', `/bridges/${enc(host)}/configure${portQuery(port)}`, body, 'operator'),
}

/** GET /network, refreshed every `everyMs` while mounted. */
export function useNetwork(everyMs = 4000): { info: NetworkInfo | null; error: unknown } {
  const [state, setState] = React.useState<{ info: NetworkInfo | null; error: unknown }>({ info: null, error: null })
  React.useEffect(() => {
    let alive = true
    const load = () =>
      net.network().then(
        (info) => alive && setState({ info, error: null }),
        (error) => alive && setState((s) => ({ info: s.info, error })),
      )
    void load()
    const id = setInterval(load, everyMs)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [everyMs])
  return state
}
