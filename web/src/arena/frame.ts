// Arena seats and frames (05 §7.2). The same formulas as backend/relay/peers/arena.py, checked against the shared
// vectors in docs/design/vectors/arena.json (frame.test.ts). Pure: no three.js, no React.
//
// Twelve seats sit on a 10 m circle. Seat k turns a terminal's local sim frame (ENU metres) by phi_k = 2 pi k / 12
// counter-clockwise about +z and puts its origin at T_k = (10 sin phi_k, -10 cos phi_k), so local +y faces the
// centre. A peer's local point in this terminal's frame: p_local = R(me)^-1 · (R(peer) · p_peer + T(peer) - T(me)).
// Compass headings (0 north, clockwise) turn the other way: heading_arena = heading_local - phi.

export type Vec3 = [number, number, number]

export const SEATS = 12
export const RADIUS = 10

export function seatAngle(seat: number): number {
  if (!Number.isInteger(seat) || seat < 0 || seat >= SEATS) throw new RangeError('seat')
  return (2 * Math.PI * seat) / SEATS
}

export function seatOrigin(seat: number): [number, number] {
  const phi = seatAngle(seat)
  return [RADIUS * Math.sin(phi), -RADIUS * Math.cos(phi)]
}

/** Rotate an ENU vector counter-clockwise by phi about +z. */
export function rotate(v: readonly number[], phi: number): Vec3 {
  const c = Math.cos(phi)
  const s = Math.sin(phi)
  return [c * v[0] - s * v[1], s * v[0] + c * v[1], v[2]]
}

export function toArena(seat: number, p: readonly number[]): Vec3 {
  const [x, y, z] = rotate(p, seatAngle(seat))
  const [tx, ty] = seatOrigin(seat)
  return [x + tx, y + ty, z]
}

export function fromArena(seat: number, p: readonly number[]): Vec3 {
  const [tx, ty] = seatOrigin(seat)
  return rotate([p[0] - tx, p[1] - ty, p[2]], -seatAngle(seat))
}

export function peerToLocal(p: readonly number[], seatPeer: number, seatMe: number): Vec3 {
  return fromArena(seatMe, toArena(seatPeer, p))
}

export function peerVecToLocal(v: readonly number[], seatPeer: number, seatMe: number): Vec3 {
  return rotate(v, seatAngle(seatPeer) - seatAngle(seatMe))
}

function wrap(a: number): number {
  const t = (a + Math.PI) % (2 * Math.PI)
  return (t < 0 ? t + 2 * Math.PI : t) - Math.PI
}

export function peerHeadingToLocal(heading: number, seatPeer: number, seatMe: number): number {
  return wrap(heading - seatAngle(seatPeer) + seatAngle(seatMe))
}

/** Smallest free seat after `after` (wrapping round), or null when all twelve are taken. */
export function freeSeat(taken: Iterable<number>, after = -1): number | null {
  const used = new Set(taken)
  const start = after < 0 ? 0 : after + 1
  for (let k = 0; k < SEATS; k++) {
    const seat = (start + k) % SEATS
    if (!used.has(seat)) return seat
  }
  return null
}
