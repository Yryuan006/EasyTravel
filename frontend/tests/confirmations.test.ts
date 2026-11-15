import { afterEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { ref, shallowRef } from 'vue'
import evidence from '../../Docs/M1-05a_PostgreSQL证据.json'
import persisted from '../../Docs/M1-04a_独立进程读取.json'
import { confirmTrip, confirmationPath, getConfirmationPreview, validPreview, validResult, type ConfirmationPreview } from '../src/api/confirmations'
import type { TripView } from '../src/api/contracts'
import { PREVIEW_TTL_MS, useConfirmation } from '../src/composables/useConfirmation'
import ConfirmationReview from '../src/components/ConfirmationReview.vue'

const preview = evidence.preview as unknown as ConfirmationPreview
const result = evidence.result
const s = result.snapshot.snapshot
const trip = { ...persisted.trip, trip_id: s.trip_id,
  intake: { ...persisted.trip.intake, intake_revision: 1, input: result.snapshot.context.input },
  working: { trip_id: s.trip_id, owner_id: s.confirmed_by, revision: s.source_working_revision,
    requirements: s.requirements, payload: s.payload, item_confirmations: s.item_confirmations, latest_confirmed_version_id: null, context: result.snapshot.context },
  latest_confirmed_version_id: null, synchronization: 'CURRENT', plan_confirmation: 'UNCONFIRMED',
} as unknown as TripView
const confirmed = { ...trip, working: { ...trip.working!, latest_confirmed_version_id: s.snapshot_id }, latest_confirmed_version_id: s.snapshot_id, plan_confirmation: 'CONFIRMED' } as TripView
const res = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const body = { expected_intake_revision: 1, expected_working_revision: 1, seen_disclosure_hash: preview.disclosure_hash, seen_risk_hash: preview.risk_hash, explicit_confirmation: true }
const all: ReturnType<typeof mount>[] = []
function harness() {
  const saved = shallowRef(structuredClone(trip)), token = ref('private-a'), blocked = ref(false)
  let session!: ReturnType<typeof useConfirmation>
  const w = mount({ components: { ConfirmationReview }, setup() {
    session = useConfirmation(saved, () => token.value, () => blocked.value)
    return { saved, session, blocked }
  }, template: '<ConfirmationReview :trip="saved" :session="session" :blocked="blocked" />' })
  all.push(w)
  return { w, saved, token, blocked, get session() { return session } }
}
function fetchDefault() {
  const f = vi.fn<typeof fetch>().mockImplementation(async (path, init) => init?.method === 'POST' ? res(result, 201) : String(path).endsWith('confirmation-preview') ? res(preview) : res(confirmed))
  vi.stubGlobal('fetch', f); return f
}
afterEach(() => { all.splice(0).forEach(w => w.unmount()); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers() })

it('loads actual-schema preview, expands unknown fees and risks, requires explicit checkbox, verifies 201 pointer', async () => {
  const f = fetchDefault(), h = harness()
  await h.session.load(); await flushPromises()
  expect(h.w.text()).toContain('未知费用（'); expect(h.w.text()).toContain('Mock'); expect(h.w.findAll('details')).toHaveLength(0)
  expect(h.w.find('.unknown-costs').text()).not.toContain('¥0.00')
  expect(h.session.acknowledged.value).toBe(false)
  await h.session.submit(); expect(f.mock.calls.filter(c => c[1]?.method === 'POST')).toHaveLength(0)
  await h.w.get('input[type=checkbox]').setValue(true)
  await Promise.all([h.session.submit(), h.session.submit()]); await flushPromises()
  const posts = f.mock.calls.filter(c => c[1]?.method === 'POST')
  expect(posts).toHaveLength(1); expect(JSON.parse(posts[0]![1]!.body as string)).toEqual(body)
  expect(h.saved.value.plan_confirmation).toBe('CONFIRMED'); expect(h.session.verified.value).toBe(true)
  expect(h.w.text()).toContain('规划确认成功（HTTP 201'); expect(h.w.text()).toContain('NOT_ASSESSED'); expect(h.w.text()).toContain('尚未预订')
})

it.each([
  { reason: 'known_costs_exceed_hard_limit', budget: { ...preview.budget, constraint: 'OVER_LIMIT' } },
  { reason: 'stale_intake' }, { reason: 'blocking_risks', risks: [...preview.risks, { code: 'blocked', severity: 'blocker', message: '待解决', item_ids: [] }] },
])('keeps 200 blocked preview non-actionable: $reason', async patch => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(res({ ...preview, ...(patch.budget ? { budget: patch.budget } : {}), ...('risks' in patch ? { risks: patch.risks } : {}), confirmation_allowed: false, blocking_reasons: [patch.reason] })))
  const h = harness(); await h.session.load(); await flushPromises()
  expect(h.w.text()).toContain('当前不能确认'); expect(h.w.get('input').attributes('disabled')).toBeDefined()
  if (patch.reason === 'stale_intake') {
    expect(h.w.text()).not.toContain('尚不支持重规划')
    expect(h.w.text()).toContain('完成重规划')
  }
  h.session.acknowledged.value = true; await h.session.submit(); expect(h.session.pending.value).toBeUndefined()
})

