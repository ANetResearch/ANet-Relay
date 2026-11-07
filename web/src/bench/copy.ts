// WP-B copy: strings of the Bench tab, the Control tab entry and the Records "Bench reports" group (05 §4.5). Same
// rules as the shared copy (02 §17): sentence case, enterprise tone, verbs first on buttons, no exclamation marks.
// useTB() resolves bench keys first and every other key through useT8 (WP8 copy, then the shared dictionaries), so
// call sites use one translator. copy.test.ts keeps the key sets identical and free of overlaps.
import { useCallback } from 'react'
import { format, useLang, useLangStore, type Lang, type Params } from '@/i18n'
import { translate8, type AnyKey } from '@/fleet/copy'

export const enB = {
  // entry in the Control tab
  'bench.entry.title': 'Bench check',
  'bench.entry.run': 'Run bench check',
  'bench.entry.open': 'Open bench',
  'bench.entry.note': 'Calibration, motor order, vibration, hover and step response, with a saved report.',
  'bench.entry.active': '{procedure} · step {index} of {total}',
  'bench.entry.last': 'Last run · {procedure} · {verdict}',

  // start
  'bench.procedure': 'Procedure',
  'bench.estimate': 'About {min} min',
  'bench.steps': 'Steps',
  'bench.hover': 'Hover length',
  'bench.hoverValue': '{s} s',
  'bench.autoAnswer': 'Answer the motor check from the outputs',
  'bench.autoAnswerHint': 'Simulated aircraft only: the servo outputs show which motor turned.',
  'bench.start': 'Start procedure',
  'bench.starting': 'Starting…',
  'bench.unavailable.armed': 'Disarm the aircraft to start a procedure.',
  'bench.unavailable.link': 'The aircraft is not connected.',
  'bench.unavailable.control': 'Possess the aircraft to start a procedure.',
  'bench.unavailable.crashed': 'The aircraft reports a crash.',
  'bench.agentRun': 'Started by {name}',
  'bench.operatorRun': 'Started by the operator',

  // run
  'bench.progress': 'Step {index} of {total}',
  'bench.abort': 'Abort',
  'bench.aborting': 'Aborting…',
  'bench.abortHint': 'Motor tests stop and an airborne aircraft lands.',
  'bench.newRun': 'New run',
  'bench.state.running': 'Running',
  'bench.state.waiting': 'Waiting for the operator',
  'bench.state.passed': 'Passed',
  'bench.state.warned': 'Passed with warnings',
  'bench.state.failed': 'Failed',
  'bench.state.aborted': 'Aborted',
  'bench.stepState.pending': 'Pending',
  'bench.stepState.running': 'Running',
  'bench.stepState.waiting': 'Waiting',
  'bench.stepState.done': 'Done',
  'bench.stepState.skipped': 'Skipped',
  'bench.stepState.aborted': 'Aborted',
  'bench.verdict.pass': 'Pass',
  'bench.verdict.warn': 'Warning',
  'bench.verdict.fail': 'Fail',
  'bench.verdict.skipped': 'Skipped',
  'bench.kind.auto': 'Automatic',
  'bench.kind.operator': 'Operator',
  'bench.kind.operator_device': 'Operator and device',
  'bench.kind.device': 'Device',
  'bench.kind.flight': 'Flight',
  'bench.paused': 'Paused while a person holds the aircraft.',
  'bench.operatorOnly': 'Only the operator at this window can answer this prompt.',
  'bench.answerFailed': 'The answer was not accepted · {reason}',
  'bench.confirmPrompt': 'Type {word} to continue.',
  'bench.motorLayout': 'Motor layout · {name}',
  'bench.motorLayoutNose': 'Nose',
  'bench.motor': 'Motor {n}',
  'bench.motorDir.cw': 'Clockwise',
  'bench.motorDir.ccw': 'Counter-clockwise',
  'bench.motorSpinning': 'Spinning',
  'bench.motorChecked': '{n} of {total} checked',
  'bench.blocking': 'Preflight blocks the flight: {items}',
  'bench.live': 'Live',

  // live series
  'bench.series.vibration_x': 'Vibration X',
  'bench.series.vibration_y': 'Vibration Y',
  'bench.series.vibration_z': 'Vibration Z',
  'bench.series.tilt': 'Tilt',
  'bench.series.throttle': 'Throttle',
  'bench.series.vibration': 'Vibration',
  'bench.series.command': 'Stick step',
  'bench.series.roll': 'Roll',
  'bench.series.pitch': 'Pitch',
  'bench.series.yaw_rate': 'Yaw rate',

  // report
  'bench.report': 'Report',
  'bench.report.title': 'Bench report #{id}',
  'bench.report.verdict': 'Verdict',
  'bench.report.started': 'Started',
  'bench.report.duration': 'Duration',
  'bench.report.reason': 'Abort reason',
  'bench.report.metric': 'Metric',
  'bench.report.value': 'Value',
  'bench.report.limits': 'Limits',
  'bench.report.limitsValue': 'warn {warn} · fail {bad}',
  'bench.report.peak': 'peak {v}',
  'bench.report.findings': 'Findings',
  'bench.report.noFindings': 'No findings.',
  'bench.report.suggestions': 'Suggested changes',
  'bench.report.noSuggestions': 'No changes suggested.',
  'bench.report.suggestionsNote': 'Suggestions are not written automatically.',
  'bench.report.param': 'Parameter',
  'bench.report.current': 'Current',
  'bench.report.suggested': 'Suggested',
  'bench.report.status': 'Status',
  'bench.report.exportMd': 'Export Markdown',
  'bench.report.exportJson': 'Export JSON',
  'bench.report.exported': 'Saved {file}',
  'bench.report.exportFailed': 'The report could not be exported',
  'bench.report.counts': 'Pass {pass} · Warning {warn} · Fail {fail}',
  'bench.sugg.proposed': 'Proposed',
  'bench.sugg.applied': 'Applied',
  'bench.sugg.kept': 'Kept',
  'bench.sugg.failed': 'Not confirmed',
  'bench.sugg.unavailable': 'Unavailable',

  // records
  'bench.records.title': 'Bench reports',
  'bench.records.empty': 'No bench reports yet.',
  'bench.records.failed': 'Bench reports could not be loaded · {reason}',
  'bench.records.loading': 'Loading the report…',
}

