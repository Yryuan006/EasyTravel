import { computed, onUnmounted, ref, shallowRef, watch, type ShallowRef } from 'vue'
import type { TripView } from '../api/contracts'
import { getTrip, TripApiError } from '../api/trips'
import { blockingText, confirmationPath, confirmTrip, getConfirmationPreview, type ConfirmationCommand, type ConfirmationPreview, type ConfirmResult } from '../api/confirmations'

export const PREVIEW_TTL_MS = 300000
export function useConfirmation(saved: ShallowRef<TripView>, token: () => string, blocked: () => boolean) {
  const preview = shallowRef<ConfirmationPreview>(), result = shallowRef<ConfirmResult>()
  const pending = shallowRef<ConfirmationCommand>()
  const busy = ref(false), acknowledged = ref(false), fresh = ref(false), error = ref(''), verified = ref(false), retryAllowed = ref(false)
  const opened = ref(false)
  let epoch = 0, controller = new AbortController(), expiry: ReturnType<typeof setTimeout> | undefined, loadedAt = 0
  const locked = computed(() => busy.value || !!pending.value || !!result.value && !verified.value)
  const current = computed(() => !!preview.value && preview.value.trip_id === saved.value.trip_id
    && preview.value.working_revision === saved.value.working?.revision && preview.value.intake_revision === saved.value.intake.intake_revision)
  const canSubmit = computed(() => acknowledged.value && fresh.value && current.value && !blocked() && !locked.value
    && !result.value && saved.value.plan_confirmation !== 'CONFIRMED' && saved.value.synchronization === 'CURRENT' && preview.value?.confirmation_allowed === true)
  function invalidate() { acknowledged.value = false; fresh.value = false; clearTimeout(expiry) }
  function cancel() { epoch++; controller.abort(); controller = new AbortController(); clearTimeout(expiry) }
  function reset() {
    cancel(); invalidate(); preview.value = undefined; result.value = undefined; pending.value = undefined
    busy.value = false; verified.value = false; retryAllowed.value = false; error.value = ''; opened.value = false
  }
  watch(token, reset, { flush: 'sync' })
  watch(() => [saved.value.trip_id, saved.value.intake.intake_revision, saved.value.working?.revision], () => {
    invalidate()
    if (!pending.value && !result.value) { cancel(); preview.value = undefined; busy.value = false }
  }, { flush: 'sync' })
  function visibility() { if (document.visibilityState === 'hidden') invalidate() }
  document.addEventListener('visibilitychange', visibility)
  onUnmounted(() => { reset(); document.removeEventListener('visibilitychange', visibility) })

  async function load() {
    if (locked.value || blocked() || !saved.value.working) return
    cancel(); invalidate(); opened.value = true; error.value = ''; const version = epoch
    busy.value = true
    try {
      const p = await getConfirmationPreview(token(), saved.value.trip_id, { signal: controller.signal })
      if (version !== epoch) return
      if (p.intake_revision !== saved.value.intake.intake_revision || p.working_revision !== saved.value.working?.revision) {
        preview.value = undefined
        throw new Error('行程版本已变化，请先重新读取行程，再查看预览。')
      }
      const costs = saved.value.working.payload.costs as { is_mock: boolean }[]
      if (costs.some(c => c.is_mock) && p.budget.verification === 'VERIFIED') throw new Error('模拟费用被标为已核验，响应不符合约定，已禁止确认。')
      preview.value = p; fresh.value = true; loadedAt = Date.now()
      expiry = setTimeout(invalidate, PREVIEW_TTL_MS)
    } catch (e) { if (version === epoch) { invalidate(); error.value = e instanceof Error ? e.message : '预览读取失败，请重试。' } }
    finally { if (version === epoch) busy.value = false }
  }

  async function verifyTrip(version: number) {
    const trip = await getTrip(token(), saved.value.trip_id, { signal: controller.signal })
    if (version !== epoch) return
    // A delayed read cannot overwrite a newer input already present in the workspace.
    if (trip.intake.intake_revision < saved.value.intake.intake_revision || (trip.working?.revision ?? 0) < (saved.value.working?.revision ?? 0)) throw new Error('读取结果已过期，请重新核对服务器行程。')
    saved.value = trip
    if (!result.value) return
    const s = result.value.snapshot
    verified.value = trip.latest_confirmed_version_id === s.snapshot.snapshot_id
      && trip.working?.revision === s.snapshot.source_working_revision && trip.plan_confirmation === 'CONFIRMED'
      && trip.intake.intake_revision === s.context?.intake_revision
    if (!verified.value) throw new Error('服务端已返回不可变快照，但当前行程已变化或指针不一致。请核对最新行程；该快照不代表当前稿已确认。')
  }

  async function refreshTrip() {
    if (busy.value) return
    cancel(); invalidate(); busy.value = true; error.value = ''; const version = epoch
    try { await verifyTrip(version) }
    catch (e) { if (version === epoch) error.value = e instanceof Error ? e.message : '读取行程失败。' }
    finally { if (version === epoch) busy.value = false }
  }

  async function submit(replay = false) {
    if (busy.value || blocked()) return
    if (!replay) {
      if (Date.now() - loadedAt >= PREVIEW_TTL_MS) invalidate()
      if (!canSubmit.value || !preview.value) return
      const p = preview.value
      pending.value = { path: confirmationPath(p.trip_id), key: crypto.randomUUID(), body: JSON.stringify({
        expected_intake_revision: p.intake_revision, expected_working_revision: p.working_revision,
        seen_disclosure_hash: p.disclosure_hash, seen_risk_hash: p.risk_hash, explicit_confirmation: true,
      }) }
    } else if (!pending.value || !retryAllowed.value) return
    if (!pending.value) return
    cancel(); invalidate(); const version = epoch; const command = pending.value
    busy.value = true; retryAllowed.value = false; error.value = ''
    try {
      const r = await confirmTrip(token(), saved.value.trip_id, command, { signal: controller.signal })
      if (version !== epoch) return
      result.value = r; pending.value = undefined
      await verifyTrip(version)
    } catch (e) {
      if (version !== epoch) return
      error.value = e instanceof Error ? e.message : '确认结果尚未确定。'
      if (e instanceof TripApiError && e.kind === 'http') {
        if (e.code && blockingText[e.code]) error.value = blockingText[e.code]!
        if (e.status === 409 && !['command_in_progress', 'idempotency_key_reused'].includes(e.code ?? '')) {
          pending.value = undefined; preview.value = undefined
          error.value += ' 请先读取最新行程，再重新查看预算与风险，手动勾选确认。'
        } else if ([401, 403, 404, 422].includes(e.status ?? 0)) {
          pending.value = undefined; preview.value = undefined
        }
        retryAllowed.value = !!pending.value && (e.status === 503 || (e.status ?? 0) >= 500 || e.code === 'command_in_progress')
      } else retryAllowed.value = !!pending.value
    } finally { if (version === epoch) busy.value = false }
  }
  return { preview, result, pending, busy, acknowledged, fresh, error, verified, retryAllowed, opened, locked, current, canSubmit, load, submit, refreshTrip, invalidate }
}
