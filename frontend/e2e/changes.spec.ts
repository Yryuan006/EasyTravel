import { test, expect, type Page, type APIRequestContext } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { writeFileSync } from 'node:fs'
const token = process.env.EASYTRAVEL_BROWSER_TOKEN_A!
const headers = () => ({ Authorization: `Bearer ${token}`, 'Idempotency-Key': randomUUID() })
const records: any[] = []
function control(action: string, id: string) {
  const r = spawnSync(process.env.EASYTRAVEL_PYTHON!, ['-m','scripts.m106a_test_control',action,id], { cwd: '..', env: process.env, encoding: 'utf-8', timeout: 20000 })
  expect(r.status,r.stderr).toBe(0); return action === 'proof' ? JSON.parse(r.stdout) : undefined
}
async function credential(page: Page) { await page.getByLabel('本地 Token',{exact:true}).fill(token); await page.getByRole('button',{name:'使用本地凭证'}).click() }
async function seed(request: APIRequestContext) {
  const p = (value: unknown) => ({ state:'provided',value }), no = {state:'no_preference',value:null}
  const r = await request.post('/api/v1/trips',{headers:headers(),data:{requirements:{origin:p('上海'),destination:p('杭州'),dates:p({mode:'fixed',start_date:'2026-12-04',end_date:'2026-12-06'}),days:p(3),travelers:p(2),target_budget_cents:no,hard_limit_cents:p(230000),transport:p('公共交通'),hotel:no,restrictions:no,raw_text:null}}})
  expect(r.status()).toBe(201); const t = await r.json(), path = '/api/v1/trips/'+t.trip_id
  expect((await request.post(path+'/generations',{headers:headers(),data:{expected_intake_revision:1,expected_working_revision:null,retry_of:null}})).status()).toBe(202)
  control('run',t.trip_id)
  return (await request.get(path,{headers:headers()})).json()
}
async function open(page: Page, t: any) {
  await page.goto('/trips/'+t.trip_id); await credential(page)
  await expect(page.getByRole('heading',{name:'每日行程',exact:true})).toBeVisible()
}
async function propose(page: Page, t: any, intent: string) {
  const section = page.locator('.change-review')
  await section.getByLabel('修改意图',{exact:true}).fill(intent)
  const accepted = page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/change-intents'))
  await section.getByRole('button',{name:'生成修改提案',exact:true}).click()
  const a = await accepted;expect(a.status()).toBe(202)
  control('run',t.trip_id)
  await expect(section.getByRole('heading',{name:/修改提案 · L[123] · PENDING/})).toBeVisible()
  const p = await page.request.get(`/api/v1/trips/${t.trip_id}/proposals/${(await(await page.request.get(`/api/v1/trips/${t.trip_id}/generations/${(await a.json()).run_id}`,{headers:headers()})).json()).result.proposal_id}`,{headers:headers()})
  return {section,proposal:await p.json(),accepted:await a.json()}
}
test.afterAll(()=>writeFileSync('../Docs/M1-06a_HTTP与数据库证据.json',JSON.stringify({records},null,2)))

test('E09 activity change preserves confirmed history, clears old consent, and reloads persisted r2',async({page,request})=>{
  const t=await seed(request);await open(page,t)
  const confirm = page.locator('.confirmation-review')
  await confirm.getByRole('button',{name:'查看预算与风险，确认当前行程'}).click();await confirm.getByRole('checkbox').check()
  const c = page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/confirmations'))
  await confirm.getByRole('button',{name:'确认当前行程 r1',exact:true}).click();expect((await c).status()).toBe(201)
  await expect(confirm.getByText('规划确认成功（HTTP 201，已核对当前指针）')).toBeVisible()
  const before=control('proof',t.trip_id), {section,proposal,accepted}=await propose(page,t,'第二天安排轻松一点')
  expect(proposal.proposal.level).toBe('L2');await expect(section.getByRole('checkbox')).not.toBeChecked()
  await expect(section.getByRole('button',{name:'应用到工作草案',exact:true})).toBeDisabled()
  await section.getByRole('checkbox').check()
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/decisions'))
  await section.getByRole('button',{name:'应用到工作草案',exact:true}).click();const r=await response;expect(r.status()).toBe(200)
  const decisionRecord={status:r.status(),body:r.request().postDataJSON(),key:r.request().headers()['idempotency-key'],result:await r.json()}
  await expect(section.getByText(/修改已保存并核对：工作稿 r2/)).toBeVisible()
  await expect(confirm.getByText('此前版本已确认，当前规划未确认',{exact:true})).toBeVisible()
  await expect(confirm.getByRole('checkbox')).toHaveCount(0);await expect(confirm.getByText('规划确认成功（HTTP 201，已核对当前指针）')).toHaveCount(0)
  await confirm.getByRole('button',{name:'重新读取确认预览',exact:true}).click()
  await expect(confirm.getByRole('checkbox')).not.toBeChecked()
  const after=control('proof',t.trip_id)
  expect(after.confirmed_snapshots).toEqual(before.confirmed_snapshots);expect(after.confirmation_disclosures).toEqual(before.confirmation_disclosures)
  expect(after.specific_authorizations).toHaveLength(0);expect(after.draft_versions).toHaveLength(2)
  await section.screenshot({path:'../Docs/M1-06a_普通修改成功.png'})
  await page.reload();await credential(page)
  await expect(page.getByRole('heading',{name:'每日行程',exact:true})).toBeVisible()
  await expect(page.locator('.change-review').getByText(/历史确认快照指针/)).toContainText(before.confirmed_snapshots[0].snapshot_id)
  await page.locator('.change-review').getByRole('button',{name:'读取修改运行与提案'}).click()
  await expect(page.locator('.change-review').getByRole('heading',{name:/APPLIED/})).toBeVisible()
  const trip=await(await request.get('/api/v1/trips/'+t.trip_id,{headers:headers()})).json();expect(trip.working.revision).toBe(2);expect(trip.plan_confirmation).toBe('UNCONFIRMED')
  records.push({scenario:'E09',tripId:t.trip_id,accepted,proposal,decision:decisionRecord,before,after,trip})
})

