import { afterEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { ref, shallowRef } from 'vue'
import persisted from '../../Docs/M1-04a_独立进程读取.json'
import type { TripView } from '../src/api/contracts'
import { useChanges } from '../src/composables/useChanges'
import { getProposal, validProposal, type ProposalView } from '../src/api/changes'
import { validRun, validState } from '../src/api/generations'
import ChangeReview from '../src/components/ChangeReview.vue'

const trip = persisted.trip as unknown as TripView
const w = trip.working!, payload = w.payload as any
const target = payload.items.find((i: any) => i.kind === 'activity')
const budget = { known_subtotal_cents: 60000, total_cents: null, verification: 'INDETERMINATE', constraint: 'UNDETERMINED', unknown_cost_ids: ['unknown'], verified_cost_ids: [], evidence_issues: [] }
const proposal = { proposal: { proposal_id: 'proposal-1', trip_id: trip.trip_id, owner_id: w.owner_id, requirements_revision: 1,
  output: { base_working_revision: 1, explanation: 'Mock 修改', operations: [{ operation: 'delete_item', target_item_id: target.id }] },
  candidate: payload, level: 'L2', touched_item_ids: [target.id], candidate_hash: 'a'.repeat(64) }, context_v2: null,
  candidate_notes: { risks: [{ code: 'mock', severity: 'warning', message: 'Mock 未核验', item_ids: [] }], transport_advice: [] },
  state: 'PENDING', budget, source_budget: budget, risks: [], requires_specific_authorization: false, intake_revision: 1,
  differences: [{ item_id: target.id, before: target, after: null, before_costs: payload.costs.filter((c: any) => c.item_id === target.id), after_costs: [] }],
  unknown_costs: [{ id: 'unknown', reason: 'Mock 未查询报价' }] } as unknown as ProposalView
const run = { run_id: 'run-change', trip_id: trip.trip_id, kind: 'CHANGE_INTENT', intake_revision: 1, base_working_revision: 1,
  status: 'SUCCEEDED', attempt: 1, retry_of: null, created_at: '2026-11-14T00:00:00Z', updated_at: '2026-11-14T00:00:00Z', result: { working_revision: null, proposal_id: 'proposal-1' }, error: null, diagnostics_budget: budget }
const result = { proposal_id: 'proposal-1', decision: 'approve', state: 'APPLIED', operation_id: 'operation-1', working_revision: 2, latest_confirmed_version_id: null }
const updated = { ...trip, working: { ...w, revision: 2 }, plan_confirmation: 'UNCONFIRMED' } as TripView
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const all: ReturnType<typeof mount>[] = []
function harness() {
  const saved = shallowRef(structuredClone(trip)), token = ref('private-token'), blocked = ref(false)
  let session!: ReturnType<typeof useChanges>
  const component = mount({ components: { ChangeReview }, setup() { session = useChanges(saved, () => token.value, () => blocked.value); return { saved, session, blocked } }, template: '<ChangeReview :trip="saved" :session="session" :blocked="blocked" />' })
  all.push(component)
  return { component, saved, token, blocked, get session() { return session } }
}
function mock() {
  const f = vi.fn<typeof fetch>().mockImplementation(async (path) => {
    if (String(path).endsWith('/change-intents')) return response(run, 202)
    if (String(path).includes('/generations/')) return response(run)
    if (String(path).endsWith('/decisions')) return response(result)
    if (String(path).includes('/proposals/')) return response(proposal)
    return response(updated)
  })
  vi.stubGlobal('fetch', f); return f
}
afterEach(() => { all.splice(0).forEach(c => c.unmount()); vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('accepts proposal result without treating it as an attached draft and requires manual approval', async () => {
  const f = mock(), h = harness(); expect(validRun(run)).toBe(true); expect(validProposal(proposal)).toBe(true)
  h.session.intent.value = '第二天安排轻松一点'; await h.session.start(); await flushPromises()
  expect(h.session.proposal.value).toEqual(proposal); expect(h.component.text()).toContain('未知费用（1 项）')
  await h.session.decide('approve'); expect(f.mock.calls.filter(c => String(c[0]).endsWith('/decisions'))).toHaveLength(0)
  h.session.agreed.value = true
  f.mockImplementationOnce(async () => response(result)).mockImplementationOnce(async () => response(updated)).mockImplementationOnce(async () => response({ ...proposal, state: 'APPLIED' }))
  await h.session.decide('approve'); await flushPromises()
  expect(h.saved.value.working?.revision).toBe(2); expect(h.session.verified.value).toBe(true)
  expect(h.component.text()).toContain('修改已保存并核对：工作稿 r2')
  const post = f.mock.calls.find(c => String(c[0]).endsWith('/decisions'))!
  expect(JSON.parse(post[1]!.body as string)).toMatchObject({ item_ids: [target.id], explicit_authorization: false, seen_candidate_hash: 'a'.repeat(64) })
})

it('uses exact server L3 scope only after explicit checkbox and reject omits authorization fields', async () => {
  mock(); const h = harness(); h.session.proposal.value = { ...proposal, proposal: { ...proposal.proposal, level: 'L3', touched_item_ids: ['hotel', target.id] }, requires_specific_authorization: true }
  expect(h.session.agreed.value).toBe(false); await h.session.decide('approve'); expect(h.session.pending.value).toBeUndefined()
  const f = vi.fn<typeof fetch>().mockRejectedValue(new Error('network')); vi.stubGlobal('fetch', f)
  h.session.agreed.value = true; await h.session.decide('approve')
  expect(JSON.parse(h.session.pending.value!.body)).toMatchObject({ item_ids: ['hotel', target.id], explicit_authorization: true })
  const h2 = harness(); h2.session.proposal.value = proposal; await h2.session.decide('reject')
  expect(JSON.parse(h2.session.pending.value!.body)).not.toHaveProperty('item_ids')
  expect(JSON.parse(h2.session.pending.value!.body)).not.toHaveProperty('explicit_authorization')
})

it('retains path, key and body on uncertain write and retries exactly once with the same command', async () => {
  const f = vi.fn<typeof fetch>().mockRejectedValue(new Error('network')); vi.stubGlobal('fetch', f)
  const h = harness(); h.session.intent.value = '删除活动 activity-0'; await h.session.start()
  const pending = structuredClone(h.session.pending.value)
  expect(h.session.locked.value).toBe(true); h.session.intent.value = 'different'; await h.session.start()
  expect(f).toHaveBeenCalledTimes(1)
  f.mockResolvedValueOnce(response(run,202)).mockResolvedValueOnce(response(run)).mockResolvedValueOnce(response(proposal))
  await h.session.retry(); expect(h.session.pending.value).toBeUndefined()
  expect(f.mock.calls[1]![0]).toBe(pending!.path)
  expect(f.mock.calls[1]![1]!.body).toBe(pending!.body)
  expect(f.mock.calls[1]![1]!.headers).toEqual(f.mock.calls[0]![1]!.headers)
})

it('keeps decision receipt and read-only recovery after POST 200 then GET failure', async () => {
  const f = mock(), h = harness(); h.session.proposal.value = proposal; h.session.agreed.value = true
  f.mockResolvedValueOnce(response(result)).mockResolvedValueOnce(response({ error: { code: 'business_dependency_unavailable' } },503))
  await h.session.decide('approve'); expect(h.session.receipt.value).toEqual(result); expect(h.session.awaitingRead.value).toBe(true)
  await h.session.start(); await h.session.decide('reject'); expect(f.mock.calls.filter(c => c[1]?.method === 'POST')).toHaveLength(1)
  f.mockResolvedValueOnce(response(updated)).mockResolvedValueOnce(response({ ...proposal, state: 'APPLIED' }))
  await h.session.refresh(); expect(h.session.verified.value).toBe(true); expect(h.session.awaitingRead.value).toBe(false)
})

it('clears agreement on version change and never applies a stale proposal', async () => {
  const f = mock(), h = harness(); h.session.proposal.value = proposal; h.session.agreed.value = true
  h.saved.value = { ...trip, intake: { ...trip.intake, intake_revision: 2 }, synchronization: 'STALE' }
  expect(h.session.agreed.value).toBe(false); expect(h.session.canDecide.value).toBe(false)
  await h.session.decide('approve'); expect(f).not.toHaveBeenCalled()
})

it('clears credentials-related state and suppresses late responses after identity switch', async () => {
  let resolve!: (value: Response) => void
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => new Promise(r => { resolve = r })))
  const h = harness(); h.session.intent.value = '第二天安排轻松一点'; const started = h.session.start()
  h.token.value = 'other-private'; resolve(response(run,202)); await started
  expect(h.session.run.value).toBeUndefined(); expect(h.session.pending.value).toBeUndefined(); expect(h.session.proposal.value).toBeUndefined(); expect(h.session.intent.value).toBe('')
})

it('rejects malformed proposal and wrong parent binding', async () => {
  expect(validProposal({ ...proposal, requires_specific_authorization: true })).toBe(false)
  expect(validProposal({ ...proposal, budget: { ...budget, verification: 'VERIFIED' } })).toBe(false)
  await expect(getProposal('private', 'another-trip', 'proposal-1', { fetcher: vi.fn().mockResolvedValue(response(proposal)) })).rejects.toMatchObject({ kind: 'contract' })
})

it.each(['budget', 'blocker'])('blocks approval for %s while still allowing rejection', async mode => {
  const f = mock(), h = harness()
  h.session.proposal.value = { ...proposal, ...(mode === 'budget' ? { budget: { ...proposal.budget, constraint: 'OVER_LIMIT' as const } } : { risks: [{ code: 'unsafe', severity: 'blocker', message: '必须解决', item_ids: [] }] }) }
  h.session.agreed.value = true; await h.session.decide('approve'); expect(f).not.toHaveBeenCalled()
  expect(h.session.canDecide.value).toBe(true)
})

it('recognizes the current changed draft budget source without claiming the proposal itself attached a draft', () => {
  expect(validState({ trip_id: trip.trip_id, working_revision: 2, source_run: run, latest_run: run })).toBe(true)
  expect(validState({ trip_id: trip.trip_id, working_revision: 3, source_run: run, latest_run: run })).toBe(false)
})

it('does not restore a prior-user decision through a delayed post-response Trip read', async () => {
  let resolve!: (v: Response) => void
  const f = vi.fn<typeof fetch>().mockResolvedValueOnce(response(result)).mockImplementationOnce(() => new Promise(r => { resolve = r }))
  vi.stubGlobal('fetch', f); const h = harness(); h.session.proposal.value = proposal; h.session.agreed.value = true
  const deciding = h.session.decide('approve'); await flushPromises(); expect(h.session.receipt.value).toBeDefined()
  h.token.value = 'another-user'; resolve(response(updated)); await deciding
  expect(h.session.receipt.value).toBeUndefined(); expect(h.session.verified.value).toBe(false); expect(h.saved.value.working?.revision).toBe(1)
})