it('retains exact path/key/body after uncertain result, forbids new preview and retries original request', async () => {
  const f = fetchDefault(); let first = true
  f.mockImplementation(async (path, init) => {
    if (init?.method === 'POST') { if (first) { first = false; throw new TypeError('private network detail') }; return res(result, 201) }
    return String(path).endsWith('confirmation-preview') ? res(preview) : res(confirmed)
  })
  const h = harness(); await h.session.load(); h.session.acknowledged.value = true; await h.session.submit()
  const command = structuredClone(h.session.pending.value)
  expect(h.session.locked.value).toBe(true); expect(h.w.text()).toContain('未收到有效确认响应')
  await h.session.load(); expect(h.session.pending.value).toEqual(command)
  await h.session.submit(true)
  const posts = f.mock.calls.filter(c => c[1]?.method === 'POST')
  expect(posts).toHaveLength(2); expect(posts[0]![0]).toBe(posts[1]![0]); expect(posts[0]![1]!.body).toBe(posts[1]![1]!.body); expect(posts[0]![1]!.headers).toEqual(posts[1]![1]!.headers)
  expect(h.session.verified.value).toBe(true)
})

it.each(['stale_intake', 'stale_revision', 'disclosure_not_acknowledged', 'risk_not_acknowledged'])('clears agreement after explicit conflict %s', async code => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (_, init) => init?.method === 'POST' ? res({ error: { code, message: 'PRIVATE' } }, 409) : res(preview)))
  const h = harness(); await h.session.load(); h.session.acknowledged.value = true; await h.session.submit()
  expect(h.session.acknowledged.value).toBe(false); expect(h.session.preview.value).toBeUndefined(); expect(h.session.pending.value).toBeUndefined(); expect(h.session.error.value).not.toContain('PRIVATE')
  await h.session.load(); expect(h.session.acknowledged.value).toBe(false)
})

it('invalidates agreement on reload/hash changes, revisions, visibility and expiry; blocks concurrent edits', async () => {
  vi.useFakeTimers(); const f = fetchDefault(), h = harness(); await h.session.load()
  h.session.acknowledged.value = true
  f.mockResolvedValueOnce(res({ ...preview, risk_hash: 'a'.repeat(64), disclosure_hash: 'b'.repeat(64) }))
  await h.session.load(); expect(h.session.acknowledged.value).toBe(false)
  h.session.acknowledged.value = true; await vi.advanceTimersByTimeAsync(PREVIEW_TTL_MS)
  expect(h.session.acknowledged.value).toBe(false); expect(h.session.fresh.value).toBe(false)
  await h.session.load(); h.session.acknowledged.value = true
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden'); document.dispatchEvent(new Event('visibilitychange'))
  expect(h.session.acknowledged.value).toBe(false)
  await h.session.load(); h.session.acknowledged.value = true; h.blocked.value = true
  await h.session.submit(); expect(h.session.pending.value).toBeUndefined()
  h.blocked.value = false
  h.saved.value = { ...trip, intake: { ...trip.intake, intake_revision: 2 } }
  expect(h.session.acknowledged.value).toBe(false); expect(h.session.preview.value).toBeUndefined()
})

it('clears all identity-bound state and discards late responses', async () => {
  let resolve!: (v: Response) => void
  vi.stubGlobal('fetch', vi.fn().mockImplementation(() => new Promise(r => { resolve = r })))
  const h = harness(), loading = h.session.load(); h.token.value = 'private-b'; resolve(res(preview)); await loading
  expect(h.session.preview.value).toBeUndefined(); expect(h.session.pending.value).toBeUndefined(); expect(h.session.result.value).toBeUndefined(); expect(h.session.opened.value).toBe(false)
})