export type KeyB = keyof typeof enB

export const zhB: Record<KeyB, string> = {
  'bench.entry.title': '标定与测试',
  'bench.entry.run': '运行标定检查',
  'bench.entry.open': '打开标定',
  'bench.entry.note': '传感器校准、电机顺序、振动、悬停与阶跃响应，并保存报告。',
  'bench.entry.active': '{procedure} · 第 {index} / {total} 步',
  'bench.entry.last': '上次运行 · {procedure} · {verdict}',

  'bench.procedure': '流程',
  'bench.estimate': '约 {min} 分钟',
  'bench.steps': '步骤',
  'bench.hover': '悬停时长',
  'bench.hoverValue': '{s} 秒',
  'bench.autoAnswer': '根据输出自动回答电机检查',
  'bench.autoAnswerHint': '仅限模拟机：由舵机输出判断转动的电机。',
  'bench.start': '启动流程',
  'bench.starting': '正在启动…',
  'bench.unavailable.armed': '请先上锁飞机再启动流程。',
  'bench.unavailable.link': '飞机未连接。',
  'bench.unavailable.control': '请先附身飞机再启动流程。',
  'bench.unavailable.crashed': '飞机报告坠毁。',
  'bench.agentRun': '由 {name} 启动',
  'bench.operatorRun': '由操作员启动',

  'bench.progress': '第 {index} / {total} 步',
  'bench.abort': '中止',
  'bench.aborting': '正在中止…',
  'bench.abortHint': '电机测试停止，空中的飞机降落。',
  'bench.newRun': '重新运行',
  'bench.state.running': '运行中',
  'bench.state.waiting': '等待操作员',
  'bench.state.passed': '通过',
  'bench.state.warned': '有警告',
  'bench.state.failed': '失败',
  'bench.state.aborted': '已中止',
  'bench.stepState.pending': '待执行',
  'bench.stepState.running': '执行中',
  'bench.stepState.waiting': '等待中',
  'bench.stepState.done': '完成',
  'bench.stepState.skipped': '已跳过',
  'bench.stepState.aborted': '已中止',
  'bench.verdict.pass': '通过',
  'bench.verdict.warn': '警告',
  'bench.verdict.fail': '失败',
  'bench.verdict.skipped': '跳过',
  'bench.kind.auto': '自动',
  'bench.kind.operator': '操作员',
  'bench.kind.operator_device': '操作员与设备',
  'bench.kind.device': '设备',
  'bench.kind.flight': '飞行',
  'bench.paused': '有人持有飞机，流程已暂停。',
  'bench.operatorOnly': '此提示只能由本窗口的操作员回答。',
  'bench.answerFailed': '回答未被接受 · {reason}',
  'bench.confirmPrompt': '输入 {word} 以继续。',
  'bench.motorLayout': '电机布局 · {name}',
  'bench.motorLayoutNose': '机头',
  'bench.motor': '电机 {n}',
  'bench.motorDir.cw': '顺时针',
  'bench.motorDir.ccw': '逆时针',
  'bench.motorSpinning': '转动中',
  'bench.motorChecked': '已检查 {n} / {total}',
  'bench.blocking': '起飞前检查阻止飞行：{items}',
  'bench.live': '实时',

  'bench.series.vibration_x': 'X 轴振动',
  'bench.series.vibration_y': 'Y 轴振动',
  'bench.series.vibration_z': 'Z 轴振动',
  'bench.series.tilt': '倾角',
  'bench.series.throttle': '油门',
  'bench.series.vibration': '振动',
  'bench.series.command': '阶跃杆量',
  'bench.series.roll': '横滚',
  'bench.series.pitch': '俯仰',
  'bench.series.yaw_rate': '偏航角速度',

  'bench.report': '报告',
  'bench.report.title': '标定报告 #{id}',
  'bench.report.verdict': '判定',
  'bench.report.started': '开始',
  'bench.report.duration': '时长',
  'bench.report.reason': '中止原因',
  'bench.report.metric': '指标',
  'bench.report.value': '值',
  'bench.report.limits': '门限',
  'bench.report.limitsValue': '警告 {warn} · 失败 {bad}',
  'bench.report.peak': '峰值 {v}',
  'bench.report.findings': '发现',
  'bench.report.noFindings': '无发现。',
  'bench.report.suggestions': '建议修改',
  'bench.report.noSuggestions': '无修改建议。',
  'bench.report.suggestionsNote': '建议不会被自动写入。',
  'bench.report.param': '参数',
  'bench.report.current': '当前值',
  'bench.report.suggested': '建议值',
  'bench.report.status': '状态',
  'bench.report.exportMd': '导出 Markdown',
  'bench.report.exportJson': '导出 JSON',
  'bench.report.exported': '已保存 {file}',
  'bench.report.exportFailed': '报告导出失败',
  'bench.report.counts': '通过 {pass} · 警告 {warn} · 失败 {fail}',
  'bench.sugg.proposed': '建议',
  'bench.sugg.applied': '已应用',
  'bench.sugg.kept': '保留',
  'bench.sugg.failed': '未确认',
  'bench.sugg.unavailable': '不可用',

  'bench.records.title': '标定报告',
  'bench.records.empty': '暂无标定报告。',
  'bench.records.failed': '无法加载标定报告 · {reason}',
  'bench.records.loading': '正在加载报告…',
}

export const DICTS_B: Record<Lang, Record<KeyB, string>> = { en: enB, zh: zhB }

export type BenchKey = KeyB | AnyKey

const isKeyB = (k: string): k is KeyB => k in enB

export function translateB(lang: Lang, key: BenchKey, params?: Params): string {
  if (isKeyB(key)) return format(DICTS_B[lang][key] ?? enB[key], params)
  return translate8(lang, key, params)
}

/** Non-hook lookup in the current language. */
export function tB(key: BenchKey, params?: Params): string {
  return translateB(useLangStore.getState().lang, key, params)
}

export function useTB(): (key: BenchKey, params?: Params) => string {
  const lang = useLang()
  return useCallback((key: BenchKey, params?: Params) => translateB(lang, key, params), [lang])
}
