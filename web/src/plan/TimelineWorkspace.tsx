// The Timeline tab (14 §1): flight plans by default, the stick timeline of 0.6.1 behind the switch in the
// document bar (Alt+K). Each kind keeps its own document, selection and undo.
import * as React from 'react'
import { registerHotkey } from '@/shell/keyboard'
import { SequenceWorkspace } from '@/sequence/SequenceWorkspace'
import { PlanWorkspace } from './PlanWorkspace'
import { usePlan } from './store'

export function TimelineWorkspace() {
  const kind = usePlan((s) => s.kind)
  React.useEffect(
    () =>
      registerHotkey({
        combo: 'alt+k',
        workspace: 'sequence',
        inText: true,
        handler: (e) => {
          e.stopPropagation()
          usePlan.getState().setKind(usePlan.getState().kind === 'plan' ? 'sticks' : 'plan')
        },
      }),
    [],
  )
  const swapping = usePlan((s) => s.swapping)
  if (swapping) return <section className="seqws l1" />
  return kind === 'plan' ? <PlanWorkspace /> : <SequenceWorkspace />
}
