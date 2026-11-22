/**
 * Single source of truth for the API bearer token.
 *
 * Why this is its own module
 * --------------------------
 * The auth store only needs to *store* a token, but importing `api.ts` for that
 * pulled axios — 45 KB raw / 17.4 KB gzip — into the app shell's initial
 * critical path, for every visitor, before first paint.
 *
 * So the token lives here (zero dependencies) and `api.ts` attaches the header
 * at request time via an interceptor. The token still reaches every call; it
 * just no longer costs the shell an HTTP client to hold a string.
 *
 * See KnowAll_Studio/docs/optimization-report.md §11.
 */
let currentToken: string | null = null

export const getAuthToken = (): string | null => currentToken

export const setAuthToken = (token: string | null): void => {
  currentToken = token
  if (token) localStorage.setItem('knowall_token', token)
  else localStorage.removeItem('knowall_token')
}

/**
 * The ready-to-send `Authorization` header value, or `''` when unauthenticated.
 *
 * Several call sites use raw `fetch` instead of the axios instance; they need
 * the header in exactly this shape.
 */
export const authHeaderValue = (): string => (currentToken ? `Bearer ${currentToken}` : '')
