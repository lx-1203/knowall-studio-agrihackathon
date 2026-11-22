import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, AutoComplete, Button, Collapse, Empty, InputNumber, Modal, Select, Space, Spin, Tabs, Tag } from 'antd'
import { BulbOutlined, ReloadOutlined } from '@ant-design/icons'
import LearningContent from './LearningContent'
import { generateLearningPack, getLearningPack, getLearningPreview, getModelConfiguration, listAvailableModels } from '../api'
import LearningPractice from './LearningPractice'
import MaterialAssistant from './MaterialAssistant'
import MaterialNotebook from './MaterialNotebook'
import RelatedMaterials from './RelatedMaterials'
import MaterialMindMap from './MaterialMindMap'
import type { SourceChunk } from '../materials'

interface StudyCard { id: string; front: string; back: string }
interface StudySection { id: string; title: string; notes: string; chunk_ids: string[]; flashcards: StudyCard[]; errors?: string[] }
interface StudyPack { id: string; stale?: boolean; sections: StudySection[]; summary_id?: string; quality?: { blocked: number }; coverage?: { failed_sections?: number; total_sections?: number } }
interface Budget { id: string; title: string; question_budget: number; card_budget: number }

function KnowledgeCard({ card }: { card: StudyCard }) {
  const [revealed, setRevealed] = useState(false)
  return <article className="material-knowledge-card"><div className="material-knowledge-question" role="heading" aria-level={4}><LearningContent text={card.front} /></div>{revealed && <div data-testid="material-card-answer"><LearningContent text={card.back} /></div>}<Button size="small" onClick={() => setRevealed(value => !value)}>{revealed ? '收起答案' : '显示答案'}</Button></article>
}

