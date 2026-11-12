import { test, expect, type Page } from '@playwright/test'
import { writeFileSync } from 'node:fs'

const tokenA = process.env.EASYTRAVEL_BROWSER_TOKEN_A ?? ''
const tokenB = process.env.EASYTRAVEL_BROWSER_TOKEN_B ?? ''
async function credential(page: Page, token = tokenA) {
  await page.getByLabel('本地 Token', { exact: true }).fill(token)
  await page.getByRole('button', { name: '使用本地凭证' }).click()
}
async function begin(page: Page) { await page.goto('/trips/new'); await credential(page); await page.getByLabel('一句话旅行需求').fill('上海出发去杭州放松，日期和人数稍后补充。') }
test('real FastAPI + PostgreSQL: 201, GET, refresh credential, isolation and memory-only token', async ({ page, request }) => {
  expect(Boolean(tokenA && tokenB), 'Run scripts/verify_m102.py to supply ephemeral test credentials').toBe(true)
  const consoleMessages: string[] = []
  page.on('console', message => consoleMessages.push(message.text()))
  page.on('pageerror', error => consoleMessages.push(error.message))
  await begin(page)
  await expect(page.locator('details').first()).not.toHaveAttribute('open', '')
  await page.screenshot({ path: '../Docs/M1-03b_创建回归_渐进表单_桌面.png', fullPage: true })
  await page.locator('summary').first().click()
  await page.selectOption('#travelers-state', 'provided'); await page.fill('#travelers', '2')
  await page.selectOption('#target_budget_cents-state', 'provided'); await page.fill('#target_budget_cents', '1234.56')
  await page.selectOption('#hard_limit_cents-state', 'provided'); await page.fill('#hard_limit_cents', '2000')
  await page.selectOption('#dates-state', 'provided'); await page.fill('#start-date', '2026-12-04'); await page.fill('#end-date', '2026-12-06')
  await page.locator('summary').nth(1).click(); await page.selectOption('#hotel-state', 'no_preference')
  await page.screenshot({ path: '../Docs/M1-03b_创建回归_创建表单_桌面.png', fullPage: true })
  const posted = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/api/v1/trips'))
  await page.getByRole('button', { name: '保存旅行', exact: true }).click()
  const post = await posted; expect(post.status()).toBe(201)
  const body = await post.json(); const submitted = post.request().postDataJSON()
  expect(submitted.requirements.destination).toEqual({ state: 'unanswered', value: null })
  expect(submitted.requirements.target_budget_cents.value).toBe(123456)
  expect(submitted.requirements.hotel).toEqual({ state: 'no_preference', value: null })
  expect(Object.keys(submitted)).toEqual(['requirements'])
  expect(post.request().headers()['idempotency-key']).toMatch(/^[0-9a-f-]{36}$/)
  await expect(page.getByText('旅行创建成功（HTTP 201）。')).toBeVisible()
  await expect(page).toHaveURL(`/trips/${body.trip_id}`)
  await expect(page.getByText('尚无行程草案', { exact: true })).toBeVisible()
  const read = await request.get(`/api/v1/trips/${body.trip_id}`, { headers: { Authorization: `Bearer ${tokenA}` } })
  expect(read.status()).toBe(200); expect(await read.json()).toEqual(body)
  const headers = { Authorization: `Bearer ${tokenA}`, 'Idempotency-Key': post.request().headers()['idempotency-key']! }
  const replay = await request.post('/api/v1/trips', { headers, data: submitted })
  expect(replay.status()).toBe(201); expect(await replay.json()).toEqual(body)
  const conflict = await request.post('/api/v1/trips', { headers, data: { requirements: { ...submitted.requirements, raw_text: '改去苏州' } } })
  expect(conflict.status()).toBe(409); expect((await conflict.json()).error.code).toBe('idempotency_key_reused')
  const reloadPost: string[] = []; page.on('request', r => { if (r.method() === 'POST') reloadPost.push(r.url()) })
  await page.reload(); await expect(page.getByLabel('本地 Token', { exact: true })).toHaveValue('')
  await expect(page.getByText(body.trip_id, { exact: false })).toHaveCount(0)
  const fetched = page.waitForResponse(r => r.request().method() === 'GET' && r.url().endsWith(`/trips/${body.trip_id}`))
  await credential(page); const refreshed = await fetched
  expect(refreshed.status()).toBe(200); expect(await refreshed.json()).toEqual(body)
  await expect(page.getByText(`旅行 ID：${body.trip_id}`)).toBeVisible()
  expect(reloadPost).toEqual([])
  await page.screenshot({ path: '../Docs/M1-03b_创建回归_刷新读取_桌面.png', fullPage: true })
  for (const width of [1024, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 844 })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: '../Docs/M1-03b_创建回归_刷新读取_移动.png', fullPage: true })
  const persisted = await page.evaluate(async () => ({ local: Object.entries(localStorage), session: Object.entries(sessionStorage), databases: await indexedDB.databases() }))
  expect(persisted).toEqual({ local: [], session: [], databases: [] })
  expect(page.url().includes(tokenA)).toBe(false)
  expect(consoleMessages.some(x => x.includes(tokenA) || x.includes(tokenB))).toBe(false)
  await page.getByRole('button', { name: '清除或更换凭证' }).click()
  await expect(page.getByText(`旅行 ID：${body.trip_id}`)).toHaveCount(0)
  const otherOwnerResponse = page.waitForResponse(r => r.url().endsWith(`/trips/${body.trip_id}`))
  await credential(page, tokenB)
  expect((await otherOwnerResponse).status()).toBe(404)
  await expect(page.getByRole('alert')).toContainText('旅行不存在或当前身份无权读取')
  writeFileSync('../Docs/M1-03b_创建回归_浏览器HTTP证据.json', JSON.stringify({ post: { status: post.status(), key: post.request().headers()['idempotency-key'], request: submitted, response: body }, get: { status: read.status(), identical: true }, replay: { status: replay.status(), identical: true }, changedBodySameKey: conflict.status(), refresh: { status: refreshed.status(), identical: true, automaticPosts: reloadPost.length }, crossOwner: 404, storage: persisted, tokenInUrlOrConsole: false }, null, 2))
})

