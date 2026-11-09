// Control tab entry to the timeline dock (10 §2): opens the dock under the stage and shows what is loaded.
import { Button } from '@/ui/components/ui/button'
import { Icon } from '@/ui/icons'
import { Group } from '@/ui/relay'
import { useSequence } from './store'
import { useST } from './strings'

export function SequenceEntry() {
  const st = useST()
  const open = useSequence((s) => s.open)
  const name = useSequence((s) => s.doc.name)
  const seconds = useSequence((s) => s.doc.duration_s)
  return (
    <Group label={st('seq.title')}>
      <div className="flex items-center gap-2 px-3" data-sequence-entry="">
        <span className="min-w-0 flex-1 truncate text-hud-sub text-[var(--g300)]">
          {name || '—'} <span className="num text-[var(--g500)]">· {seconds.toFixed(1)} s</span>
        </span>
        <Button variant="outline" size="sm" onClick={() => useSequence.getState().setOpen(!open)} data-sequence-toggle={open ? 'open' : 'closed'}>
          <Icon icon="seq.timeline" />
          {open ? st('seq.close') : st('seq.open')}
        </Button>
      </div>
    </Group>
  )
}
