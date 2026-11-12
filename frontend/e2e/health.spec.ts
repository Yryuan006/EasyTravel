import { test, expect } from '@playwright/test'

test('real browser reaches live backend through Vite proxy', async ({ page, request }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  const response = await request.get('/api/v1/health')
  expect(response.status()).toBe(200)
  const payload: unknown = await response.json()
  expect(payload).toEqual({ status: 'alive', scope: 'http_application_liveness', dependencies: {
    database: 'not_checked', model: 'not_checked', travel_data: 'not_checked',
  } })
  console.log('LIVE /api/v1/health via Vite:', JSON.stringify(payload))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'HTTP 应用存活' })).toBeVisible()
  await expect(page.locator('dd')).toHaveText(['未检查', '未检查', '未检查'])
  await page.getByRole('button', { name: '重新检查' }).click()
  await expect(page.getByRole('heading', { name: 'HTTP 应用存活' })).toBeVisible()
  await page.screenshot({ path: '../Docs/M1-03b_创建回归_健康检查_桌面.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: '../Docs/M1-03b_创建回归_健康检查_移动.png', fullPage: true })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(errors).toEqual([])
})

test('loading, network failure and retry remain honest', async ({ page }) => {
  let release: () => void = () => {}
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/v1/health', async route => { await gate; await route.abort('failed') })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '正在连接后端' })).toBeVisible()
  await expect(page.getByRole('button', { name: '正在检查' })).toBeDisabled()
  await page.screenshot({ path: '../Docs/M1-03b_创建回归_健康检查_加载.png', fullPage: true })
  release()
  await expect(page.getByRole('heading', { name: '健康检查失败' })).toBeVisible()
  await page.screenshot({ path: '../Docs/M1-03b_创建回归_健康检查_失败.png', fullPage: true })
  await page.unroute('**/api/v1/health')
  await page.getByRole('button', { name: '重新检查' }).click()
  await expect(page.getByRole('heading', { name: 'HTTP 应用存活' })).toBeVisible()
})

test('HTTP server failure is never displayed as success', async ({ page }) => {
  await page.route('**/api/v1/health', route => route.fulfill({ status: 503, json: { status: 'alive' } }))
  await page.goto('/')
  await expect(page.getByRole('status')).toContainText('HTTP 503')
  await expect(page.getByText('健康检查已通过')).toHaveCount(0)
})
