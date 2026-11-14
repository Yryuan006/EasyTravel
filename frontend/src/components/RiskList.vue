<script setup lang="ts">
import type { Notes } from '../api/generations'
defineProps<{ notes: Notes | null }>()
</script>
<template><section class="risk-list"><h3>风险与待核实事项</h3><ul><li v-for="risk in notes?.risks" :key="risk.code"><strong>{{ risk.severity }} · {{ risk.code }}</strong><p>{{ risk.message }}</p><small v-if="risk.item_ids.length">关联项目：{{ risk.item_ids.join('、') }}</small></li></ul><p v-for="(advice, n) in notes?.transport_advice" :key="n">{{ advice.from_item_id }} → {{ advice.to_item_id }}：{{ advice.text }}（{{ advice.mode }} · 未核验 / {{ advice.verification }}）</p><p>所有路线及酒店建议待核验；未预订、未付款、未出票，出行就绪情况未评估。</p></section></template>
