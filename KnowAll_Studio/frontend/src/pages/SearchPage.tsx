import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Alert, App, Button, Input, Skeleton, Switch, Tag } from 'antd'
import { FileTextOutlined, LoadingOutlined, SearchOutlined } from '@ant-design/icons'
import { isCancel } from 'axios'
import { searchDocuments } from '../api'
import { materialPath } from '../materials'
import './SearchPage.css'

type Mode = 'keyword' | 'ai'
type Hit = { id: string; text?: string; title?: string; href?: string; kind?: string; group_id?: string; shared?: boolean; source_id?: number; metadata?: {
  doc_id?: string; filename?: string; page_range?: string; chunk_index?: number
} }

export default function SearchPage() {
  const { message } = App.useApp()
  const [params, setParams] = useSearchParams()
  const urlQuery = params.get('q')?.trim() || ''
  const mode: Mode = ['ai', 'semantic'].includes(params.get('mode') || '') ? 'ai' : 'keyword'
  const [query, setQuery] = useState(urlQuery)
  const [hits, setHits] = useState<Hit[]>([])
  const [warnings, setWarnings] = useState<string[]>([])
  const [aiUsed, setAiUsed] = useState(false)
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)
  const [submittedQuery, setSubmittedQuery] = useState('')
  const [error, setError] = useState('')
  const [cancelled, setCancelled] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const requestVersion = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const inputRef = useRef<import('antd').InputRef>(null)

  const reset = useCallback(() => {
    requestVersion.current++
    controller.current?.abort()
    controller.current = null
    setLoading(false); setSearched(false); setError(''); setCancelled(false)
    setHits([]); setWarnings([]); setAiUsed(false); setExpanded(new Set())
  }, [])

  const runSearch = useCallback(async (value: string, searchMode: Mode) => {
    controller.current?.abort()
    const active = new AbortController()
    controller.current = active
    const version = ++requestVersion.current
    setLoading(true); setSearched(true); setSubmittedQuery(value)
    setHits([]); setWarnings([]); setAiUsed(false); setError(''); setCancelled(false); setExpanded(new Set())
    try {
      const config = { signal: active.signal, timeout: searchMode === 'keyword' ? 15000 : 90000 }
      const data = await searchDocuments(value, 50, config, searchMode)
      if (version !== requestVersion.current) return
      const results = data.results
      setHits(Array.isArray(results) ? results : [])
      setWarnings(Array.isArray(data.warnings) ? data.warnings.filter((item: unknown): item is string => typeof item === 'string') : [])
      setAiUsed(data.ai_used === true)
    } catch (e: any) {
      if (version !== requestVersion.current || isCancel(e)) return
      const detail = e.response?.data?.detail
      setError(e.code === 'ECONNABORTED' || e.code === 'ETIMEDOUT'
        ? '搜索等待超时，请重试或缩短关键词。'
        : typeof detail === 'string' ? detail : '搜索失败，请重试')
    } finally {
      if (version === requestVersion.current) { setLoading(false); controller.current = null }
    }
  }, [])

  useEffect(() => {
    if (urlQuery) { setQuery(urlQuery); void runSearch(urlQuery, mode) }
    else reset()
    return () => { requestVersion.current++; controller.current?.abort() }
  }, [urlQuery, mode, runSearch, reset])

  const submit = () => {
    const value = query.trim()
    if (!value) { message.warning('请输入搜索关键词'); inputRef.current?.focus(); return }
    if (value === urlQuery) void runSearch(value, mode)
    else setParams({ q: value, mode }, { replace: true })
  }
  const cancel = () => {
    requestVersion.current++
    controller.current?.abort(); controller.current = null
    setLoading(false); setCancelled(true)
  }
  const clear = () => {
    reset(); setQuery(''); setParams(mode === 'ai' ? { mode: 'ai' } : {}, { replace: true }); inputRef.current?.focus()
  }
  const groups = new Map<string, { title: string; href?: string; kind?: string; hits: Hit[] }>()
  for (const hit of hits) {
    const key = hit.group_id || hit.metadata?.doc_id || hit.id
    const href = safeResultHref(hit.href || (!hit.shared && hit.metadata?.doc_id ? materialPath(hit.metadata.doc_id) : undefined))
    if (!groups.has(key)) groups.set(key, {
      title: hit.title || hit.metadata?.filename || '资料片段', href, kind: hit.shared ? 'shared' : hit.kind, hits: [],
    })
    groups.get(key)!.hits.push(hit)
  }

  return (
    <section className="search-page" aria-labelledby="search-title">
      <header className="search-heading">
        <div><h2 id="search-title">统一搜索</h2><p>查找系统内内容，或让 AI 帮你检索系统与互联网。</p></div>
        <Link to="/materials">浏览我的资料</Link>
      </header>
      <div className="search-controls">
        <div className="search-input-row">
          <Input ref={inputRef} size="large" aria-label="搜索关键词或问题" maxLength={4000}
            placeholder={mode === 'ai' ? '输入问题，搜索系统内内容与互联网…' : '输入文件名、知识点或正文关键词…'}
            value={query} onChange={e => setQuery(e.target.value)} prefix={<SearchOutlined />}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing && e.keyCode !== 229) { e.preventDefault(); submit() }
              if (e.key === 'Escape' && loading) cancel()
            }} />
          <Button type="primary" size="large" aria-label="搜索" icon={loading ? <LoadingOutlined /> : <SearchOutlined />} onClick={submit}>搜索</Button>
          {(query || searched) && <Button size="large" onClick={clear}>清空搜索</Button>}
        </div>
        <div className="search-mode-row">
          <span className="search-mode-label">关键词 <Switch aria-label="AI 搜索" checked={mode === 'ai'}
            onChange={enabled => {
              reset()
              const value = query.trim()
              setParams({ ...(value ? { q: value } : {}), ...(enabled ? { mode: 'ai' } : {}) }, { replace: true })
            }} /> AI 搜索</span>
          <span className="search-hint">{mode === 'ai'
            ? 'AI 理解问题，合并关键词、语义与联网检索；覆盖可访问的资料、共享库、知识点、题库、知识卡和术语。会调用已配置的 AI 模型。'
            : '搜索我的资料与公共资料，无需先建索引；多个关键词用空格分隔。'}</span>
        </div>
      </div>

      {!searched && <div className="search-start">
        <FileTextOutlined /><h3>不用记住资料放在哪</h3>
        <p>输入文件名，或你记得的一段内容。找到结果后可以直接打开原文。</p>
        <p className="search-hint">例如：线粒体、TCP 三次握手、期末复习</p>
      </div>}

      {loading && <div className="search-wait" role="status">
        <div><span><LoadingOutlined /> 正在搜索「{submittedQuery}」…</span><Button onClick={cancel}>取消搜索</Button></div>
        <p>可以修改关键词后再次搜索。{mode === 'ai' ? '正在合并系统内容与互联网来源，AI 理解和联网可能需要一些时间。' : ''}</p>
        <Skeleton active title={false} paragraph={{ rows: 3 }} />
      </div>}

      {error && <Alert type="error" showIcon message={error} description="可以重试，也可以修改关键词重新搜索。"
        action={<Button onClick={() => void runSearch(submittedQuery, mode)}>重试搜索</Button>} />}
      {cancelled && <Alert type="info" showIcon message="已取消搜索" description="修改关键词或再次搜索即可继续。" />}
      {!loading && !cancelled && warnings.length > 0 && <Alert type="warning" showIcon message="部分检索暂不可用" description={warnings.join(' ')} />}

      {!loading && !error && !cancelled && hits.length > 0 && <div className="search-results" aria-live="polite">
        <div className="search-results-summary">「{submittedQuery}」 · {mode === 'ai' ? `${aiUsed ? 'AI 已理解查询 · ' : ''}找到 ${hits.length} 条匹配内容` : `本次找到 ${groups.size} 份资料，${hits.length} 个片段`}</div>
        {[...groups].map(([key, group]) => <article className="search-document" key={key}>
          <header><div className="search-document-title"><FileTextOutlined /><h3 title={group.title}>{highlightText(group.title, submittedQuery)}</h3></div>
            {group.href && <Button size="small" href={group.href} target={group.kind === 'web' ? '_blank' : undefined} rel={group.kind === 'web' ? 'noopener noreferrer' : undefined}>{group.kind === 'web' ? '访问来源' : group.kind && group.kind !== 'document' ? '打开内容' : '查看原文'}</Button>}</header>
          {group.kind && <Tag>{sourceLabel(group.kind)}</Tag>}
          {group.hits.map(hit => {
            const text = hit.text || ''
            const excerpt = expanded.has(hit.id) ? text : matchingExcerpt(text, submittedQuery)
            return <div className="search-fragment" key={hit.id}>
              <div className="search-fragment-meta">
                {hit.source_id && <Tag>来源{hit.source_id}</Tag>}
                {hit.metadata?.page_range && <><span>页码 </span><Tag>{hit.metadata.page_range}</Tag></>}
                {!text && <span>文件名匹配，可打开资料查看内容</span>}
              </div>
              {text && <div className={`search-excerpt${text.length > 280 && !expanded.has(hit.id) ? ' is-truncated' : ''}`}>{highlightText(excerpt, submittedQuery)}</div>}
              {text.length > 280 && <Button type="link" size="small" onClick={() => setExpanded(previous => {
                const next = new Set(previous); if (next.has(hit.id)) next.delete(hit.id); else next.add(hit.id); return next
              })}>{expanded.has(hit.id) ? '收起片段' : '展开片段'}</Button>}
            </div>
          })}
        </article>)}
        {hits.length >= 50 && <p className="search-hint">已展示本次返回的结果，增加关键词可以缩小范围。</p>}
      </div>}

      {searched && !loading && !error && !cancelled && hits.length === 0 && <div className="search-empty" role="status">
        <h3>未找到相关内容</h3>
        <p>试试更短的关键词，或去掉文件扩展名。</p>
        <p>{mode === 'ai' ? '已查询可访问的系统内容与互联网。可以换一种表述，或检查上方是否有来源暂不可用的提示。' : '文件内容需要解析完成后才能被搜索，文件名可以直接搜索。'}</p>
        <Link to="/materials">查看我的资料</Link>
      </div>}
    </section>
  )
}

