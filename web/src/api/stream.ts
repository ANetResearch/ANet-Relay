// The one stream connection of this window. app/connection.ts installs it; control/InputLayer is the only caller
// of sendSticks (CONTRACTS.md). Without an open connection sticks are dropped, never queued.

export interface StickSink {
  sendSticks(vehicle: string, s: { roll: number; pitch: number; yaw: number; throttle: number }): boolean
  cancelSticks(vehicle?: string): void
  reconnect(): void
  /** Sends one JSON message (ui_state, ui_result); false when the socket is not open. */
  send?(message: unknown): boolean
}

let instance: StickSink | null = null

export function installStream(s: StickSink | null): void {
  instance = s
}

export function stream(): StickSink | null {
  return instance
}

/** Human sticks over WS, at most 50 Hz per vehicle (03 §10.2). Returns false when the frame was dropped. */
export function sendSticks(vehicle: string, s: { roll: number; pitch: number; yaw: number; throttle: number }): boolean {
  return instance ? instance.sendSticks(vehicle, s) : false
}

export function cancelSticks(vehicle?: string): void {
  instance?.cancelSticks(vehicle)
}

/** Generic client message over the operator socket (04 §3.1). Dropped, never queued, while the socket is down. */
export function sendJson(message: unknown): boolean {
  return instance?.send ? instance.send(message) : false
}
