// Control tab: the ready-made plans of an aircraft's model (12 §4): "ANetDU7 box test" (du7_box) on an ANetDU7, the
// route of the onboard test node. Title and description come from GET /plans/builtin in the window's language;
// running it is a plan like the operator's own (possession, altitude hold), and a real aircraft that is not armed
// asks for ARM first, as the sequence does.
import * as React from 'react'
import { api, RelayApiError, type BuiltinPlan, type Vehicle } from '@/api'
import { pickText, useLang, useT } from '@/i18n'
import { notifyError } from '@/shell/notify'
import { Button } from '@/ui/components/ui/button'
import { Icon } from '@/ui/icons'
import { Group, TypeToConfirm } from '@/ui/relay'
import { modelOf } from '@/stage/models'
import { useCT } from './strings'

/** Built-in plans by model (12 §4); the backend's list decides what exists. */
export const PLANS_FOR: Record<Vehicle['model'], readonly string[]> = { 'anet-du7': ['du7_box', 'du7_square10'], micro: [] }

let cached: Promise<BuiltinPlan[]> | null = null
function loadPlans(): Promise<BuiltinPlan[]> {
  cached ??= api.plansBuiltin().then(
    (r) => (Array.isArray(r?.plans) ? r.plans : []),
    () => {
      cached = null
      return []
    },
  )
  return cached
}

function usePlans(): BuiltinPlan[] {
  const [plans, setPlans] = React.useState<BuiltinPlan[]>([])
  React.useEffect(() => {
    let alive = true
    void loadPlans().then((p) => alive && setPlans(p))
    return () => {
      alive = false
    }
  }, [])
  return plans
}

function PlanRow({ v, plan }: { v: Vehicle; plan: BuiltinPlan }) {
  const t = useT()
  const ct = useCT()
  const lang = useLang()
  const [busy, setBusy] = React.useState(false)
  const [confirming, setConfirming] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const human = v.controller === 'human'
  const mine = v.plan?.name === plan.id
  const running = mine && v.plan?.state === 'running'
  const title = pickText(lang, plan.title, plan.title_zh ?? '')
  const description = pickText(lang, plan.description ?? '', plan.description_zh ?? '')
  const start = (confirm?: string) => api.planBuiltin(v.id, plan.id, confirm ? { confirm } : undefined)
  const run = async () => {
    if (v.kind === 'real' && !v.armed && plan.steps[0]?.action === 'takeoff') {
      setError(null)
      setConfirming(true)
      return
    }
    setBusy(true)
    try {
      await start()
    } catch (e) {
      notifyError(e)
    } finally {
      setBusy(false)
    }
  }
  let status: string | null = null
  if (mine && v.plan) {
    const p = v.plan
    if (p.state === 'running') status = ct('plan.running', { n: p.index + 1, total: p.count })
    else if (p.state === 'done') status = ct('plan.done')
    else if (p.state === 'failed') status = ct('plan.failed', { n: p.index + 1, detail: p.detail ?? '' })
    else status = ct('plan.cancelled')
  }
  const blocked = !human ? ct('ctl.possessFirst') : !v.capabilities.althold ? ct('ctl.noSkills') : null
  return (
    <div className="flex flex-col gap-2" data-builtin-plan={plan.id}>
      <div className="flex items-start gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate text-ed-value text-[var(--g100)]">{title}</span>
          {description ? <span className="text-hud-sub text-[var(--g500)]">{description}</span> : null}
        </div>
        {running ? (
          <Button variant="outline" size="sm" className="shrink-0" disabled={!human || busy} onClick={() => void api.cancelPlan(v.id).catch(notifyError)} data-builtin-cancel="">
            <Icon icon="stop" />
            {ct('plan.cancel')}
          </Button>
        ) : (
          <Button variant="outline" size="sm" className="shrink-0" disabled={Boolean(blocked) || busy || v.plan?.state === 'running'} onClick={() => void run()} title={blocked ?? undefined} data-builtin-run="">
            <Icon icon="tl.play" />
            {t('builtin.run')}
          </Button>
        )}
      </div>
      {status ? (
        <p className="num text-hud-sub text-[var(--g300)]" data-builtin-status={v.plan?.state}>
          {status}
        </p>
      ) : blocked ? (
        <p className="text-hud-sub text-[var(--g500)]">{blocked}</p>
      ) : (
        <p className="text-hud-sub text-[var(--g500)]">{t('builtin.hint')}</p>
      )}
      {confirming && !v.armed && human && !running ? (
        <TypeToConfirm
          word="ARM"
          prompt={ct('plan.confirm', { aircraft: v.label })}
          actionLabel={ct('plan.run')}
          onConfirm={async () => {
            try {
              await start('ARM')
              setConfirming(false)
            } catch (e) {
              setError(e instanceof RelayApiError ? e.text(lang) : String(e))
            }
          }}
          onCancel={() => setConfirming(false)}
          error={error}
        />
      ) : null}
    </div>
  )
}

/** The ready-made plans of the aircraft's model; nothing for a model without any. */
export function BuiltinPlans({ v }: { v: Vehicle }) {
  const t = useT()
  const plans = usePlans()
  const ids = PLANS_FOR[modelOf(v)]
  const mine = plans.filter((p) => ids.includes(p.id))
  if (!mine.length) return null
  return (
    <Group label={t('builtin.title')} count={mine.length > 1 ? mine.length : undefined}>
      <div className="flex flex-col gap-3 px-3" data-builtin-plans="">
        {mine.map((p) => (
          <PlanRow key={p.id} v={v} plan={p} />
        ))}
      </div>
    </Group>
  )
}
