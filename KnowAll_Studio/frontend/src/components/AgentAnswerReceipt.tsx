import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Drawer, Spin } from 'antd'
import { readAgentSource, type AgentReceipt, type AgentSource, type AgentStep } from '../services/nativeAgent'

function stepLabel(step: AgentStep) {
  const tool = step.tool === 'search_documents' ? '资料检索' : step.tool === 'read_document_chunk' ? '来源读取' : '工具调用'
  return step.type === 'tool_completed' ? `${tool}完成` : step.type === 'tool_failed' ? `${tool}失败`
    : step.type === 'tool_started' ? `${tool}开始` : step.type === 'run_completed' ? '依据资料回答完成'
    : step.type === 'run_failed' ? '任务未完成' : step.type === 'run_cancelled' ? '任务已取消' : step.type === 'step_started' ? '请求模型判断下一步' : '开始只读资料任务'
}

export default function AgentAnswerReceipt({ receipt, summaryLabel = '执行记录' }: { receipt: AgentReceipt; summaryLabel?: string }) {
  const [selected, setSelected] = useState<AgentSource | null>(null)
  const [current, setCurrent] = useState<AgentSource | null>(null)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const controller = useRef<AbortController | null>(null)
  const sourceButton = useRef<HTMLButtonElement | null>(null)
  const reader = useRef<HTMLDivElement | null>(null)
  const retryButton = useRef<HTMLElement | null>(null)
  useEffect(() => {
    // Keep Escape within the drawer when the focused retry control is replaced
    // by loading/content. Never steal focus after the user has moved elsewhere.
    if (!pending && error && document.activeElement === reader.current) retryButton.current?.focus({ preventScroll: true })
  }, [pending, error])
  useEffect(() => () => controller.current?.abort(), [])
  useEffect(() => {
    if (selected || !sourceButton.current) return
    // Rapid close can destroy the drawer before its motion callback fires.
    // Restore after the close render too, independently of animation timing.
    const frame = requestAnimationFrame(() => { const button = sourceButton.current; if (button?.isConnected) button.focus({ preventScroll: true }) })
    return () => cancelAnimationFrame(frame)
  }, [selected])
  async function read(source: AgentSource, retry = false) {
    if (retry) reader.current?.focus({ preventScroll: true })
    controller.current?.abort()
    const next = new AbortController(); controller.current = next
    setSelected(source); setCurrent(null); setError(''); setPending(true)
    try {
      const data = await readAgentSource(source, next.signal)
      if (controller.current === next && !next.signal.aborted) setCurrent(data)
    } catch (cause) {
      if (controller.current === next && !next.signal.aborted) setError(cause instanceof Error ? cause.message : '读取失败，请稍后重试。')
    } finally {
      if (controller.current === next && !next.signal.aborted) { setPending(false); controller.current = null }
    }
  }
  function close() { controller.current?.abort(); controller.current = null; setSelected(null); setPending(false); setCurrent(null) }
  return <div className="chat-agent-receipt">
    {receipt.live && receipt.steps.length > 0 && <p className="chat-agent-live" role="status" aria-live="polite">{receipt.cancelRequested ? '停止请求已保存，等待任务结束' : `正在执行 · ${stepLabel(receipt.steps[receipt.steps.length - 1])}`}</p>}
    {receipt.sources.length > 0 && <div className="chat-agent-sources" aria-label="回答来源">
      <span className="chat-agent-caption">资料来源</span>
      {receipt.sources.map(source => <button type="button" key={source.chunk_id} className="chat-agent-source"
        onClick={event => { sourceButton.current = event.currentTarget; void read(source) }}>
        <span className="chat-agent-source-id">{source.citation_id}</span><span><strong>{source.filename}</strong><small>{source.page_range ? `页码 ${source.page_range}` : '资料片段'} · 查看引用原文</small></span><span aria-hidden="true">↗</span>
      </button>)}
    </div>}
    {receipt.steps.length > 0 && <details className="chat-agent-steps"><summary>{summaryLabel}</summary><ol>{receipt.steps.map(step => <li key={step.seq}>
      <span>{stepLabel(step)}</span>{step.code && <small>{step.code}</small>}{step.at && <time dateTime={step.at}>{new Date(step.at).toLocaleTimeString('zh-CN', { hour12: false })}</time>}
    </li>)}</ol><p>{receipt.live ? '运行中 · 实时事件已保存' : receipt.run_id ? '已保存的执行记录' : '本次请求的执行记录'}{receipt.model ? ` · ${receipt.model}` : ''}</p></details>}
    <Drawer open={Boolean(selected)} title={selected ? `来源 ${selected.citation_id}` : '资料来源'} width="min(560px, 100vw)" rootClassName="chat-agent-drawer"
      onClose={close} afterOpenChange={open => {
        // rc-drawer restores its saved focus after this callback. Restore the
        // citation button after that work, including narrow layouts.
        if (!open) queueMicrotask(() => { const button = sourceButton.current; if (button?.isConnected) button.focus() })
      }} destroyOnHidden>
      {selected && <div className="chat-agent-reader" ref={reader} tabIndex={-1}><h2>{selected.filename}</h2><p>{selected.page_range ? `页码 ${selected.page_range}` : '资料片段'}</p>
        {pending && <div role="status"><Spin size="small" /> 正在核对并读取来源…</div>}
        {error && <><Alert type="warning" message={error} showIcon /><Button ref={button => { retryButton.current = button }} onClick={() => void read(selected, true)}>重新读取来源</Button></>}
        {current && <><div className="chat-agent-original">{current.text}</div>{current.truncated && <p>当前显示引用片段，长内容已截取。</p>}</>}
      </div>}
    </Drawer>
  </div>
}
