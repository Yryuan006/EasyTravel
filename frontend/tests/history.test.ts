import { afterEach, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import TripHistory from '../src/components/TripHistory.vue'
import { getSnapshot, getSnapshots } from '../src/api/snapshots'
import evidence from '../../Docs/M1-05a_PostgreSQL证据.json'
const view = evidence.result.snapshot
const summary = { snapshot_id: view.snapshot.snapshot_id, source_working_revision: 1, confirmed_at: view.snapshot.confirmed_at, budget: view.snapshot.budget }
const page = { items: [summary], next_cursor: null }
const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status })
const wrappers: ReturnType<typeof mount>[] = []
function show(sid?: string) {
  const w = mount(TripHistory, { props: { token: 'private-a', tripId: view.snapshot.trip_id, ...(sid ? { snapshotId: sid } : {}) } }); wrappers.push(w); return w
}
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('reads empty, populated and paginated history without writes', async () => {
  const f = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ items: [], next_cursor: null })).mockResolvedValueOnce(response({ ...page, next_cursor: 'opaque' })).mockResolvedValueOnce(response({ items: [{ ...summary, snapshot_id: 'older', source_working_revision: 2 }], next_cursor: null }))
  vi.stubGlobal('fetch', f)
  const w = show(); await flushPromises(); expect(w.text()).toContain('尚无历史确认版本')
  await w.setProps({ token: 'private-b' }); await flushPromises(); expect(w.text()).toContain('来源 r1')
  await w.get('button').trigger('click'); await flushPromises(); expect(w.text()).toContain('来源 r2')
  expect(String(f.mock.calls.at(-1)?.[0])).toContain('?cursor=opaque')
  expect(f.mock.calls.every(c => c[1]?.method === 'GET')).toBe(true)
})

it('shows stored snapshot budget, daily itinerary and acknowledged risk as read only', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(view)))
  const w = show(view.snapshot.snapshot_id); await flushPromises()
  expect(w.text()).toContain('确认时保存的预算'); expect(w.text()).toContain('历史每日行程')
  expect(w.text()).not.toContain('当前工作稿未确认。'); expect(w.findAll('input,textarea,select')).toHaveLength(0)
  expect(w.text()).toContain(view.risk_disclosure.risks[0]!.message)
  expect(w.text()).toContain('booked=false'); expect(w.text()).toContain('NOT_ASSESSED')
})

it('does not fabricate context or user acknowledgment for legacy snapshots', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ ...view, context: null, risk_disclosure: null })))
  const w = show(view.snapshot.snapshot_id); await flushPromises()
  expect(w.text().match(/此历史版本未记录相关信息/g)).toHaveLength(2)
  expect(w.text()).not.toContain(view.risk_disclosure.risks[0]!.message)
})

it.each([401,403,404,422,503])('clears prior detail on identity change and handles HTTP %s', async status => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response(view)).mockResolvedValueOnce(response({ error: { code: 'failure' } }, status)))
  const w = show(view.snapshot.snapshot_id); await flushPromises()
  await w.setProps({ token: 'different-owner' }); await flushPromises()
  expect(w.find('[role="alert"]').exists()).toBe(true); expect(w.text()).not.toContain('历史每日行程')
})

it('late response cannot overwrite another snapshot or identity', async () => {
  let resolve!: (r: Response) => void
  vi.stubGlobal('fetch', vi.fn().mockImplementationOnce(() => new Promise<Response>(r => { resolve = r })).mockResolvedValueOnce(response({ ...view, snapshot: { ...view.snapshot, snapshot_id: 'second' } })))
  const w = show(view.snapshot.snapshot_id); await w.setProps({ snapshotId: 'second', token: 'new-owner' }); await flushPromises()
  resolve(response(view)); await flushPromises(); expect(w.text()).toContain('历史快照 second'); expect(w.text()).not.toContain('历史快照 '+view.snapshot.snapshot_id)
})

it('rejects wrong parents, malformed returns, and handles network timeout without invented history', async () => {
  const f = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ ...view, snapshot: { ...view.snapshot, trip_id: 'wrong' } })).mockResolvedValueOnce(response({ items: [{ snapshot_id: 'fake' }], next_cursor: null }))
  await expect(getSnapshot('token',view.snapshot.trip_id,view.snapshot.snapshot_id,{fetcher:f})).rejects.toMatchObject({kind:'contract'})
  await expect(getSnapshots('token',view.snapshot.trip_id,null,{fetcher:f})).rejects.toMatchObject({kind:'contract'})
  const aborted = vi.fn<typeof fetch>().mockImplementation((_url, init) => new Promise((_resolve,reject) => init?.signal?.addEventListener('abort', () => reject(new Error('abort')))))
  await expect(getSnapshots('token',view.snapshot.trip_id,null,{fetcher:aborted,timeoutMs:5})).rejects.toMatchObject({kind:'timeout'})
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
  const w = show(); await flushPromises(); expect(w.find('[role="alert"]').exists()).toBe(true); expect(w.text()).not.toContain('尚无历史确认版本')
})
