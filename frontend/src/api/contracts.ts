import { validWorking } from './working'
/** M1-03a OpenAPI: RequirementsInput, CreateTripRequest, IntakeView, TripView. */
export type Answer<T> = { state: 'unanswered' | 'no_preference'; value: null } | { state: 'provided'; value: T }
export type TravelDates = { mode: 'fixed'; start_date: string; end_date: string } | { mode: 'window'; earliest_start: string; latest_end: string }
export const fieldKeys = ['origin', 'destination', 'dates', 'days', 'travelers', 'target_budget_cents', 'hard_limit_cents', 'transport', 'hotel', 'restrictions'] as const
export type FieldKey = typeof fieldKeys[number]
export interface RequirementsInput {
  origin: Answer<string>; destination: Answer<string>; dates: Answer<TravelDates>
  days: Answer<number>; travelers: Answer<1 | 2>; target_budget_cents: Answer<number>; hard_limit_cents: Answer<number>
  transport: Answer<string>; hotel: Answer<string>; restrictions: Answer<string>; raw_text: string | null
}
export interface CreateTripRequest { requirements: RequirementsInput }
export interface TripView {
  trip_id: string; created_at: string
  intake: { intake_revision: number; input: RequirementsInput; collection_status: 'COLLECTING' | 'READY_TO_GENERATE'
    blocking_fields: FieldKey[]; provenance: { field: FieldKey; source: 'user' }[]
    suggested_values: { field: FieldKey; text: string; source: 'suggestion' }[] }
  working: WorkingDraft | null; latest_confirmed_version_id: string | null; synchronization: 'NO_DRAFT' | 'CURRENT' | 'STALE'; plan_confirmation: 'UNCONFIRMED' | 'CONFIRMED'
  active_run_id: string | null; readiness: 'NOT_ASSESSED'; booked: false
}
export const labels: Record<FieldKey, string> = { origin: '出发地', destination: '目的地', dates: '出行日期', days: '旅行天数', travelers: '人数', target_budget_cents: '目标预算', hard_limit_cents: '硬预算上限', transport: '交通偏好', hotel: '酒店偏好', restrictions: '特殊限制' }
export const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
export function object(v: unknown): v is Record<string, unknown> { return typeof v === 'object' && v !== null && !Array.isArray(v) }
function exact(v: Record<string, unknown>, keys: readonly string[]) { return Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v, k)) }
export function validDate(v: unknown): v is string {
  return typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v) && Number(v.slice(0, 4)) >= 1 && !Number.isNaN(Date.parse(v)) && new Date(v).toISOString().slice(0, 10) === v
}
export function validRequirements(v: unknown): v is RequirementsInput {
  if (!object(v) || !exact(v, [...fieldKeys, 'raw_text'])) return false
  if (v.raw_text !== null && !(typeof v.raw_text === 'string' && v.raw_text.trim() && v.raw_text.length <= 2000)) return false
  return fieldKeys.every(k => {
    const a = v[k]
    if (!object(a) || !exact(a, ['state', 'value'])) return false
    if (a.state === 'unanswered' || a.state === 'no_preference') return a.value === null
    if (a.state !== 'provided') return false
    const x = a.value
    if (k === 'dates') {
      if (!object(x)) return false
      const keys = x.mode === 'fixed' ? ['mode', 'start_date', 'end_date'] : x.mode === 'window' ? ['mode', 'earliest_start', 'latest_end'] : []
      const start = x[keys[1] ?? ''], end = x[keys[2] ?? '']
      return keys.length === 3 && exact(x, keys) && validDate(start) && validDate(end) && start <= end
    }
    if (k === 'days' || k === 'travelers' || k.endsWith('_cents')) return typeof x === 'number' && Number.isInteger(x) && x >= (k.endsWith('_cents') ? 0 : 1) && x <= (k === 'days' ? 5 : k === 'travelers' ? 2 : 1e12)
    return typeof x === 'string' && x.trim().length > 0 && x.length <= 500
  })
}
export function validTrip(v: unknown): v is TripView {
  if (!object(v) || !exact(v, ['trip_id', 'created_at', 'intake', 'working', 'latest_confirmed_version_id', 'synchronization', 'plan_confirmation', 'active_run_id', 'readiness', 'booked'])) return false
  const i = v.intake
  const field = (x: unknown) => typeof x === 'string' && (fieldKeys as readonly string[]).includes(x)
  return validId(v.trip_id) && typeof v.created_at === 'string' && /^\d{4}-.*T.*(?:Z|[+-]\d{2}:\d{2})$/.test(v.created_at) && !Number.isNaN(Date.parse(v.created_at))
    && object(i) && exact(i, ['intake_revision', 'input', 'collection_status', 'blocking_fields', 'provenance', 'suggested_values'])
    && typeof i.intake_revision === 'number' && Number.isInteger(i.intake_revision) && i.intake_revision >= 1 && validRequirements(i.input)
    && ['COLLECTING', 'READY_TO_GENERATE'].includes(String(i.collection_status)) && Array.isArray(i.blocking_fields) && i.blocking_fields.every(field)
    && Array.isArray(i.provenance) && i.provenance.every(x => object(x) && exact(x, ['field', 'source']) && field(x.field) && x.source === 'user')
    && Array.isArray(i.suggested_values) && i.suggested_values.every(x => object(x) && exact(x, ['field', 'text', 'source']) && field(x.field) && typeof x.text === 'string' && x.text.trim().length > 0 && x.text.length <= 500 && x.source === 'suggestion')
    && (v.working === null ? v.synchronization === 'NO_DRAFT' && v.latest_confirmed_version_id === null : validWorking(v.working) && v.working.trip_id === v.trip_id && (v.synchronization === 'STALE' || (v.synchronization === 'CURRENT' && object(v.working.context) && v.working.context.intake_revision === i.intake_revision)) && v.working.latest_confirmed_version_id === v.latest_confirmed_version_id) && (v.latest_confirmed_version_id === null || validId(v.latest_confirmed_version_id)) && (v.active_run_id === null || validId(v.active_run_id)) && (v.plan_confirmation === 'UNCONFIRMED' || (v.plan_confirmation === 'CONFIRMED' && v.synchronization === 'CURRENT' && v.latest_confirmed_version_id !== null)) && v.readiness === 'NOT_ASSESSED' && v.booked === false
}

