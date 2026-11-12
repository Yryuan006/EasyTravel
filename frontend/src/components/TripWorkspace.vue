<script setup lang="ts">
import { onMounted, ref } from 'vue'
import type { RequirementsInput, TripView, AnswerRequest } from '../api/contracts'
import { useRequirements } from '../composables/useRequirements'
import TripSummary from './TripSummary.vue'
import RequirementsForm from './RequirementsForm.vue'
import ClarificationCard from './ClarificationCard.vue'
const props = defineProps<{ initial: TripView; token: string }>()
const session = useRequirements(props.initial, () => props.token)
const { saved, next, busy, error, conflict, pending, retryAllowed } = session
const editing = ref(false), paused = ref(false), notice = ref(''), formEpoch = ref(0), reviewed = ref(true)
const retainedAnswer = ref<AnswerRequest['answer']>()
onMounted(() => void session.question())
function edit() { session.edit(); editing.value = true; paused.value = false; notice.value = '' }
async function update(input: RequirementsInput) { if (await session.write('update', input)) await finished() }
async function answer(input: AnswerRequest['answer']) {
  const field = next.value?.question?.field_key
  retainedAnswer.value = input
  if (await session.write('answer', input)) {
    await finished()
    if (input.state === 'no_preference' && field && saved.value.intake.blocking_fields.includes(field)) paused.value = true
  }
}
async function finished() { editing.value = false; retainedAnswer.value = undefined; notice.value = '修改已保存到服务器。'; await session.question() }
async function retry() {
  const field = next.value?.question?.field_key, noPreference = retainedAnswer.value?.state === 'no_preference'
  if (await session.write()) {
    await finished()
    if (noPreference && field && saved.value.intake.blocking_fields.includes(field)) paused.value = true
  }
}
async function reload() {
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
  <TripSummary :trip="saved" />
  <p v-if="notice" role="status">{{ notice }}</p>
  <div v-if="error" role="alert" class="notice form-error">
    <p>{{ error }}</p><p>上方摘要为上次已读取的保存结果，可能已有新版本；本地编辑尚未保存。</p>
    <p v-if="pending">写入结果可能尚未确定；原路径、key 和内容保留在本页。修改内容再提交会使用新键。</p>
    <button v-if="retryAllowed && reviewed" :disabled="busy" @click="retry">使用原 key 和原内容重试</button>
    <button :disabled="busy" @click="reload">读取最新需求并重新审阅</button>
  </div>
  <p v-if="retainedAnswer && (conflict || !reviewed)" class="preserve">未保存的旧回答（仅供核对，不会重放）：{{ JSON.stringify(retainedAnswer) }}</p>
  <div v-if="!reviewed" class="notice"><p>下方编辑内容仍是本地输入，尚未保存。请核对最新版本 {{ saved.intake.intake_revision }}。</p><div class="actions"><button :disabled="busy" @click="review">我已对照最新需求，继续手动编辑</button><button :disabled="busy" @click="discard">放弃本地输入，使用最新需求</button></div></div>
  <RequirementsForm v-if="editing" :key="formEpoch" :initial="saved.intake.input" editing :busy="busy" :locked="conflict || !reviewed" @submit="update" />
  <template v-else-if="!conflict && reviewed">
    <ClarificationCard v-if="!paused && next?.question" :key="next.question.question_id" :question="next.question" :busy="busy" @answer="answer" @edit="edit" @pause="paused = true" />
    <p v-if="paused" role="status">旅行已保存，可稍后继续。已保存事实不会丢失。</p>
    <p v-if="next && !next.question">{{ saved.intake.collection_status === 'READY_TO_GENERATE' ? '需求已足够。此页面暂未提供生成操作；已有模拟稿仅显示只读摘要。' : '暂无下一问题，请修改或稍后补充尚缺信息。' }}</p>
  </template>
  <p v-if="busy" role="status">正在与服务器同步…</p>
  <div class="actions"><button v-if="!editing" :disabled="busy" @click="edit">修改需求</button><button v-if="editing" :disabled="busy" @click="discard">取消编辑并读取下一问题</button><button v-if="paused && !editing" :disabled="busy" @click="paused = false; session.question()">继续补充</button><button v-if="!editing && !paused && !busy && !conflict && reviewed && !next" @click="session.question()">重新读取下一问题</button></div>
</template>
