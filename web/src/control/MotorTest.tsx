// Motor test (02 §11, R-29; collapsible): disarmed real aircraft only, propellers off. Motor 1-4, throttle 5-15 %,
// 2 s, confirmed by typing MOTOR (03 §6.1).
import * as React from 'react'
import { api, RelayApiError, type Vehicle } from '@/api'
import { useLang, useT } from '@/i18n'
import { notify } from '@/shell/notify'
import { Slider } from '@/ui/components/ui/slider'
import { ToggleGroup, ToggleGroupItem } from '@/ui/components/ui/toggle-group'
import { Button } from '@/ui/components/ui/button'
import { Group, TypeToConfirm } from '@/ui/relay'
import { useCT } from './strings'

const SECONDS = 2

export function MotorTest({ vehicle }: { vehicle: Pick<Vehicle, 'id' | 'kind' | 'armed' | 'link'> }) {
  const t = useT()
  const ct = useCT()
  const lang = useLang()
  const [motor, setMotor] = React.useState(1)
  const [pct, setPct] = React.useState(5)
  const [confirming, setConfirming] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const unavailable =
    vehicle.kind === 'sim' ? ct('ctl.motorUnavailableSim') : vehicle.armed ? ct('ctl.motorUnavailableArmed') : vehicle.link.state !== 'ok' && vehicle.link.state !== 'delayed' ? t('value.reason.notLive') : null
  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.motorTest(vehicle.id, motor, pct, SECONDS)
      notify(ct('ctl.motorSent', { n: motor }))
      setConfirming(false)
    } catch (e) {
      setError(e instanceof RelayApiError ? e.text(lang) : String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Group label={t('ctl.motorTest')} collapsible defaultOpen={false}>
      <div className="flex flex-col gap-3 px-3 pb-1" data-motor-test="">
        <p className="text-hud-sub text-[var(--g400)]">{unavailable ?? t('ctl.motorNote')}</p>
        <div className="flex items-center gap-3">
          <ToggleGroup
            value={[String(motor)]}
            onValueChange={(v: unknown[]) => v[0] && setMotor(Number(v[0]))}
            spacing={0}
            disabled={Boolean(unavailable)}
            aria-label={t('ctl.motorTest')}
            className="rounded-md bg-[var(--g900)] p-0.5 ring-1 ring-[var(--border)]"
          >
            {[1, 2, 3, 4].map((n) => (
              <ToggleGroupItem key={n} value={String(n)} aria-label={t('ctl.motor', { n })} className="num h-6 w-9 rounded-[5px] text-[11px] text-[var(--g400)] data-[pressed]:text-[var(--g50)]">
                {n}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
          <span className="num text-hud-sub text-[var(--g400)]">{ct('ctl.motorDuration', { s: SECONDS })}</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="w-24 flex-none text-hud-sub text-[var(--g300)]">{t('ctl.motorThrottle', { pct })}</span>
          <Slider min={5} max={15} step={1} value={pct} disabled={Boolean(unavailable)} onValueChange={(v) => setPct(Array.isArray(v) ? (v[0] as number) : (v as number))} aria-label={t('axis.throttle')} />
        </div>
        {confirming && !unavailable ? (
          <TypeToConfirm word="MOTOR" prompt={t('ctl.motorPrompt')} actionLabel={ct('ctl.motorRun')} onConfirm={run} onCancel={() => setConfirming(false)} busy={busy} error={error} />
        ) : (
          <Button variant="outline" className="self-start" disabled={Boolean(unavailable)} onClick={() => setConfirming(true)}>
            {ct('ctl.motorRun')}
          </Button>
        )}
      </div>
    </Group>
  )
}
