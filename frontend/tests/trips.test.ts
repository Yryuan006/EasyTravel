import { afterEach, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import RequirementsForm from '../src/components/RequirementsForm.vue'
import { emptyForm, serializeForm } from '../src/composables/requirements'
import { useTrip } from '../src/composables/useTrip'
import { createTrip, getTrip } from '../src/api/trips'
import { fieldKeys, validTrip } from '../src/api/contracts'
import contract from '../../Docs/M1-03a_openapi.json'

export function fixture() {
  const f = emptyForm(); f.raw_text = '想去杭州'
  return { trip_id: 'c7b9ae77-44f6-477d-bf9c-59a7776f741d', created_at: '2026-11-11T09:00:00Z',
    intake: { intake_revision: 1, input: serializeForm(f), collection_status: 'COLLECTING', blocking_fields: ['origin'], provenance: [], suggested_values: [] },
    working: null, latest_confirmed_version_id: null, active_run_id: null, synchronization: 'NO_DRAFT', plan_confirmation: 'UNCONFIRMED', readiness: 'NOT_ASSESSED', booked: false }
}
const command = { key: crypto.randomUUID(), body: JSON.stringify({ requirements: fixture().intake.input }) }
const response = (data: unknown = fixture(), status = 201) => vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(data), { status }))
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

