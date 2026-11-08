// WP6 copy: strings of the stage and the ring course that the shared dictionaries (src/i18n/{en,zh}.ts) do not
// carry. Same rules as the shared copy (02 §17). useT6() resolves WP6 keys first and every shared key through the
// shared dictionaries; copy.test.ts keeps the key sets identical and free of overlaps. The lead may fold these
// into src/i18n later without touching call sites.
import { useCallback } from 'react'
import { format, translate, useLang, useLangStore, type Lang, type MessageKey, type Params } from '@/i18n'

export const en6 = {
  'stage.viewSwitch': 'Camera view',
  'stage.viewHint': '{view} · C cycles views',
  'stage.cmdView': 'Camera: {view}',
  'stage.cmdReframe': 'Reframe the stage on all aircraft',
  'stage.layerSafetyAuto': 'Shown while an aircraft has a position',
  'stage.keysTitle': 'Keyboard',
  'stage.modelFallback': 'Aircraft model unavailable · showing a reference airframe',
  'stage.noWebgl': '3D view unavailable',
  'stage.noWebglHint': 'This computer could not start WebGL. Fleet, control and records keep working.',
  'course.cmdStart': 'Start the ring course on {aircraft}',
  'course.cmdStop': 'Stop the ring course on {aircraft}',
  'course.ringN': 'Ring {n}',
  'course.brief': 'Ring {n}/{total} · {t}',
  'course.briefWaiting': 'Ring course · waiting for take-off',
  'course.colRing': 'Ring',
  'course.colSplit': 'Split',
  'course.colElapsed': 'Elapsed',
  'course.noCourses': 'No courses available',
  'course.loading': 'Loading courses…',
  'course.selectFirst': 'Select an aircraft to fly the ring course',
  'course.byAgent': '{agent} is flying this course',
  'course.reason.removed': 'aircraft removed',
  'course.reason.reset': 'simulator reset',
  'course.ringCount': '{n} rings',
  // WP-C v0.5 (07 §5)
  'stage.layer.volumes': 'Collision volumes',
}

export type Key6 = keyof typeof en6

export const zh6: Record<Key6, string> = {
  'stage.viewSwitch': '视角',
  'stage.viewHint': '{view} · 按 C 切换视角',
  'stage.cmdView': '视角：{view}',
  'stage.cmdReframe': '重新框选全部飞机',
  'stage.layerSafetyAuto': '有飞机具备位置时显示',
  'stage.keysTitle': '键盘',
  'stage.modelFallback': '飞机模型不可用 · 正在显示参考机体',
  'stage.noWebgl': '三维视图不可用',
  'stage.noWebglHint': '本机无法启动 WebGL。机群、控制与记录不受影响',
  'course.cmdStart': '在 {aircraft} 上开始穿环',
  'course.cmdStop': '停止 {aircraft} 的穿环',
  'course.ringN': '第 {n} 环',
  'course.brief': '第 {n}/{total} 环 · {t}',
  'course.briefWaiting': '穿环 · 等待起飞',
  'course.colRing': '环',
  'course.colSplit': '分段',
  'course.colElapsed': '累计',
  'course.noCourses': '没有可用的航线',
  'course.loading': '正在读取航线…',
  'course.selectFirst': '请先选择一架飞机再开始穿环',
  'course.byAgent': '{agent} 正在飞这条航线',
  'course.reason.removed': '飞机已移除',
  'course.reason.reset': '模拟机已复位',
  'course.ringCount': '{n} 环',
  // WP-C v0.5 (07 §5)
  'stage.layer.volumes': '碰撞体积',
}

export const DICTS6: Record<Lang, Record<Key6, string>> = { en: en6, zh: zh6 }

export type AnyKey6 = MessageKey | Key6

const isKey6 = (k: string): k is Key6 => k in en6

export function translate6(lang: Lang, key: AnyKey6, params?: Params): string {
  if (isKey6(key)) return format(DICTS6[lang][key] ?? en6[key], params)
  return translate(lang, key, params)
}

/** Non-hook lookup in the current language. */
export function t6(key: AnyKey6, params?: Params): string {
  return translate6(useLangStore.getState().lang, key, params)
}

export function useT6(): (key: AnyKey6, params?: Params) => string {
  const lang = useLang()
  return useCallback((key: AnyKey6, params?: Params) => translate6(lang, key, params), [lang])
}
