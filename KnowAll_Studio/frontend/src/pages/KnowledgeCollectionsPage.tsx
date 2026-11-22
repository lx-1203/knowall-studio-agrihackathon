import { useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent, CompositionEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Alert, App, Button, Empty, Input, Modal, Select, Skeleton, Tag } from 'antd'
import { ArrowLeftOutlined, DownloadOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons'
import { isCancel } from 'axios'
import LearningContent from '../components/LearningContent'
import { loadMaterialCatalog } from '../materialCatalog'
import type { Material } from '../materials'
import {
  listKnowledgeCollections, getKnowledgeCollection, previewKnowledgeCollection, generateKnowledgeCollection,
  updateKnowledgeCollection, collectionMarkdown,
  type CollectionSummary, type KnowledgeCollection, type KnowledgeItem, type CollectionPreview,
} from '../knowledgeCollections'
import '../knowledgeCollections.css'

function reason(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
  return typeof detail === 'string' ? detail : fallback
}
function status(collection: CollectionSummary) {
  return collection.review_status === 'needs_review' ? '待校对'
    : collection.review_status === 'reviewed' ? '已校对' : '基础整理'
}

export default function KnowledgeCollectionsPage() {
  const [params, setParams] = useSearchParams()
  const collectionId = params.get('collection') || ''
  const query = params.get('q') || ''
  // Keep IME drafts local; routing during composition can replay unfinished pinyin.
  const [queryDraft, setQueryDraft] = useState(query)
  const composingQuery = useRef(false)
  const topic = params.get('topic') || ''
  const subject = params.get('subject') || ''
  const [list, setList] = useState<CollectionSummary[]>([])
  const [collection, setCollection] = useState<KnowledgeCollection | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState('')
  const [detailError, setDetailError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const { message } = App.useApp()
  const [open, setOpen] = useState(false)
  const [documents, setDocuments] = useState<Material[]>([])
  const [documentsLoading, setDocumentsLoading] = useState(false)
  const [ids, setIds] = useState<string[]>([])
  const [title, setTitle] = useState('我的知识点集锦')
  const [newSubject, setNewSubject] = useState('')
  const [model, setModel] = useState('deepseek-chat')
  const [preview, setPreview] = useState<CollectionPreview | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [busy, setBusy] = useState(false)
  const [generationError, setGenerationError] = useState('')
  const previewRequest = useRef<AbortController | null>(null)
  const live = useRef(true)
  const [editing, setEditing] = useState<KnowledgeItem | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    composingQuery.current = false
    setQueryDraft(query)
  }, [query, collectionId])

  useEffect(() => {
    live.current = true
    return () => { live.current = false; previewRequest.current?.abort() }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    listKnowledgeCollections(controller.signal).then(result => setList(result))
      .catch(error => { if (!isCancel(error)) setError('知识点集锦加载失败') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [attempt])

  useEffect(() => {
    const controller = new AbortController()
    setCollection(null); setDetailError('')
    if (!collectionId) { setDetailLoading(false); return }
    setDetailLoading(true)
    getKnowledgeCollection(collectionId, controller.signal).then(result => setCollection(result))
      .catch(error => { if (!isCancel(error)) setDetailError(reason(error, '集锦内容加载失败，请重试。')) })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false) })
    return () => controller.abort()
  }, [collectionId, attempt])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setDocumentsLoading(true)
    loadMaterialCatalog(() => cancelled).then(result => { if (!cancelled) setDocuments(result.filter(d => d.status === 'ready')) })
      .catch(() => { if (!cancelled) setGenerationError('资料列表加载失败，请关闭后重新打开。') })
      .finally(() => { if (!cancelled) setDocumentsLoading(false) })
    return () => { cancelled = true }
  }, [open])

  const change = (name: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(name, value); else next.delete(name)
    setParams(next, { replace: true })
  }
  const commitQuery = (value: string) => {
    setParams(current => {
      const next = new URLSearchParams(current)
      if (value) next.set('q', value); else next.delete('q')
      if (collectionId) next.delete('point')
      return next
    }, { replace: true })
  }
  const changeQuery = (event: ChangeEvent<HTMLInputElement>) => {
    const value = event.target.value
    setQueryDraft(value)
    if (!composingQuery.current && !(event.nativeEvent as InputEvent).isComposing) commitQuery(value)
  }
  const finishQueryComposition = (event: CompositionEvent<HTMLInputElement>) => {
    composingQuery.current = false
    const value = event.currentTarget.value
    setQueryDraft(value)
    commitQuery(value)
  }
  const queryInputProps = {
    value: queryDraft,
    onChange: changeQuery,
    onCompositionStart: () => { composingQuery.current = true },
    onCompositionEnd: finishQueryComposition,
  }
  const chooseCollection = (id: string) => setParams({ collection: id })
  const filtered = useMemo(() => (collection?.points || []).filter(point =>
    (!topic || point.topic === topic) && (!query.trim() ||
      [point.name, ...point.aliases, point.definition, ...point.key_points].join(' ').toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))), [collection, query, topic])
  const selected = collection?.points.find(point => point.id === params.get('point')) || filtered[0]
  const subjects = [...new Set(list.map(entry => entry.subject))]
  const topics = [...new Set(collection?.points.map(point => point.topic) || [])]
  const navigatePoint = (point: KnowledgeItem) => {
    const next = new URLSearchParams(params)
    next.set('point', point.id); next.delete('q'); next.delete('topic')
    setParams(next, { replace: true })
  }
  const reference = (name: string, label: string) => {
    const targets = collection?.points.filter(point => point.name === name || point.aliases.includes(name)) || []
    return targets.length === 1
      ? <Button key={name} size="small" aria-label={`${label} ${name}`} onClick={() => navigatePoint(targets[0])}>{name}</Button>
      : <Tag key={name}>{name}{targets.length > 1 ? '（多个主题）' : '（待补充）'}</Tag>
  }
  const exportContent = () => {
    if (!collection) return
    const url = URL.createObjectURL(new Blob([collectionMarkdown(collection)], { type: 'text/markdown;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url; anchor.download = `${collection.title.replace(/[\\/:*?"<>|]/g, '_')}.md`
    anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  const selectSources = (next: string[]) => {
    previewRequest.current?.abort(); setPreviewBusy(false); setPreview(null); setIds(next); setGenerationError('')
  }
  const previewSources = async () => {
    previewRequest.current?.abort()
    const controller = new AbortController()
    previewRequest.current = controller
    setPreviewBusy(true); setPreview(null); setGenerationError('')
    try {
      const result = await previewKnowledgeCollection(ids, controller.signal)
      if (!controller.signal.aborted) setPreview(result)
    } catch (error) {
      if (!isCancel(error)) setGenerationError(reason(error, '处理量预览失败，请重试。'))
    } finally { if (!controller.signal.aborted && live.current) setPreviewBusy(false) }
  }
  const generate = async () => {
    if (!preview || busy) return
    setBusy(true); setGenerationError('')
    try {
      const result = await generateKnowledgeCollection({ title: title.trim(), subject: newSubject.trim(), model: model.trim(), document_ids: ids })
      if (!live.current) return
      setOpen(false); setPreview(null); setAttempt(value => value + 1); chooseCollection(result.id)
      message.success('知识点已提炼并保存，请校对内容')
    } catch (error) {
      if (live.current) setGenerationError(reason(error, '请求未确认完成。请刷新集锦列表检查结果，再决定是否重试。'))
    } finally { if (live.current) setBusy(false) }
  }
  const save = async (next: KnowledgeCollection, reviewed: boolean) => {
    setSaving(true)
    try {
      const result = await updateKnowledgeCollection({ ...next, points: next.points.map(point => ({ ...point,
        formulas: point.formulas.filter(value => value.trim()), conditions: point.conditions.filter(value => value.trim()),
        key_points: point.key_points.filter(value => value.trim()), pitfalls: point.pitfalls.filter(value => value.trim()),
        supplements: point.supplements.filter(value => value.trim()),
      })) }, reviewed)
      if (!live.current) return
      setCollection(result); setEditing(null)
      setList(items => items.map(item => item.id === result.id ? result : item))
      message.success(reviewed ? '集锦已标记为已校对' : '修改已保存')
    } catch (error) { if (live.current) message.error(reason(error, '保存失败，请重试。')) }
    finally { if (live.current) setSaving(false) }
  }

  return <section className="knowledge-compilation">
    <header className="knowledge-compilation-header">
      <div><p className="material-eyebrow">把知识连起来</p><h2>知识点集锦</h2><p>按概念学习，查看公式、适用条件与易错点。</p></div>
      <Button type="primary" aria-label="从资料提炼" icon={<PlusOutlined />} onClick={() => { setGenerationError(''); setPreviewBusy(false); setOpen(true) }}>从资料提炼</Button>
    </header>
    {collectionId && <Button className="knowledge-back" icon={<ArrowLeftOutlined />} onClick={() => setParams({})}>全部集锦</Button>}
    {!collectionId && <>
      <div className="knowledge-compilation-filters">
        <Input aria-label="搜索集锦" prefix={<SearchOutlined />} placeholder="搜索集锦或学科" {...queryInputProps} allowClear />
        <Select aria-label="筛选学科" value={subject || undefined} placeholder="全部学科" allowClear onChange={value => change('subject', value || '')} options={subjects.map(name => ({ label: name, value: name }))} />
      </div>
      {loading ? <Skeleton active /> : error ? <Alert type="error" message={error} action={<Button onClick={() => setAttempt(value => value + 1)}>重试加载</Button>} />
        : <div className="knowledge-compilation-grid">{list.filter(entry => (!subject || entry.subject === subject)
          && `${entry.title} ${entry.subject}`.includes(query.trim())).map(entry => <button className="knowledge-collection-tile" key={entry.id} aria-label={`打开 ${entry.title}`} onClick={() => chooseCollection(entry.id)}>
            <span className="knowledge-collection-subject">{entry.subject}</span><h3>{entry.title}</h3>
            <p>{entry.point_count} 个知识点</p><div><Tag>{entry.kind === 'builtin' ? '系统集锦' : '我的集锦'}</Tag><Tag color={entry.review_status === 'needs_review' ? 'gold' : undefined}>{status(entry)}</Tag></div>
          </button>)}</div>}
      {!loading && !error && !list.some(entry => (!subject || entry.subject === subject) && `${entry.title} ${entry.subject}`.includes(query.trim())) && <Empty description="没有匹配的知识点集锦" />}
    </>}
    {collectionId && (detailLoading ? <Skeleton active /> : detailError ? <Alert type="error" message={detailError} action={<Button onClick={() => setAttempt(value => value + 1)}>重试加载</Button>} /> : collection && <>
      <div className="knowledge-compilation-title"><div><h3>{collection.title}</h3><Tag>{collection.subject}</Tag><Tag>{status(collection)}</Tag><span>{collection.point_count} 个知识点</span></div>
        <div><Button aria-label="导出知识点" icon={<DownloadOutlined />} onClick={exportContent}>导出知识点</Button>
          {collection.kind === 'personal' && collection.review_status !== 'reviewed' && <Button aria-label="标记已校对" loading={saving} onClick={() => Modal.confirm({ title: '确认集锦中的知识点已校对？', content: '请检查定义、公式和适用条件，核对补充说明中是否存在分歧。', okText: '确认已校对', cancelText: '继续检查', onOk: () => save(collection, true) })}>标记已校对</Button>}</div>
      </div>
      {collection.review_status === 'needs_review' && <Alert type="info" message="提炼结果待校对，请重点核对公式、适用条件和补充说明。" />}
      <div className="knowledge-compilation-filters">
        <Input aria-label="搜索知识点" prefix={<SearchOutlined />} placeholder="搜索概念、别名或重点" {...queryInputProps} allowClear />
        <Select aria-label="筛选知识主题" value={topic || undefined} placeholder="全部知识主题" allowClear onChange={value => { const next = new URLSearchParams(params); if (value) next.set('topic', value); else next.delete('topic'); next.delete('point'); setParams(next, { replace: true }) }} options={topics.map(name => ({ label: name, value: name }))} />
      </div>
      {!filtered.length ? <Empty description="没有匹配的知识点" /> : <div className="knowledge-compilation-workspace">
        <nav className="knowledge-point-list" aria-label="知识点目录">{filtered.map(point => <button key={point.id} aria-label={`学习 ${point.name}`} aria-pressed={selected?.id === point.id}
          onClick={() => change('point', point.id)}><small>{point.topic}</small><strong>{point.name}</strong></button>)}</nav>
        {selected && <article className="knowledge-compilation-detail" aria-label="知识点内容">
          <div className="knowledge-point-heading"><div><small>{selected.topic}</small><h3>{selected.name}</h3></div>
            {collection.kind === 'personal' && <Button onClick={() => setEditing({ ...selected })}>校对知识点</Button>}</div>
          {!!selected.aliases.length && <p className="knowledge-aliases">也称：{selected.aliases.join('、')}</p>}
          <LearningContent text={selected.definition} />
          {([['公式', selected.formulas], ['适用条件', selected.conditions], ['重点', selected.key_points], ['易错点', selected.pitfalls], ['补充说明（需核对）', selected.supplements]] as [string, string[]][]).map(([label, values]) => values.length > 0 && <section className="knowledge-point-section" key={label}><h4>{label}</h4><LearningContent text={values.map(value => `- ${value}`).join('\n')} /></section>)}
          {!!selected.prerequisites.length && <section className="knowledge-point-section"><h4>前置知识</h4><div className="knowledge-relations">{selected.prerequisites.map(name => reference(name, '前置知识'))}</div></section>}
          {!!selected.related.length && <section className="knowledge-point-section"><h4>关联知识</h4><div className="knowledge-relations">{selected.related.map(name => reference(name, '关联知识'))}</div></section>}
        </article>}
      </div>}
    </>)}
    <Modal title="从资料提炼知识点" open={open} onCancel={() => { if (!busy) { previewRequest.current?.abort(); setPreviewBusy(false); setPreview(null); setOpen(false) } }} footer={null} maskClosable={!busy} closable={!busy}>
      <div className="knowledge-generation-form">
        <label>集锦名称<Input aria-label="集锦名称" value={title} maxLength={120} disabled={busy} onChange={event => setTitle(event.target.value)} /></label>
        <label>学科<Input aria-label="学科" value={newSubject} placeholder="例如：高等数学" maxLength={80} disabled={busy} onChange={event => setNewSubject(event.target.value)} /></label>
        <label>参考资料<Select aria-label="选择资料" mode="multiple" value={ids} loading={documentsLoading} disabled={busy} onChange={selectSources} options={documents.map(doc => ({ label: doc.filename, value: doc.id }))} placeholder="选择已解析的资料，最多 10 份" /></label>
        {!documentsLoading && !documents.length && <p>请先<Link to="/upload">导入资料</Link>并完成解析，再回来提炼知识点。</p>}
        <label>模型<Input aria-label="提炼模型" value={model} maxLength={128} disabled={busy} onChange={event => setModel(event.target.value)} /></label>
        <Button aria-label="预览处理量" onClick={previewSources} loading={previewBusy} disabled={!ids.length || ids.length > 10 || busy}>预览处理量</Button>
        {preview && <div className="knowledge-generation-preview"><strong>预计 {preview.batch_count} 批提炼</strong><p>{preview.characters.toLocaleString()} 字符 · {preview.chunk_count} 个资料片段</p><p>每批调用一次模型；失败重试可能增加调用。费用以你的模型服务实际计费为准。</p></div>}
        <p>资料用于提炼概念，结果按知识主题组织。相同名称与主题的知识点合并，差异解释保留供校对。</p>
        {generationError && <Alert type="error" message={generationError} />}
        <Button aria-label="开始提炼" type="primary" block loading={busy} disabled={!preview || !title.trim() || !newSubject.trim() || !model.trim()} onClick={generate}>开始提炼</Button>
        {busy && <p role="status">正在分批提炼，完成后会保存到我的集锦。请保持页面打开。</p>}
      </div>
    </Modal>
    <Modal title={`校对 ${editing?.name || ''}`} open={!!editing} onCancel={() => { if (!saving) setEditing(null) }} footer={<Button aria-label="保存修改" type="primary" loading={saving} onClick={() => { if (editing && collection) void save({ ...collection, points: collection.points.map(point => point.id === editing.id ? editing : point) }, false) }}>保存修改</Button>}>
      {editing && <div className="knowledge-generation-form">
        <label>核心解释<Input.TextArea aria-label="校对核心解释" rows={3} maxLength={3000} value={editing.definition} disabled={saving} onChange={event => setEditing({ ...editing, definition: event.target.value })} /></label>
        {(['formulas', 'conditions', 'key_points', 'pitfalls', 'supplements'] as const).map((field, index) => <label key={field}>{['公式', '适用条件', '重点', '易错点', '补充说明'][index]}（每行一条）<Input.TextArea rows={2} disabled={saving} value={editing[field].join('\n')} onChange={event => setEditing({ ...editing, [field]: event.target.value.split('\n') })} /></label>)}
      </div>}
    </Modal>
  </section>
}
