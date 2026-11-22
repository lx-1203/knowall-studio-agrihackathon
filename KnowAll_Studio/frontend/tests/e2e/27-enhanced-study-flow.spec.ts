import { test, expect, E2E_TOKEN } from './fixtures'

const names=['question_bank','mindmap','study_plan','language']
test.beforeEach(async({page})=>{
  await page.route('**/api/v1/knowledge/summary/summary',route=>route.fulfill({json:{summary_id:'summary',document_ids:['source'],content_md:'# 细胞',node_count:16}}))
  await page.route('**/api/v1/knowledge/coverage/summary',route=>route.fulfill({json:null}))
})

test('failed generation shows an error and no fabricated study plan',async({page})=>{
  await page.route('**/api/v1/agents/orchestrate',route=>route.fulfill({json:{results:Object.fromEntries(names.map(name=>[name,{status:'error',result:null,error:'受控失败'}]))}}))
  await page.goto('/summary/summary')
  await page.getByRole('button',{name:/一键生成全部配套/}).first().click()
  await expect(page.getByText('配套内容生成失败')).toBeVisible()
  await page.getByRole('tab',{name:/学习计划/}).click()
  await expect(page.getByText(/学习计划生成失败.*受控失败/)).toBeVisible()
  await expect(page.getByText('计划名称:',{exact:true})).toBeHidden()
})

test('successful study plan and skipped generators have truthful status and authorization',async({page})=>{
  let authorization=''
  await page.route('**/api/v1/agents/orchestrate',route=>{
    authorization=route.request().headers()['authorization']||''
    return route.fulfill({json:{results:{
      study_plan:{status:'success',result:{plan_id:'p',name:'本地计划',ebbinghaus_nodes:[]}},
      ...Object.fromEntries(names.filter(name=>name!=='study_plan').map(name=>[name,{status:'skipped',result:null,error:null}]))
    }}})
  })
  await page.goto('/summary/summary')
  await page.getByRole('button',{name:/一键生成全部配套/}).first().click()
  await expect(page.getByText('配套内容生成完成：成功 1 项，跳过 3 项')).toBeVisible()
  await expect(page.getByText('生词表: 已跳过')).toBeVisible()
  await page.getByRole('tab',{name:/学习计划/}).click()
  await expect(page.getByText('本地计划',{exact:true})).toBeVisible()
  expect(authorization).toBe(`Bearer ${E2E_TOKEN}`)
  await page.screenshot({path:process.env.OSS_EVIDENCE_PREFIX ? `../docs/open-source-integration/${process.env.OSS_EVIDENCE_PREFIX}-summary-plan-verified.png` : '../docs/test-round-06b1/summary-plan-verified.png',fullPage:true})
})

test('partial generation is reported as partial and failed study plans stay absent',async({page})=>{
  await page.route('**/api/v1/agents/orchestrate',route=>route.fulfill({json:{results:{
    language:{status:'success',result:{vocabulary_count:2}},
    study_plan:{status:'error',result:null,error:'排期失败'},
    question_bank:{status:'skipped'},mindmap:{status:'skipped'},
  }}}))
  await page.goto('/summary/summary')
  await page.getByRole('button',{name:/一键生成全部配套/}).first().click()
  await expect(page.getByText('配套内容部分生成：成功 1 项，失败 1 项')).toBeVisible()
  await page.getByRole('tab',{name:/学习计划/}).click()
  await expect(page.getByText(/学习计划生成失败.*排期失败/)).toBeVisible()
})

test('all skipped generators do not report successful content',async({page})=>{
  await page.route('**/api/v1/agents/orchestrate',route=>route.fulfill({json:{results:Object.fromEntries(names.map(name=>[name,{status:'skipped'}]))}}))
  await page.goto('/summary/summary')
  await page.getByRole('button',{name:/一键生成全部配套/}).first().click()
  await expect(page.getByText('配套内容已跳过：4 项')).toBeVisible()
  await page.getByRole('tab',{name:/学习计划/}).click()
  await expect(page.getByText('已跳过学习计划生成')).toBeVisible()
  await expect(page.getByText('计划名称:',{exact:true})).toBeHidden()
})

test('empty generation results request retry',async({page})=>{
  await page.route('**/api/v1/agents/orchestrate',route=>route.fulfill({json:{results:{}}}))
  await page.goto('/summary/summary')
  await page.getByRole('button',{name:/一键生成全部配套/}).first().click()
  await expect(page.getByText('未返回生成结果，请重试')).toBeVisible()
  await expect(page.getByRole('button',{name:/重新生成全部配套/})).toBeEnabled()
})
