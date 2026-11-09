// Command palette registry (⌘K). The shell contributes navigation, fleet, view and application commands plus one
// select / possess / release / stop entry per aircraft; feature modules add theirs with registerCommands().
import { setLang, t, useLangStore } from '@/i18n'
import { useSequence } from '@/sequence/store'
import { translateSeq } from '@/sequence/strings'
import { useRelay } from '@/store/relay'
import { useUi } from '@/store/ui'
import type { IconKey } from '@/ui/icons'
import { addSim, possess, release, scanNetwork, stopAll, stopVehicle } from './actions'
import { resetSim } from '@/twin/actions'
import { canResetSim } from '@/twin/model'
import { tt } from '@/twin/strings'

export type CommandGroup = 'navigation' | 'aircraft' | 'fleet' | 'view' | 'app'

export interface Command {
  id: string
  group: CommandGroup
  label: string
  /** extra words for matching (ids, hosts, other language) */
  keywords?: string[]
  icon?: IconKey
  shortcut?: string
  run(): void
}

type Provider = () => Command[]
const providers = new Set<Provider>()

export function registerCommands(p: Provider): () => void {
  providers.add(p)
  return () => providers.delete(p)
}

const isMac = typeof navigator !== 'undefined' && /mac/i.test(navigator.platform || navigator.userAgent)
export const MOD = isMac ? '⌘' : 'Ctrl+'

function shellCommands(): Command[] {
  const ui = useUi.getState()
  const vs = useRelay.getState().fleet?.vehicles ?? []
  const out: Command[] = [
    { id: 'ws.fly', group: 'navigation', label: t('cmd.goFly'), icon: 'ws.fly', shortcut: '1', run: () => ui.setWorkspace('fly') },
    { id: 'ws.agents', group: 'navigation', label: t('cmd.goAgents'), icon: 'ws.agents', shortcut: '2', run: () => ui.setWorkspace('agents') },
    { id: 'ws.records', group: 'navigation', label: t('cmd.goRecords'), icon: 'ws.records', shortcut: '3', run: () => ui.setWorkspace('records') },
    { id: 'ws.lab', group: 'navigation', label: t('cmd.goLab'), icon: 'ws.lab', shortcut: `${MOD}4`, keywords: ['lab', 'program', 'code', '实验室', '程序'], run: () => ui.setWorkspace('lab') }, // WP-3
    { id: 'fleet.stopAll', group: 'fleet', label: t('cmd.stopAll'), icon: 'stop', shortcut: `${MOD}.`, keywords: ['emergency', 'stop', '急停'], run: stopAll },
    { id: 'fleet.scan', group: 'fleet', label: t('cmd.scan'), icon: 'scan', run: scanNetwork },
    { id: 'fleet.connect', group: 'fleet', label: t('cmd.connect'), icon: 'connect', run: () => ui.openConnect('address') },
    { id: 'fleet.bridge', group: 'fleet', label: t('cmd.bridge'), icon: 'wifi', run: () => ui.openConnect('bridge') },
    { id: 'fleet.serial', group: 'fleet', label: tt('serial.tab'), icon: 'usb', keywords: ['serial', 'usb', 'hitl', '串口'], run: () => ui.openConnect('serial') }, // 11 §7.1
    { id: 'fleet.addSim', group: 'fleet', label: t('cmd.addSim'), icon: 'sim', run: addSim },
    { id: 'view.fleet', group: 'view', label: t('cmd.toggleFleet'), icon: 'panel.left', shortcut: `⌥${MOD}B`, run: () => ui.toggleFleet() },
    { id: 'view.inspector', group: 'view', label: t('cmd.toggleInspector'), icon: 'panel.right', shortcut: `⌥${MOD}I`, run: () => ui.toggleInspector() },
    { id: 'view.timeline', group: 'view', label: t('cmd.toggleTimeline'), icon: 'panel.bottom', shortcut: `⌥${MOD}T`, run: () => ui.toggleTimeline() },
    { id: 'view.sequence', group: 'view', label: translateSeq(useLangStore.getState().lang, 'seq.open'), icon: 'seq.timeline', keywords: ['sequence', 'timeline', 'ars', '时间轴', '序列'], run: () => { ui.setWorkspace('fly'); useSequence.getState().setOpen(!useSequence.getState().open) } }, // 10 §2
    { id: 'app.alerts', group: 'app', label: t('cmd.alerts'), icon: 'notify', run: () => ui.setAlertCenterOpen(true) },
    { id: 'app.settings', group: 'app', label: t('cmd.settings'), icon: 'settings', shortcut: `${MOD},`, run: () => ui.openSettings() },
    { id: 'app.en', group: 'app', label: t('cmd.langEn'), icon: 'globe', shortcut: `${MOD}2`, keywords: ['english', 'language'], run: () => setLang('en') },
    { id: 'app.zh', group: 'app', label: t('cmd.langZh'), icon: 'globe', shortcut: `${MOD}1`, keywords: ['chinese', '中文', 'language'], run: () => setLang('zh') },
  ]
  for (const v of vs) {
    const kw = [v.id, v.host ?? '', v.kind]
    out.push({ id: `ac.select.${v.id}`, group: 'aircraft', label: t('cmd.select', { aircraft: v.label }), icon: 'aircraft', keywords: kw, run: () => { ui.setWorkspace('fly'); ui.select(v.id) } })
    if (v.controller === 'human' && v.possession) {
      out.push({ id: `ac.release.${v.id}`, group: 'aircraft', label: t('cmd.release', { aircraft: v.label }), icon: 'possess', keywords: kw, run: () => release(v.id) })
    } else {
      out.push({ id: `ac.possess.${v.id}`, group: 'aircraft', label: t('cmd.possess', { aircraft: v.label }), icon: 'possess', keywords: kw, run: () => possess(v.id) })
    }
    out.push({ id: `ac.stop.${v.id}`, group: 'aircraft', label: t('cmd.stopOne', { aircraft: v.label }), icon: 'stop', keywords: kw, run: () => stopVehicle(v.id) })
    // v0.5.4: a disarmed or crashed simulator resets from the palette too
    if (canResetSim(v)) out.push({ id: `ac.reset.${v.id}`, group: 'aircraft', label: tt('reset.cmd', { aircraft: v.label }), icon: 'reset', keywords: [...kw, 'reset', 'crashed', '复位', '坠毁'], run: () => void resetSim(v.id) })
  }
  return out
}

export function allCommands(): Command[] {
  const list = shellCommands()
  for (const p of providers) list.push(...p())
  return list
}
