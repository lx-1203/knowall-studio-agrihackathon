import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { Alert, Button, Drawer, Empty, Grid, Radio, Skeleton, Tag } from 'antd'
import { ArrowLeftOutlined, BookOutlined, CloseOutlined, ExpandOutlined, LeftOutlined, RightOutlined } from '@ant-design/icons'
import LearningContent from '../components/LearningContent'
import api from '../api'
import { useAppStore, useAuthStore } from '../stores'
import FilePreview from '../components/previews/FilePreview'
import MaterialResources from '../components/MaterialResources'
import { chapterTitle, kindLabel, type Material, type SourceChunk } from '../materials'
import { libraryReturn, readProgress, saveProgress, syncProgress, type ReadingMode } from '../materialProgress'
import '../materials.css'

const ORIGINAL_TYPES = new Set(['pdf', 'docx', 'pptx', 'ppt', 'xlsx', 'xls', 'csv', 'md', 'markdown', 'txt', 'text', 'py', 'js', 'ts', 'jsx', 'tsx', 'java', 'cpp', 'c', 'h', 'go', 'rs', 'sql', 'yaml', 'yml', 'json', 'xml', 'css', 'html', 'html_code', 'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'])

export default function MaterialStudyPage() {
  const { documentId = '' } = useParams<{ documentId: string }>()
  const location = useLocation()
  const sourceId = new URLSearchParams(location.search).get('source') || ''
  const token = useAuthStore(state => state.token)
  const returnTo = libraryReturn(location.state)
  const screens = Grid.useBreakpoint()
  const compact = !screens.xl
  const [toolsOpen, setToolsOpen] = useState(false)
  const [focused, setFocused] = useState(false)
  const userId = useAuthStore(state => state.user?.id || 'local')
  const setSelectedDoc = useAppStore(state => state.setSelectedDoc)
  const [doc, setDoc] = useState<Material | null>(null)
  const [chunks, setChunks] = useState<SourceChunk[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [sourceError, setSourceError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [mode, setMode] = useState<ReadingMode>('original')
  const [activeChunk, setActiveChunk] = useState(0)
  const reader = useRef<HTMLDivElement>(null)
  const positionRevision = useRef(0)
  const [syncReady, setSyncReady] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    const current = () => !controller.signal.aborted && useAuthStore.getState().token === token && (useAuthStore.getState().user?.id || 'local') === userId
    setLoading(true); setError(''); setSourceError(''); setDoc(null); setChunks([])
    const initialRevision = ++positionRevision.current; setSyncReady(false)
    setSelectedDoc(documentId)
    Promise.allSettled([api.get(`/documents/${encodeURIComponent(documentId)}`, { signal: controller.signal }).then(r => r.data), api.get(`/documents/${encodeURIComponent(documentId)}/chunks`, { signal: controller.signal }).then(r => r.data)]).then(([detail, source]) => {
      if (!current()) return
      if (detail.status === 'rejected' || !detail.value?.id) { setError('这份资料暂时无法打开，可能已删除或连接中断。'); return }
      setDoc(detail.value)
      const items: SourceChunk[] = source.status === 'fulfilled' ? (Array.isArray(source.value) ? source.value : source.value.chunks || []) : []
      setChunks(items)
      if (source.status === 'rejected') setSourceError('正文和目录未能加载，仍可以阅读原文件。')
      const saved = readProgress(userId, documentId)
      if (userId !== 'local') void syncProgress(userId, documentId, () => current() && positionRevision.current === initialRevision).then(result => {
        if (!current()) return
        if (result.status === 'downloaded' && positionRevision.current === initialRevision && !sourceId) {
          const remote = readProgress(userId, documentId), index = remote?.chunkId ? items.findIndex(item => item.id === remote.chunkId) : -1
          setActiveChunk(index >= 0 ? index : Math.min(remote?.chunkIndex || 0, Math.max(items.length - 1, 0))); setMode('text')
        }
        setSyncReady(result.status !== 'storage_error')
      }); else setSyncReady(true)
      const byId = saved?.chunkId ? items.findIndex(item => item.id === saved.chunkId) : -1
      const requested = sourceId ? items.findIndex(item => item.id === sourceId) : -1
      setActiveChunk(requested >= 0 ? requested : byId >= 0 ? byId : saved && saved.chunkIndex >= 0 && saved.chunkIndex < items.length ? saved.chunkIndex : 0)
      if (sourceId && requested < 0) setSourceError('对应原文片段已变化，请从目录查看当前资料。')
      setMode(sourceId ? 'text' : ORIGINAL_TYPES.has(detail.value.file_type?.toLowerCase()) ? saved?.mode || 'original' : 'text')
    }).finally(() => { if (current()) setLoading(false) })
    return () => controller.abort()
  }, [documentId, sourceId, attempt, userId, token, setSelectedDoc])
  useEffect(() => {
    if (loading || !doc || !syncReady) return
    const current = chunks[activeChunk]
    saveProgress(userId, documentId, { chunkId: current?.id, chunkIndex: activeChunk, mode, chapter: current ? chapterTitle(current, activeChunk) : '', updatedAt: Date.now() })
  }, [loading, doc, chunks, activeChunk, mode, userId, documentId, syncReady])
  const goTo = (index: number) => {
    positionRevision.current++; setSyncReady(true)
    setActiveChunk(index); setMode('text')
    reader.current?.scrollIntoView({ block: 'nearest', behavior: 'auto' })
  }
  const showSource = (ids?: string[]) => {
    setToolsOpen(false)
    const index = ids?.length ? chunks.findIndex(chunk => ids.includes(chunk.id)) : activeChunk
    if (index >= 0) goTo(index)
    else { setMode('text'); setSourceError('对应原文片段已变化，请从目录查看当前资料。') }
  }
  if (loading) return <Skeleton active paragraph={{ rows: 12 }} />
  if (error || !doc) return <section className="material-load-error"><Alert type="error" showIcon message={error || '资料不可用'} /><Button onClick={() => setAttempt(value => value + 1)}>重新打开</Button><Link to={returnTo}>返回我的资料</Link></section>
  const current = chunks[activeChunk]
  const resources = <MaterialResources key={`${userId}:${doc.id}`} documentId={doc.id} title={doc.filename} chunks={chunks} subject={doc.subject} chunk={current} onSource={showSource} ready={doc.status === 'ready' && chunks.length > 0} />
  return <section className={`material-study${focused ? ' material-study-focused' : ''}`}>
    <header className="material-study-header">
      <div><Link className="material-back" to={returnTo}><ArrowLeftOutlined /> 我的资料</Link><h2>{doc.filename}</h2><div className="material-study-labels"><Tag>{doc.subject || '未分类'}</Tag><Tag>{kindLabel(doc.material_kind)}</Tag><span>{doc.page_count ? `${doc.page_count} 页 · ` : ''}{doc.file_type?.toUpperCase()}</span></div></div>
      <div className="material-study-actions"><Button aria-label={focused ? '结束专注' : '专注阅读'} icon={<ExpandOutlined />} aria-pressed={focused} onClick={() => setFocused(value => !value)}>{focused ? '结束专注' : '专注阅读'}</Button>{compact && <Button aria-label="学习工具" type="primary" icon={<BookOutlined />} onClick={() => setToolsOpen(true)}>学习工具</Button>}</div>
    </header>
    <div className="material-study-grid">
      <nav className="material-outline" aria-label="资料目录"><div className="material-outline-title"><BookOutlined /><h3>资料目录</h3></div>
        <div className="material-outline-items">{chunks.length ? chunks.map((chunk, index) => <button key={chunk.id} type="button" className={index === activeChunk ? 'is-active' : ''} aria-current={index === activeChunk ? 'location' : undefined} onClick={() => goTo(index)}><span>{chapterTitle(chunk, index)}</span><small>{chunk.page_range || `片段 ${index + 1}`}</small></button>) : <p className="material-muted">解析完成后显示阅读目录。</p>}</div>
      </nav>
      <div className="material-reader" ref={reader}>
        <div className="material-reader-toolbar"><Radio.Group aria-label="阅读方式" size="small" optionType="button" buttonStyle="solid" value={mode} onChange={event => setMode(event.target.value)} options={[{ value: 'original', label: '原文件', disabled: !ORIGINAL_TYPES.has(doc.file_type?.toLowerCase()) }, { value: 'text', label: '阅读正文' }]} /><span>随时阅读，无需生成</span></div>
        {sourceError && <Alert type="warning" message={sourceError} action={<Button size="small" onClick={() => setAttempt(value => value + 1)}>重试</Button>} />}
        {mode === 'original' ? <div className="material-original material-markdown"><FilePreview key={doc.id} documentId={doc.id} fileType={doc.file_type} fileName={doc.filename} maxHeight="72vh" /></div> : current ? <>
          <article className="material-markdown material-reading-text" aria-label="资料正文"><LearningContent text={current.text} /></article>
          <div className="material-reader-pagination"><Button icon={<LeftOutlined />} disabled={activeChunk === 0} onClick={() => goTo(activeChunk - 1)}>上一段</Button><span>{activeChunk + 1} / {chunks.length}{current.page_range && ` · ${current.page_range}`}</span><Button icon={<RightOutlined />} disabled={activeChunk >= chunks.length - 1} onClick={() => goTo(activeChunk + 1)}>下一段</Button></div>
        </> : <Empty description={doc.status === 'error' ? '正文解析失败，可查看原文件或重新导入。' : '暂无可阅读的正文，请等待解析完成或重新导入。'} />}
      </div>
      {!compact && resources}
    </div>
    {compact && <Drawer className="material-tools-drawer" title="当前资料 · 学习工具" width="min(430px, 100vw)" open={toolsOpen} forceRender closeIcon={<CloseOutlined aria-label="关闭学习工具" />} onClose={() => setToolsOpen(false)}>{resources}</Drawer>}
  </section>
}
