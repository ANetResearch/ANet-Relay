// Settings → Network, arena block (05 §7.1, §7.3): callsign, arena port and automatic respawn. Backend settings
// (peer_callsign, peer_port, arena_auto_respawn) through the dialog's save; the backend validates the port
// against the MAVLink and API ports.
import * as React from 'react'
import { Input } from '@/ui/components/ui/input'
import { Switch } from '@/ui/components/ui/switch'
import { useTA } from './copy'

export interface ArenaSettingsValue {
  peer_callsign?: string
  peer_port?: number
  arena_auto_respawn?: boolean
}

const CALLSIGN_MAX = 16

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="set-row">
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="text-ed-value text-[var(--g100)]">{label}</span>
        {hint ? <span className="text-hud-sub text-[var(--g500)]">{hint}</span> : null}
      </div>
      <div className="flex min-w-0 items-center justify-end gap-2">{children}</div>
    </div>
  )
}

/** Text field that commits on Enter or blur. */
function Committed({ value, label, width, mono, inputMode, valid, onCommit }: {
  value: string
  label: string
  width: number
  mono?: boolean
  inputMode?: 'text' | 'numeric'
  /** invalid input reverts to the current value */
  valid?(v: string): boolean
  onCommit(v: string): void
}) {
  const [text, setText] = React.useState(value)
  const [seen, setSeen] = React.useState(value)
  if (seen !== value) {
    setSeen(value)
    setText(value)
  }
  return (
    <Input
      value={text}
      aria-label={label}
      inputMode={inputMode}
      maxLength={inputMode === 'numeric' ? 5 : CALLSIGN_MAX}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => {
        if (valid && !valid(text)) setText(value)
        else if (text !== value) onCommit(text)
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.currentTarget as HTMLInputElement).blur()
      }}
      className={mono ? 'h-7 text-right font-mono text-[12px]' : 'h-7 text-[12px]'}
      style={{ width }}
    />
  )
}

export function ArenaSettings({ s, save }: { s: ArenaSettingsValue; save(p: ArenaSettingsValue): Promise<boolean> }) {
  const t = useTA()
  const port = s.peer_port ?? 47114
  return (
    <>
      <Row label={t('arena.set.callsign')} hint={t('arena.set.callsignHint')}>
        <Committed value={s.peer_callsign ?? ''} label={t('arena.set.callsign')} width={160} onCommit={(v) => void save({ peer_callsign: v.trim().slice(0, CALLSIGN_MAX) })} />
      </Row>
      <Row label={t('arena.set.port')} hint={t('arena.set.portHint')}>
        <Committed
          value={String(port)}
          label={t('arena.set.port')}
          width={76}
          mono
          inputMode="numeric"
          valid={(v) => /^\d+$/.test(v) && Number(v) >= 1024 && Number(v) <= 65535}
          onCommit={(v) => void save({ peer_port: Number(v) })}
        />
      </Row>
      <Row label={t('arena.set.respawn')} hint={t('arena.set.respawnHint')}>
        <Switch checked={s.arena_auto_respawn ?? true} onCheckedChange={(v) => void save({ arena_auto_respawn: v })} aria-label={t('arena.set.respawn')} />
      </Row>
    </>
  )
}
