import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Empty, Space, Tag, Typography } from 'antd'
import { reviewLearningCard, startLearningSession, submitLearningAnswer } from '../api'
import StructuredAnswerInput from './StructuredAnswerInput'
import LearningContent from './LearningContent'

export default function LearningPractice({ packId, stale, onShowSource }: { packId: string; stale: boolean; onShowSource?: (chunkIds?: string[]) => void }) {
  const [items, setItems] = useState<any[] | null>(null)
  const [index, setIndex] = useState(0)
  const [answer, setAnswer] = useState('')
  const [result, setResult] = useState<any>(null)
  const [revealed, setRevealed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [started, setStarted] = useState(Date.now())
  const [mode, setMode] = useState<'smart' | 'practice'>('practice')
  const revision = useRef(0)
  useEffect(() => { revision.current++; setItems(null); setIndex(0); setResult(null); setAnswer(''); setRevealed(false); setError(''); setBusy(false) }, [packId])
  const begin = async (sessionMode: 'smart' | 'practice') => {
    setBusy(true); setError('')
    setMode(sessionMode)
    const current = revision.current
    try { const data = await startLearningSession(packId, sessionMode); if (current !== revision.current) return; setItems(data.items || []); setIndex(0); setAnswer(''); setResult(null); setRevealed(false); setStarted(Date.now()) }
    catch (e: any) { if (current === revision.current) setError(e.response?.status === 409 ? '学习包已过期，请重新生成后练习。' : '加载练习失败，请重试。') }
    finally { if (current === revision.current) setBusy(false) }
  }
  const next = () => { setIndex(i => i + 1); setAnswer(''); setResult(null); setRevealed(false); setError(''); setStarted(Date.now()) }
  const submit = async () => {
    setBusy(true); setError('')
    const current = revision.current
    try { const feedback = await submitLearningAnswer(packId, items![index].question.id, answer, Date.now() - started); if (current === revision.current) setResult(feedback) }
    catch (e: any) { if (current === revision.current) setError(e.response?.status === 409 ? '学习包已过期，请重新生成。' : '提交失败，答案已保留，请重试。') }
    finally { if (current === revision.current) setBusy(false) }
  }
  const rate = async (rating: number) => {
    setBusy(true); setError('')
    const current = revision.current
    try { await reviewLearningCard(packId, items![index].card.id, rating); if (current === revision.current) next() }
    catch { if (current === revision.current) setError('评分保存失败，请重试。') }
    finally { if (current === revision.current) setBusy(false) }
  }
  const item = items?.[index]
  return <Space direction="vertical" style={{ width: '100%' }}>
    {stale && <Alert type="warning" message="来源已更新，请重新生成学习包。" />}
    {error && <Alert type="error" message={error} />}
    {items === null ? <Space wrap><Button type="primary" disabled={stale || busy} loading={busy} onClick={() => begin('practice')}>章节练习</Button><Button disabled={stale || busy} onClick={() => begin('smart')}>智能复习</Button></Space> : !item ? <><Empty description="本轮练习已完成" /><Button disabled={stale || busy} onClick={() => begin(mode)}>再练一轮</Button><Button disabled={stale || busy} onClick={() => begin(mode === 'practice' ? 'smart' : 'practice')}>{mode === 'practice' ? '智能复习' : '章节练习'}</Button></> : <>
      <Typography.Text>进度 {index + 1} / {items.length}</Typography.Text>
      <Card title={item.title || '章节笔记'}><LearningContent text={item.notes || '本章节暂无笔记'} /><Tag>{({ wrong_answer: '错题回访', weak_section: '薄弱章节', practice: '章节练习', due: '到期卡片' } as Record<string, string>)[item.reason] || '练习'}</Tag></Card>
      <Card title={item.kind === 'question' ? '随堂练习' : '复习卡片'}>
        {item.kind === 'question' ? <Space direction="vertical" style={{ width: '100%' }}>
          <div><LearningContent text={item.question.question_text} /></div>
          <StructuredAnswerInput key={item.question.id} question={item.question} value={answer} onChange={setAnswer} disabled={busy || !!result} />
          {!result ? <Button type="primary" disabled={!answer.trim()} loading={busy} onClick={submit}>提交答案</Button> : <>
            <Alert type={result.is_correct ? 'success' : 'warning'} message={result.is_correct ? '回答正确' : '继续巩固'} description={<><div>正确答案：{typeof result.correct_answer === 'string' ? result.correct_answer : JSON.stringify(result.correct_answer)}</div><LearningContent text={result.analysis || ''} /></>} />
            {typeof result.score_ratio === 'number' && <Typography.Text>得分：{Math.round(result.score_ratio * 100)}%</Typography.Text>}
            <Button onClick={next}>下一项</Button>
          </>}
        </Space> : <Space direction="vertical" style={{ width: '100%' }}>
          <div><LearningContent text={item.card.front} /></div>
          {!revealed ? <Button onClick={() => setRevealed(true)}>显示答案</Button> : <><div><LearningContent text={item.card.back} /></div><Space wrap>{['重来', '困难', '良好', '简单'].map((label, i) => <Button key={label} aria-label={label} disabled={busy} onClick={() => rate(i + 1)}>{label}</Button>)}</Space></>}
        </Space>}
        <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>来源片段：{(result?.source?.chunk_ids || item.source?.chunk_ids || []).join('、') || '暂无'}；页码：{JSON.stringify(result?.source?.page_ranges || item.source?.page_ranges || [])}</Typography.Paragraph>
        {onShowSource && <Button onClick={() => onShowSource(result?.source?.chunk_ids || item.source?.chunk_ids || [])}>定位来源原文</Button>}
      </Card>
    </>}
  </Space>
}
