import { useEffect, useState } from 'react'
import { Alert, Button, Drawer, Empty, Spin, Tag } from 'antd'
import { listAgentRuns, type ObservedAgentRun } from '../services/nativeAgent'
const labels: Record<ObservedAgentRun['status'], string> = { queued: '排队中', running: '运行中', waiting_user: '等待处理', completed: '已完成', partial: '部分完成', failed: '未完成', cancelled: '已取消' }
export default function AgentRunList({ userId, open, disabled, onClose, onOpen, onInspect }: { userId: string; open: boolean; disabled: boolean; onClose: () => void; onOpen: (id: string) => void; onInspect: (id: string) => void }) {
  const [offset, setOffset] = useState(0); const [retry, setRetry] = useState(0)
  const [runs, setRuns] = useState<ObservedAgentRun[]>([]); const [hasMore, setHasMore] = useState(false)
  const [pending, setPending] = useState(false); const [error, setError] = useState('')
  useEffect(() => { setOffset(0); setRuns([]); setError(''); setHasMore(false) }, [userId, open])
  useEffect(() => {
    if (!open || !userId) return
    const controller = new AbortController(); setPending(true); setError(''); setRuns([]); setHasMore(false)
    void listAgentRuns(offset, controller.signal).then(data => {
      if (!controller.signal.aborted) { setRuns(data.runs); setHasMore(data.hasMore) }
    }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '任务记录读取失败') })
      .finally(() => { if (!controller.signal.aborted) setPending(false) })
    return () => controller.abort()
  }, [userId, open, offset, retry])
  return <Drawer title="资料任务记录" placement="right" width="min(480px, 100vw)" open={open} onClose={onClose} className="chat-agent-run-list">
    <p className="chat-agent-list-intro">查看当前账号保存的执行状态。离开页面和停止后台任务是两个操作。</p>
    <Button size="small" loading={pending} onClick={() => setRetry(n => n + 1)}>重新读取任务记录</Button>
    {error ? <Alert type="warning" showIcon message={error} /> : pending ? <Spin aria-label="正在读取任务记录" />
      : !runs.length ? <Empty description="暂无资料任务记录" image={Empty.PRESENTED_IMAGE_SIMPLE} /> : <ul className="chat-agent-run-items">
        {runs.map(run => <li key={run.run_id}>
          <div className="chat-agent-run-heading"><Tag color={run.status === 'completed' ? 'success' : run.status === 'failed' ? 'error' : 'default'}>{labels[run.status]}</Tag>
            <span>{run.execution_mode === 'background' ? '后台资料任务' : '资料问答'}</span></div>
          <p>{run.model}</p><p><time dateTime={run.created_at}>{run.created_at ? new Date(run.created_at).toLocaleString() : '历史时间未记录'}</time> · {run.steps.length} 条执行记录</p>
          {run.cancel_requested && run.status === 'running' && <p role="status">停止请求已保存，等待任务结束</p>}
          {run.recovery_state === 'unconfirmed' && <p>长时间没有新进度，状态需要核对。</p>}
          <div>{run.status === 'completed' && run.conversation_id
            ? <Button size="small" disabled={disabled} onClick={() => { onClose(); onOpen(run.conversation_id!) }}>查看已保存对话</Button>
            : <Button size="small" disabled={disabled} onClick={() => { onClose(); onInspect(run.run_id) }}>核对运行状态</Button>}</div>
        </li>)}
      </ul>}
    <div className="chat-agent-list-pagination"><Button size="small" disabled={pending || offset === 0} onClick={() => setOffset(n => Math.max(0, n - 20))}>上一页</Button>
      <span>第 {Math.floor(offset / 20) + 1} 页</span><Button size="small" disabled={pending || !hasMore} onClick={() => setOffset(n => n + 20)}>下一页</Button></div>
  </Drawer>
}
