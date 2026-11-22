import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Empty, Input } from 'antd'
import { SendOutlined } from '@ant-design/icons'
import LearningContent from './LearningContent'
import { chatWithAssistant } from '../api'
import type { SourceChunk } from '../materials'

export default function MaterialAssistant({ chunk }: { chunk?: SourceChunk }) {
  const [question, setQuestion] = useState('')
  const [reply, setReply] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const revision = useRef(0)
  useEffect(() => { revision.current++; setReply(''); setError(''); setBusy(false); return () => { revision.current++ } }, [chunk?.id])
  const ask = async () => {
    if (!chunk || !question.trim() || busy) return
    const current = ++revision.current
    setBusy(true); setError('')
    try {
      const data = await chatWithAssistant({ message: question.trim(), role_preset: 'tutor', knowledge_context: chunk.text.slice(0, 20000) })
      if (current === revision.current) setReply(data.message || '没有收到回答，请重试。')
    } catch { if (current === revision.current) setError('回答暂时不可用，请检查模型设置后重试。') }
    finally { if (current === revision.current) setBusy(false) }
  }
  return <div className="material-assistant">
    <p className="material-muted">围绕目录中选中的正文提问。发送时使用你配置的 AI 模型。</p>
    {chunk ? <><div className="material-assistant-source">当前依据：{chunk.page_range || `阅读片段 ${chunk.index + 1}`}{chunk.text.length > 20000 && '（仅使用前 20,000 字）'}</div><Input.TextArea aria-label="向当前资料提问" autoSize={{ minRows: 3, maxRows: 8 }} placeholder="帮我解释这一段，或者举个例子…" value={question} disabled={busy} onChange={event => setQuestion(event.target.value)} /><Button type="primary" icon={<SendOutlined />} loading={busy} disabled={!question.trim()} onClick={ask}>发送问题</Button></> : <Empty description="正文解析完成后，可以针对资料提问" />}
    {error && <Alert type="error" message={error} />}
    {reply && <div className="material-markdown material-assistant-reply"><LearningContent text={reply} /></div>}
  </div>
}