export interface WorkingDraft { context?: unknown; trip_id: string; owner_id: string; revision: number; requirements: Record<string, unknown>; payload: Record<string, unknown>; item_confirmations: unknown[]; latest_confirmed_version_id: string | null }
export interface UpdateRequirementsRequest { expected_intake_revision: number; expected_working_revision: number | null; requirements: RequirementsInput }
export interface AnswerRequest { expected_intake_revision: number; expected_working_revision: number | null; field_key: FieldKey; answer: RequirementsInput[FieldKey] }
export interface ClarificationQuestion { question_id: string; intake_revision: number; field_key: FieldKey; prompt: string; allow_no_preference: boolean }
export interface NextQuestionResponse { intake_revision: number; question: ClarificationQuestion | null; blocking_fields: FieldKey[] }
export function validId(v: unknown): v is string { return typeof v === 'string' && /^[A-Za-z0-9_-]{1,80}$/.test(v) }
export function validNext(v: unknown): v is NextQuestionResponse {
  if (!object(v) || !exact(v, ['intake_revision', 'question', 'blocking_fields']) || !Number.isInteger(v.intake_revision) || Number(v.intake_revision) < 1 || !Array.isArray(v.blocking_fields) || !v.blocking_fields.every(k => fieldKeys.includes(k))) return false
  const q = v.question
  return q === null || (object(q) && exact(q, ['question_id', 'intake_revision', 'field_key', 'prompt', 'allow_no_preference']) && validId(q.question_id) && q.intake_revision === v.intake_revision && fieldKeys.includes(q.field_key as FieldKey) && v.blocking_fields.includes(q.field_key) && typeof q.prompt === 'string' && q.prompt.trim().length > 0 && q.prompt.length <= 500 && typeof q.allow_no_preference === 'boolean')
}
