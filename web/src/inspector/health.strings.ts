// Flight health copy (03 §5.9): one label per item, one cause-and-fix note per finding. Same rules as src/i18n:
// sentence case, enterprise tone, identical key sets in both languages (health.test.ts).
import { useCallback } from 'react'
import { format, useLang, type Lang, type Params } from '@/i18n'

const en = {
  'fh.title': 'Flight health',
  'fh.state.ok': 'Normal',
  'fh.state.warn': 'Check',
  'fh.state.bad': 'Fault',
  'fh.state.idle': 'Not armed',
  'fh.level.ok': 'Normal',
  'fh.level.warn': 'Check',
  'fh.level.bad': 'Fault',
  'fh.level.na': '—',
  'fh.footer': 'Last {s} s. Flight items are judged after 1.5 s in the air with the sticks centred. Coarse thresholds that point at a cause; confirm with the flight log.',
  'fh.idle': 'Arm and take off to collect diagnostics. Values reset at every arming.',
  'fh.item.oscillation': 'Attitude oscillation',
  'fh.item.lean': 'Hands-off lean',
  'fh.item.yaw_drift': 'Yaw drift',
  'fh.item.alt_hold': 'Altitude hold',
  'fh.item.hover_throttle': 'Hover throttle',
  'fh.item.motors': 'Motor balance',
  'fh.item.vibration': 'Vibration (peak)',
  'fh.item.battery_sag': 'Battery sag',
  'fh.item.link': 'Link',
  'fh.item.stick_stream': 'Stick stream',
  'fh.fix.oscillation':
    'Roll and pitch rates oscillate. Check props, motor mounts and arms for play first. Then lower the rate P and D gains by 10–20 % (PX4 MC_ROLLRATE_P/D, MC_PITCHRATE_P/D; ArduPilot ATC_RAT_RLL_P/D, ATC_RAT_PIT_P/D) or run autotune.',
  'fh.fix.lean':
    'The controller holds {roll}° roll and {pitch}° pitch with the sticks centred, so the aircraft drifts. Re-run level horizon and accelerometer calibration on a flat surface, centre the battery, and check that the flight controller is mounted square.',
  'fh.fix.yaw_drift':
    'The aircraft turns with the yaw stick centred. Check motor spin direction and prop orientation against the frame diagram, motor tilt and the compass calibration.',
  'fh.fix.alt_hold':
    'Altitude hold is pumping. The barometer is probably affected by prop wash or light: cover it with open-cell foam and stay above 0.5 m to avoid ground effect. A downward rangefinder removes the problem indoors; lower the vertical gains (PX4 MPC_Z_VEL_P_ACC; ArduPilot PSC_ACCZ_P) only after that.',
  'fh.fix.hover_throttle.high':
    'Hover needs {v} % throttle: the aircraft is heavy for its motors or the battery is low. Control margin is small and sag will make it sink.',
  'fh.fix.hover_throttle.low':
    'Hover needs only {v} % throttle. Set the hover thrust to match (PX4 MPC_THR_HOVER; ArduPilot MOT_THST_HOVER or MOT_HOVER_LEARN = 2) so take-off and altitude hold are not abrupt.',
  'fh.fix.motors':
    'Motor {n} works hardest ({means} µs). That corner is heavy, or its prop or motor is weak. Rebalance the centre of gravity, replace damaged props and check that each prop is the right way up.',
  'fh.fix.motors.saturated':
    'Motors reach full output {pct} % of the time. The aircraft is under-powered or one motor or ESC is failing; land and inspect before flying again.',
  'fh.fix.vibration':
    'Vibration peaks at {v} m/s². Balance or replace the props, soft-mount the flight controller and tighten the frame.',
  'fh.fix.vibration.clip':
    'The accelerometer clipped {n} times this flight, so the estimator cannot be trusted. Fix vibration before the next flight: balance or replace props, soft-mount the flight controller, tighten the frame.',
  'fh.fix.battery_sag':
    'The battery drops {v} V per cell under load. Use a fully charged pack with a higher discharge rating; sag makes the aircraft sink and the hover throttle rise.',
  'fh.fix.link':
    'Packet loss {loss} %, round trip {rtt} ms. Move closer to the access point, prefer 5 GHz and reduce other traffic. The aircraft keeps the last stick values only for its manual-control timeout (PX4 COM_RC_LOSS_T; ArduPilot RC_OVERRIDE_TIME).',
  'fh.fix.stick_stream':
    'Stick updates from this computer arrive at {hz} Hz with gaps up to {gap} s. Keep the Relay window in focus, close heavy applications and disable power saving on wireless gamepads.',
}

type Key = keyof typeof en

