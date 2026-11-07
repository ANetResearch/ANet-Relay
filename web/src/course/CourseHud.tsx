// Course timing HUD (02 §10.4, top centre of the stage, 480 x 56) and the completion card (02 §10.5, stage centre,
// 360 wide). The running clock is written from a frame loop on the backend clock; React renders only when the
// run's discrete state changes. Registers G (start / stop the selected aircraft's course) and the course commands.
// Contract: CONTRACTS.md#coursehud.
import * as React from 'react'
import { registerDismisser } from '@/shell/keyboard'
import { api } from '@/api'
import { rememberCourse } from '@/control/actions'
import { useLang } from '@/i18n'
import { courseTime, split as splitText } from '@/lib/format'
import { useReplay } from '@/records/replay'
import { registerCommands } from '@/shell/commands'
import { registerHotkey } from '@/shell/keyboard'
import { notify, notifyError } from '@/shell/notify'
import { backendNow, useRelay } from '@/store/relay'
import { findVehicle, getVehicle } from '@/store/selectors'
import { useUi } from '@/store/ui'
import { Button } from '@/ui/components/ui/button'
import { Icon } from '@/ui/icons'
import { cn } from '@/lib/utils'
import { t6, useT6, type AnyKey6 } from '@/stage/copy'
import { useStage } from '@/stage/store'
import { isActive, liveElapsed, type CourseView } from './geometry'
import { PopNumber } from './PopNumber'
import { courseName, useCourse, watchCourses } from './store'
import './course.css'

export interface CourseHudProps {
  /** the selected (or possessed) aircraft whose course the HUD follows */
  vehicleId: string | null
}

/** Reason words of 03 §8 aborts; unknown reasons are shown as sent. */
export function abortReason(reason: string | null): string {
  if (!reason) return t6('course.reason.stopped')
  const key = `course.reason.${reason}` as AnyKey6
  const text = t6(key)
  return text === key ? reason.replace(/_/g, ' ') : text
}

/** Starts the chosen course on an aircraft, or stops its active run. Used by G, the palette and CourseControls. */
export async function toggleCourseOn(vehicleId: string, courseId?: string): Promise<void> {
  const v = getVehicle(vehicleId)
  if (!v) return
  try {
    if (isActive(v.course)) {
      await api.courseStop(vehicleId)
      return
    }
    if (!v.capabilities.position) {
      notify(t6('course.needsPosition'), { level: 'warn' })
      return
    }
    const id = courseId ?? useCourse.getState().chosen
    await api.courseStart(vehicleId, id)
    // the input layer repeats the last course on the pad's Square button
    rememberCourse(vehicleId, id)
  } catch (e) {
    notifyError(e)
  }
}

function useCourseBindings(): void {
  React.useEffect(() => {
    const offKey = registerHotkey({
      combo: 'g',
      workspace: 'fly',
      handler: () => {
        const id = useUi.getState().selectedId
        if (!id) {
          notify(t6('course.selectFirst'))
          return
        }
        void toggleCourseOn(id)
      },
    })
    const offCmd = registerCommands(() => {
      const id = useUi.getState().selectedId
      const v = id ? getVehicle(id) : null
      if (!v || !v.capabilities.position) return []
      const active = isActive(v.course)
      return [
        {
          id: `course.toggle.${v.id}`,
          group: 'aircraft' as const,
          label: t6(active ? 'course.cmdStop' : 'course.cmdStart', { aircraft: v.label }),
          icon: 'course' as const,
          shortcut: 'G',
          keywords: ['ring', 'course', '穿环', v.id],
          run: () => void toggleCourseOn(v.id),
        },
      ]
    })
    return () => {
      offKey()
      offCmd()
    }
  }, [])
}

interface HudFacts {
  courseId: string
  state: CourseView['state']
  next: number
  total: number
  lastSplit: number | null
  best: number | null
  reason: string | null
  nameEn: string | null
  nameZh: string | null
}

function useHudFacts(vehicleId: string | null): HudFacts | null {
  const key = useRelay((s) => {
    const c = findVehicle(s.fleet?.vehicles, vehicleId)?.course as CourseView | null | undefined
    if (!c) return ''
    const f: HudFacts = {
      courseId: c.course_id, state: c.state, next: c.next, total: c.total,
      lastSplit: c.splits.length ? c.splits[c.splits.length - 1] : null,
      best: c.best_s, reason: c.reason, nameEn: c.name_en ?? null, nameZh: c.name_zh ?? null,
    }
    return JSON.stringify(f)
  })
  return React.useMemo(() => (key ? (JSON.parse(key) as HudFacts) : null), [key])
}

/** The total clock, advanced every frame while running (no React render per frame). */
function RunClock({ vehicleId }: { vehicleId: string }) {
  const ref = React.useRef<HTMLSpanElement>(null)
  React.useEffect(() => {
    let raf = 0
    const tick = () => {
      const s = useRelay.getState()
      const c = findVehicle(s.fleet?.vehicles, vehicleId)?.course
      const el = ref.current
      if (c && el && s.fleet) {
        const text = courseTime(liveElapsed(c, s.fleet.t, backendNow())) ?? '—'
        if (el.textContent !== text) el.textContent = text
      }
      raf = requestAnimationFrame(tick)
    }
    tick()
    return () => cancelAnimationFrame(raf)
  }, [vehicleId])
  return <span ref={ref} className="course-hud-time" />
}

