// Inspector → Overview → Firmware of a real aircraft (11 §4, §5, §11): family, version, release type, git, board,
// the twin match level (exact / version / series / nearest / none, with the warning from series down) and the
// parameter table progress. Actions: Generate twin profile (POST /twins/profiles from_vehicle; disabled with the
// reason until the parameters are complete), Start twin (POST /sims engine sitl with the aircraft's profile,
// capturing one first), and for PX4 Hardware in the loop (USB serial only); ArduPilot states why HITL is not
// available and points at the twin.
import * as React from 'react'
import { api, type TwinProfile, type TwinProfileSummary, type Vehicle } from '@/api'
import { useLang } from '@/i18n'
import { cn } from '@/lib/utils'
import { notify } from '@/shell/notify'
import { useVehicle } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { Button } from '@/ui/components/ui/button'
import { Spinner } from '@/ui/components/ui/spinner'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/ui/components/ui/tooltip'
import { Icon, type IconKey } from '@/ui/icons'
import { Block, Kv, KvList } from '@/inspector/parts'
import { usePoll } from '@/inspector/usePoll'
import { openHitlDialog } from './HitlDialog'
import { captureBlock, familyName, hitlEntry, profileOf, startBlock, twinErrorView, type Because, type TwinErrorView } from './model'
import { TwinFailure } from './parts'
import { useTT } from './strings'

function ActionButton({ icon, label, block, busy, onClick, data }: { icon: IconKey; label: string; block: string | null; busy: boolean; onClick(): void; data: string }) {
  const button = (
    <Button variant="outline" size="sm" className="gap-1.5" disabled={Boolean(block) || busy} onClick={onClick} data-fw-action={data}>
      {busy ? <Spinner /> : <Icon icon={icon} className="size-3.5" />}
      {label}
    </Button>
  )
  if (!block) return button
  return (
    <Tooltip>
      <TooltipTrigger render={<span className="inline-flex" />}>{button}</TooltipTrigger>
      <TooltipContent className="max-w-[260px]">{block}</TooltipContent>
    </Tooltip>
  )
}

export function FirmwareBlock({ vehicleId }: { vehicleId: string }) {
  const v = useVehicle(vehicleId)
  if (!v || v.kind !== 'real') return null
  return <FirmwareBody key={v.id} v={v} /> // keyed: failures and busy state belong to one aircraft
}

