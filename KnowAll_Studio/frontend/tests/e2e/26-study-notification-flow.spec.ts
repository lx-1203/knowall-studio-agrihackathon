import { test, expect, E2E_TOKEN } from './fixtures'
const plan=(id='A')=>({id,name:`计划${id}`,description:`描述${id}`,status:'active',progress:0,goals:[{id:`goal${id}`,title:`目标${id}`,priority:'medium',completed:false}]})

test('study CRUD and reminder requests carry the current authorization',async({page})=>{
  const auth:string[]=[]
  let created=false,completed=false,dismissed=false
  await page.route('**/api/v1/study/**',route=>{
    auth.push(route.request().headers()['authorization']||'')
    const url=new URL(route.request().url()),method=route.request().method()
    if(url.pathname.endsWith('/plans')&&method==='POST'){created=true;return route.fulfill({json:{plan_id:'A',name:'计划A',goal_count:1}})}
    if(url.pathname.endsWith('/plans'))return route.fulfill({json:created?[{...plan(),status:completed?'completed':'active',progress:completed?100:0}]:[]})
    if(url.pathname.endsWith('/plans/A'))return route.fulfill({json:{...plan(),progress:completed?100:0,goals:[{...plan().goals[0],completed}]}})
    if(url.pathname.endsWith('/toggle')){completed=true;return route.fulfill({json:{completed:true,plan_progress:100}})}
    if(url.pathname.endsWith('/read')){dismissed=true;return route.fulfill({json:{status:'read'}})}
    return route.fulfill({json:{reminders:dismissed?[]:[{id:'reminder',message:'当前学习提醒'}]}})
  })
  await page.goto('/study')
  await page.getByRole('button',{name:/New Plan/}).click()
  await page.getByPlaceholder('Plan name').fill('计划A')
  await page.getByPlaceholder('Goals (one per line)').fill('目标A')
  await page.getByRole('button',{name:/^(?:OK|确\s*定)$/}).click()
  await expect(page.getByRole('dialog')).toBeHidden()
  await page.getByRole('row').filter({hasText:'计划A'}).click()
  await expect(page.getByText('目标A',{exact:true}).filter({visible:true})).toBeVisible()
  await page.getByRole('checkbox').click()
  await expect(page.getByRole('checkbox')).toBeChecked()
  await expect(page.getByRole('row').filter({hasText:'计划A'})).toContainText('completed')
  await page.getByRole('button',{name:'Dismiss'}).click()
  await expect(page.getByText('当前学习提醒')).toBeHidden()
  expect(auth.length).toBeGreaterThanOrEqual(8)
  expect(auth.every(value=>value===`Bearer ${E2E_TOKEN}`)).toBe(true)
  await page.screenshot({path:'../docs/test-round-06b/study-verified.png',fullPage:true,animations:'disabled'})
})

test('failed plan deletion does not announce success or remove selection',async({page})=>{
  await page.route('**/api/v1/study/plans',route=>route.fulfill({json:[plan()]}))
  await page.route('**/api/v1/study/plans/A',route=>route.request().method()==='DELETE'
    ?route.fulfill({status:403,json:{detail:'无权删除'}}):route.fulfill({json:plan()}))
  await page.goto('/study')
  const row=page.getByRole('row').filter({hasText:'计划A'})
  await row.click()
  await expect(page.getByText('目标A',{exact:true})).toBeVisible()
  await row.getByRole('button').click()
  await page.locator('.ant-popconfirm').getByRole('button',{name:/^(?:OK|确\s*定)$/}).click()
  await expect(page.getByText('删除计划失败')).toBeVisible()
  await expect(page.getByText('Plan deleted')).toBeHidden()
  await expect(page.getByText('目标A',{exact:true})).toBeVisible()
})

