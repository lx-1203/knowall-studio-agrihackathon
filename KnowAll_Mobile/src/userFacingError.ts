export function screenError(error: unknown, fallback: string) {
  const status = (error as { response?: { status?: unknown } } | null)?.response?.status
  // Response bodies can contain framework text, SQL or private diagnostics.
  // Only approved product copy crosses into the UI; other failures keep the
  // screen's recovery guidance, including any warning about unconfirmed saves.
  switch (status) {
    case 401: return '登录状态已失效，请重新登录后继续。'
    case 403: return '暂时无法访问这项内容，请确认当前账号的权限。'
    case 429: return '操作有些频繁，请稍等片刻再试。'
    default: return fallback
  }
}
