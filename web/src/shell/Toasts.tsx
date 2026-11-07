// Toast viewport (02 §14, §18): bottom centre, above the relay timeline; L3 surface; translateY 8 + opacity in
// (fast), opacity out (quick). Also turns selected events into toasts (handback, released, discovered).
import * as React from 'react'
import { t, useLangStore } from '@/i18n'
import { useRelay } from '@/store/relay'
import { getVehicle } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { Toast, ToastContent, ToastDescription, ToastPortal, ToastProvider, ToastTitle, ToastViewport, toast, useToastManager } from '@/ui/components/ui/toast'
import { notify } from './notify'

function List() {
  const { toasts } = useToastManager()
  return toasts.map((item) => (
    <Toast key={item.id} toast={item} className="l3 rounded-lg">
      <ToastContent className="gap-2 px-3.5 py-2.5">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <ToastTitle className="text-ed-value font-[500] text-[var(--g100)]" />
          <ToastDescription className="text-hud-sub text-[var(--g400)]" />
        </div>
      </ToastContent>
    </Toast>
  ))
}

/** Event kinds that deserve a toast (02 §5.4, §14 info). */
function useEventToasts() {
  React.useEffect(() => {
    let last = useRelay.getState().lastSeq
    return useRelay.subscribe((s) => {
      if (s.lastSeq === last) return
      const fresh = s.events.filter((e) => e.seq > last)
      last = s.lastSeq
      // history replayed on connect is older than a few seconds; only live events toast
      const now = s.fleet?.t ?? Date.now() / 1000
      for (const e of fresh) {
        if (now - e.t > 5) continue
        const v = e.vehicle ? getVehicle(e.vehicle) : null
        const aircraft = v?.label ?? (e.data?.label as string | undefined) ?? e.vehicle ?? ''
        const name = (e.data?.name as string | undefined) ?? e.session ?? ''
        if (e.kind === 'relay_handback') notify(t('relay.handback', { aircraft, agent: name }))
        else if (e.kind === 'possession_released') notify(t('possess.released', { aircraft }))
        else if (e.kind === 'vehicle_discovered') notify(t('toast.vehicleDiscovered', { aircraft }))
        else if (e.kind === 'course_finished' && v?.controller === 'agent' && v.claim) {
          const lang = useLangStore.getState().lang
          const course = (lang === 'zh' ? v.course?.name_zh : v.course?.name_en) ?? v.course?.course_id ?? ''
          notify(t('course.agentFinished', { agent: v.claim.name, course, time: String(e.data?.total_text ?? '') }))
        }
      }
    })
  }, [])
}

export function RelayToaster({ children }: { children: React.ReactNode }) {
  useEventToasts()
  const collapsed = useUi((s) => s.timelineCollapsed)
  const fly = useUi((s) => s.workspace === 'fly')
  // in Fly the row clears the stage's bottom HUD (strip and lifted stick panel; the stage publishes the height
  // above its bottom edge, which sits 8 above the timeline); elsewhere 24 above the timeline
  const lift = fly ? 'calc(8px + var(--stage-toast-lift, 88px))' : '24px'
  return (
    <ToastProvider toastManager={toast} limit={3}>
      {children}
      <ToastPortal>
        <ToastViewport
          className="fixed inset-x-auto left-1/2 mx-0 w-[min(420px,calc(100vw-32px))] -translate-x-1/2 sm:right-auto sm:left-1/2"
          style={{ bottom: `calc(${collapsed ? 'var(--timeline-h-collapsed)' : 'var(--timeline-h)'} + ${lift})` }}
        >
          <List />
        </ToastViewport>
      </ToastPortal>
    </ToastProvider>
  )
}