export function CourseHud({ vehicleId }: CourseHudProps) {
  const t = useT6()
  const lang = useLang()
  const facts = useHudFacts(vehicleId)
  const replaying = useReplay((r) => r.active)
  // the aborted line stays for 2 s after the abort was seen (the watcher clears the flag)
  const abortFresh = useCourse((s) => (vehicleId ? s.aborted[vehicleId] === true : false))
  const defs = useCourse((s) => s.defs)
  useCourseBindings()
  React.useEffect(() => watchCourses(), [])

  const shown = !!facts && !replaying && (facts.state !== 'aborted' || abortFresh)
  React.useEffect(() => {
    useStage.getState().setCourseHudShown(shown)
  }, [shown])
  React.useEffect(() => () => useStage.getState().setCourseHudShown(false), [])

  if (!shown || !facts || !vehicleId) return null
  void defs
  const name = courseName(lang, facts.courseId, { name_en: facts.nameEn, name_zh: facts.nameZh })
  if (facts.state === 'aborted') {
    return (
      <div className="course-hud" data-aborted="" role="status" data-course-hud="">
        <span className="cap course-hud-name">{name}</span>
        <span className="course-hud-msg">{t('course.aborted', { reason: abortReason(facts.reason) })}</span>
      </div>
    )
  }
  const ring = facts.state === 'finished' ? facts.total : Math.min(facts.total, facts.next + 1)
  const best = courseTime(facts.best)
  return (
    <div className="course-hud" role="status" data-course-hud="">
      <span className="cap course-hud-name max-w-[160px]">{name}</span>
      {facts.state === 'waiting_takeoff' ? (
        <span className="course-hud-msg">{t('course.takeOffToStart')}</span>
      ) : (
        <>
          <span className="course-hud-cell">
            <span className="cap">{t('course.hud.ring')}</span>
            <PopNumber text={`${ring} / ${facts.total}`} className="course-hud-val" />
          </span>
          <RunClock vehicleId={vehicleId} />
          <span className="course-hud-cell">
            <span className="cap">{t('course.hud.split')}</span>
            <PopNumber text={splitText(facts.lastSplit) ?? '—'} className={cn('course-hud-val', facts.lastSplit === null && 'text-[var(--g500)]')} />
          </span>
        </>
      )}
      <span className="course-hud-cell">
        <span className="cap">{t('course.hud.best')}</span>
        <span className={cn('course-hud-val', !best && 'text-[var(--g500)]')}>{best ?? '—'}</span>
      </span>
    </div>
  )
}

export function CourseFinishCard() {
  const t = useT6()
  const lang = useLang()
  const finish = useCourse((s) => s.finish)
  const close = useCourse((s) => s.closeFinish)
  const replaying = useReplay((r) => r.active)
  React.useEffect(() => watchCourses(), [])
  const open = Boolean(finish && !replaying)
  React.useEffect(() => {
    if (!open) return
    return registerDismisser(() => {
      close()
      return true
    })
  }, [open, close])
  if (!finish || replaying) return null
  const cumulative: number[] = []
  finish.splits.reduce((acc, s) => {
    const n = acc + s
    cumulative.push(n)
    return n
  }, 0)
  const again = () => {
    close()
    void toggleCourseOn(finish.vehicleId, finish.courseId)
  }
  return (
    <div className="l3 course-card" data-enter="" key={finish.at} role="dialog" aria-label={t('course.complete')} data-course-finish="">
      <div className="flex items-center justify-between gap-2">
        <span className="text-hud-title font-[560] text-[var(--g100)]">{t('course.complete')}</span>
        {finish.newBest ? (
          <span className="course-best">
            <Icon icon="check" className="mr-1 size-3" />
            {t('course.newBest')}
          </span>
        ) : null}
      </div>
      <div className="flex flex-col gap-0.5">
        <span className="course-card-total">{courseTime(finish.total) ?? '—'}</span>
        <span className="text-hud-sub text-[var(--g400)]">
          {lang === 'zh' ? finish.nameZh : finish.nameEn} · {getVehicle(finish.vehicleId)?.label ?? finish.label}
        </span>
      </div>
      <table className="w-full text-[11px]" data-lf-table="">
        <thead>
          <tr data-slot="table-head">
            <th className="cap py-1 text-left font-[560]">{t('course.colRing')}</th>
            <th className="cap py-1 text-right font-[560]">{t('course.colSplit')}</th>
            <th className="cap py-1 text-right font-[560]">{t('course.colElapsed')}</th>
          </tr>
        </thead>
        <tbody>
          {finish.splits.map((s, i) => (
            <tr key={i} data-slot="table-row">
              <td className="py-1 text-[var(--g300)]">{t('course.ringN', { n: i + 1 })}</td>
              <td className="num py-1 text-right text-[var(--g100)]" data-num="">{splitText(s)}</td>
              <td className="num py-1 text-right text-[var(--g300)]" data-num="">{courseTime(cumulative[i])}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex justify-end gap-2">
        <Button variant="outline" size="lg" onClick={again}>
          <Icon icon="reset" />
          {t('course.flyAgain')}
        </Button>
        <Button variant="secondary" size="lg" onClick={close}>
          {t('action.done')}
        </Button>
      </div>
    </div>
  )
}
