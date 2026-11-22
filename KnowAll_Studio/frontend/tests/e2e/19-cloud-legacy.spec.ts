import { test, expect } from '@playwright/test'

test('standalone learning page uses its cloud origin and authorizes the private snapshot', async ({ page }) => {
  const requests: { path: string; auth: string | undefined }[] = []
  await page.addInitScript(() => localStorage.setItem('knowall_token', 'cloud-legacy-test-token'))
  await page.route('**/api/v1/auth/me', async route => {
    requests.push({ path: new URL(route.request().url()).pathname, auth: route.request().headers().authorization })
    await route.fulfill({ json: { id: 'learner', username: 'learner' } })
  })
  await page.route('**/api/v1/quiz/progress**', async route => {
    requests.push({ path: new URL(route.request().url()).pathname, auth: route.request().headers().authorization })
    await route.fulfill({ json: {} })
  })
  await page.route('**/glossary_terms.kgb', async route => {
    requests.push({ path: new URL(route.request().url()).pathname, auth: route.request().headers().authorization })
    await route.fulfill({ json: { schema: 'knowall.glossary.kgb', version: 1, terms: [] } })
  })
  await page.goto('/molbio-quiz.html')
  await expect.poll(() => requests.some(r => r.path === '/glossary_terms.kgb')).toBe(true)
  await expect.poll(() => requests.some(r => r.path === '/api/v1/auth/me')).toBe(true)
  expect(requests.every(r => r.auth === 'Bearer cloud-legacy-test-token')).toBe(true)
})
