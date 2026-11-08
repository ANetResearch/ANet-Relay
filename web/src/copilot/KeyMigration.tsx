// A key saved by an earlier version sits in the system keychain (06 §9). Relay does not decrypt it on its own: the
// operator either enters the key again (it then lives in the app data folder) or keeps using the keychain.
import { Button } from '@/ui/components/ui/button'
import { useCPT } from './strings'

export function KeyMigrationNotice({ onEnter, onKeep, busy = false }: { onEnter: () => void; onKeep: () => void; busy?: boolean }) {
  const t = useCPT()
  return (
    <div className="flex flex-col items-start gap-2 py-2" data-copilot-key-migration="">
      <p className="text-hud-sub text-[var(--g300)]">{t('cp.keyMigration')}</p>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" disabled={busy} onClick={onEnter} data-copilot-key-reenter="">
          {t('cp.keyMigration.enter')}
        </Button>
        <Button variant="ghost" size="sm" disabled={busy} onClick={onKeep} data-copilot-key-keep="">
          {t('cp.keyMigration.keep')}
        </Button>
      </div>
    </div>
  )
}
