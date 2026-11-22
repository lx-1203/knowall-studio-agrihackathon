import { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Input } from 'antd'
import { SaveOutlined } from '@ant-design/icons'
import { useAuthStore } from '../stores'
import { desktopStateSlot, materialSyncMessage } from '../materialStateSync'

export default function MaterialNotebook({ documentId }: { documentId: string }) {
  const userId = useAuthStore(state => state.user?.id || 'local')
  const key = `knowall:material-note:${userId}:${documentId}`
  const [note, setNote] = useState(() => { try { return localStorage.getItem(key) || '' } catch { return '' } })
  const [status, setStatus] = useState('')
  const [failed, setFailed] = useState(false)
  const current = useRef(note)
  const scope = useRef(key)
  const liveScope = useRef(key); liveScope.current = key
  const slot = useMemo(() => desktopStateSlot(userId, documentId, 'note', () => current.current === (localStorage.getItem(key) ?? '') && useAuthStore.getState().user?.id === userId), [userId, documentId])
  useEffect(() => {
    let live = true
    try { const raw = localStorage.getItem(key) ?? ''; current.current = raw; scope.current = key; setNote(raw); setStatus(''); setFailed(false) }
    catch { setFailed(true); setStatus('本机笔记读取失败，未覆盖旧记录。'); return }
    void slot.sync().then(result => {
      if (!live) return
      if (result.status === 'downloaded') { const text = localStorage.getItem(key) ?? ''; current.current = text; setNote(text) }
      setStatus(materialSyncMessage(result) || '已保存到本机与服务器')
    })
    return () => { live = false }
  }, [slot])
  const save = (value = note) => {
    current.current = value
    void slot.saveRaw(value).then(() => {
      if (liveScope.current !== key) return { status: 'pending' as const }
      setFailed(false); setStatus('已保存到本机，正在同步…')
      return slot.sync()
    }).then(result => { if (liveScope.current === key && current.current === value) setStatus(materialSyncMessage(result) || '已保存到本机与服务器') })
      .catch(() => { if (liveScope.current === key) { setFailed(true); setStatus('本机备份或保存失败，请先复制笔记保存。') } })
  }
  return <div className="material-notebook">
    <p className="material-muted">随手记录，自动保存。笔记按账号和资料保存在本机浏览器。</p>
    <Input.TextArea aria-label="我的学习笔记" placeholder="这章讲了什么？有什么还没理解？" autoSize={{ minRows: 10, maxRows: 24 }} value={scope.current === key ? note : ''} onChange={event => { const value = event.target.value; setNote(value); save(value) }} />
    <div className="material-note-footer"><Button aria-label="保存笔记" icon={<SaveOutlined />} onClick={() => save()}>保存笔记</Button><span role="status">{!failed && status}</span></div>
    {failed && <Alert type="error" message={status} />}
  </div>
}
