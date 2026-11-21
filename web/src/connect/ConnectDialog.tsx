// Discovery and connection dialog (02 §8, 560 wide): Scan · Address · USB serial (11 §7.1) · Set up a new bridge.
// Controlled by the shell through the ui store (`useUi().connect`). Stop all (⌘.) keeps working inside it (shell
// keyboard layer).
import * as React from 'react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/ui/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/ui/components/ui/tabs'
import { useUi, type ConnectTab } from '@/store/ui'
import { useT8 } from '@/fleet/copy'
import { ScanTab } from './ScanTab'
import { AddressTab } from './AddressTab'
import { BridgeWizard } from './BridgeWizard'
import { SerialTab } from '@/twin/SerialTab' // 11 §7.1
import { useTT } from '@/twin/strings'
import { api } from '@/api'
import { notifyError } from '@/shell/notify'
import { useHello } from '@/store/selectors'
import { ToggleGroup, ToggleGroupItem } from '@/ui/components/ui/toggle-group'
import './connect.css'

export interface ConnectDialogProps {
  open: boolean
  tab: ConnectTab
  onOpenChange(open: boolean): void
}

/** What newly found real aircraft are taken for (the real_model_default setting, 12 §2.2). */
function NewModelRow({ open }: { open: boolean }) {
  const t = useT8()
  const operator = Boolean(useHello()?.operator)
  const [value, setValue] = React.useState<'anet-du7' | 'micro' | null>(null)
  React.useEffect(() => {
    if (!open) return
    api.settings().then(
      (s) => setValue(s.real_model_default === 'micro' ? 'micro' : 'anet-du7'),
      () => undefined,
    )
  }, [open])
  const change = (m: 'anet-du7' | 'micro') => {
    const before = value
    setValue(m)
    api.putSettings({ real_model_default: m }).catch((e) => {
      setValue(before)
      notifyError(e)
    })
  }
  return (
    <div className="flex shrink-0 items-center gap-3 border-t border-[var(--border)] px-5 py-3" data-connect-model="">
      <span className="text-hud-sub text-[var(--g200)]">{t('conn.newModel')}</span>
      <ToggleGroup
        value={value ? [value] : []}
        onValueChange={(v: unknown[]) => (v[0] === 'anet-du7' || v[0] === 'micro') && change(v[0])}
        disabled={!operator || value === null}
        spacing={0}
        className="rounded-md bg-[var(--g950)] p-0.5 ring-1 ring-[var(--border)]"
        aria-label={t('conn.newModel')}
      >
        <ToggleGroupItem value="anet-du7" title={t('fleet.model.du7')} className="h-6 rounded-[5px] px-2 text-[12px] text-[var(--g400)] hover:bg-transparent data-[pressed]:text-[var(--g50)]">
          ANetDU7
        </ToggleGroupItem>
        <ToggleGroupItem value="micro" title={t('fleet.model.micro')} className="h-6 rounded-[5px] px-2 text-[12px] text-[var(--g400)] hover:bg-transparent data-[pressed]:text-[var(--g50)]">
          Micro 120
        </ToggleGroupItem>
      </ToggleGroup>
      <span className="min-w-0 flex-1 text-[11px] text-[var(--g500)]">{t('conn.newModelHint')}</span>
    </div>
  )
}

export function ConnectDialog({ open, tab, onOpenChange }: ConnectDialogProps) {
  const t = useT8()
  const tt = useTT()
  const [current, setCurrent] = React.useState<ConnectTab>(tab)
  // follow the tab the shell asks for every time the dialog opens
  const [seen, setSeen] = React.useState({ open, tab })
  if (seen.open !== open || seen.tab !== tab) {
    setSeen({ open, tab })
    if (open) setCurrent(tab)
  }
  const close = React.useCallback(() => useUi.getState().closeConnect(), [])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="l3 flex max-h-[calc(100vh-48px)] flex-col gap-0 overflow-hidden p-0 sm:max-w-[560px]" data-connect-dialog="">
        <DialogHeader className="shrink-0 gap-1 px-5 pt-5 pb-3">
          <DialogTitle className="text-[15px] font-[560] text-[var(--g50)]">{t('conn.title')}</DialogTitle>
          <DialogDescription className="text-hud-sub text-[var(--g400)]">{t('conn.description')}</DialogDescription>
        </DialogHeader>
        <Tabs value={current} onValueChange={(v) => setCurrent(v as ConnectTab)} className="min-h-0 flex-1 gap-0">
          <TabsList variant="line" className="h-9 w-full shrink-0 justify-start gap-4 rounded-none border-b border-[var(--border)] px-5">
            <TabsTrigger value="scan" className="line-tab">
              {t('conn.tab.scan')}
            </TabsTrigger>
            <TabsTrigger value="address" className="line-tab">
              {t('conn.tab.address')}
            </TabsTrigger>
            <TabsTrigger value="serial" className="line-tab" data-connect-tab="serial">
              {tt('serial.tab')}
            </TabsTrigger>
            <TabsTrigger value="bridge" className="line-tab">
              {t('conn.tab.bridge')}
            </TabsTrigger>
          </TabsList>
          <div className="min-h-0 flex-1 overflow-y-auto">
            <TabsContent value="scan" className="p-5">
              <ScanTab onDone={close} />
            </TabsContent>
            <TabsContent value="address" className="p-5">
              <AddressTab onDone={close} />
            </TabsContent>
            <TabsContent value="serial" className="p-5">
              {open && current === 'serial' ? <SerialTab onDone={close} /> : null}
            </TabsContent>
            <TabsContent value="bridge" className="p-5">
              {/* remount per opening so the wizard starts at step 1 */}
              {open ? <BridgeWizard onDone={close} /> : null}
            </TabsContent>
          </div>
        </Tabs>
        <NewModelRow open={open} />
      </DialogContent>
    </Dialog>
  )
}
