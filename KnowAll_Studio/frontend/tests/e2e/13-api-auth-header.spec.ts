import { test, expect, E2E_TOKEN } from './fixtures'

/**
 * Guards the auth plumbing that `vite.config.ts`'s axios work depends on.
 *
 * The token is stored in one place and must reach every backend call. Before
 * this test existed nothing verified that: the e2e suite stubs /api and only
 * checked that pages render, so a regression in how the Authorization header is
 * attached would have shipped silently.
 */
test.describe('API auth header', () => {
  test('requests to /api carry the bearer token', async ({ page }) => {
    const authByUrl: Record<string, string | undefined> = {}

    page.on('request', (req) => {
      if (req.url().includes('/api/')) {
        authByUrl[req.url()] = req.headers()['authorization']
      }
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    // Give the dashboard's data fetches a beat to fire.
    // Poll instead of sleeping a fixed beat: under load the fetches can land
    // after any fixed window. (This test was observed flaky once, in a run
    // where the gates ran concurrently on one machine.)
    await expect
      .poll(() => Object.values(authByUrl).filter((h) => h === `Bearer ${E2E_TOKEN}`).length, {
        timeout: 15000,
        message: `no /api request carried "Bearer ${E2E_TOKEN}"`,
      })
      .toBeGreaterThan(0)

    const urls = Object.keys(authByUrl)
    expect(urls.length, 'expected the app to call /api on load').toBeGreaterThan(0)


    const wrongScheme = urls.filter((u) => authByUrl[u] && authByUrl[u] !== `Bearer ${E2E_TOKEN}`)
    expect(
      wrongScheme,
      `these /api requests used a non-Bearer Authorization header:\n` +
        wrongScheme.map((u) => `  ${u} -> ${authByUrl[u]}`).join('\n'),
    ).toHaveLength(0)
  })

  test('axios-driven pages also carry the bearer token', async ({ page }) => {
    const authByUrl: Record<string, string | undefined> = {}

    page.on('request', (req) => {
      if (req.url().includes('/api/')) {
        authByUrl[req.url()] = req.headers()['authorization']
      }
    })

    // The dashboard fetches with `fetch` + authHeaderValue(); /flashcards goes
    // through src/api.ts (axios). Only the axios path exercises the request
    // interceptor, so both paths must be visited to guard the plumbing.
    await page.goto('/flashcards')
    await page.waitForLoadState('networkidle')
    // Poll instead of sleeping a fixed beat: under load the fetches can land
    // after any fixed window. (This test was observed flaky once, in a run
    // where the gates ran concurrently on one machine.)
    await expect
      .poll(() => Object.values(authByUrl).filter((h) => h === `Bearer ${E2E_TOKEN}`).length, {
        timeout: 15000,
        message: `no /api request carried "Bearer ${E2E_TOKEN}"`,
      })
      .toBeGreaterThan(0)

    const urls = Object.keys(authByUrl)
    expect(urls.length, 'expected the axios page to call /api on load').toBeGreaterThan(0)


    const wrongScheme = urls.filter((u) => authByUrl[u] && authByUrl[u] !== `Bearer ${E2E_TOKEN}`)
    expect(
      wrongScheme,
      `these /api requests used a non-Bearer Authorization header:\n` +
        wrongScheme.map((u) => `  ${u} -> ${authByUrl[u]}`).join('\n'),
    ).toHaveLength(0)
  })
})
