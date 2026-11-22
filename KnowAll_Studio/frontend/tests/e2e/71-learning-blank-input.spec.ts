import { test, expect } from './fixtures'
const pack={id:'pack1',title:'填空学习包',sections:[],coverage:{},quality:{}}
const options=[{label:'A',text:'候选甲'},{label:'B',text:'候选乙'}]
async function open(page:any,questions:any[],fail=false){
 const sent:any[]=[]
 await page.route('**/api/v1/documents**',(r:any)=>r.fulfill({json:[{id:'doc1',filename:'填空资料'}]}))
 await page.route('**/api/v1/learning/**',(r:any)=>{
  const p=new URL(r.request().url()).pathname
  if(p.endsWith('/session'))return r.fulfill({json:{items:questions.map(q=>({kind:'question',question:q,source:{}}))}})
  if(p.endsWith('/answers')){sent.push(r.request().postDataJSON());return r.fulfill(fail&&sent.length===1?{status:503,json:{detail:'explicit fixture'}}:{json:{is_correct:false,score_ratio:.5,correct_answer:'甲;乙'}})}
  if(p.endsWith('/generate')){sent.push(r.request().postDataJSON());return r.fulfill({json:pack})}
  return r.fulfill({json:p.endsWith('/pack')?pack:{sections:[]}})
 })
 await page.goto('/learning');await page.getByRole('combobox',{name:'学习文档'}).click();await page.getByText('填空资料',{exact:true}).last().click();await page.getByRole('button',{name:'章节练习',exact:true}).click();return sent
}
const q=(extra:any={})=>({id:'q1',question_type:'fill_blank',question_text:'分别填写两空',options:[],blank_count:2,...extra})
test('fill blank is selectable and sends existing backend enum',async({page})=>{
 const sent=await open(page,[]);await page.getByRole('combobox',{name:'题型',exact:true}).click();await page.locator('.ant-select-item-option-content').filter({hasText:/^填空$/}).click();await page.keyboard.press('Escape');await page.getByRole('button',{name:'生成学习包',exact:true}).click();await expect.poll(()=>sent.length).toBe(1);expect(sent[0].question_types).toContain('fill_blank')
})
for(const choices of [[],options])test(`separate blanks preserve positions with ${choices.length} choices`,async({page})=>{
 const sent=await open(page,[q({options:choices})]);await expect(page.getByRole('radio')).toHaveCount(0)
 await expect(page.getByRole('button',{name:/提交答案$/})).toBeDisabled()
 await page.getByRole('textbox',{name:'第2空',exact:true}).fill('乙');await page.getByRole('button',{name:/提交答案$/}).click();await expect.poll(()=>sent.length).toBe(1);expect(sent[0].answer).toBe(';乙');await expect(page.getByText('得分：50%',{exact:true})).toBeVisible();await expect(page.getByRole('textbox',{name:'第2空',exact:true})).toBeDisabled()
})
test('clearing all blanks disables submission',async({page})=>{await open(page,[q({blank_count:3})]);await page.getByRole('textbox',{name:'第3空',exact:true}).fill('x');await page.getByRole('textbox',{name:'第3空',exact:true}).fill('');await expect(page.getByRole('button',{name:/提交答案$/})).toBeDisabled()})
test('submit failure retains both values for manual retry',async({page})=>{const sent=await open(page,[q()],true);await page.getByRole('textbox',{name:'第1空',exact:true}).fill('甲');await page.getByRole('textbox',{name:'第2空',exact:true}).fill('乙');await page.getByRole('button',{name:/提交答案$/}).click();await expect(page.getByText('提交失败，答案已保留，请重试。')).toBeVisible();await expect(page.getByRole('textbox',{name:'第2空',exact:true})).toHaveValue('乙');await page.getByRole('button',{name:/提交答案$/}).click();await expect.poll(()=>sent.length).toBe(2);expect(sent.map(v=>v.answer)).toEqual(['甲;乙','甲;乙'])})
test('next question clears all blank slots',async({page})=>{await open(page,[q(),q({id:'q2',blank_count:3})]);await page.getByRole('textbox',{name:'第1空',exact:true}).fill('甲');await page.getByRole('button',{name:/提交答案$/}).click();await page.getByRole('button',{name:'下一项',exact:true}).click();await expect(page.getByRole('textbox',{name:'第1空',exact:true})).toHaveValue('');await expect(page.getByRole('textbox',{name:'第3空',exact:true})).toHaveValue('');await expect(page.getByRole('button',{name:/提交答案$/})).toBeDisabled()})
for(const count of [undefined,0,-1,1.5,'2',true,101])test(`unsafe or legacy blank count ${count} falls back to text`,async({page})=>{await open(page,[q({blank_count:count,options})]);await expect(page.getByRole('radio')).toHaveCount(0);await expect(page.getByRole('textbox',{name:'你的答案',exact:true})).toBeVisible()})
test('short answer ignores incidental choice options',async({page})=>{const sent=await open(page,[q({question_type:'short_answer',options})]);await expect(page.getByRole('radio')).toHaveCount(0);await page.getByRole('textbox',{name:'你的答案',exact:true}).fill('完整说明');await page.getByRole('button',{name:/提交答案$/}).click();await expect.poll(()=>sent.length).toBe(1);expect(sent[0].answer).toBe('完整说明')})
