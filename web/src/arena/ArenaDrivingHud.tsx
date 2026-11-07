// The driving HUD with its arena row (05 §6.4 third row, §7.5): hit points and magazine of the aircraft this window
// flies, from the 20 Hz arena message. Without the arena (or for an aircraft outside it) the HUD shows only the
// throttle bar, exactly as WP-C draws it. Mounted by Stage() in place of the bare DrivingHud.
import { DrivingHud, useDrivenId, type HudAmmo } from '@/stage/DrivingHud'
import { useArenaLocal, type ArenaLocal } from './store'

/** DrivingHud row-3 values for one arena local entry (hp scaled to 0..100). */
export function arenaHudSlot(local: ArenaLocal | null): { hp?: number; ammo?: HudAmmo } {
  if (!local) return {}
  return {
    hp: (local.hp / Math.max(1, local.maxHp)) * 100,
    ammo: { n: local.ammo.n, max: local.ammo.max, reloading: local.ammo.reloading, progress: local.ammo.progress },
  }
}

export function ArenaDrivingHud({ bottom }: { bottom: number }) {
  const local = useArenaLocal(useDrivenId())
  const { hp, ammo } = arenaHudSlot(local)
  return <DrivingHud bottom={bottom} hp={hp} ammo={ammo} />
}
