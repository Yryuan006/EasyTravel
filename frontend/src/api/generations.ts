import { request, TripApiError, type RequestOptions } from './trips'
import { validId } from './contracts'
import { validSchema } from './working'
export interface BudgetResult { known_subtotal_cents: number; total_cents: number | null; verification: 'VERIFIED' | 'ESTIMATED' | 'INDETERMINATE'; constraint: 'WITHIN_LIMIT' | 'OVER_LIMIT' | 'UNDETERMINED'; unknown_cost_ids: string[]; verified_cost_ids: string[]; evidence_issues: [string, string][] }
export interface GenerationRun { kind: 'INITIAL_PLAN' | 'CHANGE_INTENT'; base_working_revision: number | null; run_id: string; trip_id: string; intake_revision: number; status: 'ACCEPTED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'; attempt: number; updated_at: string; result: { working_revision: number | null; proposal_id: string | null } | null; error: { code: string; message: string; retryable: boolean } | null; diagnostics_budget: BudgetResult | null }
export interface GenerationState { trip_id: string; working_revision: number | null; source_run: GenerationRun | null; latest_run: GenerationRun | null }
function path(id: string) { if (!validId(id)) throw new TripApiError('contract', '旅行 ID 无效。'); return `/api/v1/trips/${id}` }
export function generate(token: string, id: string, command: { key: string; body: string }, options: RequestOptions = {}) { return request(path(id) + '/generations', token, command, options, 'POST', (v): v is GenerationRun => validRun(v), 202, id) }
export function getRun(token: string, id: string, runId: string, options: RequestOptions = {}) {
  if (!validId(runId)) throw new TripApiError('contract', '运行 ID 无效。')
  return request(path(id) + '/generations/' + runId, token, undefined, options, 'GET', (v): v is GenerationRun => validRun(v) && (v as GenerationRun).run_id === runId, 200, id)
}
export function getGenerationState(token: string, id: string, options: RequestOptions = {}) { return request(path(id) + '/generation-state', token, undefined, options, 'GET', (v): v is GenerationState => validState(v), 200, id) }
export const money = (cents: number | null) => cents === null ? '未知' : `¥${(cents / 100).toFixed(2)}`
export const canRetry = (r: GenerationRun) => r.error?.retryable && ['invalid_output', 'worker_interrupted', 'persistence_failed'].includes(r.error.code)
export const failureText: Record<string, string> = {
  invalid_output: '模拟输出未通过结构校验，未保存行程。可以显式重试。',
  known_costs_exceed_hard_limit: '已知费用超过硬预算上限，未保存行程。请修改需求或预算后再生成。',
  stale_input: '生成期间需求已改变，旧结果未应用。请读取并审阅最新需求。',
  worker_interrupted: '执行者已中断，未完成本次生成。可以显式重试。',
  persistence_failed: '保存失败，未提交半份行程。服务恢复后可以显式重试。',
}
export interface PlanItem { id: string; resource_id: string; kind: 'activity' | 'hotel'; title: string; start: string; end: string; check_in: string | null; check_out: string | null; room_type: string | null; rooms: number | null }
export interface CostLine { id: string; item_id: string; category: 'quote' | 'estimate' | 'unknown'; amount_cents: number | null; is_mock: boolean; unknown_reason: string | null }
export interface Notes { risks: { code: string; severity: string; message: string; item_ids: string[] }[]; transport_advice: { from_item_id: string; to_item_id: string; mode: string; text: string; verification: 'unverified' }[] }
export function shanghaiDate(value: string) { return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value)) }
export function shanghaiTime(value: string) { return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) }

export function validRun(v: unknown): v is GenerationRun {
  if (!validSchema(v, 'GenerationRun')) return false
  const r = v as GenerationRun
  return r.status === 'SUCCEEDED' ? r.result !== null && r.error === null && ((r.result.working_revision !== null && r.result.proposal_id === null) || (r.result.working_revision === null && r.result.proposal_id !== null && r.kind === 'CHANGE_INTENT'))
    : r.status === 'FAILED' ? r.error !== null && r.result === null : r.result === null && r.error === null
}
export function validState(v: unknown): v is GenerationState {
  if (!validSchema(v, 'GenerationState')) return false
  const s = v as GenerationState
  return (!s.latest_run || validRun(s.latest_run) && s.latest_run.trip_id === s.trip_id)
    && (!s.source_run || validRun(s.source_run) && s.source_run.trip_id === s.trip_id && s.source_run.status === 'SUCCEEDED' && (s.source_run.result?.working_revision === s.working_revision || s.source_run.kind === 'CHANGE_INTENT' && (s.source_run.base_working_revision ?? 0) + 1 === s.working_revision))
}
