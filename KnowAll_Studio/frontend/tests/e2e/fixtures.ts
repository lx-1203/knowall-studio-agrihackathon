/**
 * Shared e2e fixture: seed an authenticated session.
 *
 * Why this exists
 * ---------------
 * `src/stores/index.ts::restore()` trusts localStorage: it reads
 * `knowall_token` / `knowall_user` and sets `isAuthenticated = true` with no
 * server round-trip. That lets these smoke tests run hermetically — no backend,
 * no real account, no network.
 *
 * Before this fixture, specs 01–11 navigated straight to authenticated routes
 * while `isAuthenticated` defaulted to `false`, so the app always rendered the
 * login gate and every assertion failed. Only 12-glossary logged in properly
 * (and it is gated behind GLOSSARY_E2E, so it usually skips). Net effect: 11 of
 * 12 specs provided no signal at all.
 *
 * Usage
 * -----
 *   import { test, expect } from './fixtures'   // instead of '@playwright/test'
 *
 * The seeding fixture is `auto`, so existing `async ({ page }) => ...` tests
 * need no other change.
 */
import { test as base, expect, type Page } from '@playwright/test'

export const E2E_TOKEN = 'e2e-token-not-a-real-jwt'
export const E2E_USER = {
  id: 'e2e-user',
  username: 'e2e',
  email: 'e2e@example.com',
  created_at: new Date(0).toISOString(),
}

/**
 * Make the page hermetic: no backend required.
 *
 * `vite dev` proxies /api to :8001. With no backend that yields 500s, and the
 * app's raw `fetch(...).then(r => r.json())` calls then throw
 * "SyntaxError: Unexpected end of JSON input" instead of surfacing a usable
 * error. Stubbing keeps these smoke tests deterministic and backend-free.
 *
 * The stub replies `[]`, not `{}`. Verified empirically: with `{}`, list
 * endpoints feed an object into `.map()` / `.some()`, which throws
 * ("rawData.some is not a function", "trees.map is not a function") and the
 * ErrorBoundary swallows the page. `[]` satisfies both shapes in practice —
 * array methods work, and property reads on an array yield `undefined`, which
 * the pages already guard with `|| []` / `|| 0`.
 *
 * The WebSocket sync client connects on boot; without a backend the handshake
 * failure shows up as console noise, so accept-and-idle instead.
 */
export async function stubBackend(page: Page): Promise<void> {
  await page.route('**/api/**', (route) => {
    const pathname = new URL(route.request().url()).pathname
    const checked_at = '2026-10-20T14:00:00Z'
    if (pathname === '/api/v1/ai/config') return route.fulfill({ json: { connections: [], default_model: '', presets: [], legacy_keys: [], server_connections: [] } })
    if (pathname === '/api/v1/ai/usage') return route.fulfill({ json: { tokens: 0, input_tokens: 0, output_tokens: 0, calls: 0, unmetered_calls: 0, day: '2026-10-20', utc_offset_minutes: 480, scope: 'account', source: 'local_call_logs', checked_at } })
    if (pathname === '/api/v1/ai/cache/stats') return route.fulfill({ json: { active_entries: 0, stored_entries: 0, expired_entries: 0, invalid_entries: 0, scope: 'server', source: 'cache_database', checked_at } })
    if (pathname === '/api/v1/ai/balance') return route.fulfill({ json: { status: 'unavailable', balances: [], message: '尚未选择个人默认连接', checked_at } })
    if (/\/api\/v1\/agent\/conversations\/[^/]+\/receipts$/.test(new URL(route.request().url()).pathname)) {
      return route.fulfill({ json: { receipts: [], has_more: false } })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
  })
  await page.routeWebSocket(/\/ws\//, () => {
    // Accept the connection, stay silent.
  })
}

export const test = base.extend<{ seedAuth: void }>({
  seedAuth: [
    async ({ page }, use) => {
      // addInitScript runs before any page script, so the auth store already
      // sees the session on its very first restore() call.
      await page.addInitScript(
        ({ token, user }) => {
          localStorage.setItem('knowall_token', token)
          localStorage.setItem('knowall_user', JSON.stringify(user))
        },
        { token: E2E_TOKEN, user: E2E_USER },
      )
      await stubBackend(page)
      await use()
    },
    { auto: true },
  ],
})

export { expect }
