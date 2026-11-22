import { test,expect,stubBackend } from './fixtures'
import { type Page } from '@playwright/test'
import { writeFileSync } from 'node:fs'
const bank='**/api/v1/quiz/questions?*',remove='**/api/v1/quiz/bank/delete-batch'
const questions=[1,2].map(i=>({id:'q'+i,question_text:'删除测试第'+i+'题',question_type:'single_choice',difficulty:'medium'}))
async function select(page:Page,count=1){await page.getByRole('tab',{name:/题库/}).click();await expect(page.getByText('删除测试第1题',{exact:true})).toBeVisible();if(count===2)await page.getByRole('checkbox',{name:'Select all'}).check();else await page.locator('tr[data-row-key="q1"]').getByRole('checkbox').check();await page.getByRole('button',{name:new RegExp(`删除 \\(${count}\\)$`)}).click()}
test.beforeEach(async({page})=>{await stubBackend(page);await page.route(bank,r=>r.fulfill({json:questions}))})
for(const failure of ['409','network']){
test(`bank deletion ${failure} gives actionable error and retains dialog and selection for retry`,async({page})=>{
  let calls=0,removed=false;const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await page.route(bank,r=>r.fulfill({json:removed?[]:questions}))
  await page.route(remove,r=>{calls++;if(calls===1)return failure==='network'?r.abort('failed'):r.fulfill({status:409,json:{detail:'题目已有关联记录，暂不能删除'}});removed=true;return r.fulfill({json:{deleted_count:1}})})
  await page.goto('/quiz');await select(page);const dialog=page.getByRole('dialog');await dialog.getByRole('button',{name:'确认删除',exact:true}).click()
  await expect(page.getByText(failure==='409'?'题目已有关联记录，暂不能删除':'删除失败，请重试',{exact:true})).toBeVisible();await expect(dialog).toBeVisible();expect(calls).toBe(1)
  await expect(dialog.getByRole('button',{name:/确认删除$/})).toBeEnabled()
  if(process.env.DELETE_DIAGNOSTIC_DIR)writeFileSync(process.env.DELETE_DIAGNOSTIC_DIR+`/diagnostic-${failure}.json`,JSON.stringify({Time:new Date().toISOString(),PageErrors:errors,Button:await dialog.getByRole('button',{name:/确认删除$/}).innerText()},null,2))
  expect(errors).toEqual([])
  await expect(page.locator('tr[data-row-key="q1"]').getByRole('checkbox')).toBeChecked()
  await dialog.getByRole('button',{name:/确认删除$/}).click();await expect(page.getByText('已删除 1 题',{exact:true})).toBeVisible();await expect(dialog).toBeHidden();expect(calls).toBe(2)
})}
test('delete confirmation describes protected learning records truthfully',async({page})=>{
  await page.goto('/quiz');await select(page);await expect(page.getByRole('dialog').getByText('已有答题、错题或课堂记录的题目暂不能删除。删除后不可恢复。',{exact:true})).toBeVisible()
})
test('batch success reports actual deleted count rather than selected count',async({page})=>{
  let calls=0;await page.route(remove,r=>{calls++;return r.fulfill({json:{deleted_count:1}})})
  await page.goto('/quiz');await select(page,2);await page.getByRole('dialog').getByRole('button',{name:'确认删除',exact:true}).click()
  await expect(page.getByText('已删除 1 题',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:/删除 \(0\)$/})).toBeDisabled();expect(calls).toBe(1)
})

