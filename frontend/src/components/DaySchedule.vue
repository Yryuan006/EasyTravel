<script setup lang="ts">
import type { PlanItem, CostLine } from '../api/generations'
import PlanItemCard from './PlanItemCard.vue'
defineProps<{ date: string; index: number; items: PlanItem[]; costs: CostLine[] }>()
</script>
<template><section class="day-schedule"><h3><span class="day-number">DAY {{ index + 1 }}</span> {{ date }}</h3><PlanItemCard v-for="item in items" :key="item.id" :item="item" :costs="costs.filter(c => c.item_id === item.id)" /></section></template>