it('serializes exactly the complete OpenAPI fields without extracting natural language', () => {
  const f = emptyForm(); f.raw_text = '上海出发去杭州，两个人三天预算2000'
  const r = serializeForm(f)
  expect(Object.keys(r).sort()).toEqual([...contract.components.schemas.RequirementsInput.required].sort())
  fieldKeys.forEach(k => expect(r[k]).toEqual({ state: 'unanswered', value: null }))
  f.states.hotel = 'no_preference'; f.states.target_budget_cents = 'provided'; f.values.target_budget_cents = '123.45'
  expect(serializeForm(f).hotel).toEqual({ state: 'no_preference', value: null })
  expect(serializeForm(f).target_budget_cents).toEqual({ state: 'provided', value: 12345 })
  f.states.dates = 'provided'; f.dateMode = 'window'; f.start = '2026-12-01'; f.end = '2026-12-10'
  expect(serializeForm(f).dates).toEqual({ state: 'provided', value: { mode: 'window', earliest_start: f.start, latest_end: f.end } })
})
it.each([['travelers', '3'], ['travelers', '1.5'], ['days', '0'], ['days', '6'], ['target_budget_cents', '-1'], ['hard_limit_cents', '1.234'], ['hard_limit_cents', '10000000001'], ['origin', '   ']] as const)('rejects invalid %s=%s', (key, value) => {
  const f = emptyForm(); f.raw_text = '杭州'; f.states[key] = 'provided'; f.values[key] = value
  expect(() => serializeForm(f)).toThrow()
})
it.each([['2026-02-30', '2026-03-01'], ['2026-12-03', '2026-12-01'], ['', '2026-12-01']])('rejects invalid dates %s %s', (start, end) => {
  const f = emptyForm(); f.raw_text = '杭州'; f.states.dates = 'provided'; f.start = start; f.end = end
  expect(() => serializeForm(f)).toThrow()
})
it('rejects empty requirements but accepts destination alone', () => {
  const f = emptyForm(); expect(() => serializeForm(f)).toThrow('目的地')
  f.states.destination = 'provided'; f.values.destination = '杭州'; expect(serializeForm(f).raw_text).toBe(null)
})
it('renders progressive form and emits validated input', async () => {
  const w = mount(RequirementsForm, { props: { busy: false } })
  expect(w.findAll('details')).toHaveLength(2)
  expect(w.find('details').attributes('open')).toBeUndefined()
  await w.get('form').trigger('submit'); expect(w.get('[role=alert]').text()).toContain('目的地')
  await w.get('textarea').setValue('去杭州'); await w.get('form').trigger('submit')
  expect(w.emitted('submit')?.[0]?.[0]).toEqual(serializeForm({ ...emptyForm(), raw_text: '去杭州' }))
  await w.setProps({ busy: true }); expect(w.get('fieldset').attributes('disabled')).toBeDefined(); w.unmount()
})
it('only accepts real HTTP 201 for POST and 200 for GET', async () => {
  expect(await createTrip('private', command, { fetcher: response() })).toEqual(fixture())
  await expect(createTrip('private', command, { fetcher: response(fixture(), 200) })).rejects.toMatchObject({ kind: 'contract' })
  expect(await getTrip('private', fixture().trip_id, { fetcher: response(fixture(), 200) })).toEqual(fixture())
  await expect(getTrip('private', crypto.randomUUID(), { fetcher: response(fixture(), 200) })).rejects.toMatchObject({ kind: 'contract' })
})
it.each([401, 403, 404, 409, 422, 503])('sanitizes HTTP %s', async status => {
  await expect(createTrip('private', command, { fetcher: response({ error: { message: 'secret diagnostic' } }, status) })).rejects.toMatchObject({ kind: 'http', status })
  await expect(createTrip('private', command, { fetcher: response({ error: { message: 'secret diagnostic' } }, status) })).rejects.not.toThrow('secret diagnostic')
})
it.each(['command_in_progress', 'idempotency_key_reused'])('distinguishes 409 %s', async code => {
  await expect(createTrip('private', command, { fetcher: response({ error: { code } }, 409) })).rejects.toMatchObject({ code })
})
it.each([{ booked: true }, { working: {} }, { plan_confirmation: 'CONFIRMED' }, { intake: {} }, { trip_id: 'bad/id' }])('rejects invalid/unsupported TripView %j', patch => {
  expect(validTrip({ ...fixture(), ...patch })).toBe(false)
})
it('rejects malformed JSON and incomplete HTTP 201 responses', async () => {
  await expect(createTrip('private', command, { fetcher: response({ trip_id: fixture().trip_id }) })).rejects.toMatchObject({ kind: 'contract' })
  await expect(createTrip('private', command, { fetcher: vi.fn<typeof fetch>().mockResolvedValue(new Response('<html>', { status: 201 })) })).rejects.toMatchObject({ kind: 'contract' })
})
it('keeps exact command after timeout; changed body uses a new key', async () => {
  vi.useFakeTimers()
  const calls: RequestInit[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation((_url, init) => {
    calls.push(init!)
    return new Promise((_resolve, reject) => init?.signal?.addEventListener('abort', () => reject(new Error('secret'))))
  }))
  const scope = effectScope(); const trip = scope.run(() => useTrip())!
  const first = trip.save('private', fixture().intake.input); await vi.advanceTimersByTimeAsync(12000); await first
  expect(trip.state.value).toMatchObject({ status: 'error', message: expect.stringContaining('超时') })
  const retry = trip.save('private'); await vi.advanceTimersByTimeAsync(12000); await retry
  expect(calls[0]?.body).toBe(calls[1]?.body); expect(calls[0]?.headers).toEqual(calls[1]?.headers)
  const third = trip.save('private', { ...fixture().intake.input, raw_text: '去苏州' }); await vi.advanceTimersByTimeAsync(12000); await third
  expect(calls[2]?.headers).not.toEqual(calls[1]?.headers)
  trip.reset(); expect(trip.pending.value).toBeUndefined(); scope.stop()
})
it('suppresses stale responses on identity reset and blocks repeated submission', async () => {
  let resolve!: (r: Response) => void
  const fetcher = vi.fn<typeof fetch>().mockImplementation(() => new Promise(r => { resolve = r })); vi.stubGlobal('fetch', fetcher)
  const scope = effectScope(); const trip = scope.run(() => useTrip())!
  const first = trip.save('private', fixture().intake.input)
  await trip.save('private', fixture().intake.input); expect(fetcher).toHaveBeenCalledTimes(1)
  trip.reset(); resolve(new Response(JSON.stringify(fixture()), { status: 201 })); await first; await flushPromises()
  expect(trip.state.value.status).toBe('idle'); scope.stop()
})
