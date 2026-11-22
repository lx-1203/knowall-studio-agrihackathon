import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Drawer } from 'antd'
import { readSavedAgentInput, deleteSavedAgentInput, type SavedAgentInput } from '../services/nativeAgent'

/** Manual draft retrieval; contains no submit or execution-resume action. */
export default function AgentSavedInput({ runId, open, disabled, hasDraft, onClose, onRestore, onDeleted }: {
  runId: string; open: boolean; disabled: boolean; hasDraft: boolean; onClose: () => void;
  onRestore: (question: string) => void; onDeleted: () => void;
}) {
  const [saved, setSaved] = useState<SavedAgentInput | null>(null)
  const [error, setError] = useState(''); const [pending, setPending] = useState(false); const [deleting, setDeleting] = useState(false)
  const controller = useRef<AbortController | null>(null)
  const reader = useRef<HTMLDivElement>(null)
  const load = useCallback(async () => {
    controller.current?.abort(); const next = new AbortController(); controller.current = next
    setPending(true); setError(''); setSaved(null); reader.current?.focus({ preventScroll: true })
    try { const data = await readSavedAgentInput(runId, next.signal); if (!next.signal.aborted && controller.current === next) setSaved(data) }
    catch (cause) { if (!next.signal.aborted && controller.current === next) setError(cause instanceof Error ? cause.message : '保存输入暂时无法读取') }
    finally { if (!next.signal.aborted && controller.current === next) { setPending(false); controller.current = null } }
  }, [runId])
  useEffect(() => {
    setSaved(null); setError(''); setPending(false); setDeleting(false)
    if (open) void load()
    return () => { controller.current?.abort(); controller.current = null }
  }, [open, load])
  const remove = async () => {
    controller.current?.abort(); const next = new AbortController(); controller.current = next
    setDeleting(true); setError('')
    try { await deleteSavedAgentInput(runId, next.signal); if (!next.signal.aborted && controller.current === next) { setSaved(null); onDeleted() } }
    catch (cause) { if (!next.signal.aborted && controller.current === next) setError(cause instanceof Error ? cause.message : '删除输入尚未确认') }
    finally { if (!next.signal.aborted && controller.current === next) { setDeleting(false); controller.current = null } }
  }
  return <Drawer title="已保存的任务输入" width="min(520px, 100vw)" open={open} onClose={onClose} destroyOnHidden rootClassName="chat-agent-input-drawer">
    <div ref={reader} tabIndex={-1} className="chat-agent-input-reader">
      <p>取回原问题供你编辑。不会自动发送、切换模型或重新执行任务。</p>
      {pending && <p role="status">正在核对权限并读取输入…</p>}
      {error && <Alert type="warning" showIcon message={error} />}
      {saved ? <>
        <dl className="chat-agent-input-meta"><dt>原请求模型</dt><dd>{saved.requested_model}</dd><dt>保存时间</dt><dd><time dateTime={saved.created_at}>{new Date(saved.created_at).toLocaleString('zh-CN', { hour12: false })}</time></dd></dl>
        <div className="chat-agent-saved-question">{saved.message}</div>
        {hasDraft && <p role="status">先处理当前草稿，再放入输入框。</p>}
        <div className="chat-agent-input-actions"><Button type="primary" disabled={disabled || hasDraft || deleting} onClick={() => onRestore(saved.message)}>放入输入框</Button>
          <Button danger disabled={disabled} loading={deleting} onClick={() => void remove()}>删除已保存输入</Button></div>
        <p>删除输入不会停止正在执行的任务，也不会删除回答和执行记录。</p>
      </> : !pending && <Button onClick={() => void load()}>重新读取输入</Button>}
    </div>
  </Drawer>
}
