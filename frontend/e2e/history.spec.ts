import { test, expect } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { writeFileSync, readFileSync } from 'node:fs'
const legacyEvidence = JSON.parse(readFileSync('../Docs/M1-05a_PostgreSQL证据.json','utf-8'))
const token = process.env.EASYTRAVEL_BROWSER_TOKEN_A!
const headers = () => ({ Authorization: `Bearer ${token}`, 'Idempotency-Key': randomUUID() })
function control(action: string, id: string) {
  const r = spawnSync(process.env.EASYTRAVEL_PYTHON!, ['-m','scripts.m106b_test_control',action,id], { cwd:'..', env:process.env, encoding:'utf-8', timeout:20000 })
  expect(r.status,r.stderr).toBe(0); return action === 'proof' ? JSON.parse(r.stdout) : undefined
}

test('E08 E09 E15 E16 E22 real immutable history, refresh credentials, mobile and owner boundary', async ({ page, request }) => {
  const p = (value: unknown) => ({state:'provided',value}), no = {state:'no_preference',value:null}
  const created = await request.post('/api/v1/trips',{headers:headers(),data:{requirements:{origin:p('上海'),destination:p('杭州'),dates:p({mode:'fixed',start_date:'2026-12-04',end_date:'2026-12-06'}),days:p(3),travelers:p(2),target_budget_cents:no,hard_limit_cents:p(230000),transport:p('公共交通'),hotel:no,restrictions:no,raw_text:null}}})
  expect(created.status()).toBe(201); const t = await created.json(), path = '/api/v1/trips/'+t.trip_id
  expect((await request.post(path+'/generations',{headers:headers(),data:{expected_intake_revision:1,expected_working_revision:null,retry_of:null}})).status()).toBe(202); control('run',t.trip_id)
  async function confirm() {
    const preview = await (await request.get(path+'/confirmation-preview',{headers:headers()})).json()
    const r = await request.post(path+'/confirmations',{headers:headers(),data:{expected_intake_revision:preview.intake_revision,expected_working_revision:preview.working_revision,seen_disclosure_hash:preview.disclosure_hash,seen_risk_hash:preview.risk_hash,explicit_confirmation:true}})
    expect(r.status()).toBe(201); return (await r.json()).snapshot
  }
  const s1 = await confirm(), trip = await (await request.get(path,{headers:headers()})).json()
  const input = trip.intake.input; input.dates = p({mode:'fixed',start_date:'2026-12-11',end_date:'2026-12-13'}); input.hard_limit_cents = p(300000); input.hotel = p('安静的酒店')
  expect((await request.put(path+'/requirements',{headers:headers(),data:{expected_intake_revision:1,expected_working_revision:1,requirements:input}})).status()).toBe(200)
  const generation = await request.post(path+'/generations',{headers:headers(),data:{expected_intake_revision:2,expected_working_revision:1,retry_of:null}})
  expect(generation.status()).toBe(202); control('run',t.trip_id)
  const g = await (await request.get(path+'/generations/'+(await generation.json()).run_id,{headers:headers()})).json()
  const pp = path+'/proposals/'+g.result.proposal_id, proposal = await (await request.get(pp,{headers:headers()})).json()
  expect((await request.post(pp+'/decisions',{headers:headers(),data:{decision:'approve',expected_intake_revision:2,expected_working_revision:1,seen_candidate_hash:proposal.proposal.candidate_hash,item_ids:proposal.proposal.touched_item_ids,explicit_authorization:true}})).status()).toBe(200)
  const s2 = await confirm()
  // Leave current working draft UNCONFIRMED while both historical snapshots remain accessible.
  const changed = await request.post(path+'/change-intents',{headers:headers(),data:{expected_intake_revision:2,expected_working_revision:2,intent:'第二天安排轻松一点',retry_of:null}})
  expect(changed.status()).toBe(202); control('run',t.trip_id)
  const cr = await (await request.get(path+'/generations/'+(await changed.json()).run_id,{headers:headers()})).json()
  const cp = path+'/proposals/'+cr.result.proposal_id, c = await (await request.get(cp,{headers:headers()})).json()
  expect((await request.post(cp+'/decisions',{headers:headers(),data:{decision:'approve',expected_intake_revision:2,expected_working_revision:2,seen_candidate_hash:c.proposal.candidate_hash,item_ids:c.proposal.touched_item_ids,explicit_authorization:c.proposal.level==='L3'}})).status()).toBe(200)
  const before = control('proof',t.trip_id)
  const first = await request.get(path+'/snapshots?limit=1',{headers:headers()}), page1 = await first.json()
  expect(first.status()).toBe(200); expect(page1.items).toHaveLength(1); expect(page1.next_cursor).toBeTruthy()
  const second = await request.get(path+'/snapshots?limit=1&cursor='+encodeURIComponent(page1.next_cursor),{headers:headers()}), page2 = await second.json()
  expect(second.status()).toBe(200); expect(page2.items[0].snapshot_id).toBe(s1.snapshot.snapshot_id); expect(page2.next_cursor).toBeNull()
  const errors: unknown[] = []
  for (const [url, customHeaders, status] of [
    [path+'/snapshots?cursor=tampered',headers(),422],
    [path+'/snapshots?limit=101',headers(),422],
    [path+'/snapshots',{},401],
    [path+'/snapshots',{Authorization:`Bearer ${process.env.EASYTRAVEL_BROWSER_TOKEN_B!}`},404],
    [path+'/snapshots/'+s1.snapshot.snapshot_id,headers(),200],
    [path+'/snapshots/missing',headers(),404],
    [path+'/snapshots',{...headers(),Origin:'https://evil.example'},403],
  ] as const) {
    const r = await request.get(url,{headers:customHeaders}); expect(r.status()).toBe(status)
    errors.push({method:'GET',path:url,status:r.status(),body:await r.json()})
  }
  const reads: unknown[] = []
  page.on('response', async r => { if (new URL(r.url()).pathname.startsWith('/api/v1/trips/') && new URL(r.url()).pathname.includes('/snapshots') && r.status()===200) reads.push({path:new URL(r.url()).pathname,status:r.status(),body:await r.json()}) })
  async function credential(value=token) { await page.getByLabel('本地 Token',{exact:true}).fill(value); await page.getByRole('button',{name:'使用本地凭证'}).click() }
  await page.goto('/trips/'+t.trip_id); await credential(); await page.getByRole('link',{name:'查看确认历史',exact:true}).click()
  await expect(page.getByRole('link',{name:'查看历史版本 · 来源 r2',exact:true})).toBeVisible()
  for (const width of [1280,390,320]) {
    await page.setViewportSize({width,height:900}); await page.locator('.history-page').screenshot({path:`../Docs/M1-07_历史列表_${width}.png`})
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  }
  await page.getByRole('link',{name:'查看历史版本 · 来源 r1',exact:true}).click()
  await expect(page.getByRole('heading',{name:'历史每日行程',exact:true})).toBeVisible()
  await expect(page.locator('.history-page')).toContainText('2026-12-04'); await expect(page.locator('.history-page')).not.toContainText('2026-12-11')
  for (const width of [1280,390,320]) {
    await page.setViewportSize({width,height:900}); await page.screenshot({path:`../Docs/M1-07_历史详情_${width}.png`,fullPage:true})
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  }
  await page.reload(); await expect(page.getByRole('heading',{name:'历史每日行程',exact:true})).toHaveCount(0); await credential()
  await expect(page.getByRole('heading',{name:'历史每日行程',exact:true})).toBeVisible()
  await page.getByRole('link',{name:'返回确认历史列表',exact:true}).click(); await page.getByRole('link',{name:'查看历史版本 · 来源 r2',exact:true}).click()
  await expect(page.locator('.history-page')).toContainText('2026-12-11'); await expect(page.locator('.history-page')).toContainText('¥3000.00')
  await page.getByRole('link',{name:'返回当前工作区',exact:true}).click(); await expect(page.getByRole('heading',{name:'每日行程',exact:true})).toBeVisible()
  const current = await (await request.get(path,{headers:headers()})).json(); expect(current.plan_confirmation).toBe('UNCONFIRMED')
  await page.goto(`/trips/${t.trip_id}/history/${s1.snapshot.snapshot_id}`); await credential()
  await expect(page.getByRole('heading',{name:'历史每日行程',exact:true})).toBeVisible()
  await page.getByRole('button',{name:'清除或更换凭证'}).click(); await credential(process.env.EASYTRAVEL_BROWSER_TOKEN_B!)
  await expect(page.getByRole('alert')).toBeVisible(); await expect(page.getByRole('heading',{name:'历史每日行程',exact:true})).toHaveCount(0)
  const after = control('proof',t.trip_id); expect(after).toEqual(before)
  writeFileSync('../Docs/M1-07_浏览器HTTP与PostgreSQL证据.json',JSON.stringify({tripId:t.trip_id,s1,s2,current,reads,pagination:{page1,page2},errors,before,after,readOnly:true},null,2))
})