test('goal failure is visible and cannot silently change progress',async({page})=>{
  await page.route('**/api/v1/study/plans',route=>route.fulfill({json:[plan()]}))
  await page.route('**/api/v1/study/plans/A',route=>route.fulfill({json:plan()}))
  await page.route('**/api/v1/study/goals/goalA/toggle',route=>route.fulfill({status:403,json:{detail:'无权更新'}}))
  await page.goto('/study')
  await page.getByRole('row').filter({hasText:'计划A'}).click()
  await page.getByRole('checkbox').click()
  await expect(page.getByText('更新目标失败')).toBeVisible()
  await expect(page.getByRole('checkbox')).not.toBeChecked()
})

test('late plan detail does not replace the newly selected plan',async({page})=>{
  let release!:()=>void
  const gate=new Promise<void>(resolve=>{release=resolve})
  await page.route('**/api/v1/study/plans',route=>route.fulfill({json:[plan('A'),plan('B')]}))
  await page.route('**/api/v1/study/plans/*',async route=>{
    const id=new URL(route.request().url()).pathname.split('/').at(-1)!
    if(id==='A')await gate
    await route.fulfill({json:plan(id)})
  })
  await page.goto('/study')
  await page.getByRole('row').filter({hasText:'计划A'}).click()
  await page.getByRole('row').filter({hasText:'计划B'}).click()
  await expect(page.getByText('目标B',{exact:true})).toBeVisible()
  const response=page.waitForResponse('**/api/v1/study/plans/A')
  release();await response
  await expect(page.getByText('目标B',{exact:true})).toBeVisible()
  await expect(page.getByText('目标A',{exact:true})).toBeHidden()
})

test('reminder load errors are visible instead of an empty success state',async({page})=>{
  await page.route('**/api/v1/study/reminders/due',route=>route.fulfill({status:403,json:{detail:'无权读取'}}))
  await page.goto('/study')
  await expect(page.getByText('加载提醒失败')).toBeVisible()
  await expect(page.getByText('No pending reminders')).toBeHidden()
})

test('reminder dismiss error keeps the reminder and reports failure',async({page})=>{
  await page.route('**/api/v1/study/reminders/due',route=>route.fulfill({json:{reminders:[{id:'r',message:'不可丢失提醒'}]}}))
  await page.route('**/api/v1/study/reminders/r/read',route=>route.fulfill({status:403,json:{detail:'无权读取'}}))
  await page.goto('/study')
  await page.getByRole('button',{name:'Dismiss'}).click()
  await expect(page.getByText('更新提醒失败')).toBeVisible()
  await expect(page.getByText('不可丢失提醒')).toBeVisible()
})

test('uncertain create is not retried or reported as successful',async({page})=>{
  let calls=0
  await page.route('**/api/v1/study/plans',route=>route.request().method()==='POST'
    ?(++calls,route.fulfill({status:503,json:{detail:'写入结果未知'}})):route.fulfill({json:[]}))
  await page.goto('/study')
  await page.getByRole('button',{name:/New Plan/}).click()
  await page.getByPlaceholder('Plan name').fill('保留输入')
  await page.getByRole('button',{name:/^(?:OK|确\s*定)$/}).click()
  await expect(page.getByText('Failed to create plan')).toBeVisible()
  expect(calls).toBe(1)
  await expect(page.getByPlaceholder('Plan name')).toHaveValue('保留输入')
})

test('notifications filter and mark all read use authorized requests',async({page})=>{
  let read=false
  const auth:string[]=[]
  await page.route('**/api/v1/notifications**',route=>{
    auth.push(route.request().headers()['authorization']||'')
    if(route.request().method()==='PUT'){read=true;return route.fulfill({json:{detail:'全部已读'}})}
    const filter=new URL(route.request().url()).searchParams.get('is_read')
    const items=filter==='0'&&read?[]:[{id:'n',title:'自己的学习通知',content:'受控通知',category:'study',is_read:read,created_at:'2026-10-09T00:00:00'}]
    return route.fulfill({json:{items,total:items.length,unread_count:read?0:1}})
  })
  await page.goto('/personal')
  await page.getByRole('tab',{name:/消息通知/}).click()
  await expect(page.getByText('自己的学习通知')).toBeVisible()
  await page.getByRole('button',{name:'全部已读',exact:true}).click()
  await page.getByRole('button',{name:/^确\s*定$/}).click()
  await expect(page.getByText('全部已标记为已读')).toBeVisible()
  await page.getByRole('button',{name:/未读/}).click()
  await expect(page.getByText('暂无通知')).toBeVisible()
  expect(auth.every(value=>value===`Bearer ${E2E_TOKEN}`)).toBe(true)
})

