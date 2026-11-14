<script setup lang="ts">
import { computed } from 'vue'
import { shanghaiDate, type PlanItem, type CostLine } from '../api/generations'
import DaySchedule from './DaySchedule.vue'
import HotelCard from './HotelCard.vue'
const props = defineProps<{ items: PlanItem[]; costs: CostLine[] }>()
const activities = computed(() => props.items.filter(i => i.kind === 'activity').sort((a, b) => Date.parse(a.start) - Date.parse(b.start)))
const dates = computed(() => [...new Set(activities.value.map(i => shanghaiDate(i.start)))])
</script>
<template><section class="trip-panel itinerary" aria-labelledby="itinerary-heading"><p class="eyebrow">WORKING DRAFT / 只读</p><h2 id="itinerary-heading">每日行程</h2><p>按 Asia/Shanghai（UTC+08:00）展示。当前工作稿未确认。</p><p class="notice mock-label">模拟建议，尚未核验。路线、交通班次、营业时间与价格均未核验，无真实预订。</p><DaySchedule v-for="(date, n) in dates" :key="date" :date="date" :index="n" :items="activities.filter(i => shanghaiDate(i.start) === date)" :costs="costs" /><h3>住宿建议</h3><HotelCard v-for="hotel in items.filter(i => i.kind === 'hotel')" :key="hotel.id" :item="hotel" :costs="costs.filter(c => c.item_id === hotel.id)" /><p v-if="!items.some(i => i.kind === 'hotel')">当日往返，无住宿安排。</p></section></template>
