import { test, expect, type Page, type APIRequestContext } from '@playwright/test'
import { writeFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
const a = process.env.EASYTRAVEL_BROWSER_TOKEN_A!, b = process.env.EASYTRAVEL_BROWSER_TOKEN_B!
const auth = (token = a) => ({ Authorization: `Bearer ${token}` })
const headers = (token = a) => ({ ...auth(token), 'Idempotency-Key': randomUUID() })
function input() {
  const provided = (value: unknown) => ({ state: 'provided', value })
  return { origin: provided('上海'), destination: provided('杭州'), dates: provided({ mode: 'fixed', start_date: '2026-12-04', end_date: '2026-12-06' }), days: provided(3), travelers: provided(2), target_budget_cents: provided(123456), hard_limit_cents: { state: 'unanswered', value: null }, transport: { state: 'no_preference', value: null }, hotel: { state: 'no_preference', value: null }, restrictions: { state: 'unanswered', value: null }, raw_text: '明确输入，不自动解析。' }
}
async function credential(page: Page, token = a) { await page.getByLabel('本地 Token', { exact: true }).fill(token); await page.getByRole('button', { name: '使用本地凭证' }).click() }
async function create(request: APIRequestContext) {
  const r = await request.post('/api/v1/trips', { headers: headers(), data: { requirements: input() } }); expect(r.status()).toBe(201); return r.json()
}
async function open(page: Page, id: string) { await page.goto(`/trips/${id}`); await credential(page); await expect(page.getByRole('heading', { name: '补充一条必要信息' })).toBeVisible() }
const versions = (t: any) => ({ expected_intake_revision: t.intake.intake_revision, expected_working_revision: t.working?.revision ?? null })
test('real create → hard-budget answer → date update → reload → SQL evidence', async ({ page, request }) => {
  await page.goto('/trips/new'); await credential(page)
  await page.getByLabel('一句话旅行需求').fill(input().raw_text)
  for (const [field, value] of [['origin', '上海'], ['destination', '杭州']]) { await page.selectOption(`#${field}-state`, 'provided'); await page.fill(`#${field}`, value!) }
  await page.locator('summary').first().click(); await page.selectOption('#dates-state', 'provided')
  await page.fill('#start-date', '2026-12-04'); await page.fill('#end-date', '2026-12-06')
  for (const [field, value] of [['days', '3'], ['travelers', '2'], ['target_budget_cents', '1234.56']]) { await page.selectOption(`#${field}-state`, 'provided'); await page.fill(`#${field}`, value!) }
  await page.locator('summary').nth(1).click(); await page.selectOption('#transport-state', 'no_preference'); await page.selectOption('#hotel-state', 'no_preference')
  const creation = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/api/v1/trips'))
  await page.getByRole('button', { name: '保存旅行', exact: true }).click(); const createdResponse = await creation
  expect(createdResponse.status()).toBe(201); const created = await createdResponse.json(), path = `/api/v1/trips/${created.trip_id}`
  expect(createdResponse.request().postDataJSON()).toEqual({ requirements: input() })
  await expect(page).toHaveURL(`/trips/${created.trip_id}`)
  await expect(page.getByRole('heading', { name: '补充一条必要信息' })).toBeVisible()
  const questionResponse = await request.get(path + '/clarifications/next', { headers: auth() }); const next = await questionResponse.json()
  expect(next.question.field_key).toBe('hard_limit_cents'); expect(next.intake_revision).toBe(1)
  await page.screenshot({ path: '../Docs/M1-03b_澄清_桌面.png', fullPage: true })
  await page.getByRole('button', { name: '以后再填写' }).click(); await expect(page.getByText('旅行已保存，可稍后继续。已保存事实不会丢失。')).toBeVisible()
  await page.getByRole('button', { name: '继续补充' }).click()
  await page.getByLabel('硬预算上限（元 / 总额）').fill('2345.67')
  const posted = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/answers'))
  await page.getByRole('button', { name: '保存回答', exact: true }).click(); const answered = await posted
  expect(answered.status()).toBe(200); const afterAnswer = await answered.json(), answerBody = answered.request().postDataJSON()
  expect(answerBody).toEqual({ ...versions(created), field_key: 'hard_limit_cents', answer: { state: 'provided', value: 234567 } })
  expect(afterAnswer.intake.intake_revision).toBe(2); expect(answered.request().headers()['idempotency-key']).toMatch(/^[0-9a-f-]{36}$/)
  await expect(page.getByText('Intake revision：2')).toBeVisible(); await expect(page.getByRole('heading', { name: '补充一条必要信息' })).toHaveCount(0)
  const stale = await request.post(path + `/clarifications/${next.question.question_id}/answers`, { headers: headers(), data: { ...versions(afterAnswer), field_key: 'hard_limit_cents', answer: { state: 'provided', value: 300000 } } }); expect(stale.status()).toBe(409)
  await page.getByRole('button', { name: '修改需求', exact: true }).click()
  await expect(page.locator('#origin')).toHaveValue('上海'); await expect(page.locator('#hotel-state')).toHaveValue('no_preference'); await expect(page.locator('#restrictions-state')).toHaveValue('unanswered')
  await expect(page.locator('#hard_limit_cents')).toHaveValue('2345.67')
  await page.fill('#start-date', '2026-12-11'); await page.fill('#end-date', '2026-12-13')
  await page.screenshot({ path: '../Docs/M1-03b_编辑_桌面.png', fullPage: true })
  for (const width of [390, 320]) { await page.setViewportSize({ width, height: 844 }); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); await page.screenshot({ path: `../Docs/M1-03b_编辑_${width}.png`, fullPage: true }) }
  const putPromise = page.waitForResponse(r => r.request().method() === 'PUT')
  await page.getByRole('button', { name: '保存修改', exact: true }).click(); const updated = await putPromise, final = await updated.json(); expect(updated.status()).toBe(200); expect(final.intake.intake_revision).toBe(3); expect(updated.request().postDataJSON().expected_intake_revision).toBe(2); expect(updated.request().postDataJSON().expected_working_revision).toBeNull(); expect(updated.request().headers()['idempotency-key']).not.toBe(answered.request().headers()['idempotency-key'])
  await expect(page.getByText('Intake revision：3')).toBeVisible()
  const nextFinal = await request.get(path + '/clarifications/next', { headers: auth() }); expect((await nextFinal.json()).question).toBeNull()
  let writes = 0; page.on('request', r => { if (['POST', 'PUT'].includes(r.method())) writes++ })
  await page.reload(); await expect(page.getByLabel('本地 Token', { exact: true })).toHaveValue(''); const browserRead = page.waitForResponse(r => r.url().endsWith(path) && r.request().method() === 'GET'); await credential(page); expect(await (await browserRead).json()).toEqual(final)
  await expect(page.getByText('Intake revision：3')).toBeVisible(); await expect(page.getByText('2026-12-11 至 2026-12-13（具体日期）')).toBeVisible(); expect(writes).toBe(0)
  const refreshed = await request.get(path, { headers: auth() }); expect(await refreshed.json()).toEqual(final)
  const otherGet = await request.get(path, { headers: auth(b) }), otherPut = await request.put(path + '/requirements', { headers: headers(b), data: { ...versions(final), requirements: input() } }), otherAnswer = await request.post(path + `/clarifications/${next.question.question_id}/answers`, { headers: headers(b), data: answerBody })
  expect([otherGet.status(), otherPut.status(), otherAnswer.status()]).toEqual([404, 404, 404])
  expect(await page.evaluate(() => ({ local: Object.keys(localStorage), session: Object.keys(sessionStorage) }))).toEqual({ local: [], session: [] })
  await page.screenshot({ path: '../Docs/M1-03b_刷新_移动.png', fullPage: true })
  await page.setViewportSize({ width: 1280, height: 900 }); await page.screenshot({ path: '../Docs/M1-03b_刷新_桌面.png', fullPage: true })
  const evidence = { tripId: created.trip_id, create: { status: createdResponse.status(), key: createdResponse.request().headers()['idempotency-key'], request: { requirements: input() }, response: created }, question: { status: questionResponse.status(), response: next }, answer: { status: answered.status(), key: answered.request().headers()['idempotency-key'], request: answerBody, response: afterAnswer }, update: { status: updated.status(), key: updated.request().headers()['idempotency-key'], request: updated.request().postDataJSON(), response: final }, refresh: { status: refreshed.status(), response: final, automaticWrites: writes }, staleQuestionStatus: stale.status(), crossOwnerStatuses: [404, 404, 404] }
  writeFileSync('../Docs/M1-03b_HTTP证据.json', JSON.stringify(evidence, null, 2))
  await page.getByRole('button', { name: '清除或更换凭证' }).click(); await expect(page.getByText(`旅行 ID：${created.trip_id}`)).toHaveCount(0); await credential(page, b); await expect(page.getByRole('alert')).toContainText('旅行不存在')
})
for (const [status, message] of [[401, '凭证无效'], [403, '不允许访问'], [404, '旅行不存在'], [409, '冲突'], [422, '输入有误'], [503, '依赖不可用']] as const) test(`edit HTTP ${status} keeps local input`, async ({ page, request }) => {
  const t = await create(request); await open(page, t.trip_id); await page.getByRole('button', { name: '修改需求', exact: true }).click()
  await page.getByLabel('一句话旅行需求').fill('本地输入需要保留')
  await page.route('**/requirements', r => r.fulfill({ status, json: { error: { code: status === 409 ? 'business_conflict' : 'safe', message: 'hidden diagnostic' } } }))
  await page.getByRole('button', { name: '保存修改', exact: true }).click(); await expect(page.getByRole('alert')).toContainText(message)
  await expect(page.getByLabel('一句话旅行需求')).toHaveValue('本地输入需要保留'); await expect(page.getByText('Intake revision：1')).toBeVisible()
  if (status === 409) {
    await page.getByRole('button', { name: '读取最新需求并重新审阅' }).click(); await expect(page.getByRole('button', { name: '我已对照最新需求，继续手动编辑' })).toBeVisible()
    await expect(page.getByRole('button', { name: '保存修改', exact: true })).toBeDisabled(); await expect(page.getByLabel('一句话旅行需求')).toHaveValue('本地输入需要保留')
  }
})
test('actual concurrent revision, old answer conflict, retained answer requires fresh question', async ({ page, request }) => {
  const t = await create(request); await open(page, t.trip_id)
  await page.getByLabel('硬预算上限（元 / 总额）').fill('3333')
  const r = await request.put(`/api/v1/trips/${t.trip_id}/requirements`, { headers: headers(), data: { ...versions(t), requirements: { ...input(), raw_text: '另一页面已修改' } } }); expect(r.status()).toBe(200)
  await page.getByRole('button', { name: '保存回答', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('冲突')
  await expect(page.getByText(/未保存的旧回答/)).toContainText('333300')
  await page.getByRole('button', { name: '读取最新需求并重新审阅' }).click(); await expect(page.getByText('Intake revision：2')).toBeVisible()
  await page.getByRole('button', { name: '我已对照最新需求，继续手动编辑' }).click()
  await expect(page.getByLabel('硬预算上限（元 / 总额）')).toHaveValue(''); await expect(page.getByText('当前需求版本：2')).toBeVisible()
})
test('budget conflict saved honestly; no-preference hard limit can pause without looping', async ({ page, request }) => {
  const t = await create(request); await open(page, t.trip_id); await page.getByRole('button', { name: '修改需求', exact: true }).click()
  await page.selectOption('#hard_limit_cents-state', 'provided'); await page.fill('#hard_limit_cents', '1')
  await page.getByRole('button', { name: '保存修改', exact: true }).click(); await expect(page.getByText('目标预算超过硬预算上限，请修改其中一项。需求可保存，但尚不满足生成条件。')).toBeVisible()
  await page.getByRole('button', { name: '修改需求', exact: true }).click(); await page.selectOption('#hard_limit_cents-state', 'no_preference'); await page.getByRole('button', { name: '保存修改', exact: true }).click()
  await expect(page.getByText('Intake revision：3')).toBeVisible(); await page.getByRole('button', { name: '以后再填写' }).click(); await expect(page.getByText(/旅行已保存，可稍后继续/)).toBeVisible()
  await expect(page.getByRole('button', { name: /生成|确认|预订/ })).toHaveCount(0)
})
test('update timeout retries exact command; changing body changes key; identity clears editor', async ({ page, request }) => {
  const t = await create(request); await open(page, t.trip_id); await page.getByRole('button', { name: '修改需求', exact: true }).click()
  const calls: { path: string; key?: string | undefined; body: string | null }[] = []
  await page.route('**/requirements', async r => { calls.push({ path: r.request().url(), key: r.request().headers()['idempotency-key'], body: r.request().postData() }); if (calls.length === 1) return; await r.abort('failed') })
  await page.getByRole('button', { name: '保存修改', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('超时', { timeout: 17000 })
  await page.getByRole('button', { name: '使用原 key 和原内容重试' }).click(); await expect(page.getByRole('alert')).toContainText('网络连接中断'); expect(calls[1]).toEqual(calls[0])
  await page.getByLabel('一句话旅行需求').fill('新的请求内容'); await page.getByRole('button', { name: '保存修改', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('网络连接中断'); expect(calls[2]?.key).not.toBe(calls[1]?.key)
  await page.getByRole('button', { name: '清除或更换凭证' }).click(); await credential(page, b); await expect(page.getByRole('alert')).toContainText('旅行不存在'); await expect(page.getByRole('button', { name: '使用原 key 和原内容重试' })).toHaveCount(0)
})

test('explicit no preference on required budget saves fact and pauses repeated question', async ({ page, request }) => {
  const t = await create(request); await open(page, t.trip_id)
  await page.getByRole('button', { name: '明确无偏好', exact: true }).click()
  await expect(page.getByText('Intake revision：2')).toBeVisible()
  await expect(page.getByText(/旅行已保存，可稍后继续/)).toBeVisible()
  await expect(page.getByRole('button', { name: '保存回答', exact: true })).toHaveCount(0)
  const r = await request.get(`/api/v1/trips/${t.trip_id}`, { headers: auth() }), saved = await r.json()
  expect(saved.intake.input.hard_limit_cents).toEqual({ state: 'no_preference', value: null })
  expect(saved.intake.blocking_fields).toContain('hard_limit_cents'); expect(saved.intake.collection_status).toBe('COLLECTING')
})
