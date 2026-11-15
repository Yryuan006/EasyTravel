<script setup lang="ts">
import { computed } from 'vue'
import { shanghaiDate, type PlanItem, type CostLine } from '../api/generations'
import DaySchedule from './DaySchedule.vue'
import HotelCard from './HotelCard.vue'
const props = defineProps<{ items: PlanItem[]; costs: CostLine[]; candidate?: boolean; historical?: boolean }>()
const activities = computed(() => props.items.filter(i => i.kind === 'activity').sort((a, b) => Date.parse(a.start) - Date.parse(b.start)))
const dates = computed(() => [...new Set(activities.value.map(i => shanghaiDate(i.start)))])
</script>
<template><section class="trip-panel itinerary" :aria-labelledby="candidate ? 'candidate-itinerary-heading' : 'itinerary-heading'"><p v-if="!historical" class="eyebrow">{{ candidate ? 'PROPOSAL CANDIDATE / 只读' : 'WORKING DRAFT / 只读' }}</p><h2 :id="candidate ? 'candidate-itinerary-heading' : 'itinerary-heading'">{{ historical ? '历史每日行程' : candidate ? '候选每日行程' : '每日行程' }}</h2><p>按 Asia/Shanghai（UTC+08:00）展示。{{ historical ? '此为当时明确确认的历史行程，不能编辑；当前工作稿状态请返回工作区查看。' : candidate ? '此为提案候选；应用与确认状态以服务端决定和当前工作稿为准。' : '当前工作稿未确认。' }}</p><p class="notice mock-label">{{ historical && !costs.some(c => c.is_mock) ? '历史费用核验情况以当时保存的预算与披露为准；整体确认不代表预订。' : '模拟建议，尚未核验。路线、交通班次、营业时间与价格均未核验，无真实预订。' }}</p><DaySchedule v-for="(date, n) in dates" :key="date" :date="date" :index="n" :items="activities.filter(i => shanghaiDate(i.start) === date)" :costs="costs" /><h3>住宿建议</h3><HotelCard v-for="hotel in items.filter(i => i.kind === 'hotel')" :key="hotel.id" :item="hotel" :costs="costs.filter(c => c.item_id === hotel.id)" /><p v-if="!items.some(i => i.kind === 'hotel')">当日往返，无住宿安排。</p></section></template>
