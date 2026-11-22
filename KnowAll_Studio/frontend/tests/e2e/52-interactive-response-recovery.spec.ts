import { type Page } from '@playwright/test'
import { test, expect, stubBackend } from './fixtures'
const start='**/api/v1/quiz/interactive/start?**',submit='**/api/v1/quiz/interactive/submit',stats='**/api/v1/quiz/interactive/session/*/stats'
const quiz=(id='lost',kind='single_choice')=>({session_id:id+'-session',total:2,questions:[1,2].map(i=>({id:id+i,question_type:kind,question_text:id+i+'题目',difficulty:'medium',tags:[],options:[{label:'A',text:'甲'},{label:'B',text:'乙'}]}))})
const saved=(id='lost',answer='A')=>({question_id:id+'1',is_correct:true,correct_answer:'A',analysis:'恢复原始解析',user_answer:answer,time_spent_ms:37})
const go=async(page:Page,id:string)=>page.evaluate(id=>{history.pushState(null,'','/quiz/interactive/'+id);dispatchEvent(new PopStateEvent('popstate'))},id)
test.beforeEach(async({page})=>{await stubBackend(page)})

for(const kind of ['single_choice','true_false','multi_choice','fill_blank','short_answer','calculation']){
  test(`${kind} reads committed grade after lost response without POST retry`,async({page})=>{
    let posts=0,reads=0;await page.route(start,r=>r.fulfill({json:quiz('lost',kind)}))
    await page.route(submit,r=>{posts++;return r.abort('failed')})
    await page.route(stats,r=>{reads++;return r.fulfill({json:{questions_detail:[saved()]}})})
    await page.goto('/quiz/interactive/lost')
    if(['single_choice','true_false'].includes(kind))await page.getByRole('radio',{name:'A. 甲'}).check()
    else {if(kind==='multi_choice'){await page.getByText('A. 甲',{exact:true}).click();await expect(page.getByRole('checkbox',{name:'A. 甲'})).toBeChecked()}
      else await page.getByPlaceholder(kind==='calculation'?'请输入答案...':'请输入你的答案...').fill('A')
      await page.getByRole('button',{name:/提交答案$/}).click()}
    await expect(page.getByText('恢复原始解析',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'答题卡 (1/2)'})).toBeVisible()
    await expect(page.getByText('lost2题目',{exact:true})).toBeVisible();expect(posts).toBe(1);expect(reads).toBe(1)
  })
}
for(const response of ['empty','failed','malformed','other-question']){
  test(`unconfirmed ${response} read preserves input for explicit retry`,async({page})=>{
    let posts=0,reads=0;await page.route(start,r=>r.fulfill({json:quiz('lost','fill_blank')}))
    await page.route(submit,r=>++posts===1?r.abort('failed'):r.fulfill({json:{is_correct:true,correct_answer:'A',analysis:'明确重新提交'}}))
    await page.route(stats,r=>{reads++;return response==='failed'?r.fulfill({status:503,json:{detail:'读回故障'}}):r.fulfill({json:{questions_detail:response==='empty'?[]:response==='malformed'?[{question_id:'lost1',is_correct:'yes'}]:[saved('other')]}})})
    await page.goto('/quiz/interactive/lost');await page.getByPlaceholder('请输入你的答案...').fill('保留输入');await page.getByRole('button',{name:/提交答案$/}).click()
    await expect(page.getByText(/提交失败:/)).toBeVisible();await expect(page.getByPlaceholder('请输入你的答案...')).toHaveValue('保留输入');await expect(page.getByPlaceholder('请输入你的答案...')).toBeEnabled()
    expect(posts).toBe(1);expect(reads).toBe(1);await page.getByRole('button',{name:/提交答案$/}).click();await expect(page.getByText('明确重新提交',{exact:true})).toBeVisible();expect(posts).toBe(2);expect(reads).toBe(1)
  })
}
test('409 restores original saved input and does not mislabel the later edited input',async({page})=>{
  await page.route(start,r=>r.fulfill({json:quiz('lost','fill_blank')}));await page.route(submit,r=>r.fulfill({status:409,json:{detail:'Question already answered in this session'}}))
  await page.route(stats,r=>r.fulfill({json:{questions_detail:[saved('lost','原提交输入')]}}))
  await page.goto('/quiz/interactive/lost');await page.getByPlaceholder('请输入你的答案...').fill('新编辑输入');await page.getByRole('button',{name:/提交答案$/}).click()
  await expect(page.getByText('已恢复之前提交的评分',{exact:true})).toBeVisible();await expect(page.getByPlaceholder('请输入你的答案...')).toHaveValue('原提交输入');await expect(page.getByPlaceholder('请输入你的答案...')).toBeDisabled()
})
for(const context of ['summary','account','leave','sheet']){
  test(`pending recovery cannot overwrite ${context} navigation`,async({page})=>{
    await page.clock.install();let reads=0,release!:()=>void;const gate=new Promise<void>(r=>release=r)
    await page.route(start,r=>r.fulfill({json:quiz(new URL(r.request().url()).searchParams.get('summary_id')!)}));await page.route(submit,r=>r.abort('failed'))
    await page.route(stats,async r=>{reads++;await gate;await r.fulfill({json:{questions_detail:[saved('old')]}}).catch(()=>{})})
    await page.goto('/quiz/interactive/old');await page.getByRole('radio',{name:'A. 甲'}).check();await expect.poll(()=>reads).toBe(1)
    if(context==='summary')await go(page,'new')
    else if(context==='account')await page.evaluate(async()=>{const{useAuthStore}=await import('/src/stores/index.ts');useAuthStore.getState().login('B-token',{id:'B',username:'B',email:'B@fixture.invalid'})})
    else if(context==='leave')await page.getByRole('link',{name:'全部题库',exact:true}).click()
    else {await page.getByRole('button',{name:/答题卡/}).click();await page.getByRole('button',{name:'2',exact:true}).click()}
    release();await page.clock.runFor(2200)
    if(context==='sheet'){await expect(page.getByText('old2题目',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'答题卡 (1/2)'})).toBeVisible()}
    else {await expect(page.getByText('恢复原始解析',{exact:true})).toBeHidden();if(context!=='leave')await expect(page.getByRole('button',{name:'答题卡 (0/2)'})).toBeVisible()}
  })
}
test('definitive 400 rejection does not trigger grade recovery',async({page})=>{
  let reads=0;await page.route(start,r=>r.fulfill({json:quiz()}));await page.route(submit,r=>r.fulfill({status:400,json:{detail:'明确拒绝'}}));await page.route(stats,r=>{reads++;return r.fulfill({json:{questions_detail:[saved()]}})})
  await page.goto('/quiz/interactive/lost');await page.getByRole('radio',{name:'A. 甲'}).check();await expect(page.getByText('提交失败: 明确拒绝',{exact:true})).toBeVisible();expect(reads).toBe(0)
})
test('409 multi-choice recovery restores original wrong selection rather than edited checkboxes',async({page})=>{
  await page.route(start,r=>r.fulfill({json:quiz('lost','multi_choice')}))
  await page.route(submit,r=>r.fulfill({status:409,json:{detail:'Question already answered in this session'}}))
  await page.route(stats,r=>r.fulfill({json:{questions_detail:[{...saved(),is_correct:false,correct_answer:'B',analysis:'原多选错误解析'}]}}))
  await page.goto('/quiz/interactive/lost');await page.getByText('B. 乙',{exact:true}).click();await expect(page.getByRole('checkbox',{name:'B. 乙'})).toBeChecked()
  await page.getByRole('button',{name:/提交答案$/}).click()
  await expect(page.getByText('原多选错误解析',{exact:true})).toBeVisible();await expect(page.getByRole('checkbox',{name:'A. 甲'})).toBeChecked();await expect(page.getByRole('checkbox',{name:'B. 乙'})).not.toBeChecked()
  await expect(page.getByRole('checkbox',{name:'A. 甲'})).toBeDisabled();await expect(page.getByRole('button',{name:'答题卡 (1/2)'})).toBeVisible();await expect(page.getByText('0.0% 正确率',{exact:true})).toBeVisible()
})
