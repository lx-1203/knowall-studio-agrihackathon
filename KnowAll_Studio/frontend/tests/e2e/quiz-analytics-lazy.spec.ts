import { test, expect } from './fixtures'

test('quiz analytics tab displays loaded mastery data', async ({ page }) => {
  await page.route('**/api/v1/review/mastery', r => r.fulfill({ json: { total_knowledge_points: 2, overall_mastery: .75, weak_count: 0, moderate_count: 1, strong_count: 1, weak_points: [], moderate_points: [], strong_points: [] } }))
  await page.goto('/quiz')
  await page.getByRole('tab', { name: /回顾分析/ }).click()
  await expect(page.getByText('综合掌握度', { exact: true })).toBeVisible()
  await expect(page.getByText('75%', { exact: true })).toBeVisible()
  await page.getByRole('tab', { name: /出题/ }).click()
  await expect(page.getByText('综合掌握度', { exact: true })).not.toBeVisible()
})
test('quiz recommendation tab can recover from a failed generation', async ({ page }) => {
  let calls = 0
  await page.route('**/api/v1/review/mastery', r => r.fulfill({ json: { total_knowledge_points: 0, overall_mastery: 0, weak_count: 0, moderate_count: 0, strong_count: 0, weak_points: [], moderate_points: [], strong_points: [] } }))
  await page.route('**/api/v1/review/recommend', r => r.fulfill(++calls === 1 ? { status: 503, json: { detail: '模拟暂时不可用' } } : { json: { has_weak_points: false, message: '全部知识点已掌握', recommendations: [], overall_plan: '' } }))
  await page.goto('/quiz'); await page.getByRole('tab', { name: /回顾分析/ }).click()
  await page.getByRole('tab', { name: /AI复习推荐/ }).click()
  const generate = page.getByRole('button', { name: /生成复习建议/ }).first()
  await generate.click()
  await expect(page.getByText(/生成复习建议失败/)).toBeVisible()
  await expect(generate).toBeEnabled(); await generate.click()
  await expect(page.getByRole('tabpanel', { name: /AI复习推荐/ }).getByText('全部知识点已掌握', { exact: true })).toBeVisible()
  expect(calls).toBe(2)
})
