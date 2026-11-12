import { fieldKeys, labels, validRequirements, type Answer, type FieldKey, type RequirementsInput, type TravelDates } from '../api/contracts'

export interface FormInput { raw_text: string; values: Record<FieldKey, string>; states: Record<FieldKey, Answer<unknown>['state']>; dateMode: 'fixed' | 'window'; start: string; end: string }
export function emptyForm(): FormInput {
  return { raw_text: '', values: Object.fromEntries(fieldKeys.map(k => [k, ''])) as Record<FieldKey, string>, states: Object.fromEntries(fieldKeys.map(k => [k, 'unanswered'])) as FormInput['states'], dateMode: 'fixed', start: '', end: '' }
}
/** Explicit UI choices only; raw_text is never parsed into structured facts. */
export function serializeForm(form: FormInput, creating = true): RequirementsInput {
  const data: Record<string, unknown> = { raw_text: form.raw_text.trim() ? form.raw_text : null }
  for (const key of fieldKeys) {
    const state = form.states[key]
    if (state !== 'provided') { data[key] = { state, value: null }; continue }
    const text = form.values[key]
    let value: string | number | TravelDates = text
    if (key === 'dates') value = form.dateMode === 'fixed' ? { mode: 'fixed', start_date: form.start, end_date: form.end } : { mode: 'window', earliest_start: form.start, latest_end: form.end }
    else if (key.endsWith('_cents')) {
      if (!/^\d+(?:\.\d{1,2})?$/.test(text)) throw new Error(`${labels[key]}须为非负金额，最多两位小数。`)
      const [yuan = '0', fraction = ''] = text.split('.')
      value = Number(yuan) * 100 + Number(fraction.padEnd(2, '0'))
    } else if (key === 'days' || key === 'travelers') {
      if (!/^\d+$/.test(text)) throw new Error(`${labels[key]}须为整数。`)
      value = Number(text)
    }
    data[key] = { state, value }
  }
  if (!validRequirements(data)) throw new Error('请检查输入：日期必须有效且结束不早于开始；天数 1–5，人数 1–2；金额不超过 100 亿元；已提供内容不能为空。')
  if (creating && data.destination.state !== 'provided' && data.raw_text === null) throw new Error('请填写目的地或一句话旅行需求。')
  return data
}

export function formFromRequirements(input: RequirementsInput): FormInput {
  const f = emptyForm(); f.raw_text = input.raw_text ?? ''
  for (const k of fieldKeys) {
    const a = input[k]; f.states[k] = a.state
    if (a.state !== 'provided') continue
    if (typeof a.value === 'object') {
      f.dateMode = a.value.mode
      f.start = a.value.mode === 'fixed' ? a.value.start_date : a.value.earliest_start
      f.end = a.value.mode === 'fixed' ? a.value.end_date : a.value.latest_end
    } else f.values[k] = k.endsWith('_cents') ? (Number(a.value) / 100).toFixed(2) : String(a.value)
  }
  return f
}
