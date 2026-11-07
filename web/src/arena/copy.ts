// WP-D copy: strings of the LAN arena (05 §7) that the shared dictionaries (src/i18n/{en,zh}.ts) do not carry.
// Same rules as the shared copy (02 §17): sentence case, enterprise tone, statements, no exclamation marks.
// copy.test.ts keeps both key sets identical. The integrator may fold these into src/i18n without touching
// call sites, which only use useTA() / tA().
import { useCallback } from 'react'
import { format, useLang, useLangStore, type Lang, type Params } from '@/i18n'

const en = {
  'arena.toggle': 'Arena',
  'arena.join': 'Join the arena',
  'arena.leave': 'Leave the arena',
  'arena.joinAction': 'Join',
  'arena.cancel': 'Cancel',
  'arena.disclosure': 'Your simulated aircraft and callsign are shared with Relay terminals on this network.',
  'arena.disclosureNote': 'Only simulated aircraft take part. Real aircraft are never shared and cannot be hit.',
  'arena.peersBadge': '{n} terminals in the arena',
  'arena.statusSeat': 'Seat {seat} · {n} other terminals',
  'arena.statusChoosing': 'Choosing a seat',
  'arena.portError': 'The arena port is unavailable: {error}',
  'arena.board': 'Arena scoreboard',
  'arena.col.callsign': 'Callsign',
  'arena.col.aircraft': 'Aircraft',
  'arena.col.hits': 'Hits',
  'arena.col.downs': 'Downs',
  'arena.col.deaths': 'Lost',
  'arena.col.rtt': 'RTT',
  'arena.you': 'This terminal',
  'arena.stale': 'No data',
  'arena.empty': 'No other terminals on this network',
  'arena.remoteLabel': '{callsign} / {label}',
  'arena.hp': 'HP',
  'arena.ammo': 'Ammo',
  'arena.reloading': 'Reloading',
  'arena.down': 'Shot down',
  'arena.hitConfirmed': 'Hit confirmed',
  'arena.hitTaken': 'Hit taken',
  'arena.set.title': 'Arena',
  'arena.set.callsign': 'Callsign',
  'arena.set.callsignHint': 'Shown to other terminals. Up to 16 characters; empty uses the computer name.',
  'arena.set.port': 'Arena port',
  'arena.set.portHint': 'UDP, 1024 to 65535, different from the MAVLink ports.',
  'arena.set.respawn': 'Respawn automatically',
  'arena.set.respawnHint': 'A shot-down simulator returns to its spawn point after 5 s.',
  'arena.set.saved': 'Arena settings saved',
} as const

export type ArenaKey = keyof typeof en

const zh: Record<ArenaKey, string> = {
  'arena.toggle': '联机',
  'arena.join': '加入联机场地',
  'arena.leave': '离开联机场地',
  'arena.joinAction': '加入',
  'arena.cancel': '取消',
  'arena.disclosure': '你的模拟机与呼号将共享给本网络中的 Relay 终端。',
  'arena.disclosureNote': '只有模拟机参与。真机永不共享，也不会被命中。',
  'arena.peersBadge': '联机场地中有 {n} 个终端',
  'arena.statusSeat': '座位 {seat} · 其他终端 {n} 个',
  'arena.statusChoosing': '正在选择座位',
  'arena.portError': '联机端口不可用：{error}',
  'arena.board': '联机记分板',
  'arena.col.callsign': '呼号',
  'arena.col.aircraft': '飞机',
  'arena.col.hits': '命中',
  'arena.col.downs': '击落',
  'arena.col.deaths': '被击落',
  'arena.col.rtt': '延迟',
  'arena.you': '本终端',
  'arena.stale': '无数据',
  'arena.empty': '本网络中暂无其他终端',
  'arena.remoteLabel': '{callsign} / {label}',
  'arena.hp': '生命值',
  'arena.ammo': '弹药',
  'arena.reloading': '装填中',
  'arena.down': '已被击落',
  'arena.hitConfirmed': '已命中',
  'arena.hitTaken': '被命中',
  'arena.set.title': '联机场地',
  'arena.set.callsign': '呼号',
  'arena.set.callsignHint': '向其他终端显示，最多 16 个字符；留空则使用计算机名称。',
  'arena.set.port': '联机端口',
  'arena.set.portHint': 'UDP，1024 至 65535，须与 MAVLink 端口不同。',
  'arena.set.respawn': '自动复位',
  'arena.set.respawnHint': '被击落的模拟机 5 秒后回到出生点。',
  'arena.set.saved': '联机设置已保存',
}

export const ARENA_DICTS: Record<Lang, Record<ArenaKey, string>> = { en, zh }

export function translateArena(lang: Lang, key: ArenaKey, params?: Params): string {
  return format(ARENA_DICTS[lang][key] ?? en[key] ?? key, params)
}

/** Non-hook lookup in the current language. */
export function tA(key: ArenaKey, params?: Params): string {
  return translateArena(useLangStore.getState().lang, key, params)
}

export function useTA(): (key: ArenaKey, params?: Params) => string {
  const lang = useLang()
  return useCallback((key: ArenaKey, params?: Params) => translateArena(lang, key, params), [lang])
}
