// Polling of an evaluation job (06 §3.4: GET /jobs/{id}) while it is queued or running. The job lives in the Lab
// store, so leaving the tab or the workspace and coming back shows the same run; polling stops once it finished.
import * as React from 'react'
import { labApi, jobFinished, type JobView } from './api'

export const JOB_POLL_MS = 500

export function useJobPoll(job: JobView | null, set: (j: JobView) => void, onFinished?: (j: JobView) => void): void {
  const done = React.useRef(onFinished)
  React.useLayoutEffect(() => {
    done.current = onFinished
  })
  const id = job && !jobFinished(job) ? job.id : null
  React.useEffect(() => {
    if (!id) return
    let alive = true
    let timer: ReturnType<typeof setTimeout> | null = null
    const poll = async () => {
      try {
        const next = await labApi.job(id)
        if (!alive) return
        set(next.id ? next : { ...next, id })
        if (jobFinished(next)) {
          done.current?.(next)
          return
        }
      } catch {
        /* transient: keep polling */
      }
      if (alive) timer = setTimeout(poll, JOB_POLL_MS)
    }
    timer = setTimeout(poll, JOB_POLL_MS)
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [id, set])
}

export const percent = (p: number) => `${Math.round(p * 100)} %`
