import { createParser } from 'eventsource-parser'

export class StreamHTTPError extends Error {
  constructor(readonly status: number) {
    super(`请求失败（${status}）`)
    if (status === 401) this.message = '登录已失效，请重新登录'
    if (status === 403) this.message = '没有访问权限'
  }
}

/** Consume POST SSE with the upstream protocol parser; true means business completion. */
export async function consumeJSONEventStream(
  response: Response,
  onData: (data: Record<string, unknown>) => boolean | void,
  options: { signal?: AbortSignal; allowEOFEvent?: boolean } = {},
): Promise<boolean> {
  const { signal } = options
  signal?.throwIfAborted()
  if (!response.ok) throw new StreamHTTPError(response.status)
  if (!response.headers.get('content-type')?.includes('text/event-stream')) throw new Error('服务未返回流式消息')
  const reader = response.body?.getReader()
  if (!reader) throw new Error('无法读取流式消息')
  const decoder = new TextDecoder('utf-8', { fatal: true })
  let completed = false
  const parser = createParser({
    maxBufferSize: 1024 * 1024,
    onError(error) { if (error.type === 'max-buffer-size-exceeded') throw error },
    onEvent(event) {
      if (completed || signal?.aborted) return
      const data = JSON.parse(event.data)
      if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('流式消息格式错误')
      completed = onData(data) === true
    },
  })
  const abort = () => { void reader.cancel().catch(() => {}) }
  signal?.addEventListener('abort', abort, { once: true })
  try {
    signal?.throwIfAborted()
    while (!completed) {
      const { done, value } = await reader.read()
      signal?.throwIfAborted()
      if (done) {
        parser.feed(decoder.decode())
        // Legacy reading replies sometimes omit the final blank line. Only this
        // opt-in caller accepts a complete JSON event at EOF, still requiring done/cached.
        if (!completed && options.allowEOFEvent) parser.feed('\n\n')
        break
      }
      parser.feed(decoder.decode(value, { stream: true }))
    }
    return completed
  } finally {
    signal?.removeEventListener('abort', abort)
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
