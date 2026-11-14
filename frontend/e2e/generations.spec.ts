import { test, expect, type Page, type APIRequestContext } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
const a=process.env.EASYTRAVEL_BROWSER_TOKEN_A!, b=process.env.EASYTRAVEL_BROWSER_TOKEN_B!
const auth=(token=a)=>({Authorization:`Bearer ${token}`})
const headers=()=>({...auth(),'Idempotency-Key':randomUUID()})
const cases: any[]=[]
let runtimeErrors:string[]=[]
test.beforeEach(async({page})=>{runtimeErrors=[];page.on('pageerror',e=>runtimeErrors.push(e.message))})
test.afterEach(()=>expect(runtimeErrors).toEqual([]))
function worker(action: string) {
  const r=spawnSync(process.env.EASYTRAVEL_PYTHON!,['-m','scripts.m104b_worker_control',action],{cwd:'..',env:process.env,encoding:'utf-8',timeout:20000})
  expect(r.status,r.stderr).toBe(0)
}
async function credential(page:Page,token=a) { await page.getByLabel('本地 Token',{exact:true}).fill(token);await page.getByRole('button',{name:'使用本地凭证'}).click() }
async function create(request:APIRequestContext,days=3,limit=230000) {
  const p=(value:unknown)=>({state:'provided',value}), none={state:'no_preference',value:null}
  const r=await request.post('/api/v1/trips',{headers:headers(),data:{requirements:{origin:p('上海'),destination:p('杭州'),dates:p({mode:'fixed',start_date:'2026-12-04',end_date:`2026-12-${String(3+days).padStart(2,'0')}`}),days:p(days),travelers:p(2),target_budget_cents:none,hard_limit_cents:p(limit),transport:p('公共交通优先'),hotel:none,restrictions:none,raw_text:null}}})
  expect(r.status()).toBe(201); return r.json()
}
async function open(page:Page,id:string) { await page.goto(`/trips/${id}`);await credential(page);await expect(page.getByRole('button',{name:'生成模拟行程',exact:true})).toBeVisible() }
async function accepted(page:Page) {
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/generations'))
  await page.getByRole('button',{name:'生成模拟行程',exact:true}).click()
  const r=await response;expect(r.status()).toBe(202);const run=await r.json()
  await expect(page.getByText('生成请求已接受，尚未开始',{exact:true})).toBeVisible()
  expect(run.status).toBe('ACCEPTED');expect(run.result).toBeNull()
  return {httpStatus:r.status(),method:'POST',path:new URL(r.url()).pathname,run,body:r.request().postDataJSON(),key:r.request().headers()['idempotency-key']}
}
test.afterAll(()=>writeFileSync('../Docs/M1-04b_HTTP证据.json',JSON.stringify({cases},null,2)))
for (const days of [1,5]) test(`real ${days} day generation, polling, refresh and saved budget`,async({page,request})=>{
  const t=await create(request,days);await open(page,t.trip_id)
  let posts=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/generations'))posts++})
  const record=await accepted(page);expect(record.body).toEqual({expected_intake_revision:1,expected_working_revision:null,retry_of:null})
  expect(record.key).toMatch(/^[0-9a-f-]{36}$/);expect(posts).toBe(1)
  const runningResponse=page.waitForResponse(async r=>r.request().method()==='GET'&&r.url().endsWith('/generations/'+record.run.run_id)&&(await r.json()).status==='RUNNING')
  worker('claim');await expect(page.getByText('正在生成模拟行程',{exact:true})).toBeVisible()
  const running=await(await runningResponse).json()
  worker('run');await expect(page.getByRole('heading',{name:'每日行程',exact:true})).toBeVisible()
  await expect(page.locator('.day-schedule')).toHaveCount(days);await expect(page.locator('.hotel-card')).toHaveCount(days===1?0:1)
  await expect(page.locator('.budget-summary')).toContainText('总额暂无法确定');await expect(page.locator('.budget-state')).toHaveText('INDETERMINATE · UNDETERMINED')
  await expect(page.locator('.cost-breakdown')).toContainText('未知原因');await expect(page.locator('.cost-breakdown')).toContainText('Mock')
  if(days===5) await expect(page.locator('.budget-grid')).toContainText('¥1200.00')
  else await expect(page.locator('.budget-grid')).toContainText('¥0.00')
  const path=`/api/v1/trips/${t.trip_id}`
  const beforeTrip=await (await request.get(path,{headers:auth()})).json(), beforeState=await (await request.get(path+'/generation-state',{headers:auth()})).json()
  const replay=await request.post(path+'/generations',{headers:{...auth(),'Idempotency-Key':record.key!},data:record.body});expect(replay.status()).toBe(202);expect(await replay.json()).toEqual(record.run)
  await page.reload();await expect(page.getByLabel('本地 Token',{exact:true})).toHaveValue('');await credential(page)
  await expect(page.getByRole('heading',{name:'每日行程',exact:true})).toBeVisible();await expect(page.locator('.budget-state')).toHaveText('INDETERMINATE · UNDETERMINED')
  const refreshTrip=await (await request.get(path,{headers:auth()})).json(), refreshState=await (await request.get(path+'/generation-state',{headers:auth()})).json()
  expect(refreshTrip).toEqual(beforeTrip);expect(refreshState).toEqual(beforeState);expect(posts).toBe(1)
  for(const width of [1280,390,320]) {await page.setViewportSize({width,height:900});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:`../Docs/M1-04b_${days}天_${width}.png`,fullPage:true});if(days===5){await page.locator('.budget-summary').scrollIntoViewIfNeeded();await page.screenshot({path:`../Docs/M1-04b_预算_${width}.png`});await page.locator('.itinerary').scrollIntoViewIfNeeded();await page.screenshot({path:`../Docs/M1-04b_行程_${width}.png`})}}
  await expect(page.getByRole('button',{name:'生成模拟行程',exact:true})).toHaveCount(0)
  expect((await request.get(path+'/generation-state',{headers:auth(b)})).status()).toBe(404)
  await page.getByRole('button',{name:'清除或更换凭证'}).click();await expect(page.getByRole('heading',{name:'每日行程',exact:true})).toHaveCount(0)
  await credential(page,b);await expect(page.getByRole('alert')).toContainText('不存在或当前身份无权读取')
  cases.push({tripId:t.trip_id,days,accepted:record,running,replay:await replay.json(),refresh:{trip:refreshTrip,state:refreshState}})
})
test('uncertain POST response reuses exact key, duplicate click protected, network query recovers',async({page,request})=>{
  const t=await create(request);await open(page,t.trip_id);const sent:any[]=[]
  await page.route('**/generations',async route=>{if(route.request().method()!=='POST')return route.continue();sent.push({key:route.request().headers()['idempotency-key'],body:route.request().postDataJSON()});await route.fetch();await route.abort('failed')})
  await page.getByRole('button',{name:'生成模拟行程',exact:true}).dblclick({delay:10})
  await expect(page.getByRole('alert')).toContainText('网络连接中断');expect(sent).toHaveLength(1)
  await page.unroute('**/generations');const replayResponse=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/generations'));await page.getByRole('button',{name:'使用原生成请求重试（同一幂等键）'}).click()
  await expect(page.getByText('生成请求已接受，尚未开始',{exact:true})).toBeVisible()
  const replayed=await replayResponse;expect(replayed.request().headers()['idempotency-key']).toBe(sent[0].key);expect(replayed.request().postDataJSON()).toEqual(sent[0].body)
  const acceptedState=await (await request.get(`/api/v1/trips/${t.trip_id}/generation-state`,{headers:auth()})).json()
  worker('run');await page.route('**/generation-state',route=>route.fulfill({status:503,json:{error:{code:'business_dependency_unavailable'}}}))
  await expect(page.getByRole('alert')).toContainText('不可用');await page.unroute('**/generation-state')
  await page.getByRole('button',{name:'重新读取行程与运行状态'}).click();await expect(page.getByRole('heading',{name:'每日行程',exact:true})).toBeVisible()
  const trip=await(await request.get(`/api/v1/trips/${t.trip_id}`,{headers:auth()})).json(),state=await(await request.get(`/api/v1/trips/${t.trip_id}/generation-state`,{headers:auth()})).json()
  cases.push({tripId:t.trip_id,uncertainRequest:sent[0],acceptedState,refresh:{trip,state}})
})
for(const mode of ['invalid','interrupted','over_budget','stale']) test(`safe failure ${mode}`,async({page,request})=>{
  const t=await create(request,3,mode==='over_budget'?100:230000);await open(page,t.trip_id);const record=await accepted(page)
  if(mode==='interrupted'){worker('claim');worker('recover')}
  else if(mode==='stale') {worker('claim');const r=await request.put(`/api/v1/trips/${t.trip_id}/requirements`,{headers:headers(),data:{expected_intake_revision:1,expected_working_revision:null,requirements:{...t.intake.input,destination:{state:'provided',value:'苏州'}}}});expect(r.status()).toBe(200);worker('run')}
  else worker(mode==='invalid'?'invalid':'run')
  await expect(page.getByText('生成失败',{exact:true})).toBeVisible();await expect(page.getByRole('heading',{name:'每日行程',exact:true})).toHaveCount(0)
  if(mode==='invalid')await expect(page.locator('.generation-status')).toContainText('模拟输出未通过结构校验')
  if(mode==='interrupted')await expect(page.locator('.generation-status')).toContainText('执行者已中断')
  if(mode==='stale')await expect(page.locator('.generation-status')).toContainText('需求已改变')
  if(mode==='over_budget'){await expect(page.locator('.budget-state')).toContainText('OVER_LIMIT');await expect(page.getByRole('button',{name:'显式重试模拟生成'})).toHaveCount(0);await expect(page.locator('.generation-status')).toContainText('请修改需求或预算')}
  await page.screenshot({path:`../Docs/M1-04b_失败_${mode}.png`,fullPage:true})
  const failed=await(await request.get(`/api/v1/trips/${t.trip_id}/generations/${record.run.run_id}`,{headers:auth()})).json()
  cases.push({tripId:t.trip_id,mode,accepted:record,failed})
  if(mode==='invalid'||mode==='interrupted') {
    await page.reload();await credential(page);await expect(page.getByRole('button',{name:'显式重试模拟生成'})).toBeVisible()
    const p=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/generations'));await page.getByRole('button',{name:'显式重试模拟生成'}).click();const r=await p;expect(r.request().postDataJSON().retry_of).toBe(record.run.run_id)
    worker('run');await expect(page.getByRole('heading',{name:'每日行程',exact:true})).toBeVisible()
    const trip=await(await request.get(`/api/v1/trips/${t.trip_id}`,{headers:auth()})).json(),state=await(await request.get(`/api/v1/trips/${t.trip_id}/generation-state`,{headers:auth()})).json()
    cases[cases.length-1].refresh={trip,state}
  }
})

test('estimated known total within limit and explicit zero remain unverified',async({page,request})=>{
  const t=await create(request,1);await open(page,t.trip_id);const record=await accepted(page);worker('estimated')
  await expect(page.getByRole('heading',{name:'每日行程',exact:true})).toBeVisible()
  await expect(page.locator('.budget-state')).toHaveText('ESTIMATED · WITHIN_LIMIT')
  await expect(page.locator('.cost-breakdown')).toContainText('¥0.00');await expect(page.locator('.cost-breakdown')).toContainText('estimate')
  await expect(page.locator('.budget-grid')).not.toContainText('总额暂无法确定')
  await page.screenshot({path:'../Docs/M1-04b_估算与零费用.png',fullPage:true})
  const path=`/api/v1/trips/${t.trip_id}`,trip=await(await request.get(path,{headers:auth()})).json(),state=await(await request.get(path+'/generation-state',{headers:auth()})).json()
  expect(state.source_run.diagnostics_budget.verified_cost_ids).toEqual([])
  cases.push({tripId:t.trip_id,mode:'estimated-zero',accepted:record,refresh:{trip,state}})
})
