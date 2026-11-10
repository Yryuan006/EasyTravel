import { onMounted, onUnmounted, ref } from 'vue'
import { ApiError, getHealth } from '../api/health'
import type { HealthResponse } from '../api/health'

export type HealthState =
  | { status: 'loading' }
  | { status: 'success'; data: HealthResponse; checkedAt: Date }
  | { status: 'error'; message: string }

export function useHealth() {
  const state = ref<HealthState>({ status: 'loading' })
  let active: AbortController | undefined
  let disposed = false

  async function refresh() {
    active?.abort()
    const request = new AbortController()
    active = request
    state.value = { status: 'loading' }
    try {
      const data = await getHealth({ signal: request.signal })
      if (!disposed && active === request) {
        state.value = { status: 'success', data, checkedAt: new Date() }
      }
    } catch (error) {
      if (!disposed && active === request) {
        state.value = { status: 'error', message: error instanceof ApiError ? error.message : '健康检查失败，请重试。' }
      }
    }
  }

  onMounted(() => { void refresh() })
  onUnmounted(() => { disposed = true; active?.abort() })
  return { state, refresh }
}
