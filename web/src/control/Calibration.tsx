// Calibration (02 §5.5, collapsible): stick signs for roll / pitch / yaw (+1 / -1), "Mark signs as verified" (the
// preflight `signs` item, 03 §6.3), and the throttle hover point 0.20-0.70 (sim default 0.50, real 0.35).
import * as React from 'react'
import { api, type Vehicle } from '@/api'
import { useT } from '@/i18n'
import { notify, notifyError } from '@/shell/notify'
import { Button } from '@/ui/components/ui/button'
import { Slider } from '@/ui/components/ui/slider'
import { ToggleGroup, ToggleGroupItem } from '@/ui/components/ui/toggle-group'
import { Icon } from '@/ui/icons'
import { Group } from '@/ui/relay'
import { useCT } from './strings'

type Signs = Vehicle['calibration']['signs']
const AXES: (keyof Signs)[] = ['roll', 'pitch', 'yaw']

/** `focus` (from openInspector(id, 'control', 'calibration')) opens the group and scrolls it into view once. */
export function Calibration({ vehicle, focus = false, onFocused }: { vehicle: Pick<Vehicle, 'id' | 'kind' | 'calibration' | 'confirmed'>; focus?: boolean; onFocused?(): void }) {
  const root = React.useRef<HTMLDivElement>(null)
  React.useEffect(() => {
    if (!focus) return
    root.current?.scrollIntoView({ block: 'start', behavior: 'auto' })
    onFocused?.()
  }, [focus, onFocused])
  const t = useT()
  const ct = useCT()
  const [draft, setDraft] = React.useState<number | null>(null)
  const [busy, setBusy] = React.useState(false)
  const hover = draft ?? vehicle.calibration.hover
  const signs = vehicle.calibration.signs
  const setSign = async (axis: keyof Signs, value: 1 | -1) => {
    if (signs[axis] === value) return
    try {
      await api.calibration(vehicle.id, { signs: { ...signs, [axis]: value } })
    } catch (e) {
      notifyError(e)
    }
  }
  const verify = async () => {
    setBusy(true)
    try {
      await api.confirm(vehicle.id, 'signs')
    } catch (e) {
      notifyError(e)
    } finally {
      setBusy(false)
    }
  }
  const commitHover = async (value: number) => {
    try {
      await api.calibration(vehicle.id, { hover: Math.round(value * 100) / 100 })
      notify(ct('ctl.hoverSaved'))
    } catch (e) {
      notifyError(e)
    } finally {
      setDraft(null)
    }
  }
  return (
    <div ref={root}>
      <Group label={t('calib.title')} collapsible defaultOpen={focus} key={focus ? 'focus' : 'rest'}>
        <div className="flex flex-col gap-3 px-3 pb-1" data-calibration="">
          <div className="flex flex-col gap-2">
            <span className="text-ed-value font-[500] text-[var(--g100)]">{t('calib.signs')}</span>
            {AXES.map((axis) => (
              <div key={axis} className="flex items-center gap-3">
                <span className="w-16 text-hud-sub text-[var(--g300)]">{t(`axis.${axis}`)}</span>
                <ToggleGroup
                  value={[String(signs[axis])]}
                  onValueChange={(v: unknown[]) => v[0] && void setSign(axis, Number(v[0]) as 1 | -1)}
                  spacing={0}
                  aria-label={t(`axis.${axis}`)}
                  className="rounded-md bg-[var(--g900)] p-0.5 ring-1 ring-[var(--border)]"
                >
                  <ToggleGroupItem value="1" className="num h-6 w-10 rounded-[5px] text-[11px] text-[var(--g400)] data-[pressed]:text-[var(--g50)]">
                    +1
                  </ToggleGroupItem>
                  <ToggleGroupItem value="-1" className="num h-6 w-10 rounded-[5px] text-[11px] text-[var(--g400)] data-[pressed]:text-[var(--g50)]">
                    −1
                  </ToggleGroupItem>
                </ToggleGroup>
              </div>
            ))}
            <p className="text-hud-sub text-[var(--g400)]">{t('calib.signsBody')}</p>
            {vehicle.kind === 'real' ? (
              vehicle.confirmed.signs ? (
                <p className="flex items-center gap-1.5 text-hud-sub text-[var(--g300)]">
                  <Icon icon="check" className="size-3.5" />
                  {ct('ctl.signVerified')}
                </p>
              ) : (
                <Button variant="outline" className="self-start" disabled={busy} onClick={verify} data-verify-signs="">
                  {t('calib.markVerified')}
                </Button>
              )
            ) : null}
          </div>
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <span className="text-ed-value font-[500] text-[var(--g100)]">{t('calib.hover')}</span>
              <span className="num text-[11px] text-[var(--g200)]">{hover.toFixed(2)}</span>
            </div>
            <Slider
              min={0.2}
              max={0.7}
              step={0.01}
              value={hover}
              onValueChange={(v) => setDraft(Array.isArray(v) ? (v[0] as number) : (v as number))}
              onValueCommitted={(v) => void commitHover(Array.isArray(v) ? (v[0] as number) : (v as number))}
              aria-label={t('calib.hover')}
            />
            <p className="text-hud-sub text-[var(--g400)]">{t('calib.hoverBody')}</p>
          </div>
        </div>
      </Group>
    </div>
  )
}
