<script setup lang="ts">
import { reactive, ref } from 'vue'
import { labels, type FieldKey, type RequirementsInput } from '../api/contracts'
import { emptyForm, serializeForm, formFromRequirements } from '../composables/requirements'
const props = defineProps<{ busy: boolean; locked?: boolean; initial?: RequirementsInput; editing?: boolean }>()
const emit = defineEmits<{ submit: [input: RequirementsInput] }>()
const form = reactive(props.initial ? formFromRequirements(props.initial) : emptyForm())
const error = ref('')
const basics: FieldKey[] = ['origin', 'destination']
const schedule: FieldKey[] = ['days', 'travelers', 'target_budget_cents', 'hard_limit_cents']
const preferences: FieldKey[] = ['transport', 'hotel', 'restrictions']
function submit() {
  try { const input = serializeForm(form, !props.editing); error.value = ''; emit('submit', input) }
  catch (e) { error.value = e instanceof Error ? e.message : '请检查输入。' }
}
</script>
<template>
  <form class="trip-panel" novalidate @submit.prevent="submit">
    <fieldset :disabled="busy || locked">
      <legend>{{ editing ? '修改需求（尚未保存）' : '创建旅行' }}</legend>
      <p class="scope-note">{{ editing ? '表单来自已保存需求。修改仅在保存成功后成为新版本。' : '先填写目的地或一句话需求即可保存。' }}其他信息可留空；文字不会被自动提取为已确认事实。</p>
      <label for="raw-text">一句话旅行需求</label>
      <textarea id="raw-text" v-model="form.raw_text" maxlength="2000" rows="3" placeholder="例如：想去杭州放松几天，还没定日期。" />
      <div class="form-grid">
        <div v-for="key in basics" :key="key">
          <label :for="key + '-state'">{{ labels[key] }} · 回答状态</label>
          <select :id="key + '-state'" v-model="form.states[key]"><option value="unanswered">尚未回答</option><option value="no_preference">明确无偏好</option><option value="provided">填写具体内容</option></select>
          <label v-if="form.states[key] === 'provided'" :for="key">{{ labels[key] }}</label>
          <input v-if="form.states[key] === 'provided'" :id="key" v-model="form.values[key]" maxlength="500" />
        </div>
      </div>
      <details :open="editing"><summary>补充日期、人数与预算</summary>
        <label for="dates-state">出行日期 · 回答状态</label>
        <select id="dates-state" v-model="form.states.dates"><option value="unanswered">尚未回答</option><option value="no_preference">明确无偏好</option><option value="provided">填写具体内容</option></select>
        <template v-if="form.states.dates === 'provided'">
          <label for="date-mode">日期方式</label><select id="date-mode" v-model="form.dateMode"><option value="fixed">具体出行日期</option><option value="window">可出行的日期范围（尚未选定）</option></select>
          <div class="form-grid"><div><label for="start-date">{{ form.dateMode === 'fixed' ? '出发日期' : '最早开始日期' }}</label><input id="start-date" v-model="form.start" type="date" /></div><div><label for="end-date">{{ form.dateMode === 'fixed' ? '结束日期' : '最晚结束日期' }}</label><input id="end-date" v-model="form.end" type="date" /></div></div>
        </template>
        <div class="form-grid"><div v-for="key in schedule" :key="key">
          <label :for="key + '-state'">{{ labels[key] }} · 回答状态</label>
          <select :id="key + '-state'" v-model="form.states[key]"><option value="unanswered">尚未回答</option><option value="no_preference">明确无偏好</option><option value="provided">填写具体内容</option></select>
          <label v-if="form.states[key] === 'provided'" :for="key">{{ labels[key] }}{{ key.endsWith('_cents') ? '（元 / 总额）' : '' }}</label>
          <input v-if="form.states[key] === 'provided'" :id="key" v-model="form.values[key]" :inputmode="key.endsWith('_cents') ? 'decimal' : 'numeric'" />
        </div></div>
        <p class="scope-note">旅行 1–5 天，人数 1 或 2 人。目标预算表示希望花多少；硬预算上限表示不可超过多少。两者均为人民币总额，可分别留空。</p>
      </details>
      <details :open="editing"><summary>补充交通、酒店偏好与特殊限制</summary>
        <div v-for="key in preferences" :key="key"><label :for="key + '-state'">{{ labels[key] }} · 回答状态</label>
          <select :id="key + '-state'" v-model="form.states[key]"><option value="unanswered">尚未回答</option><option value="no_preference">明确无偏好</option><option value="provided">填写具体内容</option></select>
          <label v-if="form.states[key] === 'provided'" :for="key">{{ labels[key] }}</label><input v-if="form.states[key] === 'provided'" :id="key" v-model="form.values[key]" maxlength="500" />
        </div>
      </details>
      <p v-if="error" role="alert" class="form-error">{{ error }}</p>
      <button class="primary" type="submit" :disabled="busy || locked">{{ busy ? '正在保存旅行…' : editing ? '保存修改' : '保存旅行' }}</button>
    </fieldset>
  </form>
</template>
