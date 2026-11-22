import { test, expect, E2E_TOKEN } from './fixtures'
import { headingLabels, summaryData } from './summary-navigation-fixture'
import type { Page } from '@playwright/test'
import path from 'node:path'

async function routes(page: Page) {
  await page.route('**/api/v1/knowledge/summary/*', route => {
    expect(route.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`)
    const id = new URL(route.request().url()).pathname.split('/').at(-1)!
    return route.fulfill({ json: summaryData(id, id === 'empty' ? '' : id === 'other' ? '# 新目录\n\n新原文' : undefined) })
  })
  await page.route('**/api/v1/knowledge/coverage/**', route => route.fulfill({json:{ full_coverage_rate:0.5,total_knowledge_points:8,covered_by_questions:4,covered_by_flashcards:2,uncovered_points:[] }}))
  await page.setViewportSize({width:1600,height:900})
}
async function open(page: Page) { await page.goto('/summary/nav'); await expect(page.getByText('知识总纲目录',{exact:true})).toBeVisible() }
const bodyHeadings = (page: Page) => page.locator('.markdown-body').locator('h1,h2,h3,h4,h5,h6')
const toc = (page: Page) => page.locator('.ant-tree')
test.beforeEach(async ({page}) => routes(page))

test('actual summary TOC uses native heading syntax and inline labels without fenced pseudo headings',async({page})=>{
  await open(page)
  await expect(toc(page).locator('.ant-tree-title')).toHaveText(headingLabels)
  await expect(bodyHeadings(page)).toHaveText(headingLabels)
  await expect(toc(page)).not.toContainText('代码中的伪标题')
})
test('clicking second duplicate scrolls and focuses the actual matching heading',async({page})=>{
  await open(page)
  const target=bodyHeadings(page).filter({hasText:'重复标题 😀'}).nth(1)
  const before=(await target.boundingBox())!.y;expect(before).toBeGreaterThan(900)
  await toc(page).getByText('重复标题 😀',{exact:true}).nth(1).click()
  await expect(target).toBeFocused()
  await expect.poll(async()=>(await target.boundingBox())!.y).toBeLessThan(900)
  const ids=await bodyHeadings(page).evaluateAll(els=>els.map(el=>el.id));expect(new Set(ids).size).toBe(8);expect(ids.every(Boolean)).toBe(true)
  await page.screenshot({path:path.resolve('../docs/open-source-integration/round-11-summary-navigation.png')})
})
test('native Tree keyboard selects and focuses a heading',async({page})=>{
  await open(page)
  await toc(page).locator('input').focus();await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter')
  await expect(bodyHeadings(page).first()).toBeFocused()
})
test('selecting the same heading again navigates after focus moved away',async({page})=>{
  await open(page);const target=bodyHeadings(page).last()
  await toc(page).getByText('六级',{exact:true}).click();await expect(target).toBeFocused()
  await page.getByRole('button',{name:/返回资料库$/}).focus()
  await toc(page).getByText('六级',{exact:true}).click();await expect(target).toBeFocused()
})
test('TOC selection returns from another tab to the actual body',async({page})=>{
  await open(page);await page.getByRole('tab',{name:/题库 \(\?\)$/}).click()
  await toc(page).getByText('六级',{exact:true}).click()
  await expect(page.getByRole('tab',{name:/知识总纲$/})).toHaveAttribute('aria-selected','true')
  await expect(bodyHeadings(page).last()).toBeFocused()
})
test('navigation is scoped to body even when an unrelated element has the same id',async({page})=>{
  await open(page);const target=bodyHeadings(page).last()
  await target.evaluate(el=>{const decoy=document.createElement('h6');decoy.id=el.id;decoy.textContent='其他资料标题';decoy.tabIndex=-1;document.body.prepend(decoy)})
  await toc(page).getByText('六级',{exact:true}).click();await expect(target).toBeFocused()
})
test('empty and changed summary replace directory without stale selections',async({page})=>{
  await open(page);await page.evaluate(()=>{history.pushState(null,'','/summary/empty');dispatchEvent(new PopStateEvent('popstate'))})
  await expect(page.getByText('无目录结构',{exact:true})).toBeVisible();await expect(toc(page)).toHaveCount(0)
  await page.evaluate(()=>{history.pushState(null,'','/summary/other');dispatchEvent(new PopStateEvent('popstate'))});await expect(toc(page).locator('.ant-tree-title')).toHaveText(['新目录'])
  await toc(page).getByText('新目录',{exact:true}).click();await expect(bodyHeadings(page).first()).toBeFocused()
})
test('summary generation keeps source ids, count, partial statuses and original content',async({page})=>{
  let sent:unknown
  await page.route('**/api/v1/agents/orchestrate',route=>{expect(route.request().headers().authorization).toBe(`Bearer ${E2E_TOKEN}`);sent=route.request().postDataJSON();return route.fulfill({json:{results:{question_bank:{status:'error',error:'暂不可用'},mindmap:{status:'skipped'},study_plan:{status:'error',error:'保留失败原因'},language:{status:'skipped'}}}})})
  await open(page);await page.getByRole('button',{name:/一键生成全部配套$/}).first().click()
  await expect.poll(()=>sent).toEqual({summary_id:'nav',document_id:'source-doc',config:{question_count:30}})
  await expect(page.getByText('题库生成: 失败',{exact:true})).toBeVisible();await expect(page.getByText('来源: 1 个文档',{exact:true})).toBeVisible()
  await expect(bodyHeadings(page)).toHaveText(headingLabels)
})

