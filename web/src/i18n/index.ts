// i18n (R-61, 02 §17): English by default, Chinese on request; the choice persists in localStorage (relay.lang).
// `?lang=zh|en` overrides it for one launch (screenshots, reviews). Components use useT(); non-React code uses t().
import { useCallback } from 'react'
import { create } from 'zustand'
import { en, type MessageKey } from './en'
import { zh } from './zh'

export type Lang = 'en' | 'zh'
export type { MessageKey }
export type Params = Record<string, string | number>

export const DICTS: Record<Lang, Record<MessageKey, string>> = { en, zh }
const KEY = 'relay.lang'

function initialLang(): Lang {
  try {
    const q = typeof location !== 'undefined' ? new URLSearchParams(location.search).get('lang') : null
    if (q === 'zh' || q === 'en') return q
    const s = localStorage.getItem(KEY)
    if (s === 'zh' || s === 'en') return s
  } catch {
    /* storage unavailable */
  }
  return 'en'
}

function applyDocument(lang: Lang): void {
  if (typeof document === 'undefined') return
  document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en'
  document.documentElement.dataset.lang = lang
  // native menus and dialogs of the desktop shell follow the interface language
  if (typeof window !== 'undefined') void window.relay?.setLanguage?.(lang)?.catch?.(() => undefined)
}

export const useLangStore = create<{ lang: Lang; setLang(l: Lang): void }>()((set) => ({
  lang: initialLang(),
  setLang: (lang) => {
    try {
      localStorage.setItem(KEY, lang)
    } catch {
      /* storage unavailable */
    }
    applyDocument(lang)
    set({ lang })
  },
}))
applyDocument(useLangStore.getState().lang)

/** Replaces {name} placeholders; unknown placeholders are left visible so a missing param is noticed. */
export function format(template: string, params?: Params): string {
  if (!params) return template
  return template.replace(/\{(\w+)\}/g, (m, k: string) => (k in params ? String(params[k]) : m))
}

export function translate(lang: Lang, key: MessageKey, params?: Params): string {
  return format(DICTS[lang][key] ?? en[key] ?? key, params)
}

/** Non-hook lookup in the current language (event handlers, toasts, stores). */
export function t(key: MessageKey, params?: Params): string {
  return translate(useLangStore.getState().lang, key, params)
}

export const useLang = () => useLangStore((s) => s.lang)
export const setLang = (l: Lang) => useLangStore.getState().setLang(l)

export function useT(): (key: MessageKey, params?: Params) => string {
  const lang = useLang()
  return useCallback((key: MessageKey, params?: Params) => translate(lang, key, params), [lang])
}

/** Picks the backend's bilingual text pair (alerts, events, errors). */
export function pickText(lang: Lang, en_: string, zh_: string): string {
  return lang === 'zh' ? zh_ || en_ : en_
}