const zh: Record<Key, string> = {
  'fh.title': '飞行健康',
  'fh.state.ok': '正常',
  'fh.state.warn': '注意',
  'fh.state.bad': '异常',
  'fh.state.idle': '未解锁',
  'fh.level.ok': '正常',
  'fh.level.warn': '注意',
  'fh.level.bad': '异常',
  'fh.level.na': '—',
  'fh.footer': '统计最近 {s} 秒。飞行类项目需在空中且摇杆回中 1.5 秒后才评估。阈值较粗，用于指向原因，请以飞行日志确认。',
  'fh.idle': '解锁并起飞后开始采集诊断数据。每次解锁重新计算。',
  'fh.item.oscillation': '姿态振荡',
  'fh.item.lean': '松杆倾斜',
  'fh.item.yaw_drift': '航向漂移',
  'fh.item.alt_hold': '定高稳定性',
  'fh.item.hover_throttle': '悬停油门',
  'fh.item.motors': '电机均衡',
  'fh.item.vibration': '振动（峰值）',
  'fh.item.battery_sag': '电池压降',
  'fh.item.link': '链路',
  'fh.item.stick_stream': '摇杆数据流',
  'fh.fix.oscillation':
    '横滚、俯仰角速度在振荡。先检查桨叶、电机座和机臂是否松动；再将角速度环 P、D 下调 10–20%（PX4：MC_ROLLRATE_P/D、MC_PITCHRATE_P/D；ArduPilot：ATC_RAT_RLL_P/D、ATC_RAT_PIT_P/D），或执行自动调参。',
  'fh.fix.lean':
    '摇杆回中时飞控仍保持横滚 {roll}°、俯仰 {pitch}°，飞机会持续漂移。请在水平面上重新做水平校准和加速度计校准，将电池居中，并确认飞控安装端正。',
  'fh.fix.yaw_drift': '偏航杆回中时飞机仍在旋转。请对照机架图检查电机转向和桨叶正反，检查电机是否歪斜，并重新校准罗盘。',
  'fh.fix.alt_hold':
    '定高上下起伏。气压计可能受桨下洗气流或光照影响：用开孔海绵遮盖气压计，悬停高度保持在 0.5 米以上以避开地效。室内加装下视测距模块可根本解决；确有必要再下调垂直增益（PX4：MPC_Z_VEL_P_ACC；ArduPilot：PSC_ACCZ_P）。',
  'fh.fix.hover_throttle.high': '悬停需要 {v}% 油门：相对电机而言机体偏重，或电池电量偏低。控制余量小，电压下降时会掉高。',
  'fh.fix.hover_throttle.low':
    '悬停仅需 {v}% 油门。请将悬停推力参数设为实际值（PX4：MPC_THR_HOVER；ArduPilot：MOT_THST_HOVER，或设置 MOT_HOVER_LEARN = 2），起飞和定高会更平顺。',
  'fh.fix.motors': '{n} 号电机输出最高（{means} µs）。该角偏重，或其桨叶、电机偏弱。请调整重心、更换损伤的桨叶，并确认每支桨安装方向正确。',
  'fh.fix.motors.saturated': '电机有 {pct}% 的时间输出已到上限。动力不足，或某个电机或电调故障；请降落检查后再飞。',
  'fh.fix.vibration': '振动峰值 {v} m/s²。请做桨叶动平衡或更换桨叶，对飞控做软减震，并紧固机架。',
  'fh.fix.vibration.clip':
    '本次飞行加速度计削波 {n} 次，状态估计已不可信。下次起飞前请先解决振动：桨叶动平衡或更换、飞控软减震、紧固机架。',
  'fh.fix.battery_sag': '负载下电池每节压降 {v} V。请使用充满电、放电倍率更高的电池；压降会导致掉高和悬停油门升高。',
  'fh.fix.link':
    '丢包 {loss}%，往返延迟 {rtt} 毫秒。请靠近接入点、优先使用 5 GHz 并减少其他流量。飞机只在手动控制超时时间内保持最后一次杆量（PX4：COM_RC_LOSS_T；ArduPilot：RC_OVERRIDE_TIME）。',
  'fh.fix.stick_stream':
    '本机发送的摇杆数据为 {hz} Hz，最大间隔 {gap} 秒。请保持 Relay 窗口在前台、关闭占用资源的程序，并关闭无线手柄的节能模式。',
}

export type HealthKey = Key
export const HEALTH_DICTS: Record<Lang, Record<Key, string>> = { en, zh }

export function useHT(): (key: Key, params?: Params) => string {
  const lang = useLang()
  return useCallback((key: Key, params?: Params) => format(HEALTH_DICTS[lang][key] ?? en[key] ?? key, params), [lang])
}