test('legacy history shows missing facts and list loads additional pages with browser navigation', async ({ page }) => {
  const view = { ...legacyEvidence.result.snapshot, context:null, risk_disclosure:null }, s = view.snapshot
  const summary = { snapshot_id:s.snapshot_id, source_working_revision:1, confirmed_at:s.confirmed_at, budget:s.budget }
  await page.route('**/api/v1/trips/*/snapshots**', async route => {
    const url = new URL(route.request().url())
    const body = url.pathname.endsWith('/snapshots') ? url.searchParams.has('cursor') ? {items:[{...summary,snapshot_id:'older'}],next_cursor:null} : {items:[summary],next_cursor:'opaque'} : view
    await route.fulfill({json:body})
  })
  await page.goto(`/trips/${s.trip_id}/history`)
  await page.getByLabel('本地 Token',{exact:true}).fill(token); await page.getByRole('button',{name:'使用本地凭证'}).click()
  await page.getByRole('button',{name:'加载更多历史版本',exact:true}).click()
  await expect(page.locator('.history-list li')).toHaveCount(2)
  await page.locator('.history-list a').first().click()
  await expect(page.getByRole('heading',{name:'历史每日行程',exact:true})).toBeVisible()
  await expect(page.getByText('需求来源与生成上下文：此历史版本未记录相关信息。',{exact:true})).toBeVisible()
  await expect(page.getByText('风险披露：此历史版本未记录相关信息；无法推断用户曾经审阅过哪些风险。',{exact:true})).toBeVisible()
  await expect(page.locator('input,textarea,select')).toHaveCount(0)
  await page.goBack(); await expect(page.getByRole('heading',{name:'确认历史',exact:true})).toBeVisible()
})
