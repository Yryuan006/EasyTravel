<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { RequirementsInput, TripView, AnswerRequest } from '../api/contracts'
import { useRequirements } from '../composables/useRequirements'
import { useGeneration } from '../composables/useGeneration'
import { type PlanItem, type CostLine, type Notes } from '../api/generations'
import ItineraryView from './ItineraryView.vue'
import BudgetSummary from './BudgetSummary.vue'
import CostBreakdown from './CostBreakdown.vue'
import RiskList from './RiskList.vue'
import GenerationStatus from './GenerationStatus.vue'
import ConfirmationReview from './ConfirmationReview.vue'
import ChangeReview from './ChangeReview.vue'
import { useChanges } from '../composables/useChanges'
import { useConfirmation } from '../composables/useConfirmation'
import TripSummary from './TripSummary.vue'
import RequirementsForm from './RequirementsForm.vue'
import ClarificationCard from './ClarificationCard.vue'
defineEmits<{ navigate: [url: string] }>()
const props = defineProps<{ initial: TripView; token: string }>()
const session = useRequirements(props.initial, () => props.token)
const { saved, next, busy, error, conflict, pending, retryAllowed } = session
const editing = ref(false), paused = ref(false), notice = ref(''), formEpoch = ref(0), reviewed = ref(true)
const retainedAnswer = ref<AnswerRequest['answer']>()
const generation = useGeneration(saved, () => props.token)
const { run, projection, pending: generationPending, busy: generationBusy, error: generationError, ready: generationReady, active: generationActive, eligible, retryable } = generation
const confirmationBlocked = computed(() => busy.value || editing.value || !reviewed.value || conflict.value || !!pending.value || generationBusy.value || generationActive.value || !!generationPending.value || changes.locked.value)
const confirmation = useConfirmation(saved, () => props.token, () => confirmationBlocked.value)
const changeBlocked = computed(() => busy.value || editing.value || !reviewed.value || conflict.value || !!pending.value || confirmation.locked.value || generationBusy.value || !!generationPending.value)
const changes = useChanges(saved, () => props.token, () => changeBlocked.value)
const confirmationLocked = computed(() => confirmation.locked.value || changes.locked.value)
const items = computed(() => (saved.value.working?.payload.items ?? []) as PlanItem[])
const costs = computed(() => (saved.value.working?.payload.costs ?? []) as CostLine[])
const notes = computed(() => (saved.value.working?.context as { notes: Notes } | null)?.notes ?? null)
const sourceBudget = computed(() => projection.value?.working_revision === saved.value.working?.revision ? projection.value?.source_run?.diagnostics_budget : null)
const hardLimit = computed(() => Number(saved.value.working?.requirements.hard_limit_cents ?? saved.value.intake.input.hard_limit_cents.value))
onMounted(() => { void session.question(); void generation.refresh() })
function edit() { if (confirmationLocked.value) return; confirmation.invalidate(); session.edit(); editing.value = true; paused.value = false; notice.value = '' }
async function update(input: RequirementsInput) { if (confirmationLocked.value) return; if (await session.write('update', input)) await finished() }
async function answer(input: AnswerRequest['answer']) {
  if (confirmationLocked.value) return
  const field = next.value?.question?.field_key
  retainedAnswer.value = input
  if (await session.write('answer', input)) {
    await finished()
    if (input.state === 'no_preference' && field && saved.value.intake.blocking_fields.includes(field)) paused.value = true
  }
}
async function finished() { editing.value = false; retainedAnswer.value = undefined; notice.value = '修改已保存到服务器。'; await session.question() }
async function retry() {
  if (confirmationLocked.value) return
  const field = next.value?.question?.field_key, noPreference = retainedAnswer.value?.state === 'no_preference'
  if (await session.write()) {
    await finished()
    if (noPreference && field && saved.value.intake.blocking_fields.includes(field)) paused.value = true
  }
}
async function reload() {
  if (confirmationLocked.value) return
  await session.reload()
  if (!error.value) {
    reviewed.value = false
    notice.value = '已读取最新需求。请对照下方已保存摘要审阅本地输入；不会自动重放旧回答。'
  }
}
function review() { reviewed.value = true; retainedAnswer.value = undefined; notice.value = ''; if (!editing.value) void session.question() }
function discard() { formEpoch.value++; reviewed.value = true; notice.value = ''; retainedAnswer.value = undefined; editing.value = false; void session.question() }
</script>
<template>
  <div class="actions"><a :href="`/trips/${saved.trip_id}/history`" @click.prevent="$emit('navigate', `/trips/${saved.trip_id}/history`)">查看确认历史</a></div>
  <section class="trip-panel" aria-labelledby="generation-heading">
    <p class="eyebrow">MOCK PLANNER / 模拟规划</p><h2 id="generation-heading">生成与运行</h2>
    <p v-if="saved.intake.collection_status !== 'READY_TO_GENERATE'">需求尚不完整，请补充下方必要信息。</p>
    <p v-if="saved.working">当前工作稿{{ saved.plan_confirmation === 'CONFIRMED' ? '已确认规划' : '未确认' }}，已有工作稿不能直接覆盖；新需求需经重规划提案审阅与 L3 授权后应用。</p>
    <GenerationStatus :run="run" />
    <p v-if="generationError" role="alert" class="notice form-error">{{ generationError }} 已显示的数据为上次读取结果，可能已有变化。</p>
    <p v-if="generationBusy" role="status">正在同步生成状态…</p>
    <div class="actions">
      <button v-if="eligible && !confirmationLocked && !editing && !busy && !conflict && reviewed && (!run || run.status !== 'FAILED' || run.intake_revision !== saved.intake.intake_revision || retryable)" class="primary" @click="generation.start()">{{ retryable ? '显式重试模拟生成' : '生成模拟行程' }}</button>
      <button v-if="generationPending && !saved.working && !generationActive" :disabled="generationBusy || busy || confirmationLocked" @click="generation.start(true)">使用原生成请求重试（同一幂等键）</button>
      <button :disabled="generationBusy || busy || confirmationLocked" @click="generation.refresh()">重新读取行程与运行状态</button>
    </div>
    <p v-if="generationPending">生成请求结果可能尚未确定，原键及内容保留在当前页面。请恢复查询或使用原请求重试，不要创建新请求。</p>
  </section>
  <ConfirmationReview v-if="saved.working" :trip="saved" :session="confirmation" :blocked="confirmationBlocked" />
  <ChangeReview v-if="saved.working" :trip="saved" :session="changes" :blocked="changeBlocked" />
  <template v-if="saved.working">
    <p v-if="saved.synchronization === 'STALE'" class="notice">STALE：以下是旧需求下的工作稿与预算，不能作为当前需求的有效结果。</p>
    <BudgetSummary v-if="sourceBudget" :budget="sourceBudget" :hard-limit="hardLimit" :current-limit="saved.intake.input.hard_limit_cents.value" />
    <p v-else class="notice">{{ generationReady ? '无法定位当前工作稿的成功来源预算，不推断总额。' : '正在读取来源预算；已有显示可能过期。' }}</p>
    <ItineraryView :items="items" :costs="costs" />
    <section class="trip-panel"><CostBreakdown :items="items" :costs="costs" /><RiskList :notes="notes" /></section>
  </template>
  <BudgetSummary v-else-if="run?.status === 'FAILED' && run.intake_revision === saved.intake.intake_revision && run.diagnostics_budget" :budget="run.diagnostics_budget" :hard-limit="hardLimit" />
  <TripSummary :trip="saved" />
  <p v-if="notice" role="status">{{ notice }}</p>
  <div v-if="error" role="alert" class="notice form-error">
    <p>{{ error }}</p><p>上方摘要为上次已读取的保存结果，可能已有新版本；本地编辑尚未保存。</p>
    <p v-if="pending">写入结果可能尚未确定；原路径、key 和内容保留在本页。修改内容再提交会使用新键。</p>
    <button v-if="retryAllowed && reviewed" :disabled="busy || confirmationLocked" @click="retry">使用原 key 和原内容重试</button>
    <button :disabled="busy || confirmationLocked" @click="reload">读取最新需求并重新审阅</button>
  </div>
  <p v-if="retainedAnswer && (conflict || !reviewed)" class="preserve">未保存的旧回答（仅供核对，不会重放）：{{ JSON.stringify(retainedAnswer) }}</p>
  <div v-if="!reviewed" class="notice"><p>下方编辑内容仍是本地输入，尚未保存。请核对最新版本 {{ saved.intake.intake_revision }}。</p><div class="actions"><button :disabled="busy || confirmationLocked" @click="review">我已对照最新需求，继续手动编辑</button><button :disabled="busy || confirmationLocked" @click="discard">放弃本地输入，使用最新需求</button></div></div>
  <RequirementsForm v-if="editing" :key="formEpoch" :initial="saved.intake.input" editing :busy="busy || confirmationLocked" :locked="conflict || !reviewed || confirmationLocked" @submit="update" />
  <template v-else-if="!conflict && reviewed">
    <ClarificationCard v-if="!paused && next?.question" :key="next.question.question_id" :question="next.question" :busy="busy || confirmationLocked" @answer="answer" @edit="edit" @pause="paused = true" />
    <p v-if="paused" role="status">旅行已保存，可稍后继续。已保存事实不会丢失。</p>
    <p v-if="next && !next.question">{{ saved.intake.collection_status === 'READY_TO_GENERATE' ? '需求已足够，可以生成模拟行程。' : '暂无下一问题，请修改或稍后补充尚缺信息。' }}</p>
  </template>
  <p v-if="busy" role="status">正在与服务器同步…</p>
  <div class="actions"><button v-if="!editing" :disabled="busy || confirmationLocked" @click="edit">修改需求</button><button v-if="editing" :disabled="busy || confirmationLocked" @click="discard">取消编辑并读取下一问题</button><button v-if="paused && !editing" :disabled="busy || confirmationLocked" @click="paused = false; session.question()">继续补充</button><button v-if="!editing && !paused && !busy && !conflict && reviewed && !next" @click="session.question()">重新读取下一问题</button></div>
</template>
