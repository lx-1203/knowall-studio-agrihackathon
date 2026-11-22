import { test, expect } from '@playwright/test'
const literals=['alpha;beta','alpha；beta','1,000','一，二','say "yes"','["literal"]','path\\name']
async function input(page:any,view:string,index:number,text:string){
 const field=view==='learning'?page.getByRole('textbox',{name:`第${index}空`,exact:true}):page.getByPlaceholder(`第${index}空`,{exact:true})
 await field.fill(text);await expect(field).toHaveValue(text)
}
for(const view of ['learning','exam']){
 for(const literal of literals)test(`${view} keeps literal ${literal} in its original slot`,async({page})=>{
  await page.goto(`/blank-harness.html?view=${view}`)
  await input(page,view,1,literal);await input(page,view,2,'氧')
  await input(page,view,2,'氧气');await input(page,view,2,'氧')
  const value=await page.getByTestId('wire').textContent()
  const unsafe=/[;；,，\n]/.test(literal)||literal.trimStart().startsWith('[')
  expect(value).toBe(unsafe?JSON.stringify([literal,'氧']):literal+';氧')
  await expect(view==='learning'?page.getByRole('textbox',{name:'第1空',exact:true}):page.getByPlaceholder('第1空',{exact:true})).toHaveValue(literal)
  if(literal==='alpha;beta'&&process.env.BLANK_TRANSPORT_SCREENSHOTS)await page.screenshot({path:process.env.BLANK_TRANSPORT_SCREENSHOTS+`/${view}-literal.png`,fullPage:true})
 })
 test(`${view} single blank preserves delimiters`,async({page})=>{
  await page.goto(`/blank-harness.html?view=${view}&count=1`)
  const field=view==='learning'?page.getByRole('textbox',{name:'第1空',exact:true}):page.getByPlaceholder('第1空',{exact:true})
  await field.fill('1,000; literal');await expect(field).toHaveValue('1,000; literal')
  expect(await page.getByTestId('wire').textContent()).toBe(JSON.stringify(['1,000; literal']))
 })
 test(`${view} edits legacy values without losing empty first slot`,async({page})=>{
  await page.goto(`/blank-harness.html?view=${view}&legacy=1`)
  await input(page,view,2,'氧');expect(await page.getByTestId('wire').textContent()).toBe(';氧')
  await input(page,view,1,'甲;乙');expect(await page.getByTestId('wire').textContent()).toBe(JSON.stringify(['甲;乙','氧']))
 })
 test(`${view} clearing encoded values yields empty input`,async({page})=>{
  await page.goto(`/blank-harness.html?view=${view}`);await input(page,view,1,'甲;乙')
  await input(page,view,1,'');expect(await page.getByTestId('wire').textContent()).toBe('')
 })
 test(`${view} failed mock submit preserves manual retry payload`,async({page})=>{
  const bodies:any[]=[]
  await page.route('**/api/test/blank-answer',r=>{bodies.push(r.request().postDataJSON());return r.fulfill({status:bodies.length===1?503:200,json:{ok:bodies.length!==1}})})
  await page.goto(`/blank-harness.html?view=${view}`);await input(page,view,1,'alpha;beta');await input(page,view,2,'氧')
  await page.getByRole('button',{name:'模拟提交',exact:true}).click();await expect(page.getByTestId('status')).toHaveText('503')
  await page.getByRole('button',{name:'模拟提交',exact:true}).click();await expect(page.getByTestId('status')).toHaveText('200')
  expect(bodies).toEqual([{answer:JSON.stringify(['alpha;beta','氧'])},{answer:JSON.stringify(['alpha;beta','氧'])}])
 })
 test(`${view} legacy unruled single text stays raw`,async({page})=>{
  await page.goto(`/blank-harness.html?view=${view}&count=0`)
  const field=view==='learning'?page.getByRole('textbox',{name:'你的答案',exact:true}):page.getByPlaceholder('请输入答案...',{exact:true})
  await field.fill('alpha;beta');await expect(field).toHaveValue('alpha;beta')
  expect(await page.getByTestId('wire').textContent()).toBe('alpha;beta')
 })
}
test('exam feedback displays encoded answers as readable blank text',async({page})=>{
 await page.goto('/blank-harness.html?view=exam&results=1')
 await expect(page.getByText('你的答案: alpha;beta；氧',{exact:true})).toBeVisible()
})
