import { request, TripApiError, type RequestOptions } from './trips'
import { validId } from './contracts'
import { validSchema } from './working'
import type { ConfirmationPreview, ConfirmRequest, ConfirmResult } from './confirmation-contracts'
export type { ConfirmationPreview, ConfirmRequest, ConfirmResult } from './confirmation-contracts'

export function confirmationPath(id: string) {
  if (!validId(id)) throw new TripApiError('contract', '旅行 ID 无效。')
  return `/api/v1/trips/${id}/confirmations`
}
export function validPreview(v: unknown): v is ConfirmationPreview {
  if (!validSchema(v, 'ConfirmationPreview')) return false
  const p = v as ConfirmationPreview
  return p.confirmation_allowed === (p.blocking_reasons.length === 0)
    && (!p.confirmation_allowed || p.budget.constraint !== 'OVER_LIMIT' && !p.risks.some(r => r.severity === 'blocker'))
    && p.unknown_costs.length === p.budget.unknown_cost_ids.length
    && p.unknown_costs.every(c => p.budget.unknown_cost_ids.includes(c.id))
}
export function validResult(v: unknown): v is ConfirmResult {
  if (!validSchema(v, 'ConfirmResult')) return false
  const r = v as ConfirmResult, s = r.snapshot
  return s.context !== null && s.risk_disclosure !== null
    && s.snapshot.budget.constraint !== 'OVER_LIMIT'
    && !(s.snapshot.payload.costs.some(cost => cost.is_mock) && s.snapshot.budget.verification === 'VERIFIED')
    && !s.risk_disclosure.risks.some(risk => risk.severity === 'blocker')
}
export function getConfirmationPreview(token: string, id: string, options: RequestOptions = {}) {
  return request(confirmationPath(id).replace(/confirmations$/, 'confirmation-preview'), token, undefined, options, 'GET', validPreview, 200, id)
}
export interface ConfirmationCommand { path: string; key: string; body: string }
export function confirmTrip(token: string, id: string, command: ConfirmationCommand, options: RequestOptions = {}) {
  if (command.path !== confirmationPath(id)) throw new TripApiError('contract', '确认请求路径与旅行不一致。')
  const b = JSON.parse(command.body) as ConfirmRequest
  return request(command.path, token, command, options, 'POST', (v): v is ConfirmResult => {
    if (!validResult(v)) return false
    const s = v.snapshot
    return s.snapshot.trip_id === id && s.snapshot.source_working_revision === b.expected_working_revision
      && s.context?.intake_revision === b.expected_intake_revision && s.snapshot.disclosure_hash === b.seen_disclosure_hash
      && s.risk_disclosure?.risk_hash === b.seen_risk_hash
  }, 201)
}

export const blockingText: Record<string, string> = {
  stale_intake: '需求与工作稿不同步。请先审阅新需求并完成重规划，再读取当前行程的预算与风险；不能确认旧稿。',
  stale_revision: '工作稿已变化。请重新读取行程和预览，再次审阅。',
  draft_not_ready: '草案或来源上下文不完整，暂不能确认。请核对生成结果。',
  blocking_risks: '存在必须解决的 blocker 风险，不能以整体确认豁免。',
  known_costs_exceed_hard_limit: '已知费用超过硬预算上限。请调整需求；不能通过勾选豁免超支。',
  pending_requirements_budget_unavailable: '最新需求的预算尚不能与旧稿比较，请核对日期和明确硬上限。',
  disclosure_not_acknowledged: '预算披露已变化。请重新读取预览并审阅，原勾选已清除。',
  risk_not_acknowledged: '风险披露已变化。请重新读取预览并审阅，原勾选已清除。',
  snapshot_disclosure_conflict: '同一源版本已有不同披露或缺少历史风险记录，不能补造已阅读事实。请核对版本。',
}
