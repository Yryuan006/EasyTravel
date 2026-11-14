import { computed, onUnmounted, ref, shallowRef, watch, type ShallowRef } from 'vue'
import type { TripView } from '../api/contracts'
import { getTrip, TripApiError } from '../api/trips'
import { canRetry, generate, getRun, getGenerationState, type GenerationRun, type GenerationState } from '../api/generations'
export function useGeneration(saved: ShallowRef<TripView>, token: () => string) {
  const run = shallowRef<GenerationRun>(), projection = shallowRef<GenerationState>()
  const pending = shallowRef<{ key: string; body: string }>()
  const busy = ref(false), error = ref(''), ready = ref(false)
  let epoch = 0, controller = new AbortController(), timer: ReturnType<typeof setTimeout> | undefined
  function cancel() { epoch++; controller.abort(); controller = new AbortController(); clearTimeout(timer); busy.value = false }
  onUnmounted(cancel)
  const active = computed(() => run.value?.status === 'ACCEPTED' || run.value?.status === 'RUNNING')
  const eligible = computed(() => ready.value && !error.value && !pending.value && !busy.value && !active.value && !saved.value.active_run_id && !saved.value.working && saved.value.intake.collection_status === 'READY_TO_GENERATE')
  const retryable = computed(() => !!run.value && run.value.status === 'FAILED' && run.value.intake_revision === saved.value.intake.intake_revision && canRetry(run.value))
  function poll(id: string, version: number) {
    clearTimeout(timer)
    timer = setTimeout(async () => {
      try {
        const next = await getRun(token(), saved.value.trip_id, id, { signal: controller.signal })
        if (version !== epoch) return
        run.value = next
        if (next.status === 'ACCEPTED' || next.status === 'RUNNING') poll(id, version)
        else await refresh()
      } catch (e) { if (version === epoch) error.value = e instanceof Error ? e.message : '运行读取失败，请恢复查询。' }
    }, 350)
  }
  async function refresh() {
    cancel(); const version = epoch
    busy.value = true; ready.value = false; error.value = ''
    try {
      const trip = await getTrip(token(), saved.value.trip_id, { signal: controller.signal })
      if (version !== epoch) return
      const state = await getGenerationState(token(), trip.trip_id, { signal: controller.signal })
      if (version !== epoch) return
      if (state.working_revision !== (trip.working?.revision ?? null)) throw new Error('工作稿版本已变化，请重新读取。')
      // Keep requirements writes authoritative: never replace a newer local revision.
      if (trip.intake.intake_revision < saved.value.intake.intake_revision || (trip.working?.revision ?? 0) < (saved.value.working?.revision ?? 0)) throw new Error('读取结果已过期，请重新读取。')
      saved.value = trip; projection.value = state; run.value = state.latest_run ?? undefined; ready.value = true
      if (active.value && run.value) poll(run.value.run_id, version)
    } catch (e) { if (version === epoch) error.value = e instanceof Error ? e.message : '读取生成状态失败。' }
    finally { if (version === epoch) busy.value = false }
  }
  async function start(replay = false) {
    if (busy.value || active.value || saved.value.working) return
    if (!replay && (!eligible.value || (run.value?.status === 'FAILED' && run.value.intake_revision === saved.value.intake.intake_revision && !retryable.value))) return
    if (!replay) pending.value = { key: crypto.randomUUID(), body: JSON.stringify({ expected_intake_revision: saved.value.intake.intake_revision, expected_working_revision: null, retry_of: retryable.value ? run.value!.run_id : null }) }
    if (!pending.value) return
    cancel(); const version = epoch; busy.value = true; error.value = ''
    try {
      const accepted = await generate(token(), saved.value.trip_id, pending.value, { signal: controller.signal })
      if (version !== epoch) return
      pending.value = undefined
      // A replay is the original ACCEPTED receipt. Always GET its current status.
      run.value = accepted; ready.value = true; poll(accepted.run_id, version)
    } catch (e) {
      if (version === epoch) {
        error.value = e instanceof Error ? e.message : '请求结果尚未确定，请使用原请求重试。'
        // These conflicts occur only after receipt replay: the key was not accepted.
        if (e instanceof TripApiError && e.status === 409 && ['stale_intake', 'requirements_incomplete', 'scope_replan_not_supported', 'generation_in_progress', 'invalid_retry', 'explicit_retry_required'].includes(e.code ?? '')) {
          pending.value = undefined; ready.value = false
          error.value += ' 请重新读取行程与运行状态，审阅当前需求。'
        }
      }
    }
    finally { if (version === epoch) busy.value = false }
  }
  watch(() => saved.value.intake.intake_revision, () => { cancel(); projection.value = undefined; ready.value = false; void refresh() })
  return { run, projection, pending, busy, error, ready, active, eligible, retryable, refresh, start }
}