export default function MaterialResources({ documentId, title, chunks, subject, chunk, onSource, ready }: { documentId: string; title: string; chunks: SourceChunk[]; subject?: string; chunk?: SourceChunk; onSource: (ids?: string[]) => void; ready: boolean }) {
  const [pack, setPack] = useState<StudyPack | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [questionCount, setQuestionCount] = useState(10)
  const [cardCount, setCardCount] = useState(10)
  const [model, setModel] = useState('')
  const [models, setModels] = useState<{ value: string; label: string }[]>([])
  const [modelError, setModelError] = useState('')
  const [types, setTypes] = useState(['single_choice'])
  const [budget, setBudget] = useState<Budget[]>([])
  const [budgetError, setBudgetError] = useState('')
  const [generationError, setGenerationError] = useState('')
  const [notice, setNotice] = useState('')
  const revision = useRef(0)
  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    setModelError('')
    getModelConfiguration({ signal: controller.signal }).then(saved => {
      if (!controller.signal.aborted) {
        setModel(saved?.model || '')
        if (!saved?.model) setModelError('请先到系统设置选择默认模型，或在这里明确填写已配置的模型。')
      }
    }).catch(() => { if (!controller.signal.aborted) setModelError('默认模型未能加载，可手动填写模型，或到系统设置检查配置。') })
    listAvailableModels(false, { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setModels(result.models || [])
    }).catch(() => { /* Manual entry remains available. */ })
    return () => controller.abort()
  }, [open, documentId])
  useEffect(() => {
    const current = ++revision.current
    setLoading(true); setError(''); setPack(null)
    getLearningPack(documentId).then(result => { if (current === revision.current) setPack(result?.id ? result : null) })
      .catch(() => { if (current === revision.current) setError('学习内容未能加载，原文仍可正常阅读。') })
      .finally(() => { if (current === revision.current) setLoading(false) })
    return () => { revision.current++ }
  }, [documentId, attempt])
  useEffect(() => {
    if (!open) return
    let cancelled = false
    setBudget([]); setBudgetError('')
    getLearningPreview(documentId, questionCount, cardCount).then(result => { if (!cancelled) setBudget(result.sections || []) })
      .catch(() => { if (!cancelled) setBudgetError('章节预算未能加载，请检查资料解析状态。') })
    return () => { cancelled = true }
  }, [open, documentId, questionCount, cardCount])
  const generate = async () => {
    const current = revision.current
    setBusy(true); setGenerationError(''); setNotice('')
    try {
      const result = await generateLearningPack({ document_id: documentId, question_count: questionCount, card_count: cardCount, model: model.trim(), question_types: types, concurrency: 2 })
      if (current !== revision.current) return
      if (!result?.id) throw new Error('没有收到有效的学习内容')
      setPack(result)
      const failed = result.coverage?.failed_sections || 0
      if (failed && failed === result.coverage?.total_sections) setGenerationError('所有章节生成失败，请检查模型配置后重试。')
      else { setOpen(false); setNotice(failed ? `${failed} 个章节存在缺漏，其他章节已保留。` : result.from_cache ? '已复用这份资料的学习内容' : '学习内容已整理，可开始练习或查看知识卡') }
    } catch (error: any) { if (current === revision.current) setGenerationError(typeof error?.response?.data?.detail === 'string' ? error.response.data.detail : '生成未完成，请检查模型设置后重试。原文仍可阅读。') }
    finally { if (current === revision.current) setBusy(false) }
  }
  const empty = <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="这份资料还没有生成学习内容"><p className="material-muted">可以直接阅读，也可以点击上方“整理学习内容”。</p></Empty>
  const sections = pack?.sections || []
  const cards = sections.flatMap(section => section.flashcards || [])
  return <aside className="material-tools" aria-label="当前资料学习工具">
    <div className="material-tools-heading"><div><h3>伴随学习</h3><p>围绕当前这份资料</p></div><Button aria-label="整理学习内容" icon={<BulbOutlined />} size="small" disabled={!ready || loading} loading={busy} onClick={() => setOpen(true)}>整理学习内容</Button></div>
    {error && <Alert type="error" message={error} action={<Button size="small" onClick={() => setAttempt(value => value + 1)}>重试</Button>} />}
    {notice && <Alert type="info" message={notice} closable onClose={() => setNotice('')} />}
    {pack?.stale && <Alert type="warning" message="资料已更新，请重新整理后练习。" />}
    {!!pack?.quality?.blocked && <Alert type="warning" message={`${pack.quality.blocked} 道题需修复，已从练习中隔离。`} description={<Link to="/learning">打开内容质检与修复</Link>} />}
    <Tabs defaultActiveKey="notebook" size="small" items={[
      { key: 'notebook', label: '笔记', children: <MaterialNotebook key={documentId} documentId={documentId} /> },
      { key: 'assistant', label: '问答', children: <MaterialAssistant chunk={chunk} /> },
      { key: 'practice', label: '练习', children: loading ? <Spin /> : pack ? <><p className="material-muted">根据这份资料生成的练习。原试卷保留在阅读区。</p><LearningPractice packId={pack.id} stale={!!pack.stale} onShowSource={onSource} /></> : empty },
      { key: 'cards', label: '知识卡', children: loading ? <Spin /> : cards.length ? <><p className="material-muted">先回忆，再展开答案。到期卡片可在“练习 → 智能复习”中评分。</p>{sections.map(section => section.flashcards?.length ? <section key={section.id}><div className="material-card-section"><h4>{section.title}</h4><Button size="small" type="link" onClick={() => onSource(section.chunk_ids)}>查看原文</Button></div>{section.flashcards.map(card => <KnowledgeCard key={card.id} card={card} />)}</section> : null)}</> : empty },
      { key: 'knowledge', label: '知识点', children: loading ? <Spin /> : sections.length ? <Collapse items={sections.map(section => ({ key: section.id, label: section.title, children: <><div className="material-markdown"><LearningContent text={section.notes || '本章讲解尚未生成'} /></div><Button size="small" onClick={() => onSource(section.chunk_ids)}>阅读对应原文</Button>{section.errors?.map((issue, index) => <Alert key={index} type="warning" message={issue} />)}</> }))} /> : empty },
      { key: 'map', label: '导图', children: <>{loading && <Spin />}<MaterialMindMap title={title} sections={sections} chunks={chunks} activeId={chunk?.id} onSource={onSource} />{pack?.summary_id && <Link to={`/mindmap/${pack.summary_id}`}>打开完整思维导图</Link>}</> },
    ]} />
    <RelatedMaterials documentId={documentId} subject={subject} />
    <Modal title="整理这份资料的学习内容" open={open} onCancel={() => { if (!busy) setOpen(false) }} maskClosable={!busy} closable={!busy} footer={<Button type="primary" loading={busy} disabled={!ready || !model.trim() || questionCount + cardCount === 0 || !types.length} onClick={generate}>生成学习包</Button>}>
      <p className="material-muted">使用你配置的 AI 模型，按章节生成讲解、练习与知识卡。阅读原文无需生成。</p>
      <div className="material-form"><Space wrap><label>习题数量<InputNumber aria-label="生成习题数量" min={0} max={100} value={questionCount} disabled={busy} onChange={value => setQuestionCount(value ?? 0)} /></label><label>知识卡数量<InputNumber aria-label="生成知识卡数量" min={0} max={100} value={cardCount} disabled={busy} onChange={value => setCardCount(value ?? 0)} /></label></Space>
        <label>模型<AutoComplete aria-label="学习生成模型" value={model} options={models} disabled={busy} onChange={setModel} /></label>
        {modelError && <Alert type="warning" message={modelError} />}
        <Link to="/settings">检查 API 与模型配置</Link>
        <label>题型<Select aria-label="生成题型" mode="multiple" value={types} disabled={busy} onChange={setTypes} options={[['single_choice', '单选'], ['multi_choice', '多选'], ['true_false', '判断'], ['short_answer', '简答'], ['matching', '匹配'], ['ordering', '排序'], ['classification', '分类']].map(([value, label]) => ({ value, label }))} /></label>
        {budgetError && <Alert type="warning" message={budgetError} />}
        {!!budget.length && <Collapse size="small" items={[{ key: 'budget', label: `${budget.length} 个章节 · 查看分配`, children: budget.map(section => <p key={section.id}>{section.title} <Tag>习题 {section.question_budget}</Tag><Tag>卡片 {section.card_budget}</Tag></p>) }]} />}
        {generationError && <Alert type="error" message={generationError} />}
        {pack && <p className="material-muted"><ReloadOutlined /> 相同来源与配置会复用有效缓存。</p>}
      </div>
    </Modal>
  </aside>
}
