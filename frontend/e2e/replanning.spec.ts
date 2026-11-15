import { test,expect } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { writeFileSync } from 'node:fs'
const token=process.env.EASYTRAVEL_BROWSER_TOKEN_A!
const headers=()=>({Authorization:`Bearer ${token}`,'Idempotency-Key':randomUUID()})
function control(action:string,id:string){const r=spawnSync(process.env.EASYTRAVEL_PYTHON!,['-m','scripts.m106b_test_control',action,id],{cwd:'..',env:process.env,encoding:'utf-8',timeout:20000});expect(r.status,r.stderr).toBe(0);return action==='proof'?JSON.parse(r.stdout):undefined}

test('E19 real date change, REPLAN v2, exact L3 approval and refresh preserve historical S1',async({page,request})=>{
  const p=(value:unknown)=>({state:'provided',value}),no={state:'no_preference',value:null}
  const created=await request.post('/api/v1/trips',{headers:headers(),data:{requirements:{origin:p('上海'),destination:p('杭州'),dates:p({mode:'fixed',start_date:'2026-12-04',end_date:'2026-12-06'}),days:p(3),travelers:p(2),target_budget_cents:no,hard_limit_cents:p(230000),transport:p('公共交通'),hotel:no,restrictions:no,raw_text:null}}})
  expect(created.status()).toBe(201);const t=await created.json(),path='/api/v1/trips/'+t.trip_id
  expect((await request.post(path+'/generations',{headers:headers(),data:{expected_intake_revision:1,expected_working_revision:null,retry_of:null}})).status()).toBe(202);control('run',t.trip_id)
  async function credential(){await page.getByLabel('本地 Token',{exact:true}).fill(token);await page.getByRole('button',{name:'使用本地凭证'}).click()}
  await page.goto('/trips/'+t.trip_id);await credential()
  const confirmation=page.locator('.confirmation-review'),section=page.locator('.change-review')
  await confirmation.getByRole('button',{name:'查看预算与风险，确认当前行程'}).click();await confirmation.getByRole('checkbox').check()
  const confirming=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/confirmations'))
  await confirmation.getByRole('button',{name:'确认当前行程 r1',exact:true}).click();expect((await confirming).status()).toBe(201)
  await expect(confirmation.getByText('规划确认成功（HTTP 201，已核对当前指针）')).toBeVisible()
  const before=control('proof',t.trip_id)
  await page.getByRole('button',{name:'修改需求',exact:true}).click();await page.getByLabel('出发日期',{exact:true}).fill('2026-12-11');await page.getByLabel('结束日期',{exact:true}).fill('2026-12-13')
  const saving=page.waitForResponse(r=>r.request().method()==='PUT'&&r.url().endsWith('/requirements'))
  await page.getByRole('button',{name:'保存修改',exact:true}).click();const saved=await saving;expect(saved.status()).toBe(200);const stale=await saved.json();expect(stale.synchronization).toBe('STALE');expect(stale.working.revision).toBe(1)
  await expect(confirmation.getByText('规划确认成功（HTTP 201，已核对当前指针）')).toHaveCount(0)
  await expect(section.getByText(/需求已更新，当前行程仍是旧版本/)).toBeVisible()
  const accepting=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/generations'))
  await section.getByRole('button',{name:'根据新需求重新规划',exact:true}).click();const accepted=await accepting;expect(accepted.status()).toBe(202);const run=await accepted.json();expect(run.kind).toBe('REPLAN')
  control('run',t.trip_id);await expect(section.getByRole('heading',{name:'新旧需求差异 · 重规划 v2',exact:true})).toBeVisible()
  await expect(section.getByRole('heading',{name:/修改提案 · L3 · PENDING/})).toBeVisible()
  await expect(section.getByText(/原需求：.*2026-12-04/)).toBeVisible();await expect(section.getByRole('heading',{name:'候选每日行程',exact:true})).toBeVisible()
  const waiting=control('proof',t.trip_id);expect(waiting.draft_versions).toEqual(before.draft_versions)
  expect(waiting.confirmed_snapshots).toEqual(before.confirmed_snapshots)
  const proposal=await(await request.get(path+'/proposals/'+waiting.change_proposals[0].proposal_id,{headers:headers()})).json()
  expect(proposal.context_v2.candidate_requirements.start_date).toBe('2026-12-11');expect(proposal.proposal.level).toBe('L3')
  for(const width of [1280,390,320]){await page.setViewportSize({width,height:900});await section.screenshot({path:`../Docs/M1-06b_重规划审阅_${width}.png`});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)}
  await expect(section.getByRole('button',{name:'明确授权并应用',exact:true})).toBeDisabled();await section.getByRole('checkbox').check()
  const deciding=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/decisions'))
  await section.getByRole('button',{name:'明确授权并应用',exact:true}).click();const decided=await deciding;expect(decided.status()).toBe(200);const result=await decided.json()
  expect(decided.request().postDataJSON().item_ids).toEqual(proposal.proposal.touched_item_ids)
  await expect(section.getByText(/修改已保存并核对：工作稿 r2/)).toBeVisible()
  const after=control('proof',t.trip_id);expect(after.confirmed_snapshots).toEqual(before.confirmed_snapshots);expect(after.confirmation_disclosures).toEqual(before.confirmation_disclosures)
  expect(after.draft_versions).toHaveLength(2);expect(after.specific_authorizations).toHaveLength(1)
  expect(after.specific_authorizations[0].consumed_by_operation_id).toBe(result.operation_id)
  await page.reload();await credential();await expect(page.getByRole('heading',{name:'每日行程',exact:true})).toBeVisible()
  await expect(section.getByText(/历史确认快照指针/)).toContainText(before.confirmed_snapshots[0].snapshot_id)
  const trip=await(await request.get(path,{headers:headers()})).json();expect(trip.working.revision).toBe(2);expect(trip.synchronization).toBe('CURRENT');expect(trip.plan_confirmation).toBe('UNCONFIRMED')
  expect(trip.working.requirements.requirements_revision).toBe(2);expect(trip.working.context.intake_revision).toBe(2)
  await section.screenshot({path:'../Docs/M1-06b_重规划后刷新.png'})
  writeFileSync('../Docs/M1-06b_HTTP与PostgreSQL证据.json',JSON.stringify({tripId:t.trip_id,stale,accepted:run,proposal,decision:{path:new URL(decided.url()).pathname,key:decided.request().headers()['idempotency-key'],body:decided.request().postDataJSON(),status:200,result},before,waiting,after,trip},null,2))
})
