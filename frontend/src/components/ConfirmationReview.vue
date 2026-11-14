<script setup lang="ts">
import { computed } from 'vue'
import type { TripView } from '../api/contracts'
import type { useConfirmation } from '../composables/useConfirmation'
import { blockingText } from '../api/confirmations'
import { money, type CostLine, type PlanItem } from '../api/generations'
import BudgetSummary from './BudgetSummary.vue'
import CostBreakdown from './CostBreakdown.vue'
import RiskList from './RiskList.vue'

const props = defineProps<{ trip: TripView; session: ReturnType<typeof useConfirmation>; blocked: boolean }>()
const { preview, result, pending, busy, acknowledged, fresh, error, verified, retryAllowed, opened, locked, current, canSubmit } = props.session
const items = computed(() => (props.trip.working?.payload.items ?? []) as PlanItem[])
const costs = computed(() => (props.trip.working?.payload.costs ?? []) as CostLine[])
const sourceLimit = computed(() => Number(props.trip.working?.requirements.hard_limit_cents))
const confirmedAt = computed(() => result.value ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'medium' }).format(new Date(result.value.snapshot.snapshot.confirmed_at)) : '')
</script>
<template>
  <section class="trip-panel confirmation-review" aria-labelledby="confirmation-heading" :aria-busy="busy">
    <h2 id="confirmation-heading">确认旅行规划</h2>
    <p>审阅预算、未知费用和全部风险后，明确确认当前工作草案。整体确认不代表酒店授权、预订、购票或支付。</p>
    <p class="preserve">Trip ID：{{ trip.trip_id }}<br>当前工作草案 r{{ trip.working?.revision }} · intake revision {{ trip.intake.intake_revision }}</p>
    <p v-if="trip.plan_confirmation === 'CONFIRMED'" class="confirmation-status" role="status">当前行程已确认规划（CONFIRMED）。快照：<span class="preserve">{{ trip.latest_confirmed_version_id }}</span>。尚未预订，readiness=NOT_ASSESSED。</p>
    <div class="actions">
      <button :disabled="locked || blocked" @click="session.load()">{{ opened ? '重新读取确认预览' : '查看预算与风险，确认当前行程' }}</button>
      <button v-if="opened" :disabled="busy" @click="session.refreshTrip()">读取最新行程并核对确认状态</button>
    </div>
    <p v-if="busy" role="status">{{ pending ? '正在提交原确认请求，请勿重复操作…' : '正在从服务器读取并核对…' }}</p>
    <div v-if="error" role="alert" class="notice form-error"><p>{{ error }}</p><p v-if="result">服务端已返回快照；当前行程指针尚未核对成功，请只重试读取。</p></div>
    <div v-if="pending" class="notice" role="status">
      <p>未收到有效确认响应，不代表数据库没有确认成功。原路径、Idempotency-Key 和请求内容保留在本页；请恢复原请求，不要用新键重复确认。</p>
      <p>恢复完成前已暂停需求修改。清除凭证或离开页面会丢失本页内存中的请求；再次进入须先读取服务器确认状态。</p>
      <button v-if="retryAllowed" :disabled="busy || blocked" @click="session.submit(true)">使用原确认请求安全重试</button>
    </div>
    <template v-if="preview && current">
      <p v-if="!fresh" class="notice" role="status">预览已失效，原勾选已清除。请重新读取预算与风险。预览有效期为五分钟；切换页面后需重新审阅。</p>
      <div class="confirmation-budget">
        <BudgetSummary heading-id="confirmation-budget-heading" :budget="preview.budget" :hard-limit="sourceLimit" />
      </div>
      <div v-if="preview.pending_requirements_budget" class="notice">
        <h3>当前需求预算诊断</h3><p>{{ preview.pending_requirements_budget.verification }} · {{ preview.pending_requirements_budget.constraint }}</p>
        <p>已知费用：{{ money(preview.pending_requirements_budget.known_subtotal_cents) }}；总额：{{ money(preview.pending_requirements_budget.total_cents) }}；当前需求硬上限：{{ money(trip.intake.input.hard_limit_cents.value) }}。此诊断不替代原稿预算。</p>
      </div>
      <h3>未知费用（{{ preview.unknown_costs.length }} 项）</h3>
      <ul class="unknown-costs"><li v-for="cost in preview.unknown_costs" :key="cost.id"><strong>{{ items.find(i => i.id === costs.find(c => c.id === cost.id)?.item_id)?.title ?? cost.id }}：未知</strong><p>{{ cost.reason }}</p><small>费用 ID：{{ cost.id }}</small></li></ul>
      <CostBreakdown :items="items" :costs="costs" />
      <RiskList :notes="{ risks: preview.risks, transport_advice: [] }" />
      <div v-if="!preview.confirmation_allowed" class="notice form-error" role="alert">
        <h3>当前不能确认</h3><ul><li v-for="reason in preview.blocking_reasons" :key="reason">{{ blockingText[reason] ?? '服务端禁止本次确认，请核对当前行程。' }} <small>（{{ reason }}）</small></li></ul>
      </div>
      <p v-else class="confirmation-eligibility">服务端允许确认当前规划；仍需你明确同意，且页面版本保持同步。</p>
      <p class="scope-note">本次预览五分钟内有效。重新读取、版本或披露变化、切换页面后，都需要重新审阅并勾选。</p>
      <p v-if="trip.synchronization !== 'CURRENT'" class="form-error">工作稿为 STALE，无法确认当前需求下的行程。</p>
      <label class="confirmation-consent"><input v-model="acknowledged" type="checkbox" :disabled="!fresh || !current || !preview.confirmation_allowed || trip.synchronization !== 'CURRENT' || locked || blocked || !!result || trip.plan_confirmation === 'CONFIRMED'" /><span>我已查看当前行程的预算、未知费用及相关风险，确认这份旅行规划，并理解这不代表已经完成酒店预订、购票或支付。</span></label>
      <button class="primary" :disabled="!canSubmit" @click="session.submit()">确认当前行程 r{{ preview.working_revision }}</button>
    </template>
    <div v-if="result" class="confirmation-receipt" role="status">
      <h3>{{ verified ? '规划确认成功（HTTP 201，已核对当前指针）' : '已收到服务端确认快照，当前指针待核对' }}</h3>
      <dl class="requirements-list">
        <div><dt>快照 ID</dt><dd>{{ result.snapshot.snapshot.snapshot_id }}</dd></div>
        <div><dt>来源工作稿</dt><dd>r{{ result.snapshot.snapshot.source_working_revision }}</dd></div>
        <div><dt>确认时间</dt><dd>{{ confirmedAt }}（Asia/Shanghai）</dd></div>
        <div><dt>确认时预算</dt><dd>{{ result.snapshot.snapshot.budget.verification }} · {{ result.snapshot.snapshot.budget.constraint }}<br>已知小计 {{ money(result.snapshot.snapshot.budget.known_subtotal_cents) }}；总额 {{ money(result.snapshot.snapshot.budget.total_cents) }}</dd></div>
      </dl>
      <p>尚未预订（booked=false），出行就绪情况未评估（readiness=NOT_ASSESSED）。整体确认不授予酒店具体授权。</p>
    </div>
  </section>
</template>
