import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { freeSeat, fromArena, peerHeadingToLocal, peerToLocal, peerVecToLocal, RADIUS, seatAngle, seatOrigin, SEATS, toArena } from './frame'

interface ArenaVectors {
  radius: number
  seats: { seat: number; phi: number; origin: number[] }[]
  cases: { seat_peer: number; seat_me: number; p_peer: number[]; p_local: number[]; p_arena: number[]; v_peer: number[]; v_local: number[]; heading_peer: number; heading_local: number }[]
  free_seat: { taken: number[]; after: number; seat: number | null }[]
}

const vectors = JSON.parse(readFileSync(resolve(import.meta.dirname, '../../../docs/design/vectors/arena.json'), 'utf8')) as ArenaVectors

function close(a: readonly number[], b: readonly number[]): void {
  expect(a.length).toBe(b.length)
  a.forEach((x, i) => expect(x).toBeCloseTo(b[i], 9))
}

describe('arena frames (shared vectors with the backend)', () => {
  it('seat angles and origins', () => {
    expect(vectors.radius).toBe(RADIUS)
    for (const s of vectors.seats) {
      expect(seatAngle(s.seat)).toBeCloseTo(s.phi, 9)
      close(seatOrigin(s.seat), s.origin)
    }
  })
  it('peer points, vectors and headings into the local frame', () => {
    for (const c of vectors.cases) {
      close(peerToLocal(c.p_peer, c.seat_peer, c.seat_me), c.p_local)
      close(toArena(c.seat_peer, c.p_peer), c.p_arena)
      close(peerVecToLocal(c.v_peer, c.seat_peer, c.seat_me), c.v_local)
      expect(peerHeadingToLocal(c.heading_peer, c.seat_peer, c.seat_me)).toBeCloseTo(c.heading_local, 9)
    }
  })
  it('free seats', () => {
    for (const f of vectors.free_seat) expect(freeSeat(f.taken, f.after)).toBe(f.seat)
  })
  it('round trips and faces the centre', () => {
    for (let k = 0; k < SEATS; k++) {
      close(fromArena(k, toArena(k, [1, -2, 3])), [1, -2, 3])
      const [tx, ty] = seatOrigin(k)
      const ahead = toArena(k, [0, 1, 0])
      expect((ahead[0] - tx) * -tx + (ahead[1] - ty) * -ty).toBeCloseTo(RADIUS, 9)
    }
    expect(() => seatAngle(12)).toThrow()
  })
})
