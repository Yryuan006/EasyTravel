<script setup lang="ts">
import { money, shanghaiTime, type PlanItem, type CostLine } from '../api/generations'
defineProps<{ item: PlanItem; costs: CostLine[] }>()
</script>
<template><article class="plan-item"><p class="item-time">{{ shanghaiTime(item.start) }} — {{ shanghaiTime(item.end) }} <span>活动</span></p><h4>{{ item.title }}</h4><p class="mock-label">模拟建议，尚未核验</p><ul class="item-costs"><li v-for="cost in costs" :key="cost.id">{{ money(cost.amount_cents) }} · {{ cost.category === 'unknown' ? 'INDETERMINATE / 未知' : cost.category === 'estimate' ? 'ESTIMATED / 估算' : '报价（核验状态见预算）' }}</li></ul></article></template>