test('progressive mobile form validates and preserves input, clears on identity change', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 }); await page.goto('/trips/new')
  await page.keyboard.press('Tab'); await expect(page.getByRole('link', { name: '跳至主内容' })).toBeFocused()
  await expect(page.getByRole('link', { name: '跳至主内容' })).toHaveCSS('opacity', '1')
  await credential(page)
  await page.getByRole('button', { name: '保存旅行', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('目的地')
  await page.getByLabel('一句话旅行需求').fill('杭州')
  await page.locator('summary').first().click(); await page.selectOption('#travelers-state', 'provided'); await page.fill('#travelers', '3')
  await page.getByRole('button', { name: '保存旅行', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('人数')
  await expect(page.getByLabel('一句话旅行需求')).toHaveValue('杭州')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: '../Docs/M1-03b_创建回归_表单校验_移动.png', fullPage: true })
  await page.getByRole('button', { name: '清除或更换凭证' }).click(); await credential(page)
  await expect(page.getByLabel('一句话旅行需求')).toHaveValue('')
})

for (const [status, code, message] of [
  [401, 'authentication_required', '凭证无效'], [403, 'local_only_api', '本地环境不允许访问'], [404, 'resource_not_found', '旅行不存在'],
  [409, 'command_in_progress', '仍在处理中'], [409, 'idempotency_key_reused', '不同内容'], [422, 'invalid_request', '输入有误'], [503, 'business_dependency_unavailable', '依赖不可用'],
] as const) test(`HTTP ${status} ${code} displays safe error`, async ({ page }) => {
  await page.route('**/api/v1/trips', r => r.fulfill({ status, json: { error: { code, message: 'private diagnostic' } } }))
  await begin(page); await page.getByRole('button', { name: '保存旅行', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText(message)
  await expect(page.getByLabel('一句话旅行需求')).toHaveValue('上海出发去杭州放松，日期和人数稍后补充。')
  await expect(page.getByText('private diagnostic')).toHaveCount(0)
  await expect(page.getByText('旅行创建成功（HTTP 201）。')).toHaveCount(0)
  if (code === 'idempotency_key_reused') await expect(page.getByRole('button', { name: '使用原 key 和原内容重试' })).toHaveCount(0)
})

test('browser timeout, unchanged retry and edited request use correct idempotency keys', async ({ page }) => {
  const calls: { key: string | undefined; body: string | null }[] = []
  await page.route('**/api/v1/trips', async r => {
    calls.push({ key: r.request().headers()['idempotency-key'], body: r.request().postData() })
    if (calls.length === 1) return // intentionally no response until AbortController timeout
    await r.abort('failed')
  })
  await begin(page); await page.getByRole('button', { name: '保存旅行', exact: true }).click()
  await expect(page.getByRole('button', { name: '正在保存旅行…' })).toBeDisabled()
  await expect(page.getByRole('alert')).toContainText('请求超时', { timeout: 17000 })
  await page.getByRole('button', { name: '使用原 key 和原内容重试' }).click(); await expect(page.getByRole('alert')).toContainText('网络连接中断')
  expect(calls[1]).toEqual(calls[0])
  await page.getByLabel('一句话旅行需求').fill('去苏州')
  await page.getByRole('button', { name: '保存旅行', exact: true }).click(); await expect(page.getByRole('alert')).toContainText('网络连接中断')
  expect(calls[2]?.key).not.toBe(calls[1]?.key); expect(calls[2]?.body).not.toBe(calls[1]?.body)
  await page.reload(); await credential(page); expect(calls).toHaveLength(3)
})

test('malformed response and HTTP 200 never claim creation success', async ({ page }) => {
  await page.route('**/api/v1/trips', r => r.fulfill({ status: 200, json: { trip_id: 'fake', booked: true, working: {} } }))
  await begin(page); await page.getByRole('button', { name: '保存旅行', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('非预期成功状态')
  await expect(page.getByText('旅行创建成功（HTTP 201）。')).toHaveCount(0)
  await page.unroute('**/api/v1/trips'); await page.route('**/api/v1/trips', r => r.fulfill({ status: 201, body: '<html>invalid</html>' }))
  await page.getByRole('button', { name: '使用原 key 和原内容重试' }).click(); await expect(page.getByRole('alert')).toContainText('不是有效 JSON')
})
