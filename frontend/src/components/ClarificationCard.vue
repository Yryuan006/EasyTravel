<script setup lang="ts">
import { reactive, ref } from 'vue'
import { labels, type ClarificationQuestion, type RequirementsInput } from '../api/contracts'
import { emptyForm, serializeForm } from '../composables/requirements'
const props = defineProps<{ question: ClarificationQuestion; busy: boolean }>()
const emit = defineEmits<{ answer: [value: RequirementsInput[keyof Omit<RequirementsInput, 'raw_text'>]]; edit: []; pause: [] }>()
const form = reactive(emptyForm()), error = ref('')
const value = ref(''), start = ref(''), end = ref('')
function submit(noPreference = false) {
  try {
    const k = props.question.field_key
    form.states[k] = noPreference ? 'no_preference' : 'provided'
    form.values[k] = value.value; form.start = start.value; form.end = end.value
    const answer = serializeForm(form, false)[k]
    error.value = ''; emit('answer', answer)
  } catch (e) { error.value = e instanceof Error ? e.message : '请检查回答。' }
}
</script>
<template>
  <section class="trip-panel" aria-labelledby="question-heading">
    <h2 id="question-heading">补充一条必要信息</h2>
    <p>当前需求版本：{{ question.intake_revision }}</p>
    <form novalidate @submit.prevent="submit()">
      <fieldset :disabled="busy">
        <legend class="question-prompt">{{ question.prompt }}</legend>
        <template v-if="question.field_key === 'dates'">
          <label for="answer-start">出发日期</label><input id="answer-start" v-model="start" type="date" />
          <label for="answer-end">结束日期</label><input id="answer-end" v-model="end" type="date" />
        </template>
        <template v-else>
          <label for="question-answer">{{ labels[question.field_key] }}{{ question.field_key.endsWith('_cents') ? '（元 / 总额）' : '' }}</label>
          <select v-if="question.field_key === 'days' || question.field_key === 'travelers'" id="question-answer" v-model="value"><option value="" disabled>请选择</option><option v-for="n in question.field_key === 'days' ? 5 : 2" :key="n" :value="String(n)">{{ n }}</option></select>
          <input v-else id="question-answer" v-model="value" :inputmode="question.field_key.endsWith('_cents') ? 'decimal' : 'text'" maxlength="500" />
        </template>
        <p v-if="question.field_key.endsWith('_cents')" class="scope-note">请按人民币元填写，最多两位小数；提交时精确转换为整数分。金额为所有旅客整趟合计。</p>
        <p v-if="error" role="alert" class="form-error">{{ error }}</p>
        <div class="actions"><button class="primary" type="submit">保存回答</button><button v-if="question.allow_no_preference" type="button" @click="submit(true)">明确无偏好</button></div>
        <p v-if="question.field_key === 'hard_limit_cents'" class="scope-note">预算还不确定时可以以后再填写。生成前需要明确硬预算上限，不会为你假定金额。</p>
      </fieldset>
    </form>
    <div class="actions"><button :disabled="busy" @click="emit('edit')">返回编辑已填信息</button><button :disabled="busy" @click="emit('pause')">以后再填写</button></div>
  </section>
</template>