test('E10 E18 hotel L3 requires explicit full scope; lost response replays exact command once',async({page,request})=>{
  const t=await seed(request);await open(page,t)
  const hotel=t.working.payload.items.find((i:any)=>i.kind==='hotel')
  const {section,proposal,accepted}=await propose(page,t,'更换酒店 '+hotel.id)
  expect(proposal.proposal.level).toBe('L3');expect(proposal.requires_specific_authorization).toBe(true)
  for(const width of [1280,390,320]) {await page.setViewportSize({width,height:900});await section.screenshot({path:`../Docs/M1-06a_L3授权_${width}.png`});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)}
  await expect(section.getByRole('button',{name:'明确授权并应用',exact:true})).toBeDisabled()
  const sent:any[]=[]
  await page.route('**/decisions',async route=>{const r=route.request();const response=await route.fetch();sent.push({path:new URL(r.url()).pathname,key:r.headers()['idempotency-key'],body:r.postData(),result:await response.json()});await route.abort('failed')})
  await section.getByRole('checkbox').check();await section.getByRole('button',{name:'明确授权并应用',exact:true}).dblclick({delay:10})
  await expect(section.getByRole('alert')).toContainText('网络连接中断');expect(sent).toHaveLength(1)
  await expect(page.getByRole('button',{name:'修改需求',exact:true})).toBeDisabled()
  await page.unroute('**/decisions');const replay=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/decisions'))
  await section.getByRole('button',{name:'使用原修改请求安全重试'}).click();const r=await replay;expect(r.status()).toBe(200)
  expect(r.request().postData()).toBe(sent[0].body);expect(r.request().headers()['idempotency-key']).toBe(sent[0].key);expect(await r.json()).toEqual(sent[0].result)
  await expect(section.getByText(/修改已保存并核对：工作稿 r2/)).toBeVisible()
  const proof=control('proof',t.trip_id);expect(proof.specific_authorizations).toHaveLength(1);expect(proof.proposal_decisions).toHaveLength(1);expect(proof.draft_versions).toHaveLength(2)
  expect(proof.specific_authorizations[0].consumed_by_operation_id).toBe(sent[0].result.operation_id)
  expect(proof.specific_authorizations[0].item_ids).toEqual(proposal.proposal.touched_item_ids)
  records.push({scenario:'E10/E12/E18',tripId:t.trip_id,accepted,proposal,sent,replay:await r.json(),proof})
})

test('E11 reject persists without grants or new version, reload reads REJECTED',async({page,request})=>{
  const t=await seed(request);await open(page,t)
  const hotel=t.working.payload.items.find((i:any)=>i.kind==='hotel'),{section,proposal}=await propose(page,t,'更换酒店 '+hotel.id)
  const r=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/decisions'))
  await section.getByRole('button',{name:'拒绝此提案',exact:true}).click();const response=await r;expect(response.status()).toBe(200)
  const decisionResult=await response.json()
  expect(response.request().postDataJSON()).not.toHaveProperty('explicit_authorization')
  await expect(section.getByText(/提案已拒绝并核对/)).toBeVisible()
  const proof=control('proof',t.trip_id);expect(proof.specific_authorizations).toHaveLength(0);expect(proof.draft_versions).toHaveLength(1)
  await page.reload();await credential(page);await page.locator('.change-review').getByRole('button',{name:'读取修改运行与提案'}).click()
  await expect(page.locator('.change-review').getByRole('heading',{name:/REJECTED/})).toBeVisible()
  await page.locator('.change-review').screenshot({path:'../Docs/M1-06a_拒绝.png'})
  records.push({scenario:'E11',tripId:t.trip_id,proposal,decision:decisionResult,proof})
})
