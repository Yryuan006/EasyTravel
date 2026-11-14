import { test, expect, type Page, type APIRequestContext, type Response as PWResponse } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
const a=process.env.EASYTRAVEL_BROWSER_TOKEN_A!, b=process.env.EASYTRAVEL_BROWSER_TOKEN_B!
const headers=(token=a)=>({ Authorization:`Bearer ${token}`,'Idempotency-Key':randomUUID() })
const records:any[]=[]
function control(action:string,id='') {
  const r=spawnSync(process.env.EASYTRAVEL_PYTHON!,['-m','scripts.m105b_test_control',action,id],{cwd:'..',env:process.env,encoding:'utf-8',timeout:20000})
  expect(r.status,r.stderr).toBe(0)
  return action==='proof'?JSON.parse(r.stdout):undefined
}
function section(page:Page) { return page.locator('.confirmation-review') }
async function credential(page:Page,token=a) { await page.getByLabel('本地 Token',{exact:true}).fill(token);await page.getByRole('button',{name:'使用本地凭证'}).click() }
async function seed(request:APIRequestContext,mode='run') {
  control('rules-v1')
  const p=(value:unknown)=>({state:'provided',value}), none={state:'no_preference',value:null}
  const r=await request.post('/api/v1/trips',{headers:headers(),data:{requirements:{origin:p('上海'),destination:p('杭州'),dates:p({mode:'fixed',start_date:'2026-12-04',end_date:'2026-12-06'}),days:p(3),travelers:p(2),target_budget_cents:none,hard_limit_cents:p(230000),transport:p('公共交通优先'),hotel:none,restrictions:none,raw_text:null}}})
  expect(r.status()).toBe(201);const t=await r.json();const path='/api/v1/trips/'+t.trip_id
  const g=await request.post(path+'/generations',{headers:headers(),data:{expected_intake_revision:1,expected_working_revision:null,retry_of:null}})
  expect(g.status()).toBe(202);control(mode,t.trip_id)
  return (await request.get(path,{headers:headers()})).json()
}
async function open(page:Page,t:any) {
  await page.goto('/trips/'+t.trip_id);await credential(page)
  await expect(section(page).getByRole('button',{name:'查看预算与风险，确认当前行程'})).toBeEnabled()
  const response=page.waitForResponse(r=>r.url().endsWith('/confirmation-preview'))
  await section(page).getByRole('button',{name:'查看预算与风险，确认当前行程'}).click()
  const r=await response;expect(r.status()).toBe(200)
  await expect(section(page).getByRole('heading',{name:'未知费用',exact:false})).toBeVisible()
  return r.json()
}
async function send(page:Page) {
  await section(page).getByRole('checkbox').check()
  const response=page.waitForResponse(r=>r.url().endsWith('/confirmations')&&r.request().method()==='POST')
  await section(page).getByRole('button',{name:'确认当前行程 r1',exact:true}).click()
  return response
}
async function record(r:PWResponse) {
  return {method:r.request().method(),path:new URL(r.url()).pathname,status:r.status(),body:r.request().postDataJSON(),key:r.request().headers()['idempotency-key'],response:await r.json()}
}
test.beforeEach(()=>control('rules-v1'))
test.afterEach(()=>control('rules-v1'))
test.afterAll(()=>writeFileSync('../Docs/M1-05b_HTTP与数据库证据.json',JSON.stringify({records},null,2)))

