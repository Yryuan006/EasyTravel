import { afterEach, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { fieldKeys, validNext, validTrip, type TripView } from '../src/api/contracts'
import { emptyForm, serializeForm, formFromRequirements } from '../src/composables/requirements'
import { useRequirements } from '../src/composables/useRequirements'
import RequirementsForm from '../src/components/RequirementsForm.vue'
import ClarificationCard from '../src/components/ClarificationCard.vue'
import TripWorkspace from '../src/components/TripWorkspace.vue'
import TripSummary from '../src/components/TripSummary.vue'
import { updateRequirements, answerQuestion, getNextQuestion } from '../src/api/trips'
import schema from '../../Docs/M1-03a_openapi.json'
import currentSchema from '../../Docs/M1-04a_openapi.json'
import persisted from '../../Docs/M1-04a_独立进程读取.json'
import workingSchema from '../src/api/working-schema.json'
function trip(): TripView {
  const f = emptyForm(); f.raw_text = '去杭州'; f.states.hotel = 'no_preference'; f.states.hard_limit_cents = 'provided'; f.values.hard_limit_cents = '1234.56'
  return { trip_id: crypto.randomUUID(), created_at: '2026-11-12T00:00:00Z', intake: { intake_revision: 1, input: serializeForm(f), collection_status: 'COLLECTING', blocking_fields: ['origin'], provenance: [], suggested_values: [] }, working: null, latest_confirmed_version_id: null, synchronization: 'NO_DRAFT', plan_confirmation: 'UNCONFIRMED', active_run_id: null, readiness: 'NOT_ASSESSED', booked: false }
}
const q = { question_id: 'question-1', intake_revision: 1, field_key: 'origin' as const, prompt: '请填写出发地', allow_no_preference: false }
const next = { intake_revision: 1, question: q, blocking_fields: ['origin'] }
const res = (x: unknown, status = 200) => new Response(JSON.stringify(x), { status })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })
it('aligns all copied legacy draft schemas and request keys with actual OpenAPI', () => {
  for (const [k, v] of Object.entries(workingSchema)) expect(v).toEqual(k === 'WorkingDraft' ? schema.components.schemas.WorkingDraft : (currentSchema.components.schemas as Record<string, unknown>)[k])
  expect(schema.components.schemas.AnswerRequest.required.sort()).toEqual(['expected_intake_revision', 'expected_working_revision', 'field_key', 'answer'].sort())
  expect(schema.components.schemas.UpdateRequirementsRequest.required.sort()).toEqual(['expected_intake_revision', 'expected_working_revision', 'requirements'].sort())
})
it('round trips every explicit field, date window, zero and fractional yuan without inference', async () => {
  const f = emptyForm(); f.raw_text = '上海2人'; for (const k of fieldKeys) f.states[k] = 'no_preference'
  f.states.dates = 'provided'; f.dateMode = 'window'; f.start = '2026-12-01'; f.end = '2026-12-10'
  f.states.target_budget_cents = 'provided'; f.values.target_budget_cents = '0.01'
  const r = serializeForm(f); expect(serializeForm(formFromRequirements(r))).toEqual(r)
  expect(r.origin).toEqual({ state: 'no_preference', value: null })
  const w = mount(RequirementsForm, { props: { busy: false, editing: true, initial: trip().intake.input } })
  expect(w.get('#hotel-state').element).toHaveProperty('value', 'no_preference')
  expect(w.get('#origin-state').element).toHaveProperty('value', 'unanswered')
  expect(w.get('#hard_limit_cents').element).toHaveProperty('value', '1234.56')
  await w.get('form').trigger('submit'); expect(w.emitted('submit')?.[0]?.[0]).toEqual(trip().intake.input); w.unmount()
})
it('renders one server prompt and appropriate controls; no preference follows server flag', async () => {
  const w = mount(ClarificationCard, { props: { question: q, busy: false } })
  expect(w.text()).toContain(q.prompt); expect(w.text()).not.toContain('明确无偏好')
  await w.get('input').setValue('上海'); await w.get('form').trigger('submit')
  expect(w.emitted('answer')?.[0]?.[0]).toEqual({ state: 'provided', value: '上海' })
  await w.setProps({ question: { ...q, field_key: 'hard_limit_cents', allow_no_preference: true } })
  await w.get('input').setValue('0.29'); await w.get('form').trigger('submit')
  expect(w.emitted('answer')?.[1]?.[0]).toEqual({ state: 'provided', value: 29 }); w.unmount()
})
it.each([401, 403, 404, 409, 422, 503])('handles HTTP %s for both mutation methods and next question', async status => {
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => res({ error: { code: 'safe', message: 'secret' } }, status))
  for (const call of [() => updateRequirements('t', 'id', { key: crypto.randomUUID(), body: '{}' }, { fetcher }), () => answerQuestion('t', 'id', 'q', { key: crypto.randomUUID(), body: '{}' }, { fetcher }), () => getNextQuestion('t', 'id', { fetcher })]) await expect(call()).rejects.toMatchObject({ status, kind: 'http' })
})
it('checks next question identity, revision, field and shape; permits null with blockers', () => {
  expect(validNext(next)).toBe(true); expect(validNext({ ...next, question: null })).toBe(true)
  for (const patch of [{ intake_revision: 2 }, { field_key: 'hotel' }, { question_id: 'bad/id' }, { prompt: '' }]) expect(validNext({ ...next, question: { ...q, ...patch } })).toBe(false)
})
it('retains exact path/key/body after mutation timeout and changes key on changed content', async () => {
  vi.useFakeTimers(); const calls: { path: string; init: RequestInit }[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation((url, init) => {
    calls.push({ path: String(url), init: init! }); return new Promise((_r, reject) => init?.signal?.addEventListener('abort', () => reject(new Error('aborted'))))
  }))
  const scope = effectScope(), s = scope.run(() => useRequirements(trip(), () => 'private'))!
  let p = s.write('update', s.saved.value.intake.input); await vi.advanceTimersByTimeAsync(12000); await p
  expect(s.error.value).toContain('超时'); expect(s.saved.value.intake.intake_revision).toBe(1)
  p = s.write(); await vi.advanceTimersByTimeAsync(12000); await p
  expect(calls[0]?.path).toBe(calls[1]?.path); expect(calls[0]?.init.body).toBe(calls[1]?.init.body); expect(calls[0]?.init.headers).toEqual(calls[1]?.init.headers)
  p = s.write('update', { ...s.saved.value.intake.input, raw_text: '苏州' }); await vi.advanceTimersByTimeAsync(12000); await p
  expect(calls[2]?.init.headers).not.toEqual(calls[1]?.init.headers); scope.stop()
})
it('preserves unsaved editor on conflict, reloads server summary and requires manual review', async () => {
  const t = trip(), newer = structuredClone(t); newer.intake.intake_revision = 2; newer.intake.input.raw_text = '服务器新内容'
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (url, init) => String(url).endsWith('/clarifications/next') ? res(next) : init?.method === 'PUT' ? res({ error: { code: 'business_conflict' } }, 409) : res(newer)))
  const w = mount(TripWorkspace, { props: { initial: t, token: 'private' } }); await flushPromises()
  await w.findAll('button').find(x => x.text() === '修改需求')!.trigger('click')
  await w.get('textarea').setValue('本地未保存'); await w.get('form').trigger('submit'); await flushPromises()
  expect(w.get('textarea').element).toHaveProperty('value', '本地未保存'); expect(w.text()).toContain('本地输入已保留')
  await w.findAll('button').find(x => x.text() === '读取最新需求并重新审阅')!.trigger('click'); await flushPromises()
  expect(w.text()).toContain('服务器新内容'); expect(w.get('textarea').element).toHaveProperty('value', '本地未保存')
  expect(w.get('fieldset').attributes('disabled')).toBeDefined(); w.unmount()
})
it('rejects stale question response and suppresses late mutation after unmount', async () => {
  let resolve!: (r: Response) => void
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValueOnce(res({ ...next, intake_revision: 2, question: { ...q, intake_revision: 2 } })).mockImplementation(() => new Promise(r => { resolve = r })))
  const scope = effectScope(), s = scope.run(() => useRequirements(trip(), () => 'private'))!
  await s.question(); expect(s.conflict.value).toBe(true); expect(s.next.value).toBeUndefined()
  s.conflict.value = false; const p = s.write('update', s.saved.value.intake.input); s.edit(); resolve(res({ ...trip(), intake: { ...trip().intake, intake_revision: 3 } })); await p
  expect(s.saved.value.intake.intake_revision).toBe(1); scope.stop()
})
it('accepts actual legacy draft as STALE and never as confirmed', () => {
  const t = trip()
  const working = { trip_id: t.trip_id, owner_id: 'a', revision: 2, requirements: { destination: '杭州', start_date: '2026-12-01', end_date: '2026-12-01', timezone: 'Asia/Shanghai', currency: 'CNY', hard_limit_cents: 100, requirements_revision: 1 }, payload: { items: [{ id: 'a', kind: 'activity', resource_id: 'a', title: '旧活动', start: '2026-12-01T10:00:00+08:00', end: '2026-12-01T11:00:00+08:00', room_type: null, check_in: null, check_out: null, rooms: null }], costs: [{ id: 'c', item_id: 'a', amount_cents: null, category: 'unknown', unknown_reason: '未知', evidence_id: null, is_mock: true, query: { resource_id: 'a', start_date: '2026-12-01', end_date: '2026-12-01', currency: 'CNY', room_type: null, rooms: null } }] }, item_confirmations: [], latest_confirmed_version_id: 'snapshot-old' }
  const v = { ...t, working, synchronization: 'STALE', latest_confirmed_version_id: 'snapshot-old' }
  expect(validTrip(v)).toBe(true)
  if (!validTrip(v)) throw new Error('Invalid legacy fixture')
  const w = mount(TripSummary, { props: { trip: v } }); expect(w.text()).toContain('旧工作稿 r2 · STALE · 只读'); expect(w.text()).toContain('它不代表当前工作稿已确认'); expect(w.findAll('button')).toHaveLength(0); w.unmount()
  expect(validTrip({ ...v, synchronization: 'NO_DRAFT' })).toBe(false); expect(validTrip({ ...v, plan_confirmation: 'CONFIRMED' })).toBe(false)
})
it.each(['dates', 'days', 'travelers'] as const)('uses bounded controls for %s', async field => {
  const w = mount(ClarificationCard, { props: { question: { ...q, field_key: field }, busy: false } })
  if (field === 'dates') {
    expect(w.findAll('input[type=date]')).toHaveLength(2)
    await w.get('#answer-start').setValue('2026-12-04'); await w.get('#answer-end').setValue('2026-12-06')
  } else { expect(w.findAll('option')).toHaveLength(field === 'days' ? 6 : 3); await w.get('select').setValue(field === 'days' ? '5' : '2') }
  await w.get('form').trigger('submit'); expect(w.emitted('answer')).toHaveLength(1); w.unmount()
})
it('sends actual working revision and current question identity; clears old answer retry when editing', async () => {
  const t = trip(); t.working = { trip_id: t.trip_id, owner_id: 'a', revision: 7, requirements: {}, payload: {}, item_confirmations: [], latest_confirmed_version_id: null }
  const calls: RequestInit[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (_url, init) => { calls.push(init!); return init?.method === 'GET' ? res(next) : res({ error: { code: 'business_dependency_unavailable' } }, 503) }))
  const scope = effectScope(), s = scope.run(() => useRequirements(t, () => 'private'))!
  await s.question(); await s.write('answer', { state: 'provided', value: '上海' })
  expect(JSON.parse(String(calls[1]?.body))).toEqual({ expected_intake_revision: 1, expected_working_revision: 7, field_key: 'origin', answer: { state: 'provided', value: '上海' } })
  expect(s.pending.value?.path).toContain('/clarifications/question-1/answers'); s.edit(); expect(s.pending.value).toBeUndefined(); expect(s.next.value).toBeUndefined(); scope.stop()
})
it('successful retry of no-preference required answer also pauses the next question', async () => {
  const t = trip(), saved = structuredClone(t); saved.intake.intake_revision = 2; saved.intake.input.origin = { state: 'no_preference', value: null }
  let writes = 0, reads = 0
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (_url, init) => {
    if (init?.method === 'GET') { reads++; const revision = reads === 1 ? 1 : 2; return res({ ...next, intake_revision: revision, question: { ...q, intake_revision: revision, allow_no_preference: true } }) }
    if (++writes === 1) throw new TypeError('network')
    return res(saved)
  }))
  const w = mount(TripWorkspace, { props: { initial: t, token: 'private' } }); await flushPromises()
  await w.findAll('button').find(x => x.text() === '明确无偏好')!.trigger('click'); await flushPromises()
  await w.findAll('button').find(x => x.text() === '使用原 key 和原内容重试')!.trigger('click'); await flushPromises()
  expect(w.text()).toContain('旅行已保存，可稍后继续'); expect(w.findComponent(ClarificationCard).exists()).toBe(false); w.unmount()
})

it('accepts persisted CURRENT draft and shows truthful unconfirmed mock compatibility notice', () => {
  expect(validTrip(persisted.trip)).toBe(true)
  const w = mount(TripSummary, { props: { trip: persisted.trip as TripView } })
  expect(w.text()).toContain('模拟工作稿 r1 · 未确认')
  expect(w.text()).toContain('完整行程展示将在后续阶段提供')
  expect(w.text()).not.toContain('无法证明旧工作稿')
  expect(validTrip({ ...persisted.trip, working: { ...persisted.trip.working, context: null } })).toBe(false)
  const broken = structuredClone(persisted.trip); (broken.working.context as unknown as Record<string, unknown>).intake_revision = 0
  expect(validTrip(broken)).toBe(false)
  w.unmount()
})