it('drops an uncertain original command on identity change and never replays it as the new actor', async () => {
  const f = fetchDefault(); f.mockImplementation(async (_, init) => {
    if (init?.method === 'POST') throw new TypeError('offline')
    return res(preview)
  })
  const h = harness(); await h.session.load(); h.session.acknowledged.value = true; await h.session.submit()
  expect(h.session.pending.value).toBeDefined()
  h.token.value = 'private-b'
  expect(h.session.pending.value).toBeUndefined(); expect(h.session.preview.value).toBeUndefined(); expect(h.session.acknowledged.value).toBe(false)
  await h.session.submit(true); expect(f.mock.calls.filter(c => c[1]?.method === 'POST')).toHaveLength(1)
})

it('rejects a delayed successful POST after switching identity', async () => {
  let resolve!: (v: Response) => void
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation((_, init) => init?.method === 'POST' ? new Promise(r => { resolve = r }) : Promise.resolve(res(preview))))
  const h = harness(); await h.session.load(); h.session.acknowledged.value = true; const sending = h.session.submit()
  h.token.value = 'private-b'; resolve(res(result,201)); await sending
  expect(h.session.result.value).toBeUndefined(); expect(h.session.pending.value).toBeUndefined(); expect(h.saved.value.plan_confirmation).toBe('UNCONFIRMED')
})

it('never claims current confirmation when POST succeeds but Trip verification fails; only retries GET', async () => {
  const f = fetchDefault(); f.mockImplementation(async (path, init) => init?.method === 'POST' ? res(result, 201) : String(path).endsWith('confirmation-preview') ? res(preview) : res({ error: { code: 'business_dependency_unavailable' } }, 503))
  const h = harness(); await h.session.load(); h.session.acknowledged.value = true; await h.session.submit()
  expect(h.session.result.value).toBeDefined(); expect(h.session.verified.value).toBe(false); expect(h.session.pending.value).toBeUndefined()
  f.mockResolvedValueOnce(res(confirmed)); await h.session.refreshTrip()
  expect(h.session.verified.value).toBe(true); expect(f.mock.calls.filter(c => c[1]?.method === 'POST')).toHaveLength(1)
})

it('keeps the receipt as history after i2 becomes STALE/UNCONFIRMED, then allows a newly reviewed version', async () => {
  const f = fetchDefault(), h = harness()
  await h.session.load(); h.session.acknowledged.value = true; await h.session.submit()
  const receipt = structuredClone(h.session.result.value)
  h.saved.value = { ...confirmed, intake: { ...confirmed.intake, intake_revision: 2 }, synchronization: 'STALE', plan_confirmation: 'UNCONFIRMED' }
  await flushPromises()
  expect(h.session.verified.value).toBe(false)
  expect(h.session.result.value).toEqual(receipt)
  expect(h.w.text()).toContain('此前版本已确认，当前规划未确认')
  expect(h.w.text()).not.toContain('规划确认成功（HTTP 201，已核对当前指针）')
  expect(h.w.text()).toContain(s.snapshot_id)
  expect(h.session.locked.value).toBe(false)
  expect(h.session.acknowledged.value).toBe(false)
  // A future valid r2/i2 read must permit a fresh review without reusing r1 consent.
  h.saved.value = { ...trip, intake: { ...trip.intake, intake_revision: 2 }, working: { ...trip.working!, revision: 2 }, synchronization: 'CURRENT' }
  f.mockResolvedValueOnce(res({ ...preview, intake_revision: 2, working_revision: 2, disclosure_hash: 'a'.repeat(64), risk_hash: 'b'.repeat(64) }))
  await h.session.load()
  expect(h.session.canSubmit.value).toBe(false)
  h.session.acknowledged.value = true
  expect(h.session.canSubmit.value).toBe(true)
  expect(h.session.result.value).toEqual(receipt)
  const nextResult = { ...result, snapshot: { ...result.snapshot,
    snapshot: { ...s, snapshot_id: 'snapshot-r2', source_working_revision: 2, disclosure_hash: 'a'.repeat(64) },
    context: { ...result.snapshot.context, intake_revision: 2 },
    risk_disclosure: { ...result.snapshot.risk_disclosure, risk_hash: 'b'.repeat(64) } } }
  f.mockResolvedValueOnce(res(nextResult,201)).mockResolvedValueOnce(res({ error: { code: 'business_dependency_unavailable' } },503))
  await h.session.submit()
  expect(h.session.result.value).toEqual(nextResult)
  expect(h.session.verified.value).toBe(false)
  expect(h.session.locked.value).toBe(true) // The new 201 needs its own successful GET.
  expect(h.session.awaitingVerification.value).toBe(true)
  const posts = f.mock.calls.filter(c => c[1]?.method === 'POST')
  expect(posts).toHaveLength(2)
  expect(JSON.parse(posts[1]![1]!.body as string).expected_working_revision).toBe(2)
  expect(posts[1]![1]!.headers).not.toEqual(posts[0]![1]!.headers)
})

