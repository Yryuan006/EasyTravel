import { computed, onUnmounted, ref, shallowRef, watch, type ShallowRef } from 'vue'
import type { TripView } from '../api/contracts'
import { getTrip, TripApiError } from '../api/trips'
import { getRun, getGenerationState, type GenerationRun } from '../api/generations'
import { getProposal, sendChange, tripPath, type ChangeCommand, type ProposalView, type DecisionResult } from '../api/changes'

export function useChanges(saved: ShallowRef<TripView>, token: () => string, blocked: () => boolean) {
  const intent = ref(''), agreed = ref(false), busy = ref(false), error = ref(''), retryAllowed = ref(false)
  const run = shallowRef<GenerationRun>(), proposal = shallowRef<ProposalView>(), pending = shallowRef<ChangeCommand>(), receipt = shallowRef<DecisionResult>()
  const awaitingRead = ref(false), verified = ref(false)
  let epoch = 0, controller = new AbortController(), timer: ReturnType<typeof setTimeout> | undefined
  const active = computed(() => run.value?.status === 'ACCEPTED' || run.value?.status === 'RUNNING')
  const locked = computed(() => busy.value || active.value || !!pending.value || awaitingRead.value)
  const current = computed(() => proposal.value?.state === 'PENDING' && saved.value.synchronization === 'CURRENT'
    && proposal.value.intake_revision === saved.value.intake.intake_revision && proposal.value.proposal.output.base_working_revision === saved.value.working?.revision)
  const canDecide = computed(() => current.value && !locked.value && !blocked())
  function cancel() { epoch++; controller.abort(); controller = new AbortController(); clearTimeout(timer) }
  function reset() { cancel(); intent.value = ''; agreed.value = false; busy.value = false; error.value = ''; retryAllowed.value = false; run.value = undefined; proposal.value = undefined; pending.value = undefined; receipt.value = undefined; awaitingRead.value = false; verified.value = false }
  watch(token, reset, { flush: 'sync' })
  watch(() => [saved.value.trip_id, saved.value.intake.intake_revision, saved.value.working?.revision], () => { agreed.value = false; verified.value = false }, { flush: 'sync' })
  onUnmounted(reset)
  async function readBack(version: number) {
    const trip = await getTrip(token(), saved.value.trip_id, { signal: controller.signal })
    if (version !== epoch) return
    if (trip.intake.intake_revision < saved.value.intake.intake_revision || (trip.working?.revision ?? 0) < (saved.value.working?.revision ?? 0)) throw new Error('读取结果已过期，请重新读取。')
    saved.value = trip
    if (receipt.value) {
      const p = await getProposal(token(), trip.trip_id, receipt.value.proposal_id, { signal: controller.signal })
      if (version !== epoch) return
      proposal.value = p
      // A successful read settles the receipt; a newer current version makes it history.
      awaitingRead.value = false
      if (p.state !== receipt.value.state || saved.value.working?.revision !== receipt.value.working_revision || saved.value.latest_confirmed_version_id !== receipt.value.latest_confirmed_version_id) throw new Error('已收到决定回执，但当前版本已变化。回执保留为历史事实，请重新审阅当前稿。')
      verified.value = true
    }
  }
  async function inspectRun(id: string, version: number) {
    const r = await getRun(token(), saved.value.trip_id, id, { signal: controller.signal })
    if (version !== epoch) return
    run.value = r
    if (r.status === 'ACCEPTED' || r.status === 'RUNNING') {
      timer = setTimeout(() => { void inspectRun(id, version).catch(e => { if (version === epoch) error.value = e instanceof Error ? e.message : '读取失败。' }) }, 350)
    } else if (r.status === 'SUCCEEDED' && r.result?.proposal_id) {
      const p = await getProposal(token(), saved.value.trip_id, r.result.proposal_id, { signal: controller.signal })
      if (version === epoch) { proposal.value = p; agreed.value = false }
    } else if (r.status === 'FAILED') error.value = r.error?.code === 'unsupported_change_intent' ? 'Mock 未识别此修改，请按支持的示例改写。' : '修改运行失败，未应用候选。请查看运行状态并重试。'
  }
  async function refresh() {
    if (busy.value) return
    cancel(); const version = epoch; busy.value = true; error.value = ''; agreed.value = false
    try {
      await readBack(version)
      if (version !== epoch || awaitingRead.value || receipt.value) return
      const s = await getGenerationState(token(), saved.value.trip_id, { signal: controller.signal })
      if (version !== epoch) return
      if (s.latest_run?.kind === 'CHANGE_INTENT') await inspectRun(s.latest_run.run_id, version)
    } catch (e) { if (version === epoch) error.value = e instanceof Error ? e.message : '读取失败。' }
    finally { if (version === epoch) busy.value = false }
  }
  async function send(command: ChangeCommand) {
    cancel(); const version = epoch; busy.value = true; error.value = ''; retryAllowed.value = false; agreed.value = false
    try {
      const r = await sendChange(token(), saved.value.trip_id, command, { signal: controller.signal })
      if (version !== epoch) return
      pending.value = undefined
      if (command.kind === 'intent') { proposal.value = undefined; receipt.value = undefined; await inspectRun((r as GenerationRun).run_id, version) }
      else { receipt.value = r as DecisionResult; awaitingRead.value = true; await readBack(version) }
    } catch (e) {
      if (version !== epoch) return
      error.value = e instanceof Error ? e.message : '请求结果尚未确定。'
      if (e instanceof TripApiError && e.kind === 'http' && e.status && e.status < 500 && e.code !== 'command_in_progress') {
        if (e.code !== 'idempotency_key_reused') pending.value = undefined
        error.value += ' 请重新读取提案并手动审阅。'
        if (e.status === 409 && proposal.value) proposal.value = { ...proposal.value, state: 'STALE' }
      } else retryAllowed.value = !!pending.value
    } finally { if (version === epoch) busy.value = false }
  }
  async function start() {
    if (locked.value || blocked() || !intent.value.trim() || saved.value.synchronization !== 'CURRENT' || !saved.value.working || saved.value.active_run_id) return
    pending.value = { kind: 'intent', path: tripPath(saved.value.trip_id) + '/change-intents', key: crypto.randomUUID(), body: JSON.stringify({ expected_intake_revision: saved.value.intake.intake_revision, expected_working_revision: saved.value.working.revision, intent: intent.value.trim(), retry_of: run.value?.status === 'FAILED' && run.value.intake_revision === saved.value.intake.intake_revision && run.value.base_working_revision === saved.value.working.revision ? run.value.run_id : null }) }
    await send(pending.value)
  }
  async function decide(decision: 'approve' | 'reject') {
    if (!canDecide.value || !proposal.value || (decision === 'approve' && !agreed.value)) return
    const p = proposal.value
    if (decision === 'approve' && (p.budget.constraint === 'OVER_LIMIT' || p.risks.some(r => r.severity === 'blocker'))) return
    const body = { expected_intake_revision: p.intake_revision, expected_working_revision: p.proposal.output.base_working_revision, seen_candidate_hash: p.proposal.candidate_hash, decision,
      ...(decision === 'approve' ? { item_ids: p.proposal.touched_item_ids, explicit_authorization: p.requires_specific_authorization } : {}) }
    pending.value = { kind: 'decision', path: tripPath(saved.value.trip_id) + '/proposals/' + p.proposal.proposal_id + '/decisions', key: crypto.randomUUID(), body: JSON.stringify(body) }
    await send(pending.value)
  }
  async function retry() { if (!busy.value && pending.value && retryAllowed.value && !blocked()) await send(pending.value) }
  return { intent, agreed, busy, error, run, proposal, pending, receipt, awaitingRead, verified, locked, current, canDecide, retryAllowed, start, decide, retry, refresh }
}
