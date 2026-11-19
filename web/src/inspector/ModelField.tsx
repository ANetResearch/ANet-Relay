// Inspector Overview "Model" readout (12 §2.2): what the aircraft is drawn and flown as. A simulator's model follows
// its frame (the Simulator block changes it); a real aircraft's is the operator's choice (POST /vehicles/{id}/model,
// remembered by airframe identity), shown as a quiet select.
import * as React from 'react'
import { api, type Vehicle } from '@/api'
import { useT } from '@/i18n'
import { notifyError } from '@/shell/notify'
import { useHello } from '@/store/selectors'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/ui/components/ui/select'
import { modelOf, MODELS } from '@/stage/models'

export function ModelField({ v }: { v: Vehicle }) {
  const t = useT()
  const operator = Boolean(useHello()?.operator)
  const [busy, setBusy] = React.useState(false)
  const model = modelOf(v)
  const name = (m: Vehicle['model']) => (m === 'anet-du7' ? MODELS['anet-du7'].title : t('model.micro'))
  if (v.kind !== 'real') return <span data-model={model}>{name(model)}</span>
  const items = [
    { value: 'anet-du7', label: name('anet-du7') },
    { value: 'micro', label: name('micro') },
  ]
  const change = (m: string | null) => {
    if ((m !== 'anet-du7' && m !== 'micro') || m === model) return
    setBusy(true)
    api
      .setModel(v.id, m)
      .catch(notifyError)
      .finally(() => setBusy(false))
  }
  return (
    <Select items={items} value={model} onValueChange={(x) => change(x as string | null)} disabled={busy || !operator}>
      <SelectTrigger
        size="sm"
        className="-ml-2 h-6 w-auto gap-1 border-transparent bg-transparent px-2 text-[12px] text-[var(--g100)] hover:bg-[var(--g850)] dark:bg-transparent dark:hover:bg-[var(--g850)]"
        aria-label={t('insp.field.model')}
        title={operator ? t('model.realHint') : t('model.operatorOnly')}
        data-model={model}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {items.map((it) => (
          <SelectItem key={it.value} value={it.value} className="text-[12px]">
            {it.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
