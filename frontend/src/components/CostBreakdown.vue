<script setup lang="ts">
import { money, type CostLine, type PlanItem } from '../api/generations'
defineProps<{ costs: CostLine[]; items: PlanItem[] }>()
</script>
<template><section class="cost-breakdown"><h3>费用明细</h3><p>金额均为人民币元，已包含对应人数、房间和晚数；不再次相乘。</p><ul class="cost-lines"><li v-for="c in costs" :key="c.id"><strong>{{ items.find(i => i.id === c.item_id)?.title ?? c.item_id }}</strong><p class="cost-amount">{{ money(c.amount_cents) }}</p><p>类别：{{ c.category }} · {{ c.is_mock ? 'Mock · 模拟建议，尚未核验' : '非 Mock，核验状态以服务端预算为准' }}</p><p v-if="c.amount_cents === 0">此费用行明确记录为零元；{{ c.is_mock ? 'Mock 估算不代表真实免费，仍待核验。' : '是否已核验仍以服务端预算与来源为准。' }}</p><p v-if="c.unknown_reason">未知原因：{{ c.unknown_reason }}</p><small>费用 {{ c.id }} / 归属 {{ c.item_id }}</small></li></ul></section></template>