test('E06 E08 real preview, explicit keyboard confirmation, SQL facts, refresh and owner isolation',async({page,request})=>{
  const t=await seed(request);const p=await open(page,t)
  expect(p.budget.total_cents).toBeNull();expect(p.budget.verification).toBe('INDETERMINATE')
  await expect(section(page).getByRole('checkbox')).not.toBeChecked();await expect(section(page).getByRole('button',{name:'确认当前行程 r1',exact:true})).toBeDisabled()
  await expect(section(page).locator('.unknown-costs')).not.toContainText('¥0.00')
  await expect(section(page).locator('.risk-list')).toContainText('尚未预订')
  for(const width of [1280,390,320]) {
    await page.setViewportSize({width,height:900});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
    await section(page).screenshot({path:`../Docs/M1-05b_确认预览_${width}.png`})
  }
  await section(page).getByRole('checkbox').focus();await page.keyboard.press('Space')
  await page.keyboard.press('Tab');await expect(section(page).getByRole('button',{name:'确认当前行程 r1',exact:true})).toBeFocused()
  const response=page.waitForResponse(r=>r.url().endsWith('/confirmations')&&r.request().method()==='POST')
  await page.keyboard.press('Enter');const post=await response;expect(post.status()).toBe(201)
  const confirmation=await record(post)
  await expect(section(page).getByText('规划确认成功（HTTP 201，已核对当前指针）')).toBeVisible()
  for(const width of [1280,390,320]) {await page.setViewportSize({width,height:900});await section(page).locator('.confirmation-receipt').screenshot({path:`../Docs/M1-05b_确认成功_${width}.png`})}
  const proof=control('proof',t.trip_id);expect(proof.confirmed_snapshots).toHaveLength(1);expect(proof.confirmation_disclosures).toHaveLength(1);expect(proof.operation_receipts).toHaveLength(1)
  expect(proof.specific_authorizations).toHaveLength(0);expect(proof.draft_versions[0].item_confirmations).toEqual([])
  expect(proof.working_drafts[0].latest_confirmed_version_id).toBe(confirmation.response.snapshot.snapshot.snapshot_id)
  expect(proof.confirmation_disclosures[0].risk_hash).toBe(p.risk_hash)
  const commands=proof.api_commands.filter((c:any)=>c.path.endsWith('/confirmations'));expect(commands).toHaveLength(1);expect(commands[0].result).toEqual(confirmation.response)
  await page.reload();await expect(page.getByLabel('本地 Token',{exact:true})).toHaveValue('');await credential(page)
  await expect(section(page).getByText(/当前行程已确认规划/)).toBeVisible()
  expect(await page.evaluate(()=>({local:Object.keys(localStorage),session:Object.keys(sessionStorage)}))).toEqual({local:[],session:[]})
  expect(page.url()).not.toContain(a)
  const otherPreview=await request.get(`/api/v1/trips/${t.trip_id}/confirmation-preview`,{headers:headers(b)});expect(otherPreview.status()).toBe(404)
  const otherPost=await request.post(`/api/v1/trips/${t.trip_id}/confirmations`,{headers:headers(b),data:confirmation.body});expect(otherPost.status()).toBe(404)
  await page.getByRole('button',{name:'清除或更换凭证'}).click();await expect(section(page)).toHaveCount(0);await credential(page,b)
  await expect(page.getByRole('alert')).toContainText('不存在或当前身份无权读取')
  records.push({scenario:'E06/E08/E16 real chain and refresh',tripId:t.trip_id,preview:p,confirmation,proof})
})

test('known Mock prices and explicit zero remain estimates in confirmation',async({page,request})=>{
  const t=await seed(request,'estimated'),p=await open(page,t)
  await expect(section(page).locator('.budget-state')).toHaveText('ESTIMATED · WITHIN_LIMIT')
  await expect(section(page).locator('.cost-breakdown')).toContainText('¥0.00');await expect(section(page).locator('.cost-breakdown')).toContainText('Mock')
  const r=await send(page);expect(r.status()).toBe(201)
  records.push({scenario:'known estimates and explicit zero',tripId:t.trip_id,preview:p,confirmation:await record(r),proof:control('proof',t.trip_id)})
})

test('FIX-01 confirmed r1 becomes historical after saving i2 in the same workspace',async({page,request})=>{
  const t=await seed(request);await open(page,t)
  const post=await send(page);expect(post.status()).toBe(201)
  const receipt=await post.json(), sid=receipt.snapshot.snapshot.snapshot_id
  await expect(section(page).getByText('规划确认成功（HTTP 201，已核对当前指针）')).toBeVisible()
  const before=control('proof',t.trip_id)
  await section(page).getByRole('button',{name:'读取最新行程并核对确认状态'}).click()
  await expect(section(page).getByText('规划确认成功（HTTP 201，已核对当前指针）')).toBeVisible()
  await page.getByRole('button',{name:'修改需求',exact:true}).click()
  await page.getByLabel('目的地',{exact:true}).fill('苏州')
  const saved=page.waitForResponse(r=>r.request().method()==='PUT'&&r.url().endsWith('/requirements'))
  await page.getByRole('button',{name:'保存修改',exact:true}).click()
  const response=await saved;expect(response.status()).toBe(200);const trip=await response.json()
  expect(trip.intake.intake_revision).toBe(2);expect(trip.synchronization).toBe('STALE');expect(trip.plan_confirmation).toBe('UNCONFIRMED')
  expect(trip.latest_confirmed_version_id).toBe(sid)
  await expect(section(page).getByText('此前版本已确认，当前规划未确认',{exact:true})).toBeVisible()
  await expect(section(page).getByText('规划确认成功（HTTP 201，已核对当前指针）')).toHaveCount(0)
  await expect(section(page).locator('.confirmation-status')).toHaveCount(0)
  await expect(section(page).locator('.confirmation-receipt')).toContainText(sid)
  await expect(section(page).getByRole('button',{name:'重新读取确认预览',exact:true})).toBeEnabled()
  await section(page).getByRole('button',{name:'重新读取确认预览',exact:true}).click()
  await expect(section(page).getByRole('checkbox')).not.toBeChecked()
  await expect(section(page).getByRole('checkbox')).toBeDisabled()
  const after=control('proof',t.trip_id)
  expect(after.confirmed_snapshots).toEqual(before.confirmed_snapshots)
  expect(after.confirmation_disclosures).toEqual(before.confirmation_disclosures)
  expect(after.draft_versions).toEqual(before.draft_versions)
  await section(page).locator('.confirmation-receipt').screenshot({path:'../Docs/M1-05b_FIX-01_历史确认状态.png'})
  writeFileSync('../Docs/M1-05b_FIX-01_连续交互证据.json',JSON.stringify({tripId:t.trip_id,receipt,updatedTrip:trip,before,after},null,2))
})

