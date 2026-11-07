// Binds the one TimelineModel of this window to the relay store: new events first, then the snapshot.
import { useRelay } from '@/store/relay'
import { TimelineModel } from './model'

export const timeline = new TimelineModel()
const KEEP_S = 16 * 60

let bound = false
export function bindTimeline(): () => void {
  if (bound) return () => {}
  bound = true
  let lastEvents: unknown = null
  let lastFleet: unknown = null
  const sync = () => {
    const s = useRelay.getState()
    if (s.events !== lastEvents) {
      lastEvents = s.events
      timeline.applyEvents(s.events)
    }
    if (s.fleet && s.fleet !== lastFleet) {
      lastFleet = s.fleet
      timeline.applySnapshot(s.fleet)
      timeline.prune(s.fleet.t - KEEP_S)
    }
  }
  sync()
  const unsub = useRelay.subscribe(sync)
  return () => {
    unsub()
    bound = false
  }
}
