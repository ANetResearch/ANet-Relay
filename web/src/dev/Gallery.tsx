// Dev-only primitives gallery (?fixture=busy&gallery=1): every shared primitive against the fixture aircraft, so
// the next work packages can see the vocabulary they compose. Not reachable in production navigation.
import * as React from 'react'
import { useT } from '@/i18n'
import { angle, climb, metres, volts } from '@/lib/format'
import { useRelay } from '@/store/relay'
import { useVehicleIds, useVehicle } from '@/store/selectors'
import { Button } from '@/ui/components/ui/button'
import {
  ArmedWord, BatteryReadout, ControllerPill, EmptyState, Group, HoldProgress, LinkStatus, Readout, Section, SimTag, TypeToConfirm, Value,
} from '@/ui/relay'
import { possess, release } from '@/shell/actions'

function Row({ id }: { id: string }) {
  const v = useVehicle(id)
  const t = useT()
  if (!v) return null
  return (
    <div className="grid grid-cols-[150px_150px_90px_200px_1fr_auto] items-center gap-3 border-b border-[var(--border)] px-3 py-2">
      <span className="flex items-center gap-1.5 text-hud-title font-[560] text-[var(--g100)]">
        {v.kind === 'sim' ? <SimTag /> : null}
        {v.label}
      </span>
      <LinkStatus link={v.link} critical={v.link.state === 'lost' && v.armed} />
      <ArmedWord armed={v.armed} />
      <BatteryReadout battery={v.battery} />
      <ControllerPill vehicle={v} showWaiting />
      <Button size="sm" variant="outline" onClick={() => (v.controller === 'human' ? release(v.id) : possess(v.id))}>
        {v.controller === 'human' ? t('action.release') : t('action.possess')}
      </Button>
    </div>
  )
}

export function Gallery() {
  const ids = useVehicleIds()
  const q2 = useRelay((s) => s.fleet?.vehicles.find((v) => v.id === 'q02') ?? null)
  const [hold, setHold] = React.useState(false)
  return (
    <div className="absolute inset-0 overflow-auto bg-[var(--g950)] p-4">
      <div className="mx-auto flex max-w-[1100px] flex-col gap-4">
        <div className="l1 rounded-[var(--radius)]">
          <Section title="Primitives · fleet rows">
            {ids.map((id) => (
              <Row key={id} id={id} />
            ))}
          </Section>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div className="l1 flex flex-col gap-4 rounded-[var(--radius)] pb-3">
            <Section title="Values">
              <div className="grid grid-cols-3 gap-3 px-3">
                <Readout label="ROLL" size="kpi"><Value value={angle(q2?.attitude?.roll)} reason="—" /></Readout>
                <Readout label="ALT" size="kpi"><Value value={metres(q2?.altitude_m)} reason="—" /></Readout>
                <Readout label="VS" size="kpi"><Value value={climb(q2?.climb_ms)} reason="—" /></Readout>
                <Readout label="BAT"><Value value={volts(q2?.battery.voltage)} reason="—" /></Readout>
                <Readout label="GPS"><Value value={null} reason="The autopilot does not send GPS_RAW_INT" /></Readout>
              </div>
            </Section>
            <Group label="Collapsible group" count={3} collapsible>
              <div className="px-3 text-hud-sub text-[var(--g400)]">Group body</div>
            </Group>
          </div>
          <div className="l1 flex flex-col gap-3 rounded-[var(--radius)] p-3">
            <TypeToConfirm word="ARM" prompt="Type ARM to arm Quad 02." actionLabel="Arm" onConfirm={() => {}} autoFocus={false} />
            <div className="flex items-center gap-3">
              <Button size="sm" variant="outline" onPointerDown={() => setHold(true)} onPointerUp={() => setHold(false)} onPointerLeave={() => setHold(false)}>
                Hold 1 s
              </Button>
              <HoldProgress active={hold} durationMs={1000} className="w-40" />
            </div>
            <div className="h-48 rounded-md border border-[var(--border)]">
              <EmptyState title="No aircraft yet" body="Relay listens on UDP 14550." actions={<Button size="lg" variant="outline">Scan network</Button>} />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
