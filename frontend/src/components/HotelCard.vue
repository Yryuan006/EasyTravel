<script setup lang="ts">
import { money, type PlanItem, type CostLine } from '../api/generations'
defineProps<{ item: PlanItem; costs: CostLine[] }>()
</script>
<template><article class="hotel-card"><h4>{{ item.title }}</h4><p class="mock-label">模拟建议，尚未核验</p><dl class="requirements-list"><div><dt>入住 / 退房</dt><dd>{{ item.check_in }} / {{ item.check_out }}</dd></div><div><dt>房型 / 房间数</dt><dd>{{ item.room_type }} · {{ item.rooms }} 间</dd></div></dl><p v-for="c in costs" :key="c.id">{{ money(c.amount_cents) }} · {{ c.category === 'unknown' ? '未知' : '估算，非已核验报价' }}（整段住宿总额）</p><p>酒店尚未预订，房型、供应情况及入住退房规定待核验。</p></article></template>
