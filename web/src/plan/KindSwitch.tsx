// The Timeline's two kinds of document (14 §1): a flight plan (the default) or a stick timeline.
import { usePlan, type TimelineKind } from './store'
import { usePT } from './strings'

export function KindSwitch() {
  const pt = usePT()
  const kind = usePlan((s) => s.kind)
  return (
    <div className="seq-seg" role="group" title={`${pt('plan.kind.hint')} · Alt+K`} data-timeline-kind={kind}>
      {(['plan', 'sticks'] as const).map((k: TimelineKind) => (
        <button key={k} type="button" data-on={kind === k ? '' : undefined} onClick={() => usePlan.getState().setKind(k)} data-timeline-kind-button={k}>
          {pt(k === 'plan' ? 'plan.kind.plan' : 'plan.kind.sticks')}
        </button>
      ))}
    </div>
  )
}
