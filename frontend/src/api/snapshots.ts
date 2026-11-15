import { request, TripApiError, type RequestOptions } from './trips'
import { validId } from './contracts'
import { validSchema } from './working'
import type { SnapshotView, BudgetResult } from './confirmation-contracts'
export type { SnapshotView }
export interface SnapshotSummary { snapshot_id: string; source_working_revision: number; confirmed_at: string; budget: BudgetResult }
export interface SnapshotPage { items: SnapshotSummary[]; next_cursor: string | null }
async function historyRead<T>(promise: Promise<T>): Promise<T> {
  try { return await promise }
  catch (error) {
    if (!(error instanceof TripApiError)) throw error
    const message = error.kind === 'timeout' ? '历史查询超时，请重新读取。'
      : error.kind === 'network' ? '网络连接中断，历史查询失败，请重新读取。'
      : error.kind === 'contract' ? '返回数据不符合历史版本契约，无法展示。'
      : error.status === 404 ? '旅行或历史版本不存在，或当前身份无权读取。'
      : error.status === 422 ? '历史分页参数或游标无效，请从第一页重新读取。' : error.message
    throw new TripApiError(error.kind, message, error.status, error.code)
  }
}
function path(id: string) {
  if (!validId(id)) throw new TripApiError('contract', '旅行 ID 无效。')
  return `/api/v1/trips/${id}/snapshots`
}
export function getSnapshots(token: string, id: string, cursor: string | null = null, options: RequestOptions = {}) {
  return historyRead(request(path(id) + (cursor === null ? '' : `?cursor=${encodeURIComponent(cursor)}`), token, undefined, options, 'GET',
    (v): v is SnapshotPage => validSchema(v, 'SnapshotPage') && new Set((v as SnapshotPage).items.map(x => x.snapshot_id)).size === (v as SnapshotPage).items.length, 200))
}
export function getSnapshot(token: string, id: string, sid: string, options: RequestOptions = {}) {
  if (!validId(sid)) throw new TripApiError('contract', '历史版本 ID 无效。')
  return historyRead(request(path(id) + '/' + sid, token, undefined, options, 'GET', (v): v is SnapshotView => {
    if (!validSchema(v, 'SnapshotView')) return false
    const s = (v as SnapshotView).snapshot
    return s.trip_id === id && s.snapshot_id === sid
  }, 200))
}
