// Copy used by WP7 surfaces (control, controllers, video) that the shared dictionaries in src/i18n do not carry yet.
// Same rules as src/i18n: sentence case, enterprise tone, verbs first on buttons, identical key sets in both
// languages (strings.test.ts). The lead may fold these into src/i18n/{en,zh}.ts; call sites only use useCT()/ct().
import { useCallback } from 'react'
import { format, useLang, useLangStore, type Lang, type Params } from '@/i18n'

const en = {
  // possession block
  'ctl.possessKeyboard': 'Possess with the keyboard and on-screen sticks',
  'ctl.flownBy': 'Flown by {input} in this window',
  'ctl.possessFirst': 'Possess this aircraft to arm it, change modes or run skills.',
  'ctl.sticks': 'Sticks',
  'ctl.screenHint': 'Drag either box to fly with the pointer. The throttle stays where you leave it.',
  'ctl.padHint': 'Hold Options for 1 s to release. L2 + R2 stops the aircraft.',
  'ctl.moveToResume': 'Move a stick to resume',
  'ctl.alreadyFlown': '{aircraft} is already flown by {input}. Release it first.',
  'ctl.selectFirst': 'Select an aircraft first.',
  'ctl.hover': 'Hover {pct} %',
  'ctl.armedSince': 'Armed {t}',
  'ctl.armedFail': 'Arming was refused',
  'ctl.preflightBlocksArm': 'Arming is blocked until every blocking item passes.',
  'ctl.armPending': 'Arming',
  'ctl.modePending': 'Switching mode',
  'ctl.skillRunning': '{skill} running',
  'ctl.skillDone': '{skill} finished',
  'ctl.skillFailed': '{skill} failed',
  'ctl.noSkills': 'This aircraft reports no altitude hold. Only Land is available.',
  'ctl.modeHint': 'Modes reported by the autopilot',
  'ctl.motorRun': 'Run motor test',
  'ctl.motorDuration': '{s} s',
  'ctl.motorSent': 'Motor {n} test sent',
  'ctl.motorUnavailableSim': 'Simulated aircraft have no motors to test.',
  'ctl.motorUnavailableArmed': 'Disarm the aircraft to run a motor test.',
  'ctl.signVerified': 'Signs verified for this run',
  'ctl.signsChanged': 'Sign changes reset the verification.',
  'ctl.hoverSaved': 'Hover point saved',

  // preflight list
  'pf.pass': 'Pass',
  'pf.fail': 'Blocking',
  'pf.warn': 'Warning',
  'pf.info': 'Info',
  'pf.waiting': 'Waiting',
  'pf.loading': 'Reading preflight checks',
  'pf.error': 'Preflight checks are not available',
  'pf.summaryOk': 'All blocking items pass',
  'pf.summaryBlocked': '{n} blocking',

  // ring course fallback (used until course/CourseControls exists)
  'rc.select': 'Course',
  'rc.stop': 'Stop',
  'rc.state.waiting_takeoff': 'Waiting for take-off',
  'rc.state.running': 'Ring {n} / {total}',
  'rc.state.finished': 'Finished',
  'rc.state.aborted': 'Aborted',

  // controllers
  'pad.n': 'Controller {n}',
  'pad.short': 'PAD {n}',
  'pad.model.dualsense': 'DualSense',
  'pad.model.standard': 'Standard layout',
  'pad.model.unknown': 'Unrecognised layout',
  'pad.disconnectedFor': 'Disconnected · releasing in {s} s',
  'pad.livePreview': 'Live input',
  'pad.bindHint': 'Choosing an aircraft possesses it with this controller.',
  'pad.auto': 'New controllers fly a free simulated aircraft automatically. Real aircraft are never bound automatically.',
  'pad.armHold': 'Hold L1 + R1 to arm',
  'pad.bound': '{aircraft} selected for Controller {n}',
  'pad.unrecognisedNote': 'Buttons may not match the mapping below.',
  'pad.enableHint': 'Press any button on the controller, or click anywhere in the window.',

  // mapping reference (02 §7.5)
  'map.title': 'Mapping · Mode 2',
  'map.leftY': 'Left stick up / down',
  'map.leftYDo': 'Throttle; centre is the hover point or altitude hold',
  'map.leftX': 'Left stick left / right',
  'map.leftXDo': 'Yaw',
  'map.right': 'Right stick',
  'map.rightDo': 'Pitch and roll',
  'map.options': 'Options, hold 1 s',
  'map.optionsDo': 'Possess or release',
  'map.l1r1': 'L1 + R1, hold 1 s',
  'map.l1r1Do': 'Arm or disarm, simulated aircraft only',
  'map.l2r2': 'L2 + R2, fully pressed',
  'map.l2r2Do': 'Stop this aircraft immediately',
  'map.triangle': 'Triangle',
  'map.triangleDo': 'Mode: stabilise or altitude hold',
  'map.square': 'Square',
  'map.squareDo': 'Start or stop the ring course',
  'map.circle': 'Circle, hold 1 s',
  'map.circleDo': 'Reset a disarmed simulated aircraft',
  'map.dpad': 'D-pad left / right',
  'map.dpadDo': 'Select the previous or next aircraft',
  'map.touchpad': 'Touchpad press',
  'map.touchpadDo': 'Cycle the stage view',
  'map.cross': 'Cross',
  'map.crossDo': 'No action',

  // video
  'vid.unbind': 'Unbind',
  'vid.boundTo': 'Bound to {aircraft}',
  'vid.boundElsewhere': 'Bound to {aircraft}. Binding here moves it.',
  'vid.inputN': 'Video input {n}',
  'vid.choose': 'Choose a device',
  'vid.openSettings': 'Open system settings',
  'vid.show': 'Show on stage',
  'vid.hide': 'Close video',
  'vid.starting': 'Starting video',
  'vid.missing': 'The bound device {device} is not connected',
  'vid.thumbHint': 'Click to show on the stage',
} as const

