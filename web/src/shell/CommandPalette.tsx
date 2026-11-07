// Command palette (⌘K / Ctrl+K): cmdk inside the shadcn CommandDialog; groups Navigation, Aircraft, Fleet, View,
// Application. ⌘. still stops every aircraft while it is open (keyboard layer, capture phase).
import { useLang, useT } from '@/i18n'
import { useVehicleIds } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { Command as CommandRoot, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandShortcut } from '@/ui/components/ui/command'
import { Icon } from '@/ui/icons'
import { allCommands, type Command, type CommandGroup as Group } from './commands'

const ORDER: Group[] = ['fleet', 'aircraft', 'navigation', 'view', 'app']

export function CommandPalette() {
  const t = useT()
  useLang()
  const open = useUi((s) => s.commandOpen)
  const setOpen = useUi((s) => s.setCommandOpen)
  // subscribed so the list is rebuilt when the language changes or aircraft come and go
  useVehicleIds()
  const commands = open ? allCommands() : []
  const groups = ORDER.map((g) => [g, commands.filter((c) => c.group === g)] as const).filter(([, cs]) => cs.length > 0)
  const run = (c: Command) => {
    setOpen(false)
    c.run()
  }
  return (
    <CommandDialog open={open} onOpenChange={setOpen} title={t('cmd.title')} description={t('cmd.placeholder')} className="l3 w-[560px] max-w-[calc(100vw-32px)] sm:max-w-[560px]">
      <CommandRoot loop className="rounded-none bg-transparent p-0">
      <CommandInput placeholder={t('cmd.placeholder')} />
      <CommandList className="max-h-[min(420px,60vh)]">
        <CommandEmpty>{t('cmd.empty')}</CommandEmpty>
        {groups.map(([g, cs]) => (
          <CommandGroup key={g} heading={t(`cmd.group.${g}`)}>
            {cs.map((c) => (
              <CommandItem key={c.id} value={`${c.label} ${c.keywords?.join(' ') ?? ''} ${c.id}`} onSelect={() => run(c)}>
                {c.icon ? <Icon icon={c.icon} className="text-[var(--g400)]" /> : null}
                <span className="truncate">{c.label}</span>
                {c.shortcut ? <CommandShortcut className="num">{c.shortcut}</CommandShortcut> : null}
              </CommandItem>
            ))}
          </CommandGroup>
        ))}
      </CommandList>
      </CommandRoot>
    </CommandDialog>
  )
}