it.each([
  { trip_id: 'another-trip' },
  { latest_confirmed_version_id: 'another-snapshot' },
  { working: { ...confirmed.working!, revision: 2 } },
  { intake: { ...confirmed.intake, intake_revision: 2 } },
  { plan_confirmation: 'UNCONFIRMED' as const },
  { synchronization: 'STALE' as const },
])('binds current success to every current Trip fact: %j', async patch => {
  fetchDefault(); const h = harness()
  await h.session.load(); h.session.acknowledged.value = true; await h.session.submit()
  h.saved.value = { ...confirmed, ...patch }; await flushPromises()
  expect(h.session.verified.value).toBe(false)
  expect(h.w.text()).not.toContain('规划确认成功（HTTP 201，已核对当前指针）')
  expect(h.session.result.value?.snapshot.snapshot.snapshot_id).toBe(s.snapshot_id)
})

it('keeps CONFIRMED after reading the same version again', async () => {
  const f = fetchDefault(), h = harness()
  await h.session.load(); h.session.acknowledged.value = true; await h.session.submit()
  await h.session.refreshTrip(); await flushPromises()
  expect(h.session.verified.value).toBe(true)
  expect(h.w.text()).toContain('规划确认成功（HTTP 201，已核对当前指针）')
  expect(f.mock.calls.filter(c => c[1]?.method === 'POST')).toHaveLength(1)
})

it('retains an unchecked 201 receipt across newer reads until GET verification actually succeeds', async () => {
  const f = fetchDefault(); f.mockImplementation(async (path, init) => init?.method === 'POST' ? res(result,201) : String(path).endsWith('confirmation-preview') ? res(preview) : res({ error: { code: 'business_dependency_unavailable' } },503))
  const h = harness(); await h.session.load(); h.session.acknowledged.value = true; await h.session.submit()
  const newer = { ...confirmed, intake: { ...confirmed.intake, intake_revision: 2 }, synchronization: 'STALE', plan_confirmation: 'UNCONFIRMED' } as TripView
  h.saved.value = newer; await flushPromises()
  expect(h.session.result.value).toEqual(result)
  expect(h.session.locked.value).toBe(true)
  expect(h.w.text()).toContain('已收到服务端确认快照，当前指针待核对')
  await h.session.load(); await h.session.submit()
  expect(f.mock.calls.filter(c => c[1]?.method === 'POST')).toHaveLength(1)
  f.mockResolvedValueOnce(res(newer)); await h.session.refreshTrip(); await flushPromises()
  expect(h.session.result.value).toEqual(result)
  expect(h.session.locked.value).toBe(false)
  expect(h.w.text()).toContain('此前版本已确认，当前规划未确认')
})

it('does not restore prior-user confirmation through a delayed Trip verification response', async () => {
  let resolve!: (v: Response) => void
  const f = fetchDefault(); f.mockImplementation((path, init) => init?.method === 'POST' ? Promise.resolve(res(result,201)) : String(path).endsWith('confirmation-preview') ? Promise.resolve(res(preview)) : new Promise(r => { resolve = r }))
  const h = harness(); await h.session.load(); h.session.acknowledged.value = true
  const sending = h.session.submit(); await flushPromises()
  expect(h.session.result.value).toEqual(result)
  h.token.value = 'private-b'; resolve(res(confirmed)); await sending; await flushPromises()
  expect(h.session.result.value).toBeUndefined()
  expect(h.session.verified.value).toBe(false)
  expect(h.saved.value.plan_confirmation).toBe('UNCONFIRMED')
  expect(h.w.text()).not.toContain(s.snapshot_id)
})