export type ControlKey = keyof typeof en

const zh: Record<ControlKey, string> = {
  'ctl.possessKeyboard': '使用键盘与屏幕摇杆附身',
  'ctl.flownBy': '本窗口的 {input} 正在控制',
  'ctl.possessFirst': '附身后才能解锁、切换模式或执行技能。',
  'ctl.sticks': '杆量',
  'ctl.screenHint': '拖动任一方框即可用指针控制。油门停留在松手的位置。',
  'ctl.padHint': '按住 Options 1 秒释放。L2 + R2 急停本机。',
  'ctl.moveToResume': '动一下杆即可恢复',
  'ctl.alreadyFlown': '{aircraft} 正由 {input} 控制，请先释放。',
  'ctl.selectFirst': '请先选中一架飞机。',
  'ctl.hover': '悬停点 {pct} %',
  'ctl.armedSince': '已解锁 {t}',
  'ctl.armedFail': '解锁被拒绝',
  'ctl.preflightBlocksArm': '所有阻断项通过之前不能解锁。',
  'ctl.armPending': '正在解锁',
  'ctl.modePending': '正在切换模式',
  'ctl.skillRunning': '{skill} 执行中',
  'ctl.skillDone': '{skill} 已完成',
  'ctl.skillFailed': '{skill} 失败',
  'ctl.noSkills': '这架飞机未报告定高能力，只能执行降落。',
  'ctl.modeHint': '飞控报告的模式',
  'ctl.motorRun': '运行电机测试',
  'ctl.motorDuration': '{s} 秒',
  'ctl.motorSent': '已发送电机 {n} 测试',
  'ctl.motorUnavailableSim': '模拟机没有可测试的电机。',
  'ctl.motorUnavailableArmed': '请先上锁再进行电机测试。',
  'ctl.signVerified': '本次运行已确认符号',
  'ctl.signsChanged': '修改符号会清除确认。',
  'ctl.hoverSaved': '悬停点已保存',

  'pf.pass': '通过',
  'pf.fail': '阻断',
  'pf.warn': '警告',
  'pf.info': '提示',
  'pf.waiting': '等待',
  'pf.loading': '正在读取起飞前检查',
  'pf.error': '无法获取起飞前检查',
  'pf.summaryOk': '所有阻断项已通过',
  'pf.summaryBlocked': '{n} 项阻断',

  'rc.select': '航线',
  'rc.stop': '停止',
  'rc.state.waiting_takeoff': '等待起飞',
  'rc.state.running': '第 {n} / {total} 环',
  'rc.state.finished': '已完成',
  'rc.state.aborted': '已中止',

  'pad.n': '手柄 {n}',
  'pad.short': '手柄 {n}',
  'pad.model.dualsense': 'DualSense',
  'pad.model.standard': '标准布局',
  'pad.model.unknown': '无法识别的布局',
  'pad.disconnectedFor': '已断开 · {s} 秒后释放',
  'pad.livePreview': '实时输入',
  'pad.bindHint': '选择飞机即用此手柄附身。',
  'pad.auto': '新接入的手柄会自动控制一架空闲的模拟机；真机永不自动绑定。',
  'pad.armHold': '按住 L1 + R1 解锁',
  'pad.bound': '已为手柄 {n} 选中 {aircraft}',
  'pad.unrecognisedNote': '按键可能与下表不一致。',
  'pad.enableHint': '按下手柄任意键，或在窗口中任意位置点击。',

  'map.title': '映射 · 美国手',
  'map.leftY': '左摇杆 上 / 下',
  'map.leftYDo': '油门；中位为悬停点或定高',
  'map.leftX': '左摇杆 左 / 右',
  'map.leftXDo': '偏航',
  'map.right': '右摇杆',
  'map.rightDo': '俯仰与横滚',
  'map.options': 'Options 按住 1 秒',
  'map.optionsDo': '附身或释放',
  'map.l1r1': 'L1 + R1 按住 1 秒',
  'map.l1r1Do': '解锁或上锁，仅模拟机',
  'map.l2r2': 'L2 + R2 同时按到底',
  'map.l2r2Do': '立即急停本机',
  'map.triangle': '三角',
  'map.triangleDo': '模式：自稳或定高',
  'map.square': '方块',
  'map.squareDo': '开始或停止穿环',
  'map.circle': '圆圈 按住 1 秒',
  'map.circleDo': '复位已上锁的模拟机',
  'map.dpad': '十字键 左 / 右',
  'map.dpadDo': '选中上一架或下一架飞机',
  'map.touchpad': '按下触控板',
  'map.touchpadDo': '切换舞台视角',
  'map.cross': '叉',
  'map.crossDo': '无作用',

  'vid.unbind': '解除绑定',
  'vid.boundTo': '已绑定到 {aircraft}',
  'vid.boundElsewhere': '已绑定到 {aircraft}，在此绑定会将其移过来。',
  'vid.inputN': '视频输入 {n}',
  'vid.choose': '选择设备',
  'vid.openSettings': '打开系统设置',
  'vid.show': '在舞台显示',
  'vid.hide': '关闭图传',
  'vid.starting': '正在启动图传',
  'vid.missing': '绑定的设备 {device} 未连接',
  'vid.thumbHint': '点击在舞台显示',
}

export const CONTROL_DICTS: Record<Lang, Record<ControlKey, string>> = { en, zh }

export function translateControl(lang: Lang, key: ControlKey, params?: Params): string {
  return format(CONTROL_DICTS[lang][key] ?? en[key] ?? key, params)
}

/** Non-hook lookup in the current language. */
export function ct(key: ControlKey, params?: Params): string {
  return translateControl(useLangStore.getState().lang, key, params)
}

export function useCT(): (key: ControlKey, params?: Params) => string {
  const lang = useLang()
  return useCallback((key: ControlKey, params?: Params) => translateControl(lang, key, params), [lang])
}
