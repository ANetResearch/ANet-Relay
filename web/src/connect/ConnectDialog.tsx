// Discovery and connection dialog (02 §8, 560 wide): Scan · Address · Set up a new bridge. Controlled by the shell
// through the ui store (`useUi().connect`). Stop all (⌘.) keeps working inside it (shell keyboard layer).
import * as React from 'react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/ui/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/ui/components/ui/tabs'
import { useUi, type ConnectTab } from '@/store/ui'
import { useT8 } from '@/fleet/copy'
import { ScanTab } from './ScanTab'
import { AddressTab } from './AddressTab'
import { BridgeWizard } from './BridgeWizard'
import './connect.css'

export interface ConnectDialogProps {
  open: boolean
  tab: ConnectTab
  onOpenChange(open: boolean): void
}

export function ConnectDialog({ open, tab, onOpenChange }: ConnectDialogProps) {
  const t = useT8()
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
            <TabsContent value="bridge" className="p-5">
              {/* remount per opening so the wizard starts at step 1 */}
              {open ? <BridgeWizard onDone={close} /> : null}
            </TabsContent>
          </div>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