for(const mode of ['over','blocker','stale'])test(`E07 blocked 200 preview ${mode}`,async({page,request})=>{
  const t=await seed(request,mode==='stale'?'run':mode)
  if(mode==='stale') {
    const input=structuredClone(t.intake.input);input.hard_limit_cents.value=1
    expect((await request.put(`/api/v1/trips/${t.trip_id}/requirements`,{headers:headers(),data:{expected_intake_revision:1,expected_working_revision:1,requirements:input}})).status()).toBe(200)
  }
  const p=await open(page,t);expect(p.confirmation_allowed).toBe(false)
  await expect(section(page).getByText('当前不能确认',{exact:true})).toBeVisible();await expect(section(page).getByRole('checkbox')).toBeDisabled();await expect(section(page).getByRole('button',{name:'确认当前行程 r1',exact:true})).toBeDisabled()
  await section(page).screenshot({path:`../Docs/M1-05b_阻塞_${mode}.png`})
  const proof=control('proof',t.trip_id);expect(proof.confirmed_snapshots).toHaveLength(0)
  records.push({scenario:'blocked '+mode,tripId:t.trip_id,preview:p,proof})
})

for(const mode of ['network','timeout'])test(`E12 E14 committed ${mode} response replays original key and body`,async({page,request})=>{
  test.setTimeout(45000)
  const t=await seed(request);await open(page,t);const sent:any[]=[]
  await page.route('**/confirmations',async route=>{
    const r=route.request();if(r.method()!=='POST')return route.continue()
    const response=await route.fetch();sent.push({path:new URL(r.url()).pathname,key:r.headers()['idempotency-key'],body:r.postData(),response:await response.json()})
    if(mode==='timeout')await new Promise(r=>setTimeout(r,13000))
    await route.abort('failed').catch(()=>{})
  })
  await section(page).getByRole('checkbox').check();await section(page).getByRole('button',{name:'确认当前行程 r1',exact:true}).dblclick({delay:10})
  await expect(section(page).getByRole('alert')).toContainText(mode==='timeout'?'超时':'网络连接中断',{timeout:17000})
  expect(sent).toHaveLength(1);await expect(page.getByRole('button',{name:'修改需求',exact:true})).toBeDisabled()
  await expect(section(page).getByRole('checkbox')).not.toBeChecked()
  await section(page).screenshot({path:`../Docs/M1-05b_失败_${mode}.png`})
  await page.unroute('**/confirmations')
  const replay=page.waitForResponse(r=>r.url().endsWith('/confirmations')&&r.request().method()==='POST')
  await section(page).getByRole('button',{name:'使用原确认请求安全重试'}).click();const r=await replay;expect(r.status()).toBe(201)
  expect(r.request().headers()['idempotency-key']).toBe(sent[0].key);expect(r.request().postData()).toBe(sent[0].body);expect(await r.json()).toEqual(sent[0].response)
  await expect(section(page).getByText('规划确认成功（HTTP 201，已核对当前指针）')).toBeVisible()
  const proof=control('proof',t.trip_id);expect(proof.confirmed_snapshots).toHaveLength(1);expect(proof.api_commands.filter((c:any)=>c.path.endsWith('/confirmations'))).toHaveLength(1)
  records.push({scenario:mode+' after real commit, original replay',tripId:t.trip_id,sent,replay:await record(r),proof})
})

