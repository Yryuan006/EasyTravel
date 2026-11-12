import { onUnmounted, ref, shallowRef } from 'vue'
import { getTrip, getNextQuestion, updateRequirements, answerQuestion, TripApiError } from '../api/trips'
import type { AnswerRequest, RequirementsInput, TripView, NextQuestionResponse } from '../api/contracts'

interface Command { key: string; body: string; path: string; kind: 'update' | 'answer'; questionId?: string | undefined }
export function useRequirements(initial: TripView, token: () => string) {
  const saved = shallowRef(initial)
  const next = shallowRef<NextQuestionResponse>()
  const busy = ref(false), error = ref(''), conflict = ref(false), retryAllowed = ref(false)
  const pending = shallowRef<Command>()
  let epoch = 0, active: AbortController | undefined
  function reset() { epoch++; active?.abort(); pending.value = undefined; next.value = undefined }
  onUnmounted(reset)
  async function question() {
    const generation = ++epoch
    active?.abort(); active = new AbortController()
    next.value = undefined; busy.value = true; error.value = ''
    try {
      const data = await getNextQuestion(token(), saved.value.trip_id, { signal: active.signal })
      if (generation !== epoch) return
      if (data.intake_revision !== saved.value.intake.intake_revision) {
        conflict.value = true; error.value = '需求版本已变化，请读取最新需求并重新审阅。'; return
      }
      next.value = data
    } catch (e) { if (generation === epoch) error.value = e instanceof Error ? e.message : '读取问题失败。' }
    finally { if (generation === epoch) busy.value = false }
  }
  function edit() {
    epoch++; active?.abort(); busy.value = false; next.value = undefined
    // Editing invalidates any answer retry; it must never cross into another intent.
    if (pending.value?.kind === 'answer') { pending.value = undefined; retryAllowed.value = false }
  }
  async function reload() {
    const generation = ++epoch
    active?.abort(); active = new AbortController(); busy.value = true; next.value = undefined
    error.value = ''; pending.value = undefined; retryAllowed.value = false
    try {
      const data = await getTrip(token(), saved.value.trip_id, { signal: active.signal })
      if (generation !== epoch) return
      saved.value = data; conflict.value = false
    } catch (e) { if (generation === epoch) error.value = e instanceof Error ? e.message : '读取失败。' }
    finally { if (generation === epoch) busy.value = false }
  }
  async function write(kind?: 'update' | 'answer', input?: RequirementsInput | AnswerRequest['answer']) {
    if (busy.value || conflict.value) return false
    let command = pending.value
    if (kind) {
      const q = next.value?.question
      if (kind === 'answer' && (!q || q.intake_revision !== saved.value.intake.intake_revision)) return false
      const versions = { expected_intake_revision: saved.value.intake.intake_revision, expected_working_revision: saved.value.working?.revision ?? null }
      const body = JSON.stringify(kind === 'update' ? { ...versions, requirements: input } : { ...versions, field_key: q!.field_key, answer: input })
      const path = kind === 'update' ? `/api/v1/trips/${saved.value.trip_id}/requirements` : `/api/v1/trips/${saved.value.trip_id}/clarifications/${q!.question_id}/answers`
      command = command?.body === body && command.path === path ? command : { key: crypto.randomUUID(), body, path, kind, questionId: q?.question_id }
    }
    if (!command) return false
    pending.value = command; busy.value = true; error.value = ''; retryAllowed.value = false
    const generation = ++epoch
    active?.abort(); active = new AbortController()
    try {
      const data = await (command.kind === 'update' ? updateRequirements(token(), saved.value.trip_id, command, { signal: active.signal }) : answerQuestion(token(), saved.value.trip_id, command.questionId!, command, { signal: active.signal }))
      if (generation !== epoch) return false
      saved.value = data; next.value = undefined; pending.value = undefined
      return true
    } catch (e) {
      if (generation !== epoch) return false
      error.value = e instanceof Error ? e.message : '保存结果尚未确定。'
      conflict.value = e instanceof TripApiError && e.status === 409 && e.code !== 'command_in_progress' && e.code !== 'idempotency_key_reused'
      if (conflict.value) { next.value = undefined; error.value += ' 请读取最新需求并重新审阅；本地输入已保留。' }
      retryAllowed.value = !conflict.value && !(e instanceof TripApiError && (e.code === 'idempotency_key_reused' || e.kind === 'http' && e.status !== 503 && e.code !== 'command_in_progress'))
      return false
    } finally { if (generation === epoch) busy.value = false }
  }
  return { saved, next, busy, error, conflict, pending, retryAllowed, question, edit, reload, write }
}
