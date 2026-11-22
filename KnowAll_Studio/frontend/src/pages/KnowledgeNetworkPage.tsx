import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Alert, Button, Checkbox, Empty, Input, Modal, Pagination, Select, Skeleton, Space, Tabs, Tag } from 'antd'
import { ArrowLeftOutlined, ArrowRightOutlined, BookOutlined, SearchOutlined } from '@ant-design/icons'
import { isCancel } from 'axios'
import LearningContent from '../components/LearningContent'
import { useAuthStore } from '../stores'
import { getNetworkConcept, listNetworkConcepts, listNetworkDocuments, networkError, publishNetworkConcept, saveNetworkLearning, searchNetworkSources, type ConceptDetail, type LearningState, type NetworkConcept, type NetworkScope, type KnowledgeDocument } from '../knowledgeNetwork'
import '../knowledgeNetwork.css'
import { rankNetworkResults } from '../knowledgeResultOrder'

function safeSourceUrl(value: string): string | null {
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null
  } catch { return null }
}

const emptyLearning: LearningState = { saved: false, mastery: 'unseen', note: '' }
export default function KnowledgeNetworkPage() {
  const [params, setParams] = useSearchParams()
  const scope: NetworkScope = params.get('scope') === 'public' ? 'public' : 'personal'
  const q = params.get('q') || '', subject = params.get('subject') || '', id = params.get('concept') || ''
  const documentId = scope === 'personal' ? params.get('document') || '' : ''
  const offset = Math.max(0, Number(params.get('offset')) || 0)
  const token = useAuthStore(s => s.token), accountId = useAuthStore(s => s.user?.id)
  const [draft, setDraft] = useState(q), [subjectDraft, setSubjectDraft] = useState(subject)
  const [rows, setRows] = useState<NetworkConcept[]>([]), [total, setTotal] = useState(0)
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]), [documentsLoading, setDocumentsLoading] = useState(false), [documentsError, setDocumentsError] = useState('')
  const hasRequest = !!q.trim() || !!documentId
  const resultsCurrent = hasRequest && draft.trim() === q.trim() && subjectDraft.trim() === subject.trim()
  const [detail, setDetail] = useState<ConceptDetail | null>(null), [learning, setLearning] = useState<LearningState>(emptyLearning)
  const [loading, setLoading] = useState(true), [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState(''), [detailError, setDetailError] = useState(''), [writeError, setWriteError] = useState('')
  const [attempt, setAttempt] = useState(0), [detailAttempt, setDetailAttempt] = useState(0), [busy, setBusy] = useState(false)
  const [publishOpen, setPublishOpen] = useState(false), [reviewed, setReviewed] = useState(false), [notice, setNotice] = useState('')
  const [sourceAttempt, setSourceAttempt] = useState(0), [sourceError, setSourceError] = useState('')
  const mutation = useRef<AbortController | null>(null)
  const reader = useRef<HTMLDivElement | null>(null)
  const selection = useRef(`${scope}:${id}`); selection.current = `${scope}:${id}`
  const accountCurrent = () => useAuthStore.getState().token === token && useAuthStore.getState().user?.id === accountId
  const updateParams = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(patch)) value ? next.set(key, value) : next.delete(key)
    setParams(next)
  }
  useEffect(() => { setDraft(q); setSubjectDraft(subject) }, [q, subject])
  useEffect(() => {
    const controller = new AbortController()
    setDocuments([]); setDocumentsLoading(true); setDocumentsError('')
    listNetworkDocuments(controller.signal).then(data => {
      if (!controller.signal.aborted && accountCurrent()) setDocuments(data.items)
    }).catch(e => { if (!controller.signal.aborted && accountCurrent() && !isCancel(e)) setDocumentsError(networkError(e, '上传资料列表暂时无法加载。')) })
      .finally(() => { if (!controller.signal.aborted && accountCurrent()) setDocumentsLoading(false) })
    return () => controller.abort()
  }, [token, accountId, attempt])
  useEffect(() => {
    const controller = new AbortController()
    const current = () => !controller.signal.aborted && accountCurrent()
    setRows([]); setTotal(0); setLoading(true); setError('')
    if (!resultsCurrent) { setLoading(false); return () => controller.abort() }
    listNetworkConcepts({ scope, q: q.trim(), subject: subject.trim(), document_id: documentId || undefined, limit: 50, offset }, controller.signal).then(data => {
      if (current()) { setRows(data.items); setTotal(data.total) }
    }).catch(e => { if (current() && !isCancel(e)) setError(networkError(e, '知识网络加载失败，请重试。')) })
      .finally(() => { if (current()) setLoading(false) })
    return () => controller.abort()
  }, [scope, q, subject, documentId, resultsCurrent, offset, token, accountId, attempt])
  useEffect(() => {
    const controller = new AbortController()
    const current = () => !controller.signal.aborted && accountCurrent()
    setDetail(null); setLearning(emptyLearning); setDetailError(''); setWriteError(''); setSourceError(''); setNotice(''); setPublishOpen(false); setReviewed(false)
    setDetailLoading(Boolean(id) && resultsCurrent)
    if (id && resultsCurrent) getNetworkConcept(id, controller.signal, scope).then(data => {
      if (current()) { setDetail(data); setLearning(data.learning || emptyLearning) }
    }).catch(e => { if (current() && !isCancel(e)) setDetailError(networkError(e, '概念暂时无法打开，请重试。')) })
      .finally(() => { if (current()) setDetailLoading(false) })
    return () => controller.abort()
  }, [id, scope, token, accountId, documentId, resultsCurrent, detailAttempt])
  useEffect(() => {
    setBusy(false)
    return () => { mutation.current?.abort(); mutation.current = null }
  }, [id, scope, token, accountId])
  useEffect(() => {
    if (detail?.id !== id || detail.source_search?.status !== 'searching') return
    const controller = new AbortController()
    const current = () => !controller.signal.aborted && accountCurrent() && selection.current === `${scope}:${id}`
    const budget = setTimeout(() => controller.abort(), 60000)
    let timer: ReturnType<typeof setTimeout>
    setSourceError('')
    const poll = async () => {
      try {
        const data = await getNetworkConcept(id, controller.signal, scope)
        if (!current()) return
        setSourceError('')
        // Source lookup must not overwrite the user's unsaved learning form.
        setDetail(previous => previous?.id === id ? { ...previous, source_search: data.source_search, source_count: data.source_count } : previous)
        if (data.source_search?.status === 'found') return
      } catch (error) {
        if (!current()) return
        if (!isCancel(error)) setSourceError(networkError(error, '来源检索暂时无法查询，可以重新检索。'))
      }
      if (current()) timer = setTimeout(() => void poll(), 3000)
    }
    timer = setTimeout(() => void poll(), 3000)
    return () => { controller.abort(); clearTimeout(budget); clearTimeout(timer) }
  }, [id, scope, token, accountId, detail?.id, detail?.source_search?.status, sourceAttempt])
  const write = async (kind: 'learning' | 'publish' | 'sources', state = learning) => {
    if (!detail || mutation.current || !accountCurrent() || selection.current !== `${scope}:${detail.id}`) return
    if (kind === 'publish' && (!reviewed || detail.can_contribute !== true)) return
    const controller = new AbortController(), conceptId = detail.id
    mutation.current = controller; setBusy(true); setWriteError(''); setNotice('')
    const current = () => mutation.current === controller && !controller.signal.aborted && accountCurrent() && selection.current === `${scope}:${conceptId}`
    let contributed = false
    try {
      if (kind === 'sources') {
        const source_search = await searchNetworkSources(conceptId, controller.signal)
        if (current()) { setDetail(d => d ? { ...d, source_search } : d); setSourceAttempt(n => n + 1) }
      } else if (kind === 'publish') {
        await publishNetworkConcept(conceptId, controller.signal)
        if (!current()) return
        contributed = true; setPublishOpen(false); setReviewed(false); setNotice('已贡献到中央知识库'); setAttempt(n => n + 1)
        const data = await getNetworkConcept(conceptId, controller.signal, scope)
        if (current()) setDetail(data)
      } else {
        const data = await saveNetworkLearning(conceptId, state, controller.signal)
        if (current()) { setLearning(data); setDetail(d => d ? { ...d, learning: data, in_learning: data.saved } : d); setNotice('学习记录已保存'); setAttempt(n => n + 1) }
      }
    } catch (e) { if (current() && !isCancel(e)) setWriteError(networkError(e, kind === 'sources' ? '来源检索暂未完成，请重试。' : contributed ? '已贡献，概念详情暂未刷新，请重新打开。' : '保存未能完成，请重试。')) }
    finally { if (current()) { mutation.current = null; setBusy(false) } }
  }
  const relations = (type: 'prerequisite' | 'related') => detail?.relations.filter(r => r.relation_type === type) || []
  const authoritySources = detail?.source_search?.status === 'found' ? detail.source_search.sources.map(source => ({ ...source, safeUrl: safeSourceUrl(source.url) })).filter(source => source.safeUrl) : []
  return <section className={`knowledge-network ${id && resultsCurrent ? 'has-selection' : 'is-browsing'}`}>
    <header className="knowledge-network-header"><div><span className="knowledge-network-eyebrow">LEARNING LIBRARY</span><h2>知识库</h2></div>
    <Space wrap className="knowledge-network-scopes">
      <Button type={scope === 'personal' ? 'primary' : 'default'} onClick={() => updateParams({ scope: 'personal', concept: null, offset: null })}>我的知识</Button>
      <Button type={scope === 'public' ? 'primary' : 'default'} onClick={() => updateParams({ scope: 'public', document: null, concept: null, offset: null })}>公共知识检索</Button>
    </Space>
    </header>
    <p className="knowledge-network-hint">{scope === 'public' ? '检索公共概念，收藏到自己的学习库。' : '检索自己的资料与收藏，找到正在学习的知识。'}</p>
    <form className="knowledge-network-search" onSubmit={e => { e.preventDefault(); updateParams({ q: draft.trim(), subject: subjectDraft.trim(), concept: null, offset: null }) }}>
      <Input prefix={<SearchOutlined />} aria-label="搜索知识库" placeholder="搜索概念、别名或解释" value={draft} maxLength={200} onChange={e => { setDraft(e.target.value); if (!e.target.value.trim()) updateParams({ q: null, concept: null, offset: null }) }} />
      <Input aria-label="筛选学科" placeholder="学科（可选）" value={subjectDraft} onChange={e => setSubjectDraft(e.target.value)} />
      <Button type="primary" aria-label="检索" htmlType="submit" disabled={!draft.trim() && !documentId}>检索</Button>
    </form>
    {scope === 'personal' && <div className="knowledge-network-documents"><Select aria-label="选择上传资料" placeholder="限定资料（可选）" allowClear showSearch optionFilterProp="label" value={documentId || undefined} loading={documentsLoading}
      options={documents.map(doc => ({ value: doc.id, label: doc.filename }))}
      onChange={value => updateParams({ document: value || null, concept: null, offset: null })} />
      {documentsError && <Alert type="warning" message={documentsError} action={<Button onClick={() => setAttempt(n => n + 1)}>重试</Button>} />}
      {!documentsLoading && !documentsError && !documents.length && <span>暂无上传资料，可先检索已收藏的知识。</span>}</div>}
    <div className="knowledge-network-columns">
      <aside className="knowledge-network-results" aria-label="知识网络检索结果">
        <div className="knowledge-network-result-heading"><strong>检索结果</strong><span role="status">{resultsCurrent && !loading ? `${total} 个匹配` : '按需检索'}</span></div>
        <div className="knowledge-network-results-scroll" tabIndex={0} aria-label="滚动检索结果">
        {error && <Alert type="error" showIcon message={error} action={<Button onClick={() => setAttempt(n => n + 1)}>重试加载</Button>} />}
        {!resultsCurrent ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择自己的资料或输入关键词，查看匹配的知识点集锦" /> : loading ? <Skeleton active /> : !error && <>{rows.length ? <ul className="knowledge-network-list">{rankNetworkResults(rows, q).map(row => <li key={row.id} className={row.id === id ? 'is-active' : ''}>
          <button className="knowledge-network-result" aria-label={`学习 ${row.name}`} aria-pressed={row.id === id} onClick={() => updateParams({ concept: row.id })}>
            <span className="knowledge-network-result-title"><strong>{row.name}</strong><ArrowRightOutlined /></span>
            <span className="knowledge-network-result-meta">{row.subject || '未分类'}{row.review_status === 'needs_review' && <Tag color="orange">待校对</Tag>}{row.in_learning && <Tag>已收藏</Tag>}</span>
            <span className="knowledge-network-result-excerpt">{row.definition}</span>
          </button>
        </li>)}</ul> : <Empty description={scope === 'public' ? '暂未找到已贡献概念' : '还没有符合条件的学习概念'} />}
        </>}
        </div>
        {resultsCurrent && total > 50 && <Pagination size="small" simple current={offset / 50 + 1} pageSize={50} total={total} showSizeChanger={false} onChange={page => updateParams({ offset: String((page - 1) * 50), concept: null })} />}
      </aside>
      <div className="knowledge-network-reader" ref={reader}><Button aria-label="返回检索结果" className="knowledge-network-back" icon={<ArrowLeftOutlined />} onClick={() => updateParams({ concept: null })}>返回检索结果</Button>
        {detailLoading ? <Skeleton active paragraph={{ rows: 8 }} /> : detailError ? <Alert type="error" message={detailError} action={<Button onClick={() => setDetailAttempt(n => n + 1)}>重试概念</Button>} /> : detail ? <article className="knowledge-network-detail">
        <div className="knowledge-network-detail-heading"><div><span className="knowledge-network-eyebrow">概念学习</span><h2>{detail.name}</h2><Space wrap><Tag>{detail.subject}</Tag><Tag>{detail.topic}</Tag><Tag color={detail.review_status === 'needs_review' ? 'orange' : 'green'}>{detail.review_status === 'needs_review' ? '待校对' : detail.review_status === 'compiled' ? '内置整理' : '已校对贡献'}</Tag></Space></div><BookOutlined /></div>
        <Tabs key={detail.id} defaultActiveKey="explanation" onChange={() => reader.current?.querySelector('.ant-tabs-content-holder')?.scrollTo({ top: 0 })} className="knowledge-network-detail-tabs" items={[{ key: 'explanation', label: '知识讲解', children: <div className="knowledge-network-explanation">
        {detail.review_status === 'needs_review' && <Alert type="warning" message="AI 提炼的候选解释，请核对概念与关系，并参考权威来源。" />}
        <LearningContent text={detail.content.definition || detail.definition} />
        {([['别名', detail.content.aliases], ['公式', detail.content.formulas], ['适用条件', detail.content.conditions], ['重点', detail.content.key_points], ['易错点', detail.content.pitfalls], ['待核对的补充说明', detail.content.supplements]] as [string, string[]][]).map(([title, values]) => values?.length ? <section key={title}><h3>{title}</h3>{values.map((text, index) => <LearningContent key={index} text={text} />)}</section> : null)}
        </div> }, { key: 'connections', label: '关联与来源', children: <div>
        {(['prerequisite', 'related'] as const).map(type => <section key={type}><h3>{type === 'prerequisite' ? '前置知识' : '关联知识'}</h3><Space wrap>{relations(type).map(r => <Button key={`${r.source_id}:${r.target_id}:${type}`} aria-label={`${type === 'prerequisite' ? '前置知识' : '关联知识'} ${r.target_name}`} onClick={() => updateParams({ concept: r.target_id })}>{r.target_name}</Button>)}{!relations(type).length && <span>暂未建立关系</span>}</Space></section>)}
        <section className="knowledge-network-authority"><h3>权威来源</h3>
          {authoritySources.length ? authoritySources.map((source, index) => <div className="knowledge-network-source" key={`${source.safeUrl}:${index}`}>
            <a href={source.safeUrl!} target="_blank" rel="noopener noreferrer">{source.title}</a>
            <p>{source.authority}</p>{source.excerpt && <blockquote>{source.excerpt}</blockquote>}
          </div>) : <p role="status">来源搜索中</p>}
          <Button disabled={busy} onClick={() => void write('sources')}>高级检索权威来源</Button>
          {sourceError && <Alert type="warning" message={sourceError} />}
          {writeError && <Alert type="error" message={writeError} />}
        </section>
        </div> }, { key: 'learning', label: '学习记录', children: <section className="knowledge-network-learning"><h3>我的学习记录</h3><Button disabled={busy} onClick={() => void write('learning', { ...learning, saved: !learning.saved })}>{learning.saved ? '取消收藏' : '收藏到我的学习库'}</Button>
          <label>掌握状态<Select aria-label="掌握状态" disabled={busy} value={learning.mastery} onChange={mastery => setLearning(s => ({ ...s, mastery }))} options={[{ value: 'unseen', label: '未学习' }, { value: 'learning', label: '学习中' }, { value: 'mastered', label: '已掌握' }]} /></label>
          <label>私有学习笔记<Input.TextArea aria-label="私有学习笔记" disabled={busy} rows={4} maxLength={10000} value={learning.note} onChange={e => setLearning(s => ({ ...s, note: e.target.value }))} /></label>
          <Button aria-label="保存学习记录" type="primary" disabled={busy} loading={busy} onClick={() => void write('learning')}>保存学习记录</Button>
          {detail.can_contribute === true && <Button disabled={busy} onClick={() => { setReviewed(false); setPublishOpen(true) }}>校对并贡献</Button>}
          {writeError && <Alert type="error" message={writeError} />}{notice && <p role="status">{notice}</p>}
        </section> }]} />
      </article> : <div className="knowledge-network-reader-empty"><BookOutlined /><h3>从一个概念开始</h3><p>在左侧选择检索结果，即可阅读解释、公式和学习笔记。</p></div>}</div>
    </div>
    <details className="knowledge-network-boundary"><summary>关于学习记录</summary><p>这里的收藏、掌握状态和笔记独立保存。旧题库、卡片和总纲的已有学习记录尚未与知识网络关联。</p></details>
    <Modal title="校对并贡献" open={publishOpen} onCancel={() => { if (!busy) setPublishOpen(false) }} onOk={() => { if (reviewed) void write('publish') }} okText="确认贡献" okButtonProps={{ disabled: !reviewed, loading: busy }} cancelButtonProps={{ disabled: busy }} closable={!busy} maskClosable={!busy}>
      <p>已有中央定义会保留，学习笔记只对你可见。</p>
      <Checkbox checked={reviewed} disabled={busy} onChange={e => setReviewed(e.target.checked)}>我已核对概念摘要与关系，确认贡献到中央知识库</Checkbox>
      {writeError && <Alert type="error" message={writeError} />}
    </Modal>
  </section>
}
