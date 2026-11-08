// Public surface of the API layer. Import types and helpers from '@/api', never from the generated file.
export type { Fleet, Vehicle, Alert, Hello, Health, ErrorBody, FlightHealthView, HealthItem } from './types'
// bench procedures (WP-B)
export type { ProcedureCatalogue, ProcedureEntry, ProcedureRun, ProcedureStep, ProcedurePrompt, ProcedureMetric, ProcedureFinding, ProcedureSuggestion, ProcedureBrief, TestRuns, TestRunSummary } from './types'
export type { ProcedureOptions } from './rest'
export type { RelayEvent, EventKind } from './events'
export { PROCEDURE_EVENTS, isProcedureEvent } from './events'
// LAN arena (05 §7.5, WP-D)
export type { ArenaView } from './types'
export { api, request, setTransport, httpTransport, RelayApiError, toApiError } from './rest'
export type { RestTransport, Method, Auth, StickFrame, PossessInput, Ok, SkillName, PlanAction, PlanStep } from './rest'
export { env, readEnv, setEnv, normalizeApiBase, streamUrlFor } from './env'
export type { RelayEnv, RelayBridge, BackendState, BackendStateName, MenuAction, MenuEvent, FixtureName, CopilotApi, CopilotConfig, CopilotConfigInput, CopilotEvent, CopilotKeyStorage, CopilotToolScope, CopilotWindowAction, CopilotWindowState, RelayCopilotBridge, RelayAppBridge, AppWindowAction, AppWindowState, AppScreenshot } from './env'
export { RelayStream, parseMessage, backoffDelay, STICKS_MIN_INTERVAL_MS } from './ws'
export type { StreamState, StreamHandlers, SocketLike, SocketFactory, UiCommandMessage } from './ws'
export { stream, installStream, sendSticks, cancelSticks, sendJson } from './stream'
export type { StickSink } from './stream'
