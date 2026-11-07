// Ring course group of the Control tab (02 §10.1). The stage engineer (WP6) owns the course UI: when
// src/course/CourseControls.tsx exists and exports CourseControls({ vehicleId }), it is mounted here unchanged.
// Until then a minimal group with the same behaviour stands in: course select, Start / Stop, best time, and the
// honest "needs a local position estimate" state.
import * as React from 'react'
import { api } from '@/api'
import { pickText, useLang, useT } from '@/i18n'
import { courseTime } from '@/lib/format'
import { notifyError } from '@/shell/notify'
import { useVehicle } from '@/store/selectors'
import { Button } from '@/ui/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/ui/components/ui/select'
import { Icon } from '@/ui/icons'
import { Group } from '@/ui/relay'
import { DEFAULT_COURSE, rememberCourse } from './actions'
import { useCT } from './strings'

type CourseControlsComponent = React.ComponentType<{ vehicleId: string }>

// Optional module of WP6; an empty object when the file does not exist.
const external = import.meta.glob<{ CourseControls?: CourseControlsComponent }>('../course/CourseControls.tsx', { eager: true })
const WP6Controls: CourseControlsComponent | null = Object.values(external)[0]?.CourseControls ?? null

interface CourseInfo {
  id: string
  name_en: string
  name_zh: string
  rings: unknown[]
}

let coursesCache: CourseInfo[] | null = null

function useCourses(): CourseInfo[] | null {
  const [list, setList] = React.useState<CourseInfo[] | null>(coursesCache)
  React.useEffect(() => {
    let alive = true
    api
      .courses()
      .then((res) => {
        const items = ((res as { courses?: CourseInfo[] })?.courses ?? []).filter((c) => c && typeof c.id === 'string')
        coursesCache = items
        if (alive) setList(items)
      })
      .catch(() => alive && setList((l) => l ?? []))
    return () => {
      alive = false
    }
  }, [])
  return list
}

function FallbackCourse({ vehicleId }: { vehicleId: string }) {
  const t = useT()
  const ct = useCT()
  const lang = useLang()
  const v = useVehicle(vehicleId)
  const courses = useCourses()
  const [choice, setChoice] = React.useState<string>(v?.course?.course_id ?? DEFAULT_COURSE)
  const [busy, setBusy] = React.useState(false)
  if (!v) return null
  const c = v.course
  const active = Boolean(c && (c.state === 'waiting_takeoff' || c.state === 'running'))
  const canFly = v.capabilities.position
  const label = (x: CourseInfo) => {
    const n = x.rings.length
    if (x.id === 'cage-loop') return t('course.cageLoop', { n })
    if (x.id === 'slalom') return t('course.slalom', { n })
    if (x.id === 'climb') return t('course.climb', { n })
    return `${pickText(lang, x.name_en, x.name_zh)} · ${n}`
  }
  const items = (courses ?? []).map((x) => ({ value: x.id, label: label(x) }))
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
    } catch (e) {
      notifyError(e)
    } finally {
      setBusy(false)
    }
  }
  const state =
    c?.state === 'running' ? ct('rc.state.running', { n: Math.min(c.next + 1, c.total), total: c.total }) : c ? ct(`rc.state.${c.state}`) : null
  return (
    <Group label={t('course.group')} right={c?.best_s ? <span className="num text-hud-sub text-[var(--g400)]">{t('course.best', { t: courseTime(c.best_s) ?? '' })}</span> : null}>
      <div className="flex flex-col gap-2 px-3">
        {!canFly ? (
          <p className="text-hud-sub text-[var(--g400)]">{t('course.needsPosition')}</p>
        ) : null}
        <div className="flex items-center gap-2">
          <Select items={items} value={choice} onValueChange={(x) => x && setChoice(x as string)} disabled={active || !canFly}>
            <SelectTrigger size="sm" className="h-7 min-w-0 flex-1" aria-label={ct('rc.select')}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {items.map((it) => (
                <SelectItem key={it.value} value={it.value}>
                  {it.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {active ? (
            <Button variant="outline" disabled={busy} onClick={() => run(() => api.courseStop(vehicleId))}>
              {ct('rc.stop')}
            </Button>
          ) : (
            <Button
              variant="outline"
              disabled={busy || !canFly}
              onClick={() =>
                run(() => {
                  rememberCourse(vehicleId, choice)
                  return api.courseStart(vehicleId, choice)
                })
              }
            >
              <Icon icon="course" />
              {t('action.start')}
            </Button>
          )}
        </div>
        {state ? (
          <p className="num text-hud-sub text-[var(--g400)]" data-course-state={c?.state}>
            {state}
            {c && c.state !== 'waiting_takeoff' ? ` · ${courseTime(c.elapsed_s) ?? ''}` : ''}
          </p>
        ) : null}
      </div>
    </Group>
  )
}

export function CourseGroup({ vehicleId }: { vehicleId: string }) {
  if (WP6Controls) return <WP6Controls vehicleId={vehicleId} />
  return <FallbackCourse vehicleId={vehicleId} />
}
