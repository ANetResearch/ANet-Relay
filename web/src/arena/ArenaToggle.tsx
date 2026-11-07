// Top-bar Arena toggle (05 §7.1): the network icon with the number of terminals in the arena. Joining asks once
// for the first-use disclosure (the macOS local-network permission prompt follows from the operating system when
// the backend first uses the LAN); leaving is immediate. The arena state itself comes from the stream.
import * as React from 'react'
import { notifyError } from '@/shell/notify'
import { cn } from '@/lib/utils'
import { Button } from '@/ui/components/ui/button'
import { Popover, PopoverContent } from '@/ui/components/ui/popover'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/ui/components/ui/tooltip'
import { StateIcon } from '@/ui/icons'
import { useTA } from './copy'
import { arenaDisclosed, markArenaDisclosed, setArenaEnabled, useArena, useArenaEnabled, useArenaPeerCount } from './store'
import './arena.css'

export function ArenaToggle() {
  const t = useTA()
  const enabled = useArenaEnabled()
  const peers = useArenaPeerCount()
  const seat = useArena((s) => (s.view?.enabled ? s.view.seat : null))
  const error = useArena((s) => (s.view?.enabled ? s.view.error : null))
  const [ask, setAsk] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const anchor = React.useRef<HTMLButtonElement>(null)

  const apply = (on: boolean) => {
    setBusy(true)
    void setArenaEnabled(on)
      .catch((e: unknown) => notifyError(e))
      .finally(() => setBusy(false))
  }
  const onClick = () => {
    if (enabled) apply(false)
    else if (arenaDisclosed()) apply(true)
    else setAsk(true)
  }
  const status = !enabled
    ? t('arena.join')
    : error
      ? t('arena.portError', { error })
      : seat === null
        ? t('arena.statusChoosing')
        : t('arena.statusSeat', { seat: seat + 1, n: peers })

  return (
    <>
      <Tooltip>
        <TooltipTrigger
          render={
            <button
              ref={anchor}
              type="button"
              onClick={onClick}
              disabled={busy}
              aria-pressed={enabled}
              aria-label={enabled ? t('arena.leave') : t('arena.join')}
              className={cn(
                'no-drag flex h-7 items-center gap-1.5 rounded-md px-2 text-[11px] leading-4 outline-none hover:bg-[var(--g900)] focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-60',
                enabled ? 'text-[var(--g100)]' : 'text-[var(--g500)]',
              )}
              data-arena-toggle={enabled ? 'on' : 'off'}
            />
          }
        >
          <StateIcon icon="arena" alt={!enabled} className="size-4" />
          <span className="whitespace-nowrap max-[1279px]:sr-only">{t('arena.toggle')}</span>
          {enabled ? (
            <span className="arena-toggle-badge num" aria-label={t('arena.peersBadge', { n: peers + 1 })}>
              {peers + 1}
            </span>
          ) : null}
        </TooltipTrigger>
        <TooltipContent side="bottom" align="end">
          {status}
        </TooltipContent>
      </Tooltip>
      <Popover open={ask} onOpenChange={setAsk}>
        <PopoverContent anchor={anchor} align="end" sideOffset={6} className="l3 w-[320px] gap-3 p-3" data-arena-disclosure="">
          <p className="text-[12px] leading-[18px] text-[var(--g100)]">{t('arena.disclosure')}</p>
          <p className="text-hud-sub text-[var(--g400)]">{t('arena.disclosureNote')}</p>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setAsk(false)}>
              {t('arena.cancel')}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                markArenaDisclosed()
                setAsk(false)
                apply(true)
              }}
            >
              {t('arena.joinAction')}
            </Button>
          </div>
        </PopoverContent>
      </Popover>
    </>
  )
}
