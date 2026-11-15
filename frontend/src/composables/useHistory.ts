import { onUnmounted, ref, shallowRef, watch } from 'vue'
import { getSnapshots, getSnapshot, type SnapshotSummary, type SnapshotView } from '../api/snapshots'
import { ApiError } from '../api/health'

export function useHistory(identity: () => string, trip: () => string, snapshot: () => string | undefined) {
  const items = shallowRef<SnapshotSummary[]>([]), detail = shallowRef<SnapshotView | null>(null)
  const cursor = ref<string | null>(null), busy = ref(false), error = ref(''), loaded = ref(false)
  let epoch = 0, active: AbortController | undefined
  function reset() { epoch++; active?.abort(); items.value = []; detail.value = null; cursor.value = null; busy.value = false; error.value = ''; loaded.value = false }
  async function load(more = false) {
    if (busy.value || more && !cursor.value) return
    const e = ++epoch, controller = new AbortController(); active = controller
    const previous = more ? items.value : []
    if (!more) { items.value = []; detail.value = null; cursor.value = null; loaded.value = false }
    busy.value = true; error.value = ''
    try {
      const sid = snapshot()
      if (sid) { const data = await getSnapshot(identity(), trip(), sid, { signal: controller.signal }); if (e === epoch) detail.value = data }
      else {
        const page = await getSnapshots(identity(), trip(), more ? cursor.value : null, { signal: controller.signal })
        if (e === epoch) {
          if (page.items.some(x => previous.some(p => p.snapshot_id === x.snapshot_id)) || page.next_cursor && page.next_cursor === cursor.value) throw new Error('invalid page')
          items.value = [...previous, ...page.items]; cursor.value = page.next_cursor
        }
      }
      if (e === epoch) loaded.value = true
    } catch (err) { if (e === epoch) error.value = (err instanceof ApiError ? err.message : '历史数据读取失败。') + ' 未取得新的历史版本，请重新读取；游标失效时请从第一页开始。' }
    finally { if (e === epoch) busy.value = false }
  }
  watch(() => [identity(), trip(), snapshot()], () => { reset(); if (identity()) void load() }, { immediate: true })
  onUnmounted(reset)
  return { items, detail, cursor, busy, error, loaded, load, reset }
}
