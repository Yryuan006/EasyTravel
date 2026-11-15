<script setup lang="ts">
import { computed } from 'vue'
import { useHistory } from '../composables/useHistory'
import { labels, fieldKeys } from '../api/contracts'
import { money, type PlanItem, type CostLine } from '../api/generations'
import ItineraryView from './ItineraryView.vue'
import BudgetSummary from './BudgetSummary.vue'
import CostBreakdown from './CostBreakdown.vue'
import RiskList from './RiskList.vue'
const props = defineProps<{ token: string; tripId: string; snapshotId?: string | undefined }>()
defineEmits<{ navigate: [url: string] }>()
const { items, detail, cursor, busy, error, loaded, load } = useHistory(() => props.token, () => props.tripId, () => props.snapshotId)
const planItems = computed(() => (detail.value?.snapshot.payload.items ?? []) as PlanItem[])
const costs = computed(() => (detail.value?.snapshot.payload.costs ?? []) as CostLine[])
function time(at: string) { return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'medium' }).format(new Date(at)) }
function answer(value: unknown, key: string) {
  if (key.endsWith('_cents') && typeof value === 'number') return money(value)
  if (typeof value === 'object' && value !== null) {
    const dates = value as { mode: string; start_date?: string; end_date?: string; earliest_start?: string; latest_end?: string }
    return dates.mode === 'fixed' ? `${dates.start_date} 至 ${dates.end_date}` : `${dates.earliest_start} 至 ${dates.latest_end}（可选日期范围）`
  }
  return String(value)
}
</script>
<template>
  <section class="trip-panel history-page">
    <h2>{{ snapshotId ? '历史确认版本详情' : '确认历史' }}</h2>
    <p class="notice">历史确认快照 · 只读。当前工作稿可以继续修改；有历史确认记录不代表当前工作稿已确认。</p>
    <p>整体确认不等于预订、支付、出票或出行准备就绪。未预订（booked=false），就绪情况未评估（NOT_ASSESSED）。</p>
    <div class="actions">
      <a :href="`/trips/${tripId}`" @click.prevent="$emit('navigate', `/trips/${tripId}`)">返回当前工作区</a>
      <a v-if="snapshotId" :href="`/trips/${tripId}/history`" @click.prevent="$emit('navigate', `/trips/${tripId}/history`)">返回确认历史列表</a>
    </div>
    <p v-if="busy" role="status">正在读取历史确认事实…</p>
    <div v-if="error" role="alert"><p>{{ error }}</p><button :disabled="busy" @click="load()">重新读取历史</button></div>
    <template v-if="!snapshotId">
      <p v-if="loaded && !items.length">此旅行尚无历史确认版本。</p>
      <ol class="history-list">
        <li v-for="item in items" :key="item.snapshot_id">
          <a :href="`/trips/${tripId}/history/${item.snapshot_id}`" @click.prevent="$emit('navigate', `/trips/${tripId}/history/${item.snapshot_id}`)">查看历史版本 · 来源 r{{ item.source_working_revision }}</a>
          <p>{{ time(item.confirmed_at) }}（Asia/Shanghai）</p><small>快照 {{ item.snapshot_id }}</small>
          <p>确认时已知费用 {{ money(item.budget.known_subtotal_cents) }} · 总额 {{ money(item.budget.total_cents) }}</p>
          <p>{{ item.budget.verification }} · {{ item.budget.constraint }} · 未知费用 {{ item.budget.unknown_cost_ids.length }} 项</p>
        </li>
      </ol>
      <button v-if="cursor" :disabled="busy" @click="load(true)">加载更多历史版本</button>
    </template>
    <template v-if="detail">
      <h3>历史快照 {{ detail.snapshot.snapshot_id }}</h3>
      <p>来源工作稿 r{{ detail.snapshot.source_working_revision }} · {{ time(detail.snapshot.confirmed_at) }}（Asia/Shanghai）</p>
      <p>确认人：{{ detail.snapshot.confirmed_by }}</p>
      <h3>确认时的需求</h3>
      <p>{{ detail.snapshot.requirements.destination }} · {{ detail.snapshot.requirements.start_date }} 至 {{ detail.snapshot.requirements.end_date }} · 需求版本 {{ detail.snapshot.requirements.requirements_revision }}</p>
      <p>当时硬预算上限：{{ money(detail.snapshot.requirements.hard_limit_cents) }}</p>
      <template v-if="detail.context">
        <p>需求来源：已保存输入 i{{ detail.context.intake_revision }}；原始文本：{{ detail.context.input.raw_text ?? '未记录' }}</p>
        <dl class="history-input"><template v-for="key in fieldKeys" :key="key"><dt>{{ labels[key] }}</dt><dd>{{ detail.context.input[key].state === 'provided' ? answer(detail.context.input[key].value, key) : detail.context.input[key].state === 'no_preference' ? '用户明确无偏好' : '未回答' }}</dd></template></dl>
      </template>
      <p v-else>需求来源与生成上下文：此历史版本未记录相关信息。</p>
    </template>
  </section>
  <template v-if="detail">
    <BudgetSummary :budget="detail.snapshot.budget" :hard-limit="detail.snapshot.requirements.hard_limit_cents" historical />
    <ItineraryView :items="planItems" :costs="costs" historical />
    <section class="trip-panel">
      <CostBreakdown :items="planItems" :costs="costs" />
      <h3>确认时保存的风险披露</h3>
      <RiskList v-if="detail.risk_disclosure" :notes="{ risks: detail.risk_disclosure.risks, transport_advice: [] }" />
      <p v-else>风险披露：此历史版本未记录相关信息；无法推断用户曾经审阅过哪些风险。</p>
      <template v-if="detail.context"><h3>当时生成的补充说明（不代表用户风险审阅记录）</h3><RiskList :notes="detail.context.notes" /></template>
    </section>
  </template>
</template>
<style scoped>
.history-page { overflow-wrap: anywhere; }
.history-list { padding-inline-start: 1.3rem; }
.history-list li { padding-block: 1.2rem; border-bottom: 1px solid var(--line, #cbc7ba); }
.history-input { display: grid; grid-template-columns: minmax(5rem, 1fr) minmax(0, 3fr); gap: .6rem 1rem; }
.history-input dd { margin: 0; overflow-wrap: anywhere; }
</style>
