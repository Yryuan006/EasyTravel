<script setup lang="ts">
import { failureText, type GenerationRun } from '../api/generations'
defineProps<{ run?: GenerationRun | undefined }>()
</script>
<template><div v-if="run" class="notice generation-status" role="status"><strong>{{ { ACCEPTED: '生成请求已接受，尚未开始', RUNNING: '正在生成模拟行程', SUCCEEDED: run.kind === 'CHANGE_INTENT' ? '修改提案已生成，请核对提案决定' : '生成成功，工作稿已保存', FAILED: '生成失败' }[run.status] }}</strong><p>{{ run.status }} · 第 {{ run.attempt }} 次运行 · 来源需求 i{{ run.intake_revision }}</p><p class="trip-id">运行 ID：{{ run.run_id }}</p><p v-if="run.status === 'ACCEPTED'">HTTP 202 仅表示已接受。等待执行者开始，不代表行程已生成。</p><p v-if="run.error">{{ failureText[run.error.code] ?? '运行失败，请读取最新状态并检查服务。' }}</p></div></template>
