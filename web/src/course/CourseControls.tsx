// "Ring course" group of the inspector's Control tab (02 §10.1), mounted by WP7's ControlTab: course select
// ("Cage loop · 4 rings" ...), Start / Stop, best time, the run's progress, and the honest reason when the
// aircraft reports no local position. Also CourseBrief, the one-line progress for fleet cards (02 §10.4).
// Contract: CONTRACTS.md §6.
import * as React from 'react'
import { useLang } from '@/i18n'
import { courseTime } from '@/lib/format'
import { backendNow, useRelay } from '@/store/relay'
import { findVehicle, useVehicleField } from '@/store/selectors'
import { Button } from '@/ui/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/ui/components/ui/select'
import { Icon } from '@/ui/icons'
import { Group } from '@/ui/relay'
import { t6, useT6 } from '@/stage/copy'
import { toggleCourseOn } from './CourseHud'
import { isActive, liveElapsed, presetKey, type CourseDef } from './geometry'
import { useCourse, watchCourses } from './store'

/** "Cage loop · 4 rings" for presets, "<name> · 4 rings" for custom courses. */
export function courseOptionLabel(def: CourseDef, lang: 'en' | 'zh'): string {
  const key = presetKey(def.id)
  if (key) return t6(key, { n: def.rings.length })
  return `${lang === 'zh' ? def.name_zh : def.name_en} · ${t6('course.ringCount', { n: def.rings.length })}`
}

/** One-line run text: "Ring 2/4 · 00:07.1"; null when the aircraft has no active run. */
export function courseBriefText(vehicleId: string): string | null {
  const s = useRelay.getState()
  const c = findVehicle(s.fleet?.vehicles, vehicleId)?.course
  if (!c || !s.fleet || !isActive(c)) return null
  if (c.state === 'waiting_takeoff') return t6('course.briefWaiting')
  const el = liveElapsed(c, s.fleet.t, backendNow())
  const tt = courseTime(el)
  return t6('course.brief', { n: Math.min(c.total, c.next + 1), total: c.total, t: tt ? tt.replace(/(\.\d)\d$/, '$1') : '—' })
}

/**
 * Live "Ring 2/4 · 00:07.1" for a fleet card's third row (02 §10.4: other aircraft in a course). Renders nothing
 * without an active run; the text is refreshed ten times a second without re-rendering the card.
 */
export function CourseBrief({ vehicleId, className }: { vehicleId: string; className?: string }) {
  const active = useVehicleField(vehicleId, (v) => isActive(v.course)) ?? false
  const ref = React.useRef<HTMLSpanElement>(null)
  React.useEffect(() => {
    if (!active) return
    const write = () => {
      const text = courseBriefText(vehicleId) ?? ''
      if (ref.current && ref.current.textContent !== text) ref.current.textContent = text
    }
    write()
    const tm = setInterval(write, 100)
    return () => clearInterval(tm)
  }, [active, vehicleId])
  if (!active) return null
  return <span ref={ref} className={className ?? 'num text-[11px] text-[var(--g300)]'} data-course-brief="" />
}

export function CourseControls({ vehicleId }: { vehicleId: string }) {
  const t = useT6()
  const lang = useLang()
  const defs = useCourse((s) => s.defs)
  const loading = useCourse((s) => s.loading)
  const chosen = useCourse((s) => s.chosen)
  const choose = useCourse((s) => s.choose)
  const bestSeen = useCourse((s) => s.best)
  const facts = useRelay((s) => {
    const v = findVehicle(s.fleet?.vehicles, vehicleId)
    if (!v) return ''
    return JSON.stringify({
      position: v.capabilities.position,
      active: isActive(v.course),
      course: v.course?.course_id ?? null,
      best: v.course?.best_s ?? null,
      agent: v.controller === 'agent' && isActive(v.course) ? (v.claim?.name ?? null) : null,
    })
  })
  const [busy, setBusy] = React.useState(false)

  React.useEffect(() => watchCourses(), [])
  React.useEffect(() => {
    void useCourse.getState().load()
  }, [])

  if (!facts) return null
  const f = JSON.parse(facts) as { position: boolean; active: boolean; course: string | null; best: number | null; agent: string | null }
  const selectedId = f.active && f.course ? f.course : chosen
  const list = defs ?? []
  const current = list.find((d) => d.id === selectedId) ?? list[0]
  const best = (f.course === current?.id ? f.best : null) ?? (current ? bestSeen[current.id] : undefined) ?? null
  const bestText = courseTime(best)

  const run = async () => {
    setBusy(true)
    try {
      await toggleCourseOn(vehicleId, current?.id)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Group label={t('course.group')} className="pb-1" right={bestText ? <span className="num text-[11px] text-[var(--g300)]">{t('course.best', { t: bestText })}</span> : null}>
      <div className="flex flex-col gap-2 px-3" data-course-controls="">
        {!f.position ? (
          <p className="text-hud-sub text-[var(--g400)]">{t('course.needsPosition')}</p>
        ) : null}
        <div className="flex items-center gap-2">
          <Select
            value={current?.id ?? ''}
            onValueChange={(v: unknown) => typeof v === 'string' && choose(v)}
            disabled={f.active || !list.length}
            items={list.map((d) => ({ value: d.id, label: courseOptionLabel(d, lang) }))}
          >
            <SelectTrigger className="h-8 min-w-0 flex-1" aria-label={t('course.group')}>
              <SelectValue placeholder={loading ? t('course.loading') : t('course.noCourses')} />
            </SelectTrigger>
            <SelectContent>
              {list.map((d) => (
                <SelectItem key={d.id} value={d.id}>
                  {courseOptionLabel(d, lang)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant={f.active ? 'outline' : 'secondary'}
            size="lg"
            className="w-20"
            disabled={busy || (!f.active && (!f.position || !current))}
            onClick={() => void run()}
          >
            <Icon icon={f.active ? 'square' : 'course'} />
            {f.active ? t('action.stop') : t('action.start')}
          </Button>
        </div>
        {f.active ? <CourseBrief vehicleId={vehicleId} className="num text-[11px] text-[var(--g300)]" /> : null}
        {f.agent ? <p className="text-hud-sub text-[var(--g400)]">{t('course.byAgent', { agent: f.agent })}</p> : null}
      </div>
    </Group>
  )
}
