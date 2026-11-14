import { afterEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { shallowRef } from 'vue'
import persisted from '../../Docs/M1-04a_独立进程读取.json'
import { validTrip, type TripView } from '../src/api/contracts'
import TripSummary from '../src/components/TripSummary.vue'
import { canRetry, generate, getRun, money, validState, shanghaiDate, type GenerationRun, type BudgetResult, type PlanItem, type CostLine } from '../src/api/generations'
import { useGeneration } from '../src/composables/useGeneration'
import ItineraryView from '../src/components/ItineraryView.vue'
import BudgetSummary from '../src/components/BudgetSummary.vue'
import CostBreakdown from '../src/components/CostBreakdown.vue'
import GenerationStatus from '../src/components/GenerationStatus.vue'
const trip = persisted.trip as TripView
const run = persisted.run as unknown as GenerationRun
const res = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })
it('accepts a confirmed current Trip and keeps confirmation distinct from booking and stale history', () => {
  const confirmed = { ...trip, plan_confirmation: 'CONFIRMED' as const, latest_confirmed_version_id: 'snapshot-m105',
    working: { ...trip.working!, latest_confirmed_version_id: 'snapshot-m105' } }
  expect(validTrip(confirmed)).toBe(true)
  const w = mount(TripSummary, { props: { trip: confirmed } })
  expect(w.text()).toContain('已确认规划'); expect(w.text()).toContain('尚未预订'); w.unmount()
  expect(validTrip({ ...confirmed, synchronization: 'STALE' })).toBe(false)
  expect(validTrip({ ...confirmed, booked: true })).toBe(false)
})
it('preserves unknown, explicit zero and estimates with integer-cent formatting', () => {
  expect(money(null)).toBe('未知'); expect(money(0)).toBe('¥0.00'); expect(money(29)).toBe('¥0.29')
  const w = mount(BudgetSummary, { props: { budget: run.diagnostics_budget as BudgetResult, hardLimit: 230000 } })
  expect(w.text()).toContain('总额暂无法确定'); expect(w.text()).toContain('¥600.00'); expect(w.text()).toContain('INDETERMINATE · UNDETERMINED'); w.unmount()
  const c = mount(CostBreakdown, { props: { items: trip.working!.payload.items as PlanItem[], costs: trip.working!.payload.costs as CostLine[] } })
  expect(c.text()).toContain('未知原因'); expect(c.text()).toContain('Mock'); expect(c.text()).not.toContain('¥0.00'); c.unmount()
})
it('separates multiday hotel from daily activities and uses Shanghai dates', () => {
  expect(shanghaiDate('2026-12-03T17:00:00Z')).toBe('2026-12-04')
  const w = mount(ItineraryView, { props: { items: trip.working!.payload.items as PlanItem[], costs: trip.working!.payload.costs as CostLine[] } })
  expect(w.findAll('.day-schedule')).toHaveLength(3); expect(w.findAll('.hotel-card')).toHaveLength(1)
  expect(w.findAll('.day-schedule .hotel-card')).toHaveLength(0); expect(w.text()).toContain('模拟建议，尚未核验'); w.unmount()
})
it.each(['known_costs_exceed_hard_limit','stale_input','invalid_output','worker_interrupted','persistence_failed'])('safe failure guidance for %s', code => {
  const r = { ...run, status: 'FAILED' as const, result: null, error: { code, message: 'secret raw model text', retryable: true } }
  expect(!!canRetry(r)).toBe(!['known_costs_exceed_hard_limit','stale_input'].includes(code))
  const w = mount(GenerationStatus, { props: { run: r } }); expect(w.text()).toContain('生成失败'); expect(w.text()).not.toContain('secret'); w.unmount()
})
it('validates run identity and exact schema without exposing raw diagnostics', async () => {
  await expect(getRun('private', run.trip_id, 'wrong', { fetcher: vi.fn().mockResolvedValue(res(run)) })).rejects.toMatchObject({ kind: 'contract' })
  await expect(generate('private', run.trip_id, { key: crypto.randomUUID(), body: '{}' }, { fetcher: vi.fn().mockResolvedValue(res(run, 200)) })).rejects.toMatchObject({ kind: 'contract' })
})
function harness() {
  const saved = shallowRef({ ...trip, working: null, synchronization: 'NO_DRAFT', latest_confirmed_version_id: null } as TripView)
  let session!: ReturnType<typeof useGeneration>
  const w = mount({ setup() { session = useGeneration(saved, () => 'private'); return () => null } })
  return { w, saved, get session() { return session } }
}
it('only sends POST after click, protects double clicks and reuses exact key/body after uncertain network failure', async () => {
  vi.useFakeTimers(); const calls: RequestInit[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
    if (init?.method === 'POST') { calls.push(init); throw new TypeError('network') }
    return String(url).endsWith('/generation-state') ? res({ trip_id: trip.trip_id, working_revision: null, source_run: null, latest_run: null }) : res({ ...trip, working: null, synchronization: 'NO_DRAFT', latest_confirmed_version_id: null })
  }))
  const h = harness(); await h.session.refresh(); expect(calls).toHaveLength(0)
  const first = h.session.start(); const second = h.session.start(); await Promise.all([first, second]); expect(calls).toHaveLength(1)
  const pending = h.session.pending.value; expect(pending).toBeDefined(); await h.session.start(true)
  expect(calls).toHaveLength(2); expect(calls[0]?.body).toBe(calls[1]?.body); expect(calls[0]?.headers).toEqual(calls[1]?.headers)
  expect(JSON.parse(pending!.body)).toEqual({ expected_intake_revision: 1, expected_working_revision: null, retry_of: null }); h.w.unmount()
})
it('discard late responses after identity component unmount', async () => {
  let resolve!: (r: Response) => void
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(() => new Promise(r => { resolve = r })))
  const h = harness(), pending = h.session.refresh(); h.w.unmount(); resolve(res(trip)); await pending; await flushPromises()
  expect(h.saved.value.working).toBeNull(); expect(h.session.projection.value).toBeUndefined()
})
it('new successful snapshot wins over a late FAILED poll for the same run', async () => {
  vi.useFakeTimers()
  const accepted = { ...persisted.run, status: 'ACCEPTED', result: null, error: null, diagnostics_budget: null }
  let resolve!: (r: Response) => void, complete = false
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (url) => {
    if (String(url).includes('/generations/')) return new Promise(r => { resolve = r })
    if (String(url).endsWith('/generation-state')) return res({ trip_id: trip.trip_id, working_revision: complete ? 1 : null, source_run: complete ? persisted.run : null, latest_run: complete ? persisted.run : accepted })
    return res(complete ? trip : { ...trip, working: null, synchronization: 'NO_DRAFT', latest_confirmed_version_id: null })
  }))
  const h = harness(); await h.session.refresh(); await vi.advanceTimersByTimeAsync(350)
  complete = true; await h.session.refresh()
  resolve(res({ ...persisted.run, status: 'FAILED', result: null, error: { code: 'worker_interrupted', message: 'safe', retryable: true } })); await flushPromises()
  expect(h.session.run.value?.status).toBe('SUCCEEDED'); expect(h.saved.value.working?.revision).toBe(1); h.w.unmount()
})