for(const mode of ['intake','working','disclosure','risk'])test(`E13 E17 actual API rejects old ${mode}`,async({page,request})=>{
  const t=await seed(request)
  if(mode==='disclosure')await page.route('**/confirmation-preview',async route=>{const r=await route.fetch();await route.fulfill({response:r,json:{...await r.json(),disclosure_hash:'0'.repeat(64)}})})
  const p=await open(page,t)
  if(mode==='intake')expect((await request.put(`/api/v1/trips/${t.trip_id}/requirements`,{headers:headers(),data:{expected_intake_revision:1,expected_working_revision:1,requirements:t.intake.input}})).status()).toBe(200)
  if(mode==='working')control('advance',t.trip_id)
  if(mode==='risk')control('rules-v2')
  const r=await send(page);expect(r.status()).toBe(409)
  const code=({intake:'stale_intake',working:'stale_revision',disclosure:'disclosure_not_acknowledged',risk:'risk_not_acknowledged'})[mode]
  expect((await r.json()).error.code).toBe(code)
  await expect(section(page).getByRole('checkbox')).toHaveCount(0)
  await expect(section(page).getByRole('alert')).toContainText('重新')
  const proof=control('proof',t.trip_id);expect(proof.confirmed_snapshots).toHaveLength(0)
  await section(page).screenshot({path:`../Docs/M1-05b_冲突_${mode}.png`})
  records.push({scenario:'real 409 old '+mode,tripId:t.trip_id,preview:p,rejection:await record(r),proof})
})

test('E17 reload changed risks clears consent, never auto confirms new hash',async({page,request})=>{
  const t=await seed(request);const p=await open(page,t);await section(page).getByRole('checkbox').check();control('rules-v2')
  const response=page.waitForResponse(r=>r.url().endsWith('/confirmation-preview'))
  await section(page).getByRole('button',{name:'重新读取确认预览',exact:true}).click();const q=await (await response).json()
  expect(q.risk_hash).not.toBe(p.risk_hash);await expect(section(page).getByRole('checkbox')).not.toBeChecked();await expect(section(page).getByRole('button',{name:'确认当前行程 r1',exact:true})).toBeDisabled()
  expect(control('proof',t.trip_id).confirmed_snapshots).toHaveLength(0)
  records.push({scenario:'actual risk rule change clears consent',tripId:t.trip_id,old:p,current:q})
})

for(const status of [401,403,404,422,503])test(`safe confirmation HTTP ${status}`,async({page,request})=>{
  const t=await seed(request);await open(page,t)
  await page.route('**/confirmations',route=>route.fulfill({status,json:{error:{code:status===503?'business_dependency_unavailable':'test_failure',message:'PRIVATE SQL DSN'}}}))
  const r=await send(page);expect(r.status()).toBe(status)
  await expect(section(page).getByRole('alert')).toBeVisible();await expect(section(page)).not.toContainText('PRIVATE');await expect(section(page)).not.toContainText('规划确认成功')
  expect(control('proof',t.trip_id).confirmed_snapshots).toHaveLength(0)
  if(status===503)await expect(section(page).getByRole('button',{name:'使用原确认请求安全重试'})).toBeVisible()
  if(status===503)await section(page).screenshot({path:'../Docs/M1-05b_失败_503.png'})
})

for(const status of [200,201])test(`HTTP ${status} invalid body never displays confirmation success`,async({page,request})=>{
  const t=await seed(request);await open(page,t)
  await page.route('**/confirmations',route=>route.fulfill({status,json:{snapshot:{}}}))
  await send(page)
  await expect(section(page).getByRole('alert')).toContainText(status===200?'非预期成功状态':'接口契约');await expect(section(page)).not.toContainText('规划确认成功')
  await expect(section(page).getByRole('button',{name:'使用原确认请求安全重试'})).toBeVisible()
})

test('E14 preview 503 stays blocked, reload recovers without automatic consent',async({page,request})=>{
  const t=await seed(request)
  await page.goto('/trips/'+t.trip_id);await credential(page)
  await page.route('**/confirmation-preview',route=>route.fulfill({status:503,json:{error:{code:'business_dependency_unavailable',message:'PRIVATE DSN'}}}))
  await expect(section(page).getByRole('button',{name:'查看预算与风险，确认当前行程'})).toBeEnabled()
  await section(page).getByRole('button',{name:'查看预算与风险，确认当前行程'}).click()
  await expect(section(page).getByRole('alert')).toContainText('不可用');await expect(section(page)).not.toContainText('PRIVATE')
  await expect(section(page).getByRole('checkbox')).toHaveCount(0)
  await section(page).screenshot({path:'../Docs/M1-05b_预览失败.png'})
  await page.unroute('**/confirmation-preview')
  await section(page).getByRole('button',{name:'重新读取确认预览',exact:true}).click()
  await expect(section(page).getByRole('checkbox')).not.toBeChecked()
  expect(control('proof',t.trip_id).confirmed_snapshots).toHaveLength(0)
})
