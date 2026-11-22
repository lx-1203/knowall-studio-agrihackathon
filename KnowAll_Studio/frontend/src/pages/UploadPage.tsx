import { useState, useEffect, useRef, useCallback } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Upload, Button, Card, Table, App, Tag, Space, Popconfirm, Modal, Input, Alert } from 'antd'
import { BookOutlined, UploadOutlined, FilePdfOutlined, FileTextOutlined, FileMarkdownOutlined, DeleteOutlined, FileImageOutlined, BranchesOutlined, LinkOutlined, CodeOutlined, EyeOutlined, DatabaseOutlined, PictureOutlined, ApartmentOutlined, RocketOutlined, CheckSquareOutlined } from '@ant-design/icons'
import { uploadDocument, listDocuments, deleteDocument, indexDocument, getDocumentDetail, analyzeDocumentImages, generateSummary, importDocumentUrl } from '../api'
import { useAppStore, useAuthStore } from '../stores'
import FilePreview from '../components/previews/FilePreview'
import { materialPath } from '../materials'
import useUploadRequest from '../hooks/useUploadRequest'
import UploadProgress from '../components/UploadProgress'
import LoadFailure from '../components/LoadFailure'
import { isCancel } from 'axios'
import { getNetworkExtraction, startNetworkExtraction, networkError, type ExtractionJob } from '../knowledgeNetwork'
import '../knowledgeNetwork.css'

const { Dragger } = Upload

const typeIcons: Record<string, React.ReactNode> = {
  pdf: <FilePdfOutlined style={{ color: '#f5222d' }} />,
  docx: <FileTextOutlined style={{ color: '#1890ff' }} />,
  pptx: <FileTextOutlined style={{ color: '#fa8c16' }} />,
  md: <FileMarkdownOutlined style={{ color: '#52c41a' }} />,
  txt: <FileTextOutlined />,
  jpeg: <FileImageOutlined style={{ color: '#722ed1' }} />,
  png: <FileImageOutlined style={{ color: '#722ed1' }} />,
  xmind: <BranchesOutlined style={{ color: '#13c2c2' }} />,
  code: <CodeOutlined style={{ color: '#13c2c2' }} />,
  url: <LinkOutlined style={{ color: '#eb2f96' }} />,
}

