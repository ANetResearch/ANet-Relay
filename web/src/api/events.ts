// Event aliases over the generated schema types. The schema exports `data` as an open object, which
// json-schema-to-typescript renders as `{}`; the renderer reads it as a string-keyed record.
import type { Event } from './types'

export type EventKind = Event['kind']
export type RelayEvent = Omit<Event, 'data'> & { data: Record<string, unknown> }

/** Bench procedure events (WP-B, 05 §4.4); the text comes from the backend templates (text_en / text_zh). */
export const PROCEDURE_EVENTS = ['procedure_started', 'procedure_step', 'procedure_waiting', 'procedure_finished', 'procedure_aborted'] as const satisfies readonly EventKind[]

export function isProcedureEvent(e: Pick<RelayEvent, 'kind'>): boolean {
  return (PROCEDURE_EVENTS as readonly string[]).includes(e.kind)
}
