// Control frames of the window's own requests (12 §3.1). Since v0.6 the unnamed axes of skills and plan steps (x / y /
// z, vx / vy / vz, yaw, deg, yaw_rate_dps) follow the control_frame setting (FLU by default: counter-clockwise +).
// The window's buttons and the sequence editor mean what their labels say: "turn clockwise 45°" is clockwise
// whatever the setting. So every request the window builds with such a parameter carries frame "relay" (Relay's own
// axes, the pre-0.6 meaning: forward / right / up body, ENU local, compass heading, clockwise +). Named directions
// (forward, right, left, up, down) need no frame. Pure.
import type { PlanStep, RequestFrame, SkillName } from '@/api'

export const RELAY_FRAME: RequestFrame = 'relay'

type Args = Record<string, number | string>

/** True when a skill request uses a parameter whose meaning depends on the control frame. */
export function framedSkill(name: SkillName | 'velocity', args: Args = {}): boolean {
  if (name === 'yaw' || name === 'goto' || name === 'velocity') return true
  if (name === 'move') return 'x' in args || 'y' in args || 'z' in args
  return false
}

/** Skill arguments pinned to Relay's own axes when they need a frame and name none. */
export function relaySkillArgs(name: SkillName | 'velocity', args: Args = {}): Args {
  if (!framedSkill(name, args) || 'frame' in args) return args
  return { ...args, frame: RELAY_FRAME }
}

/** Plan steps pinned to Relay's own axes (backend plans.native does the same for Relay's own plans). */
export function relayPlan(steps: readonly PlanStep[]): PlanStep[] {
  return steps.map((s) => {
    const framed = s.action === 'yaw' || s.action === 'goto' || (s.action === 'move' && ('x' in s || 'y' in s || 'z' in s))
    return framed && !s.frame ? { ...s, frame: RELAY_FRAME } : s
  })
}
