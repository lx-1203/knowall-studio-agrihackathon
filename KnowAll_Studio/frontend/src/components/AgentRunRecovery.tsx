import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button } from 'antd'
import { cancelBackgroundRun, forgetObservedRun, observedRunId, readAgentRun, type ObservedAgentRun } from '../services/nativeAgent'
import AgentAnswerReceipt from './AgentAnswerReceipt'
import AgentSavedInput from './AgentSavedInput'

/** Read and observe only; never resumes or regenerates a task. */
export default function AgentRunRecovery({ userId, disabled, onOpen, onResume, initialRunId, onDismiss, hasDraft = true, onRestoreInput }: { userId: string; disabled: boolean; onOpen: (conversationId: string) => void; onResume?: (runId: string) => void; initialRunId?: string; onDismiss?: () => void; hasDraft?: boolean; onRestoreInput?: (question: string) => void }) {
  const [runId, setRunId] = useState(() => initialRunId || observedRunId(userId))
  const [record, setRecord] = useState<ObservedAgentRun | null>(null)
  const [readAt, setReadAt] = useState('')
  const [error, setError] = useState(''); const [pending, setPending] = useState(false)
  const controller = useRef<AbortController | null>(null)
  const [inputOpen, setInputOpen] = useState(false)
  const inputButton = useRef<HTMLButtonElement | HTMLAnchorElement | null>(null)
  const rereadButton = useRef<HTMLButtonElement | HTMLAnchorElement | null>(null)
  const closeInput = () => { setInputOpen(false); requestAnimationFrame(() => inputButton.current?.focus({ preventScroll: true })) }
  const stopController = useRef<AbortController | null>(null); const [stopPending, setStopPending] = useState(false)
  const load = useCallback(async (id: string) => {
    controller.current?.abort(); const next = new AbortController(); controller.current = next
    setPending(true); setError(''); setRecord(null); setReadAt('')
    try {
      const result = await readAgentRun(id, next.signal)
      if (controller.current === next && !next.signal.aborted) { setRecord(result); setReadAt(new Date().toISOString()) }
    } catch (cause) {
      if (controller.current === next && !next.signal.aborted) setError(cause instanceof Error ? cause.message : '运行状态读取失败')
    } finally {
      if (controller.current === next && !next.signal.aborted) { setPending(false); controller.current = null }
    }
  }, [])
  useEffect(() => {
    const id = initialRunId || observedRunId(userId); setRunId(id); setRecord(null); setError(''); setInputOpen(false)
    if (id) void load(id)
    return () => { controller.current?.abort(); stopController.current?.abort() }
  }, [userId, load, initialRunId])
  if (!runId) return null
  const title = pending ? '正在核对上次资料任务…' : error ? '上次资料任务读取失败'
    : record?.status === 'completed' ? '上次资料任务已完成'
    : record?.status === 'cancelled' ? '上次资料任务已取消'
    : record?.status === 'failed' ? '上次资料任务未完成'
    : record?.status === 'waiting_user' ? '上次资料任务等待你的处理'
    : record?.status === 'partial' ? '上次资料任务部分完成'
    : record?.cancel_requested ? '停止请求已保存，等待任务结束' : '上次资料任务的状态需要核对'
  const backgroundActive = record?.execution_mode === 'background' && ['running', 'queued'].includes(record.status)
  const lastRecordedAt = record?.steps.filter(step => step.at).slice(-1)[0]?.at
  const stop = async () => {
    const next = new AbortController(); stopController.current = next; setStopPending(true)
    try { await cancelBackgroundRun(runId, next.signal); if (!next.signal.aborted) await load(runId) }
    catch (cause) { if (!next.signal.aborted) setError(cause instanceof Error ? cause.message : '停止请求尚未确认') }
    finally { if (!next.signal.aborted) setStopPending(false) }
  }
  return <section className="chat-agent-recovery" aria-label="上次资料任务">
    <Alert type={error || record?.status !== 'completed' ? 'warning' : 'info'} showIcon message={title}
      description={error || (record ? `已读取 ${record.steps.length} 条保存的执行事件。没有自动继续执行或再次生成。${record.recovery_state === 'unconfirmed' ? '长时间没有记录新进度，状态尚未确认。' : ''}` : '仅查询已保存状态。')} />
    <div className="chat-agent-recovery-actions"><Button ref={rereadButton} size="small" loading={pending} disabled={disabled} onClick={() => void load(runId)}>重新读取运行状态</Button>
      {record?.status === 'completed' && record.conversation_id && <Button size="small" disabled={disabled} onClick={() => onOpen(record.conversation_id!)}>查看已保存对话</Button>}
      {backgroundActive && onResume && <Button size="small" disabled={disabled || stopPending} onClick={() => onResume(runId)}>继续查看进度</Button>}
      {backgroundActive && !record?.cancel_requested && <Button size="small" loading={stopPending} disabled={disabled} onClick={() => void stop()}>停止后台任务</Button>}
      {record?.input_saved && onRestoreInput && <Button ref={inputButton} size="small" disabled={disabled} onClick={() => setInputOpen(true)}>取回任务输入</Button>}
      <Button size="small" disabled={disabled} onClick={() => { controller.current?.abort(); controller.current = null; forgetObservedRun(userId, runId); setRunId(null); onDismiss?.() }}>关闭任务提示</Button></div>
    {record && <div className="chat-agent-inspection">
      <h2>已保存的任务详情</h2><p>仅展示已保存的记录，重新读取可查看最新状态。</p>
      <div className="chat-agent-inspection-meta">
        {record.created_at && <span>创建于 <time dateTime={record.created_at}>{new Date(record.created_at).toLocaleString('zh-CN', { hour12: false })}</time></span>}
        {lastRecordedAt && <span>最近记录 <time dateTime={lastRecordedAt}>{new Date(lastRecordedAt).toLocaleString('zh-CN', { hour12: false })}</time></span>}
        {!record.created_at && !lastRecordedAt && <span>历史时间未记录</span>}
      </div>
      <p className="chat-agent-inspection-read">本次读取（设备时间） <time dateTime={readAt}>{new Date(readAt).toLocaleString('zh-CN', { hour12: false })}</time></p>
      <AgentAnswerReceipt key={record.run_id} summaryLabel="查看已保存步骤" receipt={{ run_id: record.run_id, model: record.model, steps: record.steps, sources: record.sources }} />
    </div>}
    <AgentSavedInput key={`${userId}:${runId}`} runId={runId} open={inputOpen} disabled={disabled} hasDraft={hasDraft} onClose={closeInput}
      onRestore={question => { if (!disabled && !hasDraft && onRestoreInput) { setInputOpen(false); onRestoreInput(question) } }}
      onDeleted={() => { setInputOpen(false); setRecord(previous => previous ? { ...previous, input_saved: false } : previous); requestAnimationFrame(() => rereadButton.current?.focus({ preventScroll: true })) }} />
  </section>
}
