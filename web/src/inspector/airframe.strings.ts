// Airframe group copy (06 §4, WP-2a). Same rules as src/i18n: sentence case, enterprise tone, identical key
// sets and placeholders in both languages (airframe.test.ts).
import { useCallback } from 'react'
import { format, useLang, type Lang, type Params } from '@/i18n'

const en = {
  'af.title': 'Airframe',
  'af.state.active': 'In effect',
  'af.state.waiting': 'Inactive with hover assist on',
  'af.withAssist': 'Also with altitude hold',
  'af.withAssistHint': 'The CG offset and level error also act with hover assist on: altitude hold keeps the height, the airframe still tilts and drifts the way a real one does. Tune a timeline sequence here before flying it on the real aircraft.',
  'af.p.arm_bias': 'Random level error per arming',
  'af.levelNow': 'Level error this flight',
  'af.levelNowValue': 'roll {roll}°, pitch {pitch}°',
  'af.note': 'Assembly defects of the simulated aircraft. They act with hover assist off, or with it on when Also with altitude hold is set. Programs never see these values; they show in flight.',
  'af.preset': 'Preset',
  'af.preset.ideal': 'Ideal',
  'af.preset.typical': 'Typical',
  'af.preset.rough': 'Rough',
  'af.preset.custom': 'Custom',
  'af.seed': 'Seed',
  'af.seedApply': 'Apply seed',
  'af.randomize': 'Randomize',
  'af.randomizeHint': 'New random seed for the typical or rough preset',
  'af.hover': 'Hover throttle',
  'af.hoverValue': '{v} %',
  'af.cannotLift': 'Cannot lift',
  'af.group.weight': 'Weight',
  'af.group.balance': 'Balance and level',
  'af.group.motors': 'Motors and propellers',
  'af.group.sensors': 'Sensors',
  'af.p.mass_kg': 'Airframe mass',
  'af.p.payload_kg': 'Payload',
  'af.p.cg_x': 'CG offset forward',
  'af.p.cg_y': 'CG offset right',
  'af.p.trim_roll': 'Level error, roll',
  'af.p.trim_pitch': 'Level error, pitch',
  'af.p.motor': 'Motor {n} efficiency',
  'af.p.prop_damage': 'Propeller damage',
  'af.p.damaged_prop': 'Damaged propeller',
  'af.p.damaged_propValue': 'Motor {n}',
  'af.p.imu_noise': 'IMU noise',
  'af.loading': 'Loading airframe',
  'af.error': 'The airframe values are not available: {msg}',
  'af.denied': 'Only the operator or the controlling session can read the airframe values.',
  'af.values': 'Adjust values',
  'af.setting': 'Airframe for new simulators',
  'af.settingHint': 'Assembly defects a new simulator takes, each with a new random seed. They act only with hover assist off.',
} as const

type Key = keyof typeof en

const zh: Record<Key, string> = {
  'af.title': '机体',
  'af.state.active': '生效中',
  'af.state.waiting': '悬停辅助开启时不生效',
  'af.withAssist': '定高下同样生效',
  'af.withAssistHint': '悬停辅助开启时，重心偏移与水平误差同样生效：定高保持高度，机身仍会像真机一样倾斜和漂移。可先在这里调好时间轴序列，再到真机上执行。',
  'af.p.arm_bias': '每次解锁的随机水平误差',
  'af.levelNow': '本次飞行水平误差',
  'af.levelNowValue': '横滚 {roll}°，俯仰 {pitch}°',
  'af.note': '模拟机的装配缺陷。悬停辅助关闭时生效；开启“定高下同样生效”后，悬停辅助开启时也生效。程序观测中不包含这些数值，只能在飞行中体现。',
  'af.preset': '预设',
  'af.preset.ideal': '理想',
  'af.preset.typical': '典型',
  'af.preset.rough': '粗糙',
  'af.preset.custom': '自定义',
  'af.seed': '种子',
  'af.seedApply': '应用种子',
  'af.randomize': '随机生成',
  'af.randomizeHint': '为典型或粗糙预设生成新的随机种子',
  'af.hover': '悬停油门',
  'af.hoverValue': '{v} %',
  'af.cannotLift': '无法起飞',
  'af.group.weight': '重量',
  'af.group.balance': '重心与水平',
  'af.group.motors': '电机与螺旋桨',
  'af.group.sensors': '传感器',
  'af.p.mass_kg': '机体质量',
  'af.p.payload_kg': '载重',
  'af.p.cg_x': '重心前偏',
  'af.p.cg_y': '重心右偏',
  'af.p.trim_roll': '水平误差（横滚）',
  'af.p.trim_pitch': '水平误差（俯仰）',
  'af.p.motor': '电机 {n} 效率',
  'af.p.prop_damage': '螺旋桨损伤',
  'af.p.damaged_prop': '受损螺旋桨',
  'af.p.damaged_propValue': '电机 {n}',
  'af.p.imu_noise': 'IMU 噪声',
  'af.loading': '正在读取机体参数',
  'af.error': '无法读取机体参数：{msg}',
  'af.denied': '只有操作员或控制该飞机的会话可以读取机体参数。',
  'af.values': '调整数值',
  'af.setting': '新模拟机的机体',
  'af.settingHint': '新模拟机采用的装配缺陷，每架使用新的随机种子；仅在悬停辅助关闭时生效。',
}

export type AirframeKey = Key
export const AIRFRAME_DICTS: Record<Lang, Record<Key, string>> = { en, zh }

export function useAT(): (key: Key, params?: Params) => string {
  const lang = useLang()
  return useCallback((key: Key, params?: Params) => format(AIRFRAME_DICTS[lang][key] ?? en[key] ?? key, params), [lang])
}
