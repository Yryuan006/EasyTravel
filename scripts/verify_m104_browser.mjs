import { chromium } from '../frontend/node_modules/@playwright/test/index.mjs'
import { writeFileSync } from 'node:fs'
const browser = await chromium.launch({ headless: true })
try {
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  await page.goto(`http://127.0.0.1:5174/trips/${process.env.EASYTRAVEL_BROWSER_TRIP}`)
  await page.getByLabel('本地 Token', { exact: true }).fill(process.env.EASYTRAVEL_BROWSER_TOKEN_A)
  await page.getByRole('button', { name: '使用本地凭证' }).click()
  await page.getByText('模拟工作稿 r1 · 未确认 · 只读摘要').waitFor()
  if (await page.getByText('无法证明旧工作稿').count()) throw new Error('Incorrect stale draft notice')
  const evidence = []
  for (const width of [1280, 390, 320]) {
    await page.setViewportSize({ width, height: 900 })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
    if (overflow) throw new Error('Horizontal overflow')
    await page.screenshot({ path: `Docs/M1-04a_工作稿摘要_${width}.png`, fullPage: true })
    evidence.push({ width, overflow, notice: await page.locator('.notice').first().innerText() })
  }
  if (errors.length) throw new Error('Browser runtime error')
  writeFileSync('Docs/M1-04a_浏览器兼容.json', JSON.stringify({ evidence, runtimeErrors: errors }, null, 2))
} finally { await browser.close() }
