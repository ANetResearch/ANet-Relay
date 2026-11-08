// Collision volumes of v0.5 (07 §2), mirrored from backend/relay/sim/collide.py for drawing only: the renderer never
// decides a collision, it shows the volumes the backend sweeps and the effects of the events it reports.

/** aircraft sphere, m (25 cm motor-to-motor plus the propeller discs) */
export const CRAFT_RADIUS = 0.15
/** ring tube radius, m; the ring's major radius is the course radius */
export const RING_TUBE = 0.03
/** bullet hit sphere, m (05 §7.4, unchanged in 07 §2) */
export const HIT_RADIUS = 0.12
/** relative normal speed at or above which a contact crashes both bodies, m/s */
export const RAM_CRASH_V = 2.0
