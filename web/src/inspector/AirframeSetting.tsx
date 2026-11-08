// Settings → Safety → Airframe for new simulators (06 §4, WP-2a): setting sim_airframe_default.
import { Segmented, type RowComponent } from '@/control/ControllerSettings'
import { useAT } from './airframe.strings'

export type AirframeDefault = 'ideal' | 'typical' | 'rough'
const DEFAULTS: readonly AirframeDefault[] = ['ideal', 'typical', 'rough']

export function AirframeDefaultSetting({ value, save, Row }: { value: string | undefined; save(p: { sim_airframe_default: AirframeDefault }): Promise<boolean>; Row: RowComponent }) {
  const t = useAT()
  const current = (DEFAULTS as readonly string[]).includes(value ?? '') ? (value as AirframeDefault) : 'typical'
  return (
    <Row label={t('af.setting')} hint={t('af.settingHint')}>
      <Segmented value={current} options={DEFAULTS.map((k) => ({ value: k, label: t(`af.preset.${k}`) }))} onChange={(v) => void save({ sim_airframe_default: v })} label={t('af.setting')} />
    </Row>
  )
}
