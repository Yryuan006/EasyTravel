<script setup lang="ts">
import { computed } from 'vue'
import type { TripView } from '../api/contracts'
import type { useChanges } from '../composables/useChanges'
import { money, shanghaiTime, type PlanItem } from '../api/generations'
import BudgetSummary from './BudgetSummary.vue'
import RiskList from './RiskList.vue'
const props = defineProps<{ trip: TripView; session: ReturnType<typeof useChanges>; blocked: boolean }>()
const { intent, agreed, busy, error, run, proposal, pending, receipt, awaitingRead, verified, locked, current, canDecide, retryAllowed } = props.session
const sourceItems = computed(() => (props.trip.working?.payload.items ?? []) as PlanItem[])
</script>
<template>
  <section class="trip-panel change-review" aria-labelledby="change-heading" :aria-busy="busy">
    <p class="eyebrow">REVIEW CHANGES / 审阅修改</p><h2 id="change-heading">修改当前工作草案</h2>
    <p>修改只生成待审提案。审阅差异、费用和风险后，才能应用；整体确认不授予酒店修改权限。</p>
    <p v-if="trip.latest_confirmed_version_id && trip.plan_confirmation !== 'CONFIRMED'" class="notice preserve">历史确认快照指针：{{ trip.latest_confirmed_version_id }}。当前工作稿 r{{ trip.working?.revision }} 未确认；历史快照仍有效。</p>
    <p v-if="trip.synchronization !== 'CURRENT'" class="notice">需求与工作稿不同步，当前不支持重规划。请保留原稿，不能将需求变更作为普通修改提交。</p>
    <label for="change-intent">修改意图</label><textarea id="change-intent" v-model="intent" maxlength="2000" :disabled="locked || blocked" rows="3" />
    <p>Mock 仅支持：第二天安排轻松一点；或“删除活动 / 替换活动 / 调整活动时间 / 添加活动 / 修改活动标题 / 更换酒店 + 项目 ID”。时间调整为延后三小时；新增活动以该活动为模板。超出此范围会明确失败。</p>
    <p class="preserve">项目 ID：{{ sourceItems.map(i => `${i.title}（${i.id}）`).join('；') }}</p>
    <div class="actions"><button :disabled="locked || blocked || !intent.trim() || trip.synchronization !== 'CURRENT' || !!trip.active_run_id" @click="session.start()">生成修改提案</button><button :disabled="busy || !!pending" @click="session.refresh()">读取修改运行与提案</button></div>
    <p v-if="run" role="status">修改运行：{{ run.status }} · {{ run.run_id }}</p>
    <p v-if="busy" role="status">正在与服务器核对修改…</p>
    <div v-if="error" role="alert" class="notice form-error">{{ error }}</div>
    <div v-if="pending" class="notice" role="status"><p>未收到有效响应不代表未提交。已保留原路径、幂等键及内容；恢复前暂停其他修改。</p><button v-if="retryAllowed" :disabled="busy || blocked" @click="session.retry()">使用原修改请求安全重试</button></div>
    <p v-if="awaitingRead" role="status">已收到决定回执，尚未核对当前草案。请只继续读取核对。</p>
    <template v-if="proposal">
      <h3>修改提案 · {{ proposal.proposal.level }} · {{ current ? 'PENDING' : proposal.state === 'PENDING' ? 'STALE' : proposal.state }}</h3>
      <p class="preserve">提案 {{ proposal.proposal.proposal_id }} · 基础工作稿 r{{ proposal.proposal.output.base_working_revision }} · intake i{{ proposal.intake_revision }}<br>候选摘要 {{ proposal.proposal.candidate_hash }}</p>
      <p>{{ proposal.proposal.output.explanation }}</p>
      <div class="proposal-differences"><article v-for="d in proposal.differences" :key="d.item_id" class="notice">
        <h4>受影响项目：{{ d.item_id }}</h4>
        <p>原稿：{{ d.before ? `${d.before.title} · ${shanghaiTime(d.before.start)}–${shanghaiTime(d.before.end)}` : '原稿中不存在（新增）' }}</p>
        <p>候选：{{ d.after ? `${d.after.title} · ${shanghaiTime(d.after.start)}–${shanghaiTime(d.after.end)}` : '删除此项目' }}</p>
        <p class="preserve">资源：{{ d.before?.resource_id ?? '无' }} → {{ d.after?.resource_id ?? '无' }}</p>
        <p>原费用：{{ d.before_costs.map(c => money(c.amount_cents)).join('、') || '无原项目费用' }}</p>
        <p>候选费用：{{ d.after_costs.map(c => `${money(c.amount_cents)}（${c.category}，${c.is_mock ? 'Mock 未核验' : '未核验'}）${c.unknown_reason ?? ''}`).join('、') || '项目已删除' }}</p>
        <p v-if="d.after?.kind === 'hotel'">酒店：{{ d.after.check_in }} 至 {{ d.after.check_out }} · {{ d.after.room_type }} · {{ d.after.rooms }} 间</p>
      </article></div>
      <p>原已知小计 {{ money(proposal.source_budget.known_subtotal_cents) }}；候选已知小计 {{ money(proposal.budget.known_subtotal_cents) }}。未知费用不能视为节省或零元。</p>
      <BudgetSummary heading-id="change-budget-heading" :budget="proposal.budget" :hard-limit="Number(trip.working?.requirements.hard_limit_cents)" />
      <h4>未知费用（{{ proposal.unknown_costs.length }} 项）</h4><ul><li v-for="c in proposal.unknown_costs" :key="c.id">{{ c.id }}：未知。{{ c.reason }}</li></ul>
      <RiskList :notes="proposal.candidate_notes" />
      <p class="notice">{{ proposal.requires_specific_authorization ? 'L3：需要精确授权。完整授权范围包括所有附带变化：' : 'L1/L2：需要明确应用，不创建具体授权。完整影响范围：' }}{{ proposal.proposal.touched_item_ids.join('、') }}</p>
      <label class="confirmation-consent"><input v-model="agreed" type="checkbox" :disabled="!canDecide || proposal.budget.constraint === 'OVER_LIMIT' || proposal.risks.some(r => r.severity === 'blocker')" /><span>我已审阅此候选的全部差异、费用和风险{{ proposal.requires_specific_authorization ? '，明确授权上述完整项目范围并应用' : '，同意应用到工作草案' }}。这不代表预订、付款或出行就绪。</span></label>
      <div class="actions"><button class="primary" :disabled="!canDecide || !agreed || proposal.budget.constraint === 'OVER_LIMIT' || proposal.risks.some(r => r.severity === 'blocker')" @click="session.decide('approve')">{{ proposal.requires_specific_authorization ? '明确授权并应用' : '应用到工作草案' }}</button><button :disabled="!canDecide" @click="session.decide('reject')">拒绝此提案</button></div>
    </template>
    <p v-if="receipt" role="status" class="notice">{{ verified ? receipt.state === 'APPLIED' ? `修改已保存并核对：工作稿 r${receipt.working_revision}，当前规划${trip.plan_confirmation === 'CONFIRMED' ? '已确认' : '未确认'}。` : '提案已拒绝并核对，行程未修改。' : '历史决定回执或当前版本待核对。' }} 提案 {{ receipt.proposal_id }}</p>
  </section>
</template>
