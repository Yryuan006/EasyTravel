import { validId } from './contracts'
import { request, TripApiError, type RequestOptions } from './trips'
import { validSchema } from './working'
import { validRun, type BudgetResult, type GenerationRun, type Notes, type PlanItem, type CostLine } from './generations'

export interface ProposalView {
  proposal: { proposal_id: string; trip_id: string; owner_id: string; requirements_revision: number; candidate_hash: string; level: 'L1' | 'L2' | 'L3'; touched_item_ids: string[]; output: { base_working_revision: number; explanation: string }; candidate: { items: PlanItem[]; costs: CostLine[] } }
  context_v2: null; candidate_notes: Notes; state: 'PENDING' | 'APPLIED' | 'REJECTED' | 'STALE'; budget: BudgetResult; source_budget: BudgetResult
  risks: Notes['risks']; requires_specific_authorization: boolean; intake_revision: number
  differences: { item_id: string; before: PlanItem | null; after: PlanItem | null; before_costs: CostLine[]; after_costs: CostLine[] }[]
  unknown_costs: { id: string; reason: string }[]
}
export interface DecisionResult { proposal_id: string; decision: 'approve' | 'reject'; state: 'APPLIED' | 'REJECTED'; operation_id: string | null; working_revision: number; latest_confirmed_version_id: string | null }
export interface ChangeCommand { path: string; key: string; body: string; kind: 'intent' | 'decision' }
export function tripPath(id: string) { if (!validId(id)) throw new TripApiError('contract', '旅行 ID 无效。'); return `/api/v1/trips/${id}` }
export function validProposal(v: unknown): v is ProposalView {
  if (!validSchema(v, 'ProposalView')) return false
  const p = v as ProposalView
  return p.requires_specific_authorization === (p.proposal.level === 'L3') && p.context_v2 === null
    && p.proposal.touched_item_ids.length === new Set(p.proposal.touched_item_ids).size
    && !(p.proposal.candidate.costs.some(c => c.is_mock) && p.budget.verification === 'VERIFIED')
}
export function getProposal(token: string, trip: string, id: string, options: RequestOptions = {}) {
  if (!validId(id)) throw new TripApiError('contract', '提案 ID 无效。')
  return request(tripPath(trip) + '/proposals/' + id, token, undefined, options, 'GET', (v): v is ProposalView => validProposal(v) && v.proposal.trip_id === trip && v.proposal.proposal_id === id, 200)
}
export function sendChange(token: string, trip: string, command: ChangeCommand, options: RequestOptions = {}) {
  const base = tripPath(trip)
  if (command.kind === 'intent') {
    if (command.path !== base + '/change-intents') throw new TripApiError('contract', '原请求路径不一致。')
    return request(command.path, token, command, options, 'POST', (v): v is GenerationRun => validRun(v) && v.kind === 'CHANGE_INTENT', 202, trip)
  }
  const id = command.path.slice((base + '/proposals/').length).replace(/\/decisions$/, '')
  if (!validId(id) || command.path !== base + '/proposals/' + id + '/decisions') throw new TripApiError('contract', '原决定路径不一致。')
  return request(command.path, token, command, options, 'POST', (v): v is DecisionResult => {
    if (!validSchema(v, 'DecisionResult')) return false
    const r = v as DecisionResult, body = JSON.parse(command.body)
    return r.proposal_id === id && r.decision === body.decision && r.state === (r.decision === 'approve' ? 'APPLIED' : 'REJECTED')
  }, 200)
}
