<script setup lang="ts">
import type { HealthState } from '../composables/useHealth'
defineProps<{ state: HealthState }>()
defineEmits<{ refresh: [] }>()
</script>

<template>
  <section class="health-panel" aria-labelledby="health-heading" :aria-busy="state.status === 'loading'">
    <div class="panel-heading">
      <div><p class="eyebrow">SERVICE STATUS</p><h2 id="health-heading">后端服务</h2></div>
      <button type="button" :disabled="state.status === 'loading'" @click="$emit('refresh')">
        {{ state.status === 'loading' ? '正在检查…' : '重新检查' }}<span aria-hidden="true"> ↗</span>
      </button>
    </div>
    <div class="connection" :class="state.status" role="status" aria-live="polite">
      <span class="status-dot" aria-hidden="true"></span>
      <div v-if="state.status === 'loading'"><h3>正在连接后端</h3><p>正在获取 HTTP 应用状态，请稍候。</p></div>
      <div v-else-if="state.status === 'success'"><h3>HTTP 应用存活</h3><p>健康检查已通过，服务能够响应 HTTP 请求。</p></div>
      <div v-else><h3>健康检查失败</h3><p>{{ state.message }}</p></div>
    </div>
    <dl class="dependency-list">
      <div><dt>数据库</dt><dd>未检查</dd></div>
      <div><dt>模型服务</dt><dd>未检查</dd></div>
      <div><dt>旅游数据</dt><dd>未检查</dd></div>
    </dl>
    <p class="scope-note">此检查仅表示 HTTP 应用存活。数据库、模型与旅游数据是否就绪，尚未验证。</p>
    <p v-if="state.status === 'success'" class="checked-at">最近检查 <time :datetime="state.checkedAt.toISOString()">{{ state.checkedAt.toLocaleTimeString('zh-CN', { hour12: false }) }}</time></p>
  </section>
</template>
