// Control tab entry of the stick timeline (10 §2, 0.6.1): what is loaded, a way into the Timeline workspace with this
// aircraft as its target, and Stop while this aircraft flies a sequence (so stopping never needs a tab switch).
import { api } from '@/api'
import { notifyError } from '@/shell/notify'
import { useSelectedVehicle } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { Button } from '@/ui/components/ui/button'
import { Icon } from '@/ui/icons'
import { Group } from '@/ui/relay'
import { fmtS } from './parts'
import { flightOf } from './run'
import { useSequence } from './store'
import { usePlan } from '@/plan/store'
import { useST } from './strings'

export function SequenceEntry() {
  const st = useST()
  const v = useSelectedVehicle()
  const name = useSequence((s) => s.doc.name)
  const seconds = useSequence((s) => s.doc.duration_s)
  const flight = flightOf(v)
  return (
    <Group label={st('seq.title')}>
      <div className="flex items-center gap-2 px-3" data-sequence-entry="">
        <span className="min-w-0 flex-1 truncate text-hud-sub text-[var(--g300)]">
          {flight.flying && flight.progress != null ? (
            <span className="num">{st('seq.state.play', { t: fmtS(flight.progress), total: fmtS(seconds) })}</span>
          ) : (
            <>
              {name || '—'} <span className="num text-[var(--g500)]">· {seconds.toFixed(1)} s</span>
            </>
          )}
        </span>
        {flight.flying && v ? (
          <Button variant="outline" size="sm" onClick={() => void api.stopSequence(v.id).catch(notifyError)} data-sequence-stop="">
            <Icon icon="stop" />
            {st('seq.entry.stop')}
          </Button>
        ) : null}
        <Button variant="outline" size="sm" onClick={() => { usePlan.getState().setKind('sticks'); useUi.getState().setWorkspace('sequence') }} data-sequence-toggle="open">
          <Icon icon="seq.timeline" />
          {st('seq.open')}
        </Button>
      </div>
    </Group>
  )
}