it('rejects source budget from another trip or working revision', () => {
  const s = { trip_id: trip.trip_id, working_revision: 1, source_run: persisted.run, latest_run: persisted.run }
  expect(validState(s)).toBe(true)
  expect(validState({ ...s, source_run: { ...persisted.run, trip_id: 'another-trip' } })).toBe(false)
  expect(validState({ ...s, working_revision: 2 })).toBe(false)
})
it('timeout retains generation key and body for exact replay', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
    if (init?.method === 'POST') return new Promise((_resolve, reject) => init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))))
    return String(url).endsWith('/generation-state') ? res({ trip_id: trip.trip_id, working_revision: null, source_run: null, latest_run: null }) : res({ ...trip, working: null, synchronization: 'NO_DRAFT', latest_confirmed_version_id: null })
  }))
  const h = harness(); await h.session.refresh(); const start = h.session.start(), original = h.session.pending.value
  await vi.advanceTimersByTimeAsync(12000); await start
  expect(h.session.error.value).toContain('请求超时'); expect(h.session.pending.value).toBe(original); expect(h.session.eligible.value).toBe(false); h.w.unmount()
})
it('definitive stale-intake conflict requires reload instead of replaying an unaccepted old command', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
    if (init?.method === 'POST') return res({ error: { code: 'stale_intake' } }, 409)
    return String(url).endsWith('/generation-state') ? res({ trip_id: trip.trip_id, working_revision: null, source_run: null, latest_run: null }) : res({ ...trip, working: null, synchronization: 'NO_DRAFT', latest_confirmed_version_id: null })
  }))
  const h = harness(); await h.session.refresh(); await h.session.start()
  expect(h.session.pending.value).toBeUndefined(); expect(h.session.eligible.value).toBe(false)
  expect(h.session.error.value).toContain('重新读取'); h.w.unmount()
})