function sourceLabel(kind: string): string {
  return ({ document: '资料正文', shared: '共享资料', knowledge_point: '个人知识点', collection: '知识集锦', knowledge_tree: '知识树', summary: '学习摘要', knowledge_node: '知识节点', question: '题库', flashcard: '知识卡', glossary: '名词解释', web: '互联网' } as Record<string, string>)[kind] || '系统内容'
}

function safeResultHref(value?: string): string | undefined {
  if (!value) return undefined
  try {
    const url = new URL(value, window.location.origin)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return undefined
    if (url.origin === window.location.origin) return url.pathname + url.search + url.hash
    return url.href
  } catch { return undefined }
}

function matchingExcerpt(text: string, query: string): string {
  if (text.length <= 280) return text
  const positions = query.split(/\s+/).filter(Boolean).map(term => text.toLowerCase().indexOf(term.toLowerCase())).filter(index => index >= 0)
  const start = Math.max(0, (positions.length ? Math.min(...positions) : 0) - 70)
  const end = Math.min(text.length, start + 260)
  return `${start > 0 ? '…' : ''}${text.slice(start, end)}${end < text.length ? '…' : ''}`
}

function highlightText(text: string, query: string): React.ReactNode {
  const terms = [...new Set(query.trim().split(/\s+/).filter(Boolean))].sort((a, b) => b.length - a.length)
  if (!terms.length) return text
  const pattern = terms.map(term => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')
  return <span>{text.split(new RegExp(`(${pattern})`, 'gi')).map((part, index) =>
    index % 2 ? <mark key={index}>{part}</mark> : part)}</span>
}
