<script setup lang="ts">
import { fieldKeys, labels, type FieldKey, type TripView } from '../api/contracts'
const props = defineProps<{ trip: TripView }>()
function text(key: FieldKey) {
  const a = props.trip.intake.input[key]
  if (a.state !== 'provided') return a.state === 'unanswered' ? '尚未回答' : '明确无偏好'
  const v = a.value
  if (typeof v === 'object') return v.mode === 'fixed' ? `${v.start_date} 至 ${v.end_date}（具体日期）` : `${v.earliest_start} 至 ${v.latest_end}（可出行范围，尚未选定）`
  return key.endsWith('_cents') ? `¥${(Number(v) / 100).toFixed(2)}（总额）` : String(v)
}
</script>
<template>
  <section class="trip-panel" aria-labelledby="trip-heading">
    <p class="eyebrow">已从服务器读取</p><h2 id="trip-heading">旅行需求</h2>
    <p class="trip-id">旅行 ID：{{ trip.trip_id }}</p>
    <a :href="`/trips/${trip.trip_id}`">重新打开此旅行</a>
    <p>Intake revision：{{ trip.intake.intake_revision }}</p>
    <p>需求收集状态：{{ trip.intake.collection_status === 'COLLECTING' ? '收集中' : '输入已满足生成前提' }}（{{ trip.intake.collection_status }}）</p>
    <div class="notice"><strong>尚无行程草案</strong><p>尚未生成、确认或预订行程，出行就绪情况尚未评估。输入充分不代表路线、价格或酒店已经核验。</p></div>
    <h3>已保存的需求摘要</h3>
    <p class="preserve">原始需求：{{ trip.intake.input.raw_text ?? '尚未填写' }}</p>
    <dl class="requirements-list"><div v-for="key in fieldKeys" :key="key"><dt>{{ labels[key] }}</dt><dd>{{ text(key) }}</dd></div></dl>
    <h3>尚需补充或核对</h3>
    <p>{{ trip.intake.blocking_fields.length ? trip.intake.blocking_fields.map(k => labels[k]).join('、') : '当前没有阻塞字段。' }}</p>
    <p class="scope-note">当前版本仅支持创建和读取，暂不提供需求修改或生成。重新打开或刷新后，需重新输入本地凭证读取。</p>
  </section>
</template>