it('does not let an older Trip read restore success after a newer intake was saved', async () => {
  const f = fetchDefault(), h = harness()
  await h.session.load(); h.session.acknowledged.value = true; await h.session.submit()
  let resolve!: (v: Response) => void
  f.mockImplementationOnce(() => new Promise(r => { resolve = r }))
  const reading = h.session.refreshTrip()
  h.saved.value = { ...confirmed, intake: { ...confirmed.intake, intake_revision: 2 }, synchronization: 'STALE', plan_confirmation: 'UNCONFIRMED' }
  resolve(res(confirmed)); await reading; await flushPromises()
  expect(h.saved.value.intake.intake_revision).toBe(2)
  expect(h.session.verified.value).toBe(false)
  expect(h.w.text()).toContain('此前版本已确认，当前规划未确认')
})

it.each([401,403,404,422,503,500])('uses safe HTTP %s handling and preserves only uncertain commands', async status => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (_, init) => init?.method === 'POST' ? res({ error: { code: 'private_error', message: 'DSN PRIVATE' } }, status) : res(preview)))
  const h = harness(); await h.session.load(); h.session.acknowledged.value = true; await h.session.submit()
  expect(h.session.error.value).not.toContain('DSN'); expect(!!h.session.pending.value).toBe(status >= 500)
})

it('keeps an idempotency conflict for inspection and never automatically replaces its key', async () => {
  const f = vi.fn<typeof fetch>().mockImplementation(async (_, init) => init?.method === 'POST' ? res({ error: { code: 'idempotency_key_reused' } }, 409) : res(preview))
  vi.stubGlobal('fetch', f)
  const h = harness(); await h.session.load(); h.session.acknowledged.value = true; await h.session.submit()
  const command = h.session.pending.value
  expect(command).toBeDefined(); expect(h.session.retryAllowed.value).toBe(false); expect(h.session.locked.value).toBe(true)
  await h.session.submit(); await h.session.submit(true); await h.session.load()
  expect(h.session.pending.value).toEqual(command); expect(f.mock.calls.filter(c => c[1]?.method === 'POST')).toHaveLength(1)
})

it('rejects invalid success status, wrong nested Trip, legacy disclosure and malformed schema', async () => {
  expect(validPreview({ ...preview, budget: { ...preview.budget, known_subtotal_cents: '0' } })).toBe(false)
  expect(validPreview({ ...preview, actor: 'forged' })).toBe(false)
  expect(validResult({ ...result, snapshot: { ...result.snapshot, risk_disclosure: null } })).toBe(false)
  expect(validResult({ ...result, snapshot: { ...result.snapshot, snapshot: { ...s, budget: { ...s.budget, verification: 'VERIFIED' } } } })).toBe(false)
  expect(validPreview({ ...preview, budget: { ...preview.budget, evidence_issues: [[2, 'wrong']] } })).toBe(false)
  const command = { key: crypto.randomUUID(), body: JSON.stringify(body), path: confirmationPath(trip.trip_id) }
  await expect(confirmTrip('private', trip.trip_id, command, { fetcher: vi.fn().mockResolvedValue(res(result,200)) })).rejects.toMatchObject({ kind: 'contract' })
  const wrong = { ...result, snapshot: { ...result.snapshot, snapshot: { ...s, trip_id: 'another-trip' } } }
  await expect(confirmTrip('private', trip.trip_id, command, { fetcher: vi.fn().mockResolvedValue(res(wrong,201)) })).rejects.toMatchObject({ kind: 'contract' })
  await expect(getConfirmationPreview('private', 'other', { fetcher: vi.fn().mockResolvedValue(res(preview)) })).rejects.toMatchObject({ kind: 'contract' })
})

it('times out without losing the original command', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation((_, init) => init?.method !== 'POST' ? Promise.resolve(res(preview)) : new Promise((_, reject) => init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted','AbortError'))))))
  const h = harness(); await h.session.load(); h.session.acknowledged.value = true
  const sending = h.session.submit(); await vi.advanceTimersByTimeAsync(12001); await sending
  expect(h.session.pending.value?.path).toBe(confirmationPath(trip.trip_id)); expect(h.session.retryAllowed.value).toBe(true); expect(h.session.error.value).toContain('超时')
})
