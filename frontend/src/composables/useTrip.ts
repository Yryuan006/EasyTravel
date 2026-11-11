import { onUnmounted, ref, shallowRef } from 'vue'
import { ApiError } from '../api/health'
import { createTrip, getTrip, TripApiError } from '../api/trips'
import type { RequirementsInput, TripView } from '../api/contracts'

export type TripState = { status: 'idle' } | { status: 'loading' } | { status: 'success'; data: TripView } | { status: 'error'; message: string }
export interface Command { key: string; body: string }
export function useTrip() {
  const state = shallowRef<TripState>({ status: 'idle' })
  const pending = shallowRef<Command>()
  const retryAllowed = ref(false)
  let epoch = 0
  let active: AbortController | undefined
  function reset() { epoch++; active?.abort(); active = undefined; state.value = { status: 'idle' }; pending.value = undefined; retryAllowed.value = false }
  async function load(token: string, id: string) {
    active?.abort()
    const controller = new AbortController(); active = controller
    const generation = ++epoch
    state.value = { status: 'loading' }
    try { const data = await getTrip(token, id, { signal: controller.signal }); if (epoch === generation) state.value = { status: 'success', data } }
    catch (e) { if (epoch === generation) state.value = { status: 'error', message: e instanceof ApiError ? e.message : '读取失败，请重试。' } }
  }
  async function save(token: string, input?: RequirementsInput) {
    if (state.value.status === 'loading') return
    const body = input ? JSON.stringify({ requirements: input }) : pending.value?.body
    if (!body) return
    const command = pending.value?.body === body ? pending.value : { key: crypto.randomUUID(), body }
    pending.value = command
    const generation = ++epoch
    retryAllowed.value = false
    state.value = { status: 'loading' }
    // Do not expose a write-cancel action: aborting the client cannot undo a commit.
    const controller = new AbortController(); active = controller
    try {
      const data = await createTrip(token, command, { signal: controller.signal })
      if (epoch === generation) { state.value = { status: 'success', data }; pending.value = undefined }
    } catch (e) {
      if (epoch !== generation) return
      retryAllowed.value = !(e instanceof TripApiError && e.code === 'idempotency_key_reused')
      state.value = { status: 'error', message: e instanceof ApiError ? e.message : '创建结果尚未确定，请使用原请求重试。' }
    }
  }
  onUnmounted(reset)
  return { state, pending, retryAllowed, reset, load, save }
}
