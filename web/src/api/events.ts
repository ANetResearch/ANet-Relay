// Event aliases over the generated schema types. The schema exports `data` as an open object, which
// json-schema-to-typescript renders as `{}`; the renderer reads it as a string-keyed record.
import type { Event } from './types'

export type EventKind = Event['kind']
export type RelayEvent = Omit<Event, 'data'> & { data: Record<string, unknown> }
