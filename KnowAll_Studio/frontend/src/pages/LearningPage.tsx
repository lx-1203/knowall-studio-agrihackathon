import { useEffect, useRef, useState } from 'react'
import { Alert, App, Button, Card, Collapse, Empty, Input, InputNumber, Modal, Select, Space, Tabs, Tag, Typography } from 'antd'
import { Link } from 'react-router-dom'
import { exportLearningPack, generateLearningPack, getDocumentChunks, getLearningPack, getLearningPreview, listDocuments, repairLearningQuestion } from '../api'
import { useAppStore } from '../stores'
import LearningPractice from '../components/LearningPractice'
import ReactMarkdown from 'react-markdown'

export default function LearningPage() {
  const { selectedDoc, setSelectedDoc } = useAppStore()
  const { message } = App.useApp()
  const [documents, setDocuments] = useState<any[]>([])
  const [preview, setPreview] = useState<any>(null)
  const [pack, setPack] = useState<any>(null)
  const [questionCount, setQuestionCount] = useState(10)
  const [cardCount, setCardCount] = useState(10)
  const [concurrency, setConcurrency] = useState(2)
  const [model, setModel] = useState('deepseek-chat')
  const [types, setTypes] = useState(['single_choice'])
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [repair, setRepair] = useState<any>(null)
  const [source, setSource] = useState<any[] | null>(null)
  const [repairFields, setRepairFields] = useState({ question_text: '', options: '', answer: '', analysis: '' })
  const request = useRef(0)
  useEffect(() => { listDocuments().then((data: any) => setDocuments(Array.isArray(data) ? data : data.items || data.documents || [])).catch(() => setError('文档加载失败，请刷新重试。')) }, [])
  useEffect(() => {
    const id = ++request.current
    setPack(null); setPreview(null); setError('')
    if (!selectedDoc) return
    setLoading(true)
    Promise.all([getLearningPreview(selectedDoc, questionCount, cardCount), getLearningPack(selectedDoc)])
      .then(([p, latest]) => { if (id === request.current) { setPreview(p); setPack(latest?.id ? latest : null) } })
      .catch(() => { if (id === request.current) setError('学习包加载失败，请重试或重新生成。') })
      .finally(() => { if (id === request.current) setLoading(false) })
  }, [selectedDoc, questionCount, cardCount])
  const generate = async () => {
    if (!selectedDoc) return
    const id = ++request.current
    setBusy(true); setError('')
    try {
      const data = await generateLearningPack({ document_id: selectedDoc, question_count: questionCount, card_count: cardCount, concurrency, model: model.trim() || 'deepseek-chat', question_types: types })
      if (id === request.current) {
        setPack(data)
        const failed = data.coverage?.failed_sections || 0
        if (failed > 0 && failed === data.coverage?.total_sections) {
          message.error('所有章节生成失败，请检查模型配置后重试。')
        } else if (failed > 0) {
          message.warning(`${failed} 个章节生成失败，已保留其他章节，请查看质检与缺漏。`)
        } else {
          message.success(data.from_cache ? '已复用相同来源的缓存学习包' : '学习包已生成')
        }
      }
    } catch (e: any) { if (id === request.current) setError(e.response?.data?.detail || '生成失败，请重试。') }
    finally { setBusy(false) }
  }
  const download = async (format: 'json' | 'markdown' | 'csv') => {
    try {
      const content = await exportLearningPack(pack.id, format)
      const url = URL.createObjectURL(new Blob([content], { type: format === 'json' ? 'application/json;charset=utf-8' : 'text/plain;charset=utf-8' }))
      const link = document.createElement('a'); link.href = url; link.download = `learning-${pack.id}.${format === 'markdown' ? 'md' : format}`; link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch { message.error('导出失败，请重试。') }
  }
  const openRepair = (q: any) => { setRepair(q); setRepairFields({ question_text: q.question_text, options: JSON.stringify(q.options || [], null, 2), answer: typeof q.answer === 'string' ? q.answer : JSON.stringify(q.answer), analysis: q.analysis || '' }) }
  const saveRepair = async () => {
    let options
    try { options = JSON.parse(repairFields.options); if (!Array.isArray(options)) throw new Error() } catch { message.error('选项必须是有效的 JSON 数组'); return }
    setBusy(true)
    try {
      const fixed = await repairLearningQuestion(pack.id, repair.id, { ...repairFields, options })
      // Reload aggregate quality and section coverage from the server after rechecking.
      const latest = await getLearningPack(selectedDoc!)
      if (latest?.id) setPack(latest)
      setRepair(null); message.success(fixed.quality?.eligible === false ? '已保存，质检仍有问题' : '已保存并重新质检')
    } catch { message.error('修复保存失败，请重试。') }
    finally { setBusy(false) }
  }
  const sections = pack?.sections || []
  const expectedQuestions = pack?.config?.question_count ?? questionCount
  const expectedCards = pack?.config?.card_count ?? cardCount
  const missing = (s: any) => ({ questions: (s.question_budget ?? expectedQuestions) > 0 && !s.questions?.length, cards: (s.card_budget ?? expectedCards) > 0 && !s.flashcards?.length, blocked: !!s.questions?.length && !s.questions.some((q: any) => q.quality?.eligible !== false) })
  const failed = sections.filter((s: any) => s.errors?.length || Object.values(missing(s)).some(Boolean))
  const showSource = async (chunkIds?: string[]) => {
    try { const data = await getDocumentChunks(selectedDoc!); const chunks = Array.isArray(data) ? data : data.chunks || []; setSource(chunkIds?.length ? chunks.filter((c: any) => chunkIds.includes(c.id)) : chunks) }
    catch { message.error('原文加载失败，请重试。') }
  }
  return <div style={{ maxWidth: 1100, margin: '0 auto', padding: 16, minWidth: 0 }}>
    <Typography.Title level={2}>学习工作台</Typography.Title>
    <Typography.Paragraph type="secondary">按章节整理笔记、习题和复习卡片，在同一处学习与巩固。</Typography.Paragraph>
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Card title="生成学习包">
        <Space wrap align="end" style={{ width: '100%' }}>
          <label>学习文档<Select aria-label="学习文档" placeholder="选择文档" disabled={busy} value={selectedDoc || undefined} style={{ width: 240, maxWidth: '100%' }} options={documents.map(d => ({ value: d.id, label: d.title || d.filename || d.name || d.id }))} onChange={setSelectedDoc} /></label>
          <label>习题数量<InputNumber aria-label="习题数量" min={0} max={100} value={questionCount} disabled={busy} onChange={v => setQuestionCount(v ?? 0)} /></label>
          <label>卡片数量<InputNumber aria-label="卡片数量" min={0} max={100} value={cardCount} disabled={busy} onChange={v => setCardCount(v ?? 0)} /></label>
          <label>并发数<InputNumber aria-label="并发数" min={1} max={4} value={concurrency} disabled={busy} onChange={v => setConcurrency(v ?? 1)} /></label>
          <label>模型<Input aria-label="模型" value={model} disabled={busy} onChange={e => setModel(e.target.value)} style={{ width: 180 }} /></label>
        </Space>
        <Select aria-label="题型" mode="multiple" style={{ width: '100%', marginTop: 16 }} value={types} disabled={busy} onChange={setTypes} options={[['single_choice', '单选'], ['multi_choice', '多选'], ['true_false', '判断'], ['fill_blank', '填空'], ['short_answer', '简答'], ['matching', '匹配'], ['ordering', '排序'], ['classification', '分类']].map(([value, label]) => ({ value, label }))} />
        <Button type="primary" style={{ marginTop: 16 }} loading={busy} disabled={!selectedDoc || loading || !types.length || questionCount + cardCount === 0} onClick={generate}>生成学习包</Button>
      </Card>
      {error && <Alert type="error" message={error} />}
      {preview?.sections?.length > 0 && <Card title="章节预算预览" loading={loading}><Space direction="vertical" style={{ width: '100%' }}>{preview.sections.map((s: any) => <div key={s.id}>{s.title} · {s.characters} 字 · 习题 {s.question_budget} / 卡片 {s.card_budget}</div>)}</Space></Card>}
      {pack ? <>
        {pack.stale && <Alert type="warning" message="来源已更新，当前学习包已过期，请重新生成。" />}
        <Card title={pack.title || '学习包'}>
          <Space wrap><Tag>章节 {pack.coverage?.total_sections ?? sections.length}</Tag><Tag>习题覆盖 {pack.coverage?.question_sections ?? 0}</Tag><Tag>卡片覆盖 {pack.coverage?.flashcard_sections ?? 0}</Tag><Tag color={pack.quality?.blocked ? 'warning' : 'success'}>质检隔离 {pack.quality?.blocked ?? 0} / {pack.quality?.total ?? 0}</Tag></Space>
          <Space wrap style={{ marginTop: 16 }}>{(['json', 'markdown', 'csv'] as const).map(f => <Button key={f} onClick={() => download(f)}>{f === 'csv' ? '下载质检 CSV' : `下载 ${f.toUpperCase()}`}</Button>)}</Space>
          <Space wrap style={{ marginTop: 16 }}>{pack.summary_id && <><Link to={`/summary/${pack.summary_id}`}>全文摘要</Link><Link to={`/mindmap/${pack.summary_id}`}>知识树</Link><Link to={`/coverage/${pack.summary_id}`}>覆盖报告</Link></>}<Link to={pack.deck_id ? `/flashcards?deck_id=${encodeURIComponent(pack.deck_id)}` : "/flashcards"}>复习卡片</Link><Button onClick={() => showSource()}>查看来源原文</Button></Space>
        </Card>
        <Tabs items={[
          { key: 'practice', label: '学习与练习', children: <LearningPractice packId={pack.id} stale={!!pack.stale} onShowSource={showSource} /> },
          { key: 'chapters', label: '章节笔记', children: <Collapse items={sections.map((s: any) => ({ key: s.id, label: s.title, children: <><ReactMarkdown>{s.notes || '暂无笔记'}</ReactMarkdown><div>习题 {s.questions?.length || 0} · 卡片 {s.flashcards?.length || 0}</div><Typography.Text type="secondary">来源片段：{s.chunk_ids?.join('、') || '暂无'} · 页码：{JSON.stringify(s.page_ranges || [])}</Typography.Text>{s.errors?.map((e: string, i: number) => <Alert key={i} type="warning" message={e} />)}</> }))} /> },
          { key: 'quality', label: '质检与缺漏', children: <Space direction="vertical" style={{ width: '100%' }}>
            <Alert type={failed.length ? 'warning' : 'info'} message={failed.length ? `${failed.length} 个章节存在生成失败或内容缺漏` : '章节内容无缺漏'} description={failed.map((s: any) => `${s.title}：${s.errors?.join('；') || ''}${missing(s).questions ? ' 缺习题' : ''}${missing(s).cards ? ' 缺卡片' : ''}${missing(s).blocked ? ' 全部习题已隔离，请修复' : ''}`).join('；')} />
            {sections.flatMap((s: any) => (s.questions || []).map((q: any) => <Card key={q.id} size="small" title={s.title}><Typography.Paragraph>{q.question_text}</Typography.Paragraph><Tag color={q.quality?.eligible === false ? 'error' : 'success'}>{q.quality?.eligible === false ? '已隔离' : '可练习'}</Tag><div>{(q.quality?.issues || []).map((issue: any) => typeof issue === 'string' ? issue : JSON.stringify(issue)).join('；')}</div><Button onClick={() => openRepair(q)}>修复题目</Button></Card>))}
          </Space> },
        ]} />
      </> : !loading && <Empty description="选择文档并生成学习包，开始章节学习" />}
    </Space>
    <Modal title="来源原文" open={source !== null} onCancel={() => setSource(null)} footer={null}>
      {source?.length ? source.map((chunk: any, i: number) => <Card key={chunk.id || i} size="small" title={`片段 ${chunk.id || i + 1}`}><Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{chunk.content || chunk.text || ''}</Typography.Paragraph></Card>) : <Empty description="暂无来源片段" />}
    </Modal>
    <Modal title="修复题目并重新质检" open={!!repair} onCancel={() => setRepair(null)} onOk={saveRepair} confirmLoading={busy} okText="保存并质检" cancelText="取消">
      <Space direction="vertical" style={{ width: '100%' }}>{(['question_text', 'options', 'answer', 'analysis'] as const).map((key, i) => <label key={key}>{['题干', '选项 JSON', '正确答案', '解析'][i]}<Input.TextArea aria-label={['题干', '选项 JSON', '正确答案', '解析'][i]} rows={key === 'options' ? 5 : 3} value={repairFields[key]} onChange={e => setRepairFields(f => ({ ...f, [key]: e.target.value }))} /></label>)}</Space>
    </Modal>
  </div>
}
