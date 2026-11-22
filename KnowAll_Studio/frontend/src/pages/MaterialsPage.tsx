import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Alert, App, Button, Dropdown, Empty, Input, Modal, Radio, Select, Skeleton, Tag, Upload } from 'antd'
import { ArrowRightOutlined, BookOutlined, FileTextOutlined, MoreOutlined, PlusOutlined, SearchOutlined, UploadOutlined } from '@ant-design/icons'
import { updateDocumentOrganization, uploadDocument } from '../api'
import { loadMaterialCatalog } from '../materialCatalog'
import { useAppStore, useAuthStore } from '../stores'
import { readProgress } from '../materialProgress'
import { builtinBiology, kindLabel, materialKinds, materialPath, type Material, type MaterialKind } from '../materials'
import useUploadRequest from '../hooks/useUploadRequest'
import UploadProgress from '../components/UploadProgress'
import { isCancel } from 'axios'
import '../materials.css'

export default function MaterialsPage() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const subject = params.get('subject') || ''
  const kind = params.get('kind') || 'all'
  const query = params.get('q') || ''
  const sort = params.get('sort') || 'recent'
  const { message } = App.useApp()
  const setSelectedDoc = useAppStore(s => s.setSelectedDoc)
  const userId = useAuthStore(s => s.user?.id || 'local')
  const [documents, setDocuments] = useState<Material[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [uploadOpen, setUploadOpen] = useState(false)
  const uploadRequest = useUploadRequest()
  const uploading = uploadRequest.active
  const [uploadError, setUploadError] = useState('')
  const [uploadSubject, setUploadSubject] = useState('')
  const [uploadKind, setUploadKind] = useState<MaterialKind>('knowledge')
  const [editing, setEditing] = useState<Material | null>(null)
  const [editSubject, setEditSubject] = useState('')
  const [editKind, setEditKind] = useState<MaterialKind>('knowledge')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError('')
    loadMaterialCatalog(() => cancelled).then(all => { if (!cancelled) setDocuments(all) })
      .catch(() => { if (!cancelled) setError('资料暂时没有加载成功，请重试。') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [attempt])

  const filter = (name: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(name, value); else next.delete(name)
    setParams(next, { replace: true })
  }
  const all = [...documents, builtinBiology]
  const returnTo = '/materials' + (params.toString() ? `?${params}` : '')
  const subjects = [...new Set(all.map(doc => doc.subject || '').filter(Boolean))].sort((a, b) => a.localeCompare(b, 'zh-CN'))
  const filtered = all.filter(doc => (!subject || (subject === '__unclassified' ? !doc.subject : doc.subject === subject))
    && (kind === 'all' || (doc.material_kind || 'knowledge') === kind)
    && (!query || `${doc.filename} ${doc.subject || ''}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())))
    .sort((a, b) => sort === 'name' ? a.filename.localeCompare(b.filename, 'zh-CN')
      : (Date.parse(b.created_at || '') || 0) - (Date.parse(a.created_at || '') || 0))
  const continueReading = filtered.map(doc => ({ doc, progress: readProgress(userId, doc.id) }))
    .filter(item => item.progress && item.progress.updatedAt > 0)
    .sort((a, b) => b.progress!.updatedAt - a.progress!.updatedAt)[0]
  const beginEdit = (doc: Material) => { setEditing(doc); setEditSubject(doc.subject || ''); setEditKind(doc.material_kind || 'knowledge') }
  const saveOrganization = async () => {
    if (!editing) return
    setSaving(true)
    try {
      const values = { subject: editSubject.trim(), material_kind: editKind }
      await updateDocumentOrganization(editing.id, values)
      setDocuments(previous => previous.map(doc => doc.id === editing.id ? { ...doc, ...values } : doc))
      setEditing(null); message.success('资料分类已保存')
    } catch { message.error('分类保存失败，请重试。') }
    finally { setSaving(false) }
  }
  const upload = async (file: File, files: File[]) => {
    if (files.length > 1) { setUploadError('请一次选择一份资料，完成后再添加下一份。'); return Upload.LIST_IGNORE }
    const scope = uploadRequest.begin()
    if (!scope) return Upload.LIST_IGNORE
    setUploadError('')
    try {
      const result = await uploadDocument(file, scope.config)
      if (!scope.current()) return false
      const id = result.document_id
      if (!id || result.status === 'error') throw new Error('资料未能完成上传，请重试')
      try {
        await updateDocumentOrganization(id, { subject: uploadSubject.trim(), material_kind: uploadKind }, { signal: scope.signal })
      } catch {
        if (!scope.current()) return false
        message.warning('文件已上传，分类未保存。可返回资料库补充分类。')
      }
      if (!scope.current()) return false
      setSelectedDoc(id)
      message.success('资料已上传，可以直接开始阅读')
      navigate(materialPath(id), { state: { returnTo } })
    } catch (err: unknown) {
      if (!scope.current() || isCancel(err)) return false
      const detail = (err as { response?: { data?: { detail?: string } }; message?: string })
      const reason = detail.response?.data?.detail || detail.message || '上传失败，请重试。'
      setUploadError(typeof reason === 'string' ? `上传失败: ${reason}` : '上传失败，请检查文件格式后重试。')
    } finally { scope.finish() }
    return false
  }

  return <section className="material-library">
    <header className="material-library-header">
      <div><p className="material-eyebrow">从一份资料开始</p><h2>我的资料</h2><p>打开教材、笔记或试卷，接着学下去。</p></div>
      <Button type="primary" icon={<PlusOutlined />} size="large" onClick={() => { setUploadSubject(subject === '__unclassified' ? '' : subject); setUploadOpen(true) }}>上传资料</Button>
    </header>
    {!loading && !error && continueReading && <div className="material-continue" data-testid="material-continue"><BookOutlined /><div><small>接着上次的进度</small><strong>{continueReading.doc.filename}</strong><p>{continueReading.progress!.mode === 'text' ? continueReading.progress!.chapter || '阅读正文' : '原文件阅读'}</p></div><Link to={materialPath(continueReading.doc.id)} state={{ returnTo }}>继续阅读 <ArrowRightOutlined /></Link></div>}
    <div className="material-library-controls">
      <Input aria-label="搜索我的资料" placeholder="搜索资料名称或科目" prefix={<SearchOutlined />} value={query} onChange={event => filter('q', event.target.value)} allowClear />
      <Radio.Group aria-label="资料类型" optionType="button" buttonStyle="solid" value={kind} onChange={event => filter('kind', event.target.value)} options={[{ label: '全部资料', value: 'all' }, ...materialKinds]} />
    </div>
    <div className="material-subjects" aria-label="按科目筛选">
      <Button type={!subject ? 'primary' : 'text'} aria-pressed={!subject} onClick={() => filter('subject', '')}>全部科目</Button>
      {subjects.map(name => <Button key={name} aria-label={`科目 ${name}`} aria-pressed={subject === name} type={subject === name ? 'primary' : 'text'} onClick={() => filter('subject', name)}>{name}</Button>)}
      {documents.some(doc => !doc.subject) && <Button aria-pressed={subject === '__unclassified'} type={subject === '__unclassified' ? 'primary' : 'text'} onClick={() => filter('subject', '__unclassified')}>未分类</Button>}
    </div>
    {error && <Alert type="error" showIcon message={error} action={<Button onClick={() => setAttempt(value => value + 1)}>重新加载</Button>} />}
    <div className="material-library-count"><span>{loading ? '正在整理资料…' : `${filtered.length} 份学习资料`}</span>
      <div className="material-library-options">
        {(subject || kind !== 'all' || query) && <Button type="text" size="small" onClick={() => { const next = new URLSearchParams(); if (sort !== 'recent') next.set('sort', sort); setParams(next, { replace: true }) }}>清除筛选</Button>}
        <Select aria-label="资料排序" value={sort} onChange={value => filter('sort', value)} options={[{ value: 'recent', label: '最近上传' }, { value: 'name', label: '资料名称' }]} />
        <Link to="/upload">高级导入与管理</Link>
      </div></div>
    {loading ? <Skeleton active paragraph={{ rows: 6 }} /> : filtered.length ? <div className="material-grid">
      {filtered.map(doc => <article className="material-tile" key={doc.id}>
        <div className={`material-cover material-cover-${doc.material_kind || 'knowledge'}`}>
          {doc.material_kind === 'question_bank' ? <FileTextOutlined /> : <BookOutlined />}
          <span>{doc.subject || '未分类'}</span><small>{doc.file_type?.toUpperCase()}</small>
        </div>
        <div className="material-tile-body">
          <div className="material-tile-labels"><Tag>{kindLabel(doc.material_kind)}</Tag>{doc.id === builtinBiology.id && <Tag>内置</Tag>}
            {doc.id !== builtinBiology.id && <Dropdown menu={{ items: [{ key: 'classify', label: '修改科目与类型' }], onClick: () => beginEdit(doc) }} trigger={['click']}><Button type="text" icon={<MoreOutlined />} aria-label={`管理 ${doc.filename}`} /></Dropdown>}
          </div>
          <h3><Link to={materialPath(doc.id)} state={{ returnTo }} aria-label={`打开 ${doc.filename}`} onClick={() => { if (doc.id !== builtinBiology.id) setSelectedDoc(doc.id) }}>{doc.filename}</Link></h3>
          <p>{doc.id === builtinBiology.id ? '按章节练习 · 保留原有学习进度' : doc.status === 'ready' ? `${doc.page_count ? `${doc.page_count} 页 · ` : ''}原文阅读与学习工具` : doc.status === 'error' ? '解析失败 · 可查看原文件' : '资料处理中 · 可打开查看'}</p>
          <Link className="material-open" to={materialPath(doc.id)} state={{ returnTo }} onClick={() => { if (doc.id !== builtinBiology.id) setSelectedDoc(doc.id) }}>{readProgress(userId, doc.id) ? '继续学习' : '开始学习'} <ArrowRightOutlined /></Link>
        </div>
      </article>)}
    </div> : <Empty description="没有符合筛选的资料"><Button onClick={() => setParams({})}>查看全部资料</Button></Empty>}
    {!loading && !error && documents.length === 0 && <div className="material-onboarding"><UploadOutlined /><div><strong>把你的第一份教材放进来</strong><p>上传后直接阅读，需要时再整理知识卡和练习。</p></div><Button onClick={() => setUploadOpen(true)}>上传自己的资料</Button></div>}
    <Modal title="添加学习资料" open={uploadOpen} onCancel={() => { if (!uploading) setUploadOpen(false) }} footer={null} maskClosable={!uploading} closable={!uploading}>
      <div className="material-form">
        {uploadError && <Alert type="error" showIcon message={uploadError} />}
        <label>科目<Input aria-label="上传资料科目" maxLength={80} placeholder="例如：生物、历史，可留空" value={uploadSubject} disabled={uploading} onChange={event => setUploadSubject(event.target.value)} /></label>
        <label>资料类型<Select aria-label="上传资料类型" value={uploadKind} options={[...materialKinds]} disabled={uploading} onChange={setUploadKind} /></label>
        <Upload.Dragger showUploadList={false} maxCount={1} beforeUpload={upload} disabled={uploading} accept=".pdf,.docx,.pptx,.xlsx,.csv,.md,.markdown,.txt,.png,.jpg,.jpeg,.gif,.bmp,.webp,.xmind,.epub,.mhtml,.mht,.py,.js,.ts,.jsx,.tsx,.java,.cpp,.c,.go,.rs,.sql,.yaml,.yml,.json,.xml,.css,.html">
          <p className="ant-upload-drag-icon"><UploadOutlined /></p><p>{uploading ? '正在上传和解析，请稍候…' : '选择文件，或拖到这里'}</p><p className="material-muted">教材、笔记、试卷都可以直接打开学习</p>
        </Upload.Dragger>
        <UploadProgress request={uploadRequest} />
        <p className="material-muted">知识资料用于阅读理解；题库用于学习习题；两者都有的文件选混合资料。</p>
      </div>
    </Modal>
    <Modal title="资料分类" open={!!editing} onCancel={() => { if (!saving) setEditing(null) }} onOk={saveOrganization} confirmLoading={saving} okText="保存分类" cancelText="取消">
      <div className="material-form"><label>科目<Input aria-label="资料科目" maxLength={80} value={editSubject} onChange={event => setEditSubject(event.target.value)} /></label><label>资料类型<Select aria-label="资料分类类型" options={[...materialKinds]} value={editKind} onChange={setEditKind} /></label></div>
    </Modal>
  </section>
}