export default function UploadPage() {
  const navigate = useNavigate()
  const { documents, setDocuments, setSelectedDoc } = useAppStore()
  const token = useAuthStore(state => state.token)
  const accountId = useAuthStore(state => state.user?.id)
  const accountCurrent = () => useAuthStore.getState().token === token && useAuthStore.getState().user?.id === accountId
  const catalogController = useRef<AbortController | null>(null)
  const [catalogLoading, setCatalogLoading] = useState(true)
  const [catalogError, setCatalogError] = useState<unknown>(null)
  const { message } = App.useApp()
  const uploadRequest = useUploadRequest()
  const uploading = uploadRequest.active
  const [uploadError, setUploadError] = useState('')
  const [urlModalOpen, setUrlModalOpen] = useState(false)
  const [importUrl, setImportUrl] = useState('')
  const urlRequest = useUploadRequest()
  const urlImporting = urlRequest.active
  const [urlError, setUrlError] = useState('')
  const [previewVisible, setPreviewVisible] = useState(false)
  const [previewDoc, setPreviewDoc] = useState<{ id: string; type: string; name: string } | null>(null)
  const [indexingId, setIndexingId] = useState<string | null>(null)
  const [analyzingDocId, setAnalyzingDocId] = useState<string | null>(null)
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([])
  const [generatingSummary, setGeneratingSummary] = useState(false)
  const [extractions, setExtractions] = useState<ExtractionJob[]>([])
  const [extractionError, setExtractionError] = useState('')
  const [pollAttempt, setPollAttempt] = useState(0)
  const [retrying, setRetrying] = useState<string[]>([])
  const retries = useRef(new Map<string, AbortController>())
  const upsertExtraction = (job?: ExtractionJob) => {
    if (job?.id) setExtractions(current => [...current.filter(item => item.document_id !== job.document_id), job])
  }
  useEffect(() => {
    setExtractions([]); setExtractionError(''); setRetrying([])
    return () => { retries.current.forEach(controller => controller.abort()); retries.current.clear() }
  }, [token, accountId])
  const pendingJobs = extractions.filter(job => job.status === 'pending' || job.status === 'running').map(job => job.id).join(',')
  useEffect(() => {
    if (!pendingJobs) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    const current = () => !controller.signal.aborted && useAuthStore.getState().token === token && useAuthStore.getState().user?.id === accountId
    const poll = async () => {
      try {
        const jobs = await Promise.all(pendingJobs.split(',').map(id => getNetworkExtraction(id, controller.signal)))
        if (!current()) return
        setExtractionError('')
        setExtractions(rows => rows.map(row => jobs.find(job => job.id === row.id) || row))
        if (jobs.some(job => job.status === 'pending' || job.status === 'running')) timer = setTimeout(() => void poll(), 1500)
      } catch (error) {
        if (current() && !isCancel(error)) setExtractionError(networkError(error, '提炼状态暂时无法读取，请重试查询。'))
      }
    }
    void poll()
    return () => { controller.abort(); if (timer) clearTimeout(timer) }
  }, [pendingJobs, token, accountId, pollAttempt])
  const retryExtraction = async (job: ExtractionJob) => {
    if (retries.current.has(job.id) || !accountCurrent()) return
    const controller = new AbortController(); retries.current.set(job.id, controller)
    const current = () => retries.current.get(job.id) === controller && !controller.signal.aborted && accountCurrent()
    setRetrying(ids => [...ids, job.id]); setExtractionError('')
    try {
      const result = await startNetworkExtraction(job.document_id, controller.signal)
      if (current()) upsertExtraction(result)
    } catch (error) {
      if (current() && !isCancel(error)) setExtractionError(networkError(error, '重试未能提交，请稍后再试。'))
    } finally {
      if (current()) { retries.current.delete(job.id); setRetrying(ids => ids.filter(id => id !== job.id)) }
    }
  }


  const stopCatalog = () => {
    catalogController.current?.abort()
    catalogController.current = null
    setCatalogLoading(false)
  }
  const loadCatalog = useCallback(async () => {
    if (catalogController.current) return
    const controller = new AbortController()
    catalogController.current = controller
    const current = () => catalogController.current === controller && !controller.signal.aborted
      && useAuthStore.getState().token === token && useAuthStore.getState().user?.id === accountId
    setCatalogLoading(true); setCatalogError(null)
    try {
      const docs = await listDocuments(undefined, undefined, { signal: controller.signal })
      if (current()) setDocuments(docs)
    } catch (error: unknown) {
      if (current() && !isCancel(error)) setCatalogError(error)
    } finally {
      if (catalogController.current === controller) {
        catalogController.current = null; setCatalogLoading(false)
      }
    }
  }, [setDocuments, token, accountId])
  useEffect(() => {
    void loadCatalog()
    return () => { catalogController.current?.abort(); catalogController.current = null }
  }, [loadCatalog])

  const handleUpload = async (file: File, files?: File[]) => {
    if (files && files.length > 1) { setUploadError('请一次选择一份资料，完成后再添加下一份。'); return Upload.LIST_IGNORE }
    const scope = uploadRequest.begin()
    if (!scope) return Upload.LIST_IGNORE
    setUploadError(''); stopCatalog()
    try {
      const result = await uploadDocument(file, scope.config)
      if (!scope.current() || !accountCurrent()) return false
      if (!result.document_id || result.status === 'error') throw new Error('资料未能完成上传，请重试')
      let successMsg = `上传成功: ${result.filename} (${result.total_chunks} 个分片)`
      if (result.native_outline) successMsg += '，已自动提取原生大纲'
      if (result.image_count > 0) successMsg += `，检测到 ${result.image_count} 张图片`
      message.success(successMsg)
      upsertExtraction(result.knowledge_extraction)
      setSelectedDoc(result.document_id)
      setSelectedDocIds(prev => [...prev, result.document_id])
      const docs = await listDocuments(undefined, undefined, { signal: scope.signal })
      if (!scope.current() || !accountCurrent()) return false
      setDocuments(docs); setCatalogError(null)
      if (!result.knowledge_extraction) {
        try {
          setIndexingId(result.document_id)
          await indexDocument(result.document_id, { signal: scope.signal })
          if (scope.current() && accountCurrent()) message.success('已自动建立向量索引')
        } catch { /* Existing indexing failure remains non-fatal. */ }
        finally { setIndexingId(current => current === result.document_id ? null : current) }
      }
    } catch (error: unknown) {
      if (!scope.current() || !accountCurrent() || isCancel(error)) return false
      const reason = (error as { response?: { data?: { detail?: string } }; message?: string })
      setUploadError(`上传失败: ${reason.response?.data?.detail || reason.message || '请重试'}`)
    } finally { scope.finish() }
    return false
  }

  const handleDelete = async (docId: string) => {
    try {
      await deleteDocument(docId)
      if (!accountCurrent()) return
      message.success('已删除')
      setDocuments(documents.filter(d => d.id !== docId))
      const removedJobs = new Set(extractions.filter(job => job.document_id === docId).map(job => job.id))
      removedJobs.forEach(id => { retries.current.get(id)?.abort(); retries.current.delete(id) })
      setRetrying(ids => ids.filter(id => !removedJobs.has(id)))
      setExtractions(jobs => jobs.filter(job => job.document_id !== docId))
      if (removedJobs.size) setExtractionError('')
    } catch (e: any) {
      if (accountCurrent()) message.error('删除失败')
    }
  }

  const handleUrlImport = async () => {
    if (!importUrl.trim()) { message.warning('请输入URL'); return }
    const scope = urlRequest.begin()
    if (!scope) return
    setUrlError(''); stopCatalog()
    try {
      const result = await importDocumentUrl({ url: importUrl }, { signal: scope.signal })
      if (!scope.current() || !accountCurrent()) return
      if (!result.document_id) throw new Error('资料未能完成导入，请重试')
      upsertExtraction(result.knowledge_extraction)
      setSelectedDoc(result.document_id)
      const docs = await listDocuments(undefined, undefined, { signal: scope.signal })
      if (!scope.current() || !accountCurrent()) return
      setDocuments(docs); setCatalogError(null)
      message.success(`URL导入成功: ${result.total_chunks} 个分片`)
      setUrlModalOpen(false); setImportUrl('')
    } catch (error: unknown) {
      if (!scope.current() || !accountCurrent() || isCancel(error)) return
      const reason = (error as { response?: { data?: { detail?: string } }; message?: string })
      setUrlError(`导入失败: ${reason.response?.data?.detail || reason.message || '请重试'}`)
    } finally { scope.finish() }
  }

  const handleIndex = async (docId: string) => {
    setIndexingId(docId)
    try {
      const result = await indexDocument(docId)
      if (!accountCurrent()) return
      message.success(`索引完成: ${result.indexed} 个分片`)
    } catch (e: any) {
      if (accountCurrent()) message.error('索引失败')
    } finally {
      setIndexingId(null)
    }
  }

  const handleAnalyzeImages = async (docId: string) => {
    setAnalyzingDocId(docId)
    try {
      const result = await analyzeDocumentImages(docId)
      if (!accountCurrent()) return
      message.success(result.message || `已分析 ${result.images_analyzed} 张图片`)
    } catch (e: any) {
      if (accountCurrent()) message.error(`图片分析失败: ${e.response?.data?.detail || e.message}`)
    } finally {
      setAnalyzingDocId(null)
    }
  }

  const handleGenerateSummary = async () => {
    if (selectedDocIds.length === 0) {
      message.warning('请先勾选要汇总的资料')
      return
    }
    setGeneratingSummary(true)
    try {
      const result = await generateSummary({ document_ids: selectedDocIds })
      if (!accountCurrent()) return
      message.success(`知识总纲生成成功！共 ${result.node_count} 个知识点`)
      navigate(`/summary/${result.summary_id}`)
    } catch (e: any) {
      if (accountCurrent()) message.error(`生成失败: ${e.response?.data?.detail || e.message}`)
    } finally {
      setGeneratingSummary(false)
    }
  }

  const toggleSelectDoc = (docId: string) => {
    setSelectedDocIds(prev =>
      prev.includes(docId) ? prev.filter(id => id !== docId) : [...prev, docId]
    )
    // Also update legacy single select for backward compatibility
    setSelectedDoc(docId)
  }

  const docColumns = [
    {
      title: '类型',
      dataIndex: 'file_type',
      width: 60,
      render: (type: string) => typeIcons[type] || <FileTextOutlined />,
    },
    {
      title: '文件名',
      dataIndex: 'filename',
      ellipsis: true,
    },
    {
      title: '格式',
      dataIndex: 'file_type',
      width: 90,
      render: (type: string) => <Tag>{type.toUpperCase()}</Tag>,
    },
    {
      title: '页数',
      dataIndex: 'page_count',
      width: 70,
      render: (v: number) => v > 0 ? v : '-',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (status: string) => <Tag color={status === 'ready' ? 'green' : 'orange'}>{status}</Tag>,
    },
    {
      title: '上传日期',
      dataIndex: 'created_at',
      width: 120,
      render: (date: string) => date?.slice(0, 10),
    },
    {
      title: '操作',
      width: 420,
      render: (_: any, doc: any) => (
        <Space wrap>
          <Button size="small" type="primary" icon={<BookOutlined />} onClick={() => { setSelectedDoc(doc.id); navigate(materialPath(doc.id)) }}>打开学习</Button>
          <Button
            size="small"
            icon={<EyeOutlined />}
            onClick={() => {
              setPreviewDoc({ id: doc.id, type: doc.file_type, name: doc.filename })
              setPreviewVisible(true)
            }}
          >预览</Button>
          <Button
            size="small"
            type={selectedDocIds.includes(doc.id) ? 'primary' : 'default'}
            icon={selectedDocIds.includes(doc.id) ? <CheckSquareOutlined /> : undefined}
            onClick={() => toggleSelectDoc(doc.id)}
          >
            {selectedDocIds.includes(doc.id) ? '已勾选' : '勾选'}
          </Button>
          <Button
            size="small"
            icon={<DatabaseOutlined />}
            loading={indexingId === doc.id}
            onClick={() => handleIndex(doc.id)}
            title="建立向量索引以启用RAG搜索"
          >索引</Button>
          {['pdf', 'docx', 'pptx', 'html'].includes(doc.file_type) && (
            <Button
              size="small"
              icon={<PictureOutlined />}
              loading={analyzingDocId === doc.id}
              onClick={() => handleAnalyzeImages(doc.id)}
              title="使用视觉模型分析文档中的图片"
            >图片</Button>
          )}
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(doc.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Card title="资料导入" extra={
        <Space>
          <Button icon={<LinkOutlined />} onClick={() => setUrlModalOpen(true)}>导入URL</Button>
        </Space>
      } style={{ marginBottom: 16 }}>
        {uploadError && <Alert type="error" showIcon message={uploadError} />}
        <Dragger
          accept=".pdf,.docx,.pptx,.xlsx,.csv,.md,.markdown,.txt,.png,.jpg,.jpeg,.gif,.bmp,.webp,.xmind,.epub,.mhtml,.mht,.py,.js,.ts,.jsx,.tsx,.java,.cpp,.c,.go,.rs,.sql,.yaml,.yml,.json,.xml,.css,.html"
          showUploadList={false}
          beforeUpload={handleUpload}
          disabled={uploading}
        >
          <p className="ant-upload-drag-icon"><UploadOutlined style={{ fontSize: 48, color: '#4f46e5' }} /></p>
          <p className="ant-upload-text"><Button disabled={uploading} icon={<UploadOutlined />}>选择文件</Button> 或拖拽文件到这里</p>
          <p className="ant-upload-hint">
            支持 PDF / Word / PPT / Excel / CSV / MD / 图片(OCR) / XMind / 代码 / URL · 最大 100MB · 资料保存到当前配置的服务
          </p>
        </Dragger>
        <UploadProgress request={uploadRequest} />
      </Card>

      {extractions.length > 0 && <section className="knowledge-extraction" aria-label="知识提炼进度">
        {extractions.map(job => <Alert key={job.id} showIcon
          type={job.status === 'failed' ? 'error' : job.status === 'completed' ? 'success' : 'info'}
          message={job.status === 'completed' ? `已提炼 ${job.point_count} 个知识点，待你校对。` : job.status === 'failed' ? '知识提炼失败' : '正在提炼关键知识点'}
          description={<Space wrap>
            {(job.status === 'pending' || job.status === 'running') && <span>资料已导入，系统正在后台整理概念与来源。</span>}
            {job.status === 'failed' && <><span>{job.error || '提炼未完成，请重试。'}</span><Button aria-label="重试知识提炼" disabled={retrying.includes(job.id)} loading={retrying.includes(job.id)} onClick={() => void retryExtraction(job)}>重试知识提炼</Button></>}
            {job.status === 'completed' && <><Link to={`/knowledge-network?scope=personal&document=${encodeURIComponent(job.document_id)}`}>查看这份资料的知识</Link>{job.summary_id && <Link to={`/summary/${encodeURIComponent(job.summary_id)}`}>查看知识总纲</Link>}</>}
          </Space>} />)}
        {extractionError && <Alert type="warning" message={extractionError} action={<Button onClick={() => setPollAttempt(n => n + 1)}>重试查询提炼状态</Button>} />}
      </section>}

      <Card title="已上传资料" extra={
        <Space>
        <Button aria-label="刷新资料" loading={catalogLoading} disabled={catalogLoading || uploading || urlImporting} onClick={() => void loadCatalog()}>刷新资料</Button>
        <Button
          icon={<RocketOutlined />}
          type="primary"
          disabled={selectedDocIds.length === 0 || catalogLoading || Boolean(catalogError)}
          loading={generatingSummary}
          onClick={handleGenerateSummary}
        >
          生成知识总纲 ({selectedDocIds.length})
        </Button>
        </Space>
      }>
        {Boolean(catalogError) && <LoadFailure inline title="资料列表加载失败" error={catalogError} retryLabel="重新加载资料"
          onRetry={() => void loadCatalog()} pending={catalogLoading || uploading || urlImporting} />}
        {(!catalogError || documents.length > 0) && <Table
          loading={catalogLoading}
          dataSource={documents}
          rowKey="id"
          locale={{ emptyText: catalogLoading ? '资料列表正在加载…' : '暂无资料，请上传你的第一个文档' }}
          columns={docColumns}
          pagination={{ pageSize: 10, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }}
          onRow={(doc) => ({
            style: { background: selectedDocIds.includes(doc.id) ? '#f0f5ff' : undefined },
          })}
          size="middle"
        />}
      </Card>

      <Modal title="导入网页URL" open={urlModalOpen} onCancel={() => { urlRequest.cancel(); setUrlModalOpen(false) }}
        onOk={handleUrlImport} okButtonProps={{ loading: urlImporting }} okText="导入">
        {urlError && <Alert type="error" showIcon message={urlError} />}
        <Input disabled={urlImporting} placeholder="https://example.com/article" value={importUrl}
          onChange={e => setImportUrl(e.target.value)} prefix={<LinkOutlined />} />
        <div style={{ color: '#999', fontSize: 12, marginTop: 8 }}>
          输入网页URL，系统将自动抓取并提取正文内容
        </div>
      </Modal>

      <Modal
        title={previewDoc?.name || '文件预览'}
        open={previewVisible}
        onCancel={() => setPreviewVisible(false)}
        footer={null}
        width="90vw"
        style={{ top: 20 }}
        styles={{ body: { padding: 0 } }}
        destroyOnHidden
      >
        {previewDoc && (
          <FilePreview
            documentId={previewDoc.id}
            fileType={previewDoc.type}
            fileName={previewDoc.name}
            maxHeight="75vh"
          />
        )}
      </Modal>
    </div>
  )
}
