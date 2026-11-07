// Notices shown when an agent operates the interface (04 §1 item 3): "<by> switched the language to Chinese".
// Same rules as src/i18n: sentence case, enterprise tone, identical keys in both languages (appctl.test.ts).
import { format, useLangStore, type Params } from '@/i18n'

const en = {
  'app.n.set_language': '{by} switched the language to {lang}',
  'app.n.set_workspace': '{by} opened the {workspace} workspace',
  'app.n.select_vehicle': '{by} selected {aircraft}',
  'app.n.deselect_vehicle': '{by} cleared the selection',
  'app.n.set_view': '{by} changed the stage view to {view}',
  'app.n.open_inspector': '{by} opened the inspector',
  'app.n.close_inspector': '{by} closed the inspector',
  'app.n.open_settings': '{by} opened settings',
  'app.n.open_connect': '{by} opened the connection dialog',
  'app.n.close_dialogs': '{by} closed the open dialogs',
  'app.n.set_pref': '{by} changed the preference {key}',
  'app.n.bind_gamepad': '{by} bound controller {index} to {aircraft}',
  'app.n.unbind_gamepad': '{by} released controller {index}',
  'app.n.possess': '{by} took control of {aircraft} as the operator',
  'app.n.release': '{by} released {aircraft}',
  'app.n.open_record': '{by} opened record {id}',
  'app.n.replay': '{by} controlled the replay',
  'app.n.toggle_copilot': '{by} changed the Copilot window',
  'app.n.window': '{by} changed the window: {action}',
  'app.n.screenshot': '{by} captured a screenshot of the window',
  'app.n.failed': '{by} could not complete {action}',
  'app.lang.en': 'English',
  'app.lang.zh': 'Chinese',
} as const

export type AppctlKey = keyof typeof en

const zh: Record<AppctlKey, string> = {
  'app.n.set_language': '{by} 将语言切换为{lang}',
  'app.n.set_workspace': '{by} 打开了“{workspace}”工作区',
  'app.n.select_vehicle': '{by} 选中了 {aircraft}',
  'app.n.deselect_vehicle': '{by} 取消了选中',
  'app.n.set_view': '{by} 将舞台视角切换为“{view}”',
  'app.n.open_inspector': '{by} 打开了检查器',
  'app.n.close_inspector': '{by} 关闭了检查器',
  'app.n.open_settings': '{by} 打开了设置',
  'app.n.open_connect': '{by} 打开了接入对话框',
  'app.n.close_dialogs': '{by} 关闭了所有打开的对话框',
  'app.n.set_pref': '{by} 修改了偏好项 {key}',
  'app.n.bind_gamepad': '{by} 将手柄 {index} 绑定到 {aircraft}',
  'app.n.unbind_gamepad': '{by} 释放了手柄 {index}',
  'app.n.possess': '{by} 以操作员身份接管了 {aircraft}',
  'app.n.release': '{by} 释放了 {aircraft}',
  'app.n.open_record': '{by} 打开了记录 {id}',
  'app.n.replay': '{by} 控制了回看',
  'app.n.toggle_copilot': '{by} 调整了 Copilot 窗口',
  'app.n.window': '{by} 调整了窗口：{action}',
  'app.n.screenshot': '{by} 截取了窗口截图',
  'app.n.failed': '{by} 未能完成 {action}',
  'app.lang.en': '英文',
  'app.lang.zh': '中文',
}

export const APPCTL_DICTS = { en: en as Record<AppctlKey, string>, zh }

/** Non-hook lookup in the current language. */
export function apt(key: AppctlKey, params?: Params): string {
  const lang = useLangStore.getState().lang
  return format(APPCTL_DICTS[lang][key] ?? APPCTL_DICTS.en[key] ?? key, params)
}