test('filtering zero read notifications does not invent negative or wrong totals',async({page})=>{
  await page.route('**/api/v1/notifications**',route=>{
    const readFilter=new URL(route.request().url()).searchParams.get('is_read')
    const items=readFilter==='1'?[]:[{id:'n',title:'未读通知',content:'提醒',category:'study',is_read:false}]
    return route.fulfill({json:{items,total:items.length,unread_count:1}})
  })
  await page.goto('/personal')
  await page.getByRole('tab',{name:/消息通知/}).click()
  await page.getByRole('button',{name:/^已\s*读/}).click()
  await expect(page.getByText('暂无通知')).toBeVisible()
  await expect(page.getByRole('button',{name:/^已\s*读/})).toHaveText(/^已\s*读(?:\s*\(\s*0\s*\))?$/)
  await expect(page.getByRole('button',{name:/^全\s*部(?:\s*\(|$)/})).toHaveText(/^全\s*部(?:\s*\(\s*1\s*\))?$/)
})

test('expired study authorization clears the session and opens login',async({page})=>{
  await page.route('**/api/v1/auth/local-session',route=>route.fulfill({json:{enabled:false}}))
  await page.route('**/api/v1/study/reminders/due',route=>route.fulfill({status:401,json:{detail:'登录已过期'}}))
  await page.goto('/study')
  await expect(page.getByLabel('账号',{exact:true})).toBeVisible()
  expect(await page.evaluate(()=>localStorage.getItem('knowall_token'))).toBeNull()
})

test('creating a pending plan cannot submit it twice',async({page})=>{
  let calls=0,release!:()=>void
  const gate=new Promise<void>(resolve=>{release=resolve})
  await page.route('**/api/v1/study/plans',async route=>{
    if(route.request().method()!=='POST')return route.fulfill({json:[]})
    calls++;await gate
    await route.fulfill({json:{plan_id:'pending'}})
  })
  await page.goto('/study')
  await page.getByRole('button',{name:/New Plan/}).click()
  await page.getByPlaceholder('Plan name').fill('一次提交')
  const submit=page.getByRole('dialog').getByRole('button').filter({hasText:/^(?:OK|确\s*定)$/})
  await submit.click()
  await expect.poll(()=>calls).toBe(1)
  await submit.dispatchEvent('click')
  const response=page.waitForResponse(response=>response.url().endsWith('/study/plans')&&response.request().method()==='POST')
  release();await response
  await expect(page.getByRole('dialog')).toBeHidden()
  expect(calls).toBe(1)
})

test('switching plans hides old goal actions until the new detail arrives',async({page})=>{
  let release!:()=>void
  const gate=new Promise<void>(resolve=>{release=resolve})
  await page.route('**/api/v1/study/plans',route=>route.fulfill({json:[plan('A'),plan('B')]}))
  await page.route('**/api/v1/study/plans/*',async route=>{
    const id=new URL(route.request().url()).pathname.split('/').at(-1)!
    if(id==='B')await gate
    await route.fulfill({json:plan(id)})
  })
  await page.goto('/study')
  await page.getByRole('row').filter({hasText:'计划A'}).click()
  await expect(page.getByText('目标A',{exact:true})).toBeVisible()
  await page.getByRole('row').filter({hasText:'计划B'}).click()
  try { await expect(page.getByText('目标A',{exact:true})).toBeHidden() }
  finally { release() }
  await expect(page.getByText('目标B',{exact:true})).toBeVisible()
})