function FirmwareBody({ v }: { v: Vehicle }) {
  const t = useTT()
  const lang = useLang()
  const id = v.id
  const [complete, setComplete] = React.useState(false)
  // the identity and the parameter progress: every 2 s until the table is read, then every 10 s
  const fwPoll = usePoll(() => api.vehicleFirmware(id), complete ? 10000 : 2000, `${id}|${v.firmware ?? ''}|${v.link.state === 'lost'}`)
  const fw = fwPoll.data
  if (fw && fw.params.complete !== complete) setComplete(fw.params.complete)
  const [nonce, setNonce] = React.useState(0)
  const profilesPoll = usePoll(() => api.twinProfiles(), 0, `${id}|${nonce}`)
  const profile: TwinProfileSummary | null = profileOf(profilesPoll.data?.profiles ?? [], id)
  const [busy, setBusy] = React.useState<'capture' | 'start' | null>(null)
  const [failure, setFailure] = React.useState<TwinErrorView | null>(null)

  const say = (b: Because | null) => (b ? t(b.key, b.params) : null)
  const capture = async (): Promise<TwinProfile> => {
    const p = await api.captureTwinProfile(profile ? { from_vehicle: id, profile: profile.id } : { from_vehicle: id })
    setNonce((n) => n + 1)
    return p
  }
  const onCapture = () => {
    setBusy('capture')
    setFailure(null)
    capture()
      .then((p) => notify(t('fw.captured', { name: p.name })))
      .catch((e: unknown) => setFailure(twinErrorView(e, lang)))
      .finally(() => setBusy(null))
  }
  const onStart = () => {
    setBusy('start')
    setFailure(null)
    ;(profile ? Promise.resolve<{ id: string }>(profile) : capture())
      .then((p) => api.addSim({ engine: 'sitl', profile: p.id }))
      .then((r) => {
        const added = ((r as { added?: unknown }).added as string[] | undefined) ?? []
        notify(t('fw.started', { aircraft: v.label, sims: added.join(', ') || '—' }))
        if (added[0]) useUi.getState().select(added[0])
      })
      .catch((e: unknown) => setFailure(twinErrorView(e, lang)))
      .finally(() => setBusy(null))
  }

  const ident = fw?.identity
  const fi = v.firmware_info
  const family = ident?.family ?? fi?.family ?? null
  const unknown = t('fw.unknown')
  const match = fw?.match
  const hitl = hitlEntry(v, fw ?? null)
  const frameProblem = lang === 'zh' ? (fw?.frame_problem_zh ?? fw?.frame_problem) : fw?.frame_problem
  const warning = match?.warning ? (lang === 'zh' ? (match.warning_zh ?? match.warning_en) : match.warning_en) || t('match.warning') : null

  return (
    <Block
      title={t('fw.title')}
      right={match ? <span className={cn('text-[11px]', match.warning ? 'font-[560] text-[var(--g50)]' : 'text-[var(--g300)]')}>{t(`match.${match.match}`)}</span> : null}
      className="gap-2"
    >
      <div data-firmware-block={id} data-match={match?.match}>
        <KvList>
          <Kv label={t('fw.field.family')} value={familyName(family, ident?.vehicle ?? fi?.vehicle) || v.autopilot} reason={unknown} />
          <Kv label={t('fw.field.version')} value={ident?.version ?? fi?.version ?? null} reason={unknown} />
          <Kv label={t('fw.field.type')} value={(ident?.type ?? fi?.type) ? t(`fw.type.${(ident?.type ?? fi?.type)!}`) : null} reason={unknown} />
          <Kv label={t('fw.field.git')} value={ident?.git ?? fi?.git ?? null} reason={unknown} />
          <Kv label={t('fw.field.board')} value={ident?.board ?? fi?.board ?? null} reason={unknown} />
          <Kv label={t('fw.field.match')}>
            <span className="flex items-center justify-end gap-1.5">
              {match?.warning ? <Icon icon="alert" className="size-3 text-[var(--g100)]" /> : null}
              <span>{match ? (match.recommended ? `${t(`match.${match.match}`)} · ${match.recommended}` : t('fw.block.noPack')) : '—'}</span>
            </span>
          </Kv>
          <Kv label={t('fw.field.params')} value={fw ? t('fw.paramsValue', { received: fw.params.received, total: fw.params.total || '—' }) : null} reason={t('fw.loading')} />
          <Kv label={t('fw.field.profile')} value={profile ? profile.name : t('fw.noProfile')} />
        </KvList>
      </div>
      {!fw && fwPoll.error ? <p className="text-hud-sub text-[var(--g500)]">{t('fw.error', { msg: twinErrorView(fwPoll.error, lang).message })}</p> : null}
      {warning ? (
        <p className="flex items-start gap-1.5 text-hud-sub text-[var(--g200)]" data-fw-warning="">
          <Icon icon="alert" className="mt-0.5 size-3 shrink-0" />
          {warning}
        </p>
      ) : null}
      {frameProblem ? <p className="text-hud-sub text-[var(--g200)]">{frameProblem}</p> : null}
      <div className="flex flex-wrap gap-2">
        <ActionButton icon="chip" label={t('fw.capture')} block={say(captureBlock(fw ?? null))} busy={busy === 'capture'} onClick={onCapture} data="capture" />
        <ActionButton icon="sim" label={busy === 'start' ? t('fw.starting') : t('fw.start')} block={busy === 'start' ? null : say(startBlock(fw ?? null, profile))} busy={busy === 'start'} onClick={onStart} data="start" />
        {hitl.mode === 'button' ? <ActionButton icon="usb" label={t('fw.hitl')} block={say(hitl.block)} busy={false} onClick={() => openHitlDialog(id)} data="hitl" /> : null}
      </div>
      {hitl.mode === 'note' ? (
        <p className="text-hud-sub text-[var(--g400)]" data-fw-hitl-note="">
          {t(hitl.note.key)}
        </p>
      ) : null}
      {failure ? <TwinFailure failure={failure} /> : null}
    </Block>
  )
}
