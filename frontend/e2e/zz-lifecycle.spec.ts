import { test, expect, type Locator } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { writeFileSync, readFileSync } from 'node:fs'
import { randomUUID } from 'node:crypto'

const a = process.env.EASYTRAVEL_BROWSER_TOKEN_A!, b = process.env.EASYTRAVEL_BROWSER_TOKEN_B!
function control(action: string, id = ''): any {
  const r = spawnSync(process.env.EASYTRAVEL_PYTHON!, ['-m','scripts.m108_test_control',action,id], { cwd:'..',env:process.env,encoding:'utf-8',timeout:30000 })
  expect(r.status,r.stderr).toBe(0)
  return r.stdout.trim() ? JSON.parse(r.stdout) : undefined
}

test('M1-08 complete real browser lifecycle: clarify, generate, S1, changes, L3, reject, REPLAN, S2, history and API restart', async ({ page, request }) => {
  test.setTimeout(180000)
  const initialRestart = control('restart') // Switch legacy fault-injection host to the unmodified formal composition root.
  const http: any[] = [], reads: Promise<void>[] = [], proofs: Record<string,any> = {}, runtimeErrors: string[] = []
  page.on('pageerror', e => runtimeErrors.push(e.message))
  page.on('response', r => {
    const url = new URL(r.url())
    if (!url.pathname.startsWith('/api/v1/trips')) return
    reads.push((async () => {
      const q = r.request()
      http.push({method:q.method(),path:url.pathname+url.search,key:q.headers()['idempotency-key'] ?? null,request:q.postData() ? q.postDataJSON() : null,status:r.status(),response:await r.json()})
    })())
  })
  async function credential(token = a) { await page.getByLabel('本地 Token',{exact:true}).fill(token); await page.getByRole('button',{name:'使用本地凭证'}).click() }
  async function capture(name: string, target: Locator) {
    for (const width of [1280,390,320]) {
      await page.setViewportSize({width,height:900})
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      await target.screenshot({path:`../Docs/M1-08_${name}_${width}.png`})
    }
  }
  const headers = (token = a) => ({Authorization:`Bearer ${token}`,'Idempotency-Key':randomUUID()})
  async function api(method: 'GET'|'POST', url: string, data?: any, status = 200, token = a) {
    const r = await request.fetch(url,{method,headers:headers(token),...(data ? {data} : {})})
    expect(r.status()).toBe(status); const result = await r.json()
    http.push({method,path:url,status:r.status(),request:data ?? null,response:result}); return result
  }
  await page.goto('/trips/new'); await credential()
  await page.getByLabel('一句话旅行需求').fill('上海到杭州，三天两晚，预算和人数逐项明确；Mock 联合验收。')
  for (const [key,value] of [['origin','上海'],['destination','杭州']]) { await page.selectOption(`#${key}-state`,'provided'); await page.fill(`#${key}`,value!) }
  await page.locator('summary').first().click(); await page.selectOption('#dates-state','provided')
  await page.fill('#start-date','2026-12-04'); await page.fill('#end-date','2026-12-06')
  for (const [key,value] of [['days','3'],['target_budget_cents','2000']]) { await page.selectOption(`#${key}-state`,'provided'); await page.fill(`#${key}`,value!) }
  await page.locator('summary').nth(1).click()
  for (const [key,value] of [['transport','公共交通优先'],['hotel','安静、干净，一间双人房'],['restrictions','不爬山']]) { await page.selectOption(`#${key}-state`,'provided'); await page.fill(`#${key}`,value!) }
  await capture('创建与待澄清',page.locator('form.trip-panel'))
  const creating = page.waitForResponse(r => r.request().method()==='POST' && new URL(r.url()).pathname==='/api/v1/trips')
  await page.getByRole('button',{name:'保存旅行',exact:true}).click()
  const createdResponse = await creating; expect(createdResponse.status()).toBe(201)
  const created = await createdResponse.json(), tid = created.trip_id, path = '/api/v1/trips/'+tid
  expect(created.working).toBeNull(); expect(created.intake.blocking_fields).toEqual(expect.arrayContaining(['travelers','hard_limit_cents']))
  proofs.created = control('proof',tid); expect(proofs.created.draft_versions).toHaveLength(0)
  await expect(page.getByRole('heading',{name:'补充一条必要信息',exact:true})).toBeVisible()
  await page.getByLabel('人数',{exact:true}).selectOption('2')
  const answering = page.waitForResponse(r => r.request().method()==='POST' && r.url().endsWith('/answers'))
  await page.getByRole('button',{name:'保存回答',exact:true}).click(); expect((await answering).status()).toBe(200)
  await expect(page.getByLabel('硬预算上限（元 / 总额）',{exact:true})).toBeVisible()
  await api('POST',path+'/generations',{expected_intake_revision:2,expected_working_revision:null,retry_of:null},409)
  await page.getByLabel('硬预算上限（元 / 总额）',{exact:true}).fill('2300')
  const budgetAnswer = page.waitForResponse(r => r.request().method()==='POST' && r.url().endsWith('/answers'))
  await page.getByRole('button',{name:'保存回答',exact:true}).click(); expect((await budgetAnswer).status()).toBe(200)
  await expect(page.getByRole('button',{name:'生成模拟行程',exact:true})).toBeVisible()
  proofs.clarified = control('proof',tid)
  expect(proofs.clarified.trip_intake_versions.map((x:any)=>x.revision)).toEqual([1,2,3])
  expect(proofs.clarified.trip_intake_versions[0].input.travelers.value).toBeNull()
  expect(proofs.clarified.trip_intake_versions[2].input.hard_limit_cents.value).toBe(230000)
  const generating = page.waitForResponse(r => r.request().method()==='POST' && r.url().endsWith('/generations'))
  await page.getByRole('button',{name:'生成模拟行程',exact:true}).click(); expect((await generating).status()).toBe(202)
  control('run',tid)
  await expect(page.getByRole('heading',{name:'每日行程',exact:true})).toBeVisible()
  await expect(page.locator('.day-schedule')).toHaveCount(3); await expect(page.locator('.hotel-card')).toHaveCount(1)
  await capture('生成后的每日行程',page.locator('.itinerary'))
  const confirmation = page.locator('.confirmation-review'), changes = page.locator('.change-review')
  async function confirm(revision: number) {
    await confirmation.getByRole('button',{name:/^(查看预算与风险，确认当前行程|重新读取确认预览)$/}).click()
    await expect(confirmation.getByRole('checkbox')).not.toBeChecked()
    await expect(confirmation).toContainText('未知费用')
    await expect(confirmation).toContainText('Mock')
    await expect(confirmation.getByRole('button',{name:`确认当前行程 r${revision}`,exact:true})).toBeDisabled()
    if (revision===1) await capture('首次确认披露',confirmation)
    await confirmation.getByRole('checkbox').check()
    const posting = page.waitForResponse(r => r.request().method()==='POST' && r.url().endsWith('/confirmations'))
    await confirmation.getByRole('button',{name:`确认当前行程 r${revision}`,exact:true}).click()
    const r = await posting; expect(r.status()).toBe(201)
    await expect(confirmation.getByText('规划确认成功（HTTP 201，已核对当前指针）',{exact:true})).toBeVisible()
    const view = (await r.json()).snapshot
    expect(view.snapshot.source_working_revision).toBe(revision); expect(view.booked).toBe(false); expect(view.readiness).toBe('NOT_ASSESSED')
    expect(view.snapshot.budget.verification).not.toBe('VERIFIED'); expect(view.snapshot.budget.total_cents).toBeNull()
    return view
  }
  const s1 = await confirm(1); proofs.s1 = control('proof',tid)
  expect(proofs.s1.specific_authorizations).toHaveLength(0)
  const original = await api('GET',path), hotel = original.working.payload.items.find((i:any)=>i.kind==='hotel')
  async function propose(intent: string) {
    await changes.getByLabel('修改意图',{exact:true}).fill(intent)
    const accepted = page.waitForResponse(r => r.request().method()==='POST' && r.url().endsWith('/change-intents'))
    await changes.getByRole('button',{name:'生成修改提案',exact:true}).click(); const r = await accepted; expect(r.status()).toBe(202)
    control('run',tid)
    await expect(changes.getByRole('heading',{name:/修改提案 · L[123] · PENDING/})).toBeVisible()
    const run = await api('GET',path+'/generations/'+(await r.json()).run_id)
    return api('GET',path+'/proposals/'+run.result.proposal_id)
  }
  async function approve(p: any, revision: number) {
    const l3 = p.proposal.level==='L3', name = l3 ? '明确授权并应用' : '应用到工作草案'
    await expect(changes.getByRole('button',{name,exact:true})).toBeDisabled()
    await changes.getByRole('checkbox').check()
    const posting = page.waitForResponse(r => r.request().method()==='POST' && r.url().endsWith('/decisions'))
    await changes.getByRole('button',{name,exact:true}).click(); const r = await posting; expect(r.status()).toBe(200)
    expect(r.request().postDataJSON().item_ids).toEqual(p.proposal.touched_item_ids)
    expect(r.request().postDataJSON().explicit_authorization).toBe(l3)
    await expect(changes.getByText(new RegExp(`修改已保存并核对：工作稿 r${revision}`))).toBeVisible()
  }
  const ordinary = await propose('第二天安排轻松一点'); expect(ordinary.proposal.level).toBe('L2'); await approve(ordinary,2)
  proofs.ordinary = control('proof',tid); expect(proofs.ordinary.confirmed_snapshots).toEqual(proofs.s1.confirmed_snapshots)
  expect(proofs.ordinary.confirmation_disclosures).toEqual(proofs.s1.confirmation_disclosures); expect(proofs.ordinary.specific_authorizations).toHaveLength(0)
  const hotelProposal = await propose('更换酒店 '+hotel.id); expect(hotelProposal.proposal.level).toBe('L3')
  const l3Body = {decision:'approve',expected_intake_revision:3,expected_working_revision:2,seen_candidate_hash:hotelProposal.proposal.candidate_hash,item_ids:hotelProposal.proposal.touched_item_ids,explicit_authorization:false}
  await api('POST',path+'/proposals/'+hotelProposal.proposal.proposal_id+'/decisions',l3Body,409)
  await capture('酒店L3完整授权',changes); await approve(hotelProposal,3)
  proofs.hotel = control('proof',tid); expect(proofs.hotel.specific_authorizations).toHaveLength(1)
  const rejectProposal = await propose('修改活动标题 '+original.working.payload.items.find((i:any)=>i.kind==='activity').id)
  const rejecting = page.waitForResponse(r => r.request().method()==='POST' && r.url().endsWith('/decisions'))
  await changes.getByRole('button',{name:'拒绝此提案',exact:true}).click(); expect((await rejecting).status()).toBe(200)
  await expect(changes.getByText(/提案已拒绝并核对/)).toBeVisible()
  proofs.rejected = control('proof',tid); expect(proofs.rejected.draft_versions).toEqual(proofs.hotel.draft_versions)
  expect(proofs.rejected.specific_authorizations).toEqual(proofs.hotel.specific_authorizations)
  await page.getByRole('button',{name:'修改需求',exact:true}).click()
  await page.fill('#start-date','2026-12-11'); await page.fill('#end-date','2026-12-13'); await page.fill('#hard_limit_cents','3000'); await page.fill('#hotel','更安静，仍需真实核验')
  const saving = page.waitForResponse(r => r.request().method()==='PUT' && r.url().endsWith('/requirements'))
  await page.getByRole('button',{name:'保存修改',exact:true}).click(); const saved = await saving; expect(saved.status()).toBe(200)
  const stale = await saved.json(); expect(stale.synchronization).toBe('STALE'); expect(stale.working.revision).toBe(3)
  const blockedPreview = await api('GET',path+'/confirmation-preview'); expect(blockedPreview.confirmation_allowed).toBe(false)
  await confirmation.getByRole('button',{name:/^(查看预算与风险，确认当前行程|重新读取确认预览)$/}).click()
  await expect(confirmation.getByRole('heading',{name:'当前不能确认',exact:true})).toBeVisible()
  await expect(confirmation).toContainText('完成重规划')
  await expect(confirmation).not.toContainText('尚不支持重规划')
  await expect(changes.getByRole('button',{name:'根据新需求重新规划',exact:true})).toBeEnabled()
  await api('POST',path+'/confirmations',{expected_intake_revision:4,expected_working_revision:3,seen_disclosure_hash:blockedPreview.disclosure_hash,seen_risk_hash:blockedPreview.risk_hash,explicit_confirmation:true},409)
  const replanning = page.waitForResponse(r => r.request().method()==='POST' && r.url().endsWith('/generations'))
  await changes.getByRole('button',{name:'根据新需求重新规划',exact:true}).click(); const replanAccepted = await replanning; expect(replanAccepted.status()).toBe(202)
  control('run',tid)
  await expect(changes.getByRole('heading',{name:'新旧需求差异 · 重规划 v2',exact:true})).toBeVisible()
  const run = await api('GET',path+'/generations/'+(await replanAccepted.json()).run_id)
  const replan = await api('GET',path+'/proposals/'+run.result.proposal_id)
  proofs.waitingReplan = control('proof',tid); expect(proofs.waitingReplan.draft_versions).toEqual(proofs.hotel.draft_versions)
  expect(replan.context_v2.candidate_requirements.requirements_revision).toBe(2)
  expect(replan.context_v2.base_intake_revision).toBe(4)
  await capture('重规划v2待审',changes); await approve(replan,4)
  const s2 = await confirm(4); proofs.s2 = control('proof',tid)
  expect(proofs.s2.confirmed_snapshots).toHaveLength(2)
  expect(proofs.s2.confirmation_disclosures.find((x:any)=>x.snapshot_id===s1.snapshot.snapshot_id)).toEqual(proofs.s1.confirmation_disclosures[0])
  await page.getByRole('link',{name:'查看确认历史',exact:true}).click()
  await expect(page.getByRole('link',{name:'查看历史版本 · 来源 r4',exact:true})).toBeVisible()
  await capture('确认历史列表',page.locator('.history-page'))
  for (const view of [s1,s2]) {
    await page.getByRole('link',{name:`查看历史版本 · 来源 r${view.snapshot.source_working_revision}`,exact:true}).click()
    await expect(page.getByRole('heading',{name:'历史每日行程',exact:true})).toBeVisible()
    await expect(page.locator('.history-page')).toContainText(view.snapshot.requirements.start_date)
    expect(await api('GET',path+'/snapshots/'+view.snapshot.snapshot_id)).toEqual(view)
    await page.getByRole('link',{name:'返回确认历史列表',exact:true}).click()
  }
  await page.getByRole('link',{name:'返回当前工作区',exact:true}).click()
  await expect(page.getByRole('heading',{name:'每日行程',exact:true})).toBeVisible()
  const finalProposal = await propose('第二天安排轻松一点'); await approve(finalProposal,5)
  const final = await api('GET',path); expect(final.plan_confirmation).toBe('UNCONFIRMED'); expect(final.latest_confirmed_version_id).toBe(s2.snapshot.snapshot_id)
  proofs.beforeRestart = control('proof',tid)
  const finalRestart = control('restart')
  await page.reload(); await expect(page.getByLabel('本地 Token',{exact:true})).toHaveValue(''); await credential()
  await expect(page.getByRole('heading',{name:'每日行程',exact:true})).toBeVisible()
  expect(await api('GET',path)).toEqual(final)
  for (const view of [s1,s2]) expect(await api('GET',path+'/snapshots/'+view.snapshot.snapshot_id)).toEqual(view)
  const rejected = await api('GET',path+'/proposals/'+rejectProposal.proposal.proposal_id); expect(rejected.state).toBe('REJECTED')
  proofs.afterRestart = control('proof',tid); expect(proofs.afterRestart).toEqual(proofs.beforeRestart)
  expect(proofs.afterRestart.clarification_answers).toHaveLength(2)
  expect(proofs.afterRestart.change_run_inputs).toHaveLength(4)
  const audit = JSON.parse(readFileSync('../Docs/M1-08_架构与契约核查.json','utf-8'))
  expect(proofs.afterRestart.migrations).toHaveLength(7)
  for (const migration of proofs.afterRestart.migrations) expect(migration.checksum).toBe(audit.migrations[migration.version])
  expect(proofs.afterRestart.draft_versions).toHaveLength(5); expect(proofs.afterRestart.specific_authorizations).toHaveLength(2)
  expect(new Set(proofs.afterRestart.specific_authorizations.map((x:any)=>x.consumed_by_operation_id)).size).toBe(2)
  for (const view of [s1,s2]) {
    expect(proofs.afterRestart.confirmed_snapshots.find((x:any)=>x.snapshot_id===view.snapshot.snapshot_id).budget).toEqual(view.snapshot.budget)
  }
  await page.getByRole('link',{name:'查看确认历史',exact:true}).click(); await page.getByRole('link',{name:'查看历史版本 · 来源 r1',exact:true}).click()
  await expect(page.getByRole('heading',{name:'历史每日行程',exact:true})).toBeVisible()
  for (const width of [1280,390,320]) {
    await page.setViewportSize({width,height:900}); expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
    await page.screenshot({path:`../Docs/M1-08_历史S1与费用风险_${width}.png`,fullPage:true})
  }
  await page.getByRole('button',{name:'清除或更换凭证',exact:true}).click(); await credential(b)
  await expect(page.getByRole('alert')).toBeVisible(); await expect(page.getByRole('heading',{name:'历史每日行程',exact:true})).toHaveCount(0)
  expect(await page.evaluate(()=>localStorage.length+sessionStorage.length)).toBe(0)
  await Promise.all(reads); expect(runtimeErrors).toEqual([])
  writeFileSync('../Docs/M1-08_完整生命周期HTTP与PostgreSQL证据.json',JSON.stringify({tripId:tid,initialRestart,finalRestart,created,stale,final,s1,s2,http,proofs,runtimeErrors,allCommittedBusinessWritesViaBrowser:true,noMockRoutes:true},null,2))
})
