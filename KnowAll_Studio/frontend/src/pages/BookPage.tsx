import { useState, useEffect, useRef } from 'react'
import { Card, Input, Button, List, Tag, Progress, Typography, Space, Spin, Empty, message, Collapse, Modal } from 'antd'
import { BookOutlined, PlusOutlined, DeleteOutlined, DownloadOutlined, EyeOutlined, ReloadOutlined } from '@ant-design/icons'
import { createBook, listBooks, getBook, deleteBook, exportBook } from '../api'

const { Title, Paragraph, Text } = Typography

interface BookBlock {
  id: string
  type: string
  content: string
  order_index: number
}

interface BookSection {
  id: string
  title: string
  description?: string
  blocks: BookBlock[]
  order_index: number
}

interface BookChapter {
  id: string
  title: string
  description?: string
  sections: BookSection[]
  order_index: number
}

interface BookItem {
  id: string
  title: string
  subtitle?: string
  description?: string
  status: string
  progress: number
  chapters?: BookChapter[]
  chapter_count?: number
  source_kb?: string
  error?: string
}

export default function BookPage() {
  const [books, setBooks] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [createModalOpen, setCreateModalOpen] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [creating, setCreating] = useState(false)
  const [selectedBook, setSelectedBook] = useState<BookItem | null>(null)
  const [bookDetail, setBookDetail] = useState<BookItem | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [pollId, setPollId] = useState<string | null>(null)
  const selection = useRef(0)
  const selectedId = useRef<string | null>(null)

  useEffect(() => {
    loadBooks()
    return () => { selection.current++ }
  }, [])

  // Poll compiling books
  useEffect(() => {
    if (!pollId) return
    const request = selection.current
    let stopped = false
    let busy = false
    const interval = setInterval(async () => {
      if (busy) return
      busy = true
      try {
        const detail = await getBook(pollId)
        if (stopped || request !== selection.current) return
        setBookDetail(detail)
        if (detail.status === 'completed' || detail.status === 'error') {
          clearInterval(interval)
          setPollId(null)
          loadBooks()
        }
      } catch {
        if (!stopped && request === selection.current) {
          setPollId(null)
          message.error('获取教材状态失败，请重新打开教材')
        }
      } finally { busy = false }
    }, 3000)
    return () => { stopped = true; clearInterval(interval) }
  }, [pollId])

  const loadBooks = async () => {
    setLoading(true)
    try {
      const data = await listBooks()
      setBooks(data.books || [])
    } catch { /* ignore */ }
    setLoading(false)
  }

  const handleCreate = async () => {
    if (creating) return
    if (!newTitle.trim()) return message.warning('请输入教材标题')
    const request = ++selection.current
    selectedId.current = null
    setPollId(null)
    setCreating(true)
    let accepted = false
    try {
      const res = await createBook({ title: newTitle.trim(), description: newDesc.trim() })
      if (request !== selection.current) return
      accepted = true
      loadBooks()
      message.success('教材创建成功，正在编译...')
      setCreateModalOpen(false)
      setNewTitle('')
      setNewDesc('')
      // Auto-open the new book and start polling
      selectedId.current = res.id
      setBookDetail(res)
      const detail = await getBook(res.id)
      if (request !== selection.current) return
      setBookDetail(detail)
      if (detail.status === 'compiling' || detail.status === 'planning') setPollId(res.id)
      loadBooks()
    } catch (err: any) {
      if (request === selection.current) message.error((accepted ? '教材已创建，获取详情失败: ' : '创建失败: ') + (err.message || '未知错误'))
    } finally { setCreating(false) }
  }

  const handleView = async (bookId: string) => {
    const request = ++selection.current
    selectedId.current = bookId
    setPollId(null)
    setDetailLoading(true)
    try {
      const detail = await getBook(bookId)
      if (request !== selection.current) return
      setBookDetail(detail)
      if (detail.status === 'compiling' || detail.status === 'planning') {
        setPollId(bookId)
      }
    } catch {
      if (request === selection.current) message.error('加载失败')
    } finally {
      if (request === selection.current) setDetailLoading(false)
    }
  }

  const handleDelete = async (bookId: string) => {
    Modal.confirm({
      title: '确认删除',
      content: '删除后不可恢复，确认删除这本教材？',
      onOk: async () => {
        try {
          await deleteBook(bookId)
          message.success('已删除')
          if (selectedId.current === bookId) {
            selection.current++
            selectedId.current = null
            setPollId(null)
            setDetailLoading(false)
          }
          setBookDetail(current => current?.id === bookId ? null : current)
          loadBooks()
        } catch {
          message.error('删除失败')
        }
      },
    })
  }

  const handleExport = async (bookId: string, title: string) => {
    try {
      const blob = await exportBook(bookId)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${title}.md`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      message.error('导出失败')
    }
  }

  const statusColors: Record<string, string> = {
    draft: 'default', planning: 'processing', compiling: 'processing',
    completed: 'success', error: 'error',
  }
  const statusLabels: Record<string, string> = {
    draft: '草稿', planning: '规划中', compiling: '编译中',
    completed: '已完成', error: '失败',
  }

  const renderBlock = (block: BookBlock) => {
    if (block.type === 'text') {
      return <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.8 }}>{block.content}</div>
    }
    if (block.type === 'quiz') {
      try {
        const data = JSON.parse(block.content)
        return (
          <Card size="small" style={{ background: '#f6f8fa' }} title="📝 章节测试">
            {(data.questions || []).map((q: any, i: number) => (
              <div key={i} style={{ marginBottom: 12 }}>
                <Text strong>{i + 1}. {q.question}</Text>
                {q.options?.map((opt: any) => (
                  <div key={opt.label} style={{ paddingLeft: 16 }}>• {opt.label}. {opt.text}</div>
                ))}
                <Text type="success">答案: {q.answer}</Text>
                {q.explanation && <div><Text type="secondary">解析: {q.explanation}</Text></div>}
              </div>
            ))}
          </Card>
        )
      } catch { return <div>{block.content}</div> }
    }
    if (block.type === 'callout') {
      try {
        const data = JSON.parse(block.content)
        const icons: Record<string, string> = { key_point: '💡', warning: '⚠️', tip: '💡', example: '📌' }
        return (
          <div style={{ borderLeft: '4px solid #4f46e5', padding: '8px 16px', background: '#f0f5ff', borderRadius: 4 }}>
            {icons[data.type] || '💡'} <Text strong>{data.label}</Text>: {data.content}
          </div>
        )
      } catch { return <div>{block.content}</div> }
    }
    if (block.type === 'retrieval_practice') {
      return (
        <div style={{ borderLeft: '4px solid #52c41a', padding: '8px 16px', background: '#f6ffed', borderRadius: 4 }}>
          📝 {block.content}
        </div>
      )
    }
    return <div style={{ color: '#999' }}>[{block.type}] {block.content?.slice(0, 200)}</div>
  }

  return (
    <div style={{ maxWidth: 960, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}><BookOutlined /> 教材生成</Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadBooks} loading={loading}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateModalOpen(true)}>创建教材</Button>
        </Space>
      </div>
      <Paragraph type="secondary">从知识库自动生成结构化学习教材，包含文本讲解、测验、重点提示和闪卡。</Paragraph>

      <div style={{ display: 'flex', gap: 16 }}>
        {/* Book List */}
        <Card style={{ width: 300, flexShrink: 0 }} bodyStyle={{ padding: 12 }}>
          {books.length === 0 ? (
            <Empty description="暂无教材" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          ) : (
            <List
              size="small"
              dataSource={books}
              renderItem={(b: any) => (
                <List.Item
                  style={{
                    cursor: 'pointer', padding: '8px 12px', borderRadius: 8,
                    background: bookDetail?.id === b.id ? '#e6f4ff' : 'transparent',
                  }}
                  onClick={() => handleView(b.id)}
                  actions={[
                    <Button type="text" size="small" icon={<DeleteOutlined />} onClick={e => { e.stopPropagation(); handleDelete(b.id) }} />,
                  ]}
                >
                  <div style={{ width: '100%' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <Text strong ellipsis style={{ maxWidth: 160 }}>{b.title}</Text>
                      <Tag color={statusColors[b.status]}>{statusLabels[b.status]}</Tag>
                    </div>
                    {b.status === 'compiling' && <Progress percent={Math.round(b.progress * 100)} size="small" />}
                  </div>
                </List.Item>
              )}
            />
          )}
        </Card>

        {/* Book Detail */}
        <Card style={{ flex: 1 }} loading={detailLoading}>
          {!bookDetail ? (
            <Empty description="选择或创建一本教材" />
          ) : (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div>
                  <Title level={4} style={{ margin: 0 }}>{bookDetail.title}</Title>
                  {bookDetail.subtitle && <Text type="secondary">{bookDetail.subtitle}</Text>}
                </div>
                <Space>
                  <Tag color={statusColors[bookDetail.status]}>{statusLabels[bookDetail.status]}</Tag>
                  {bookDetail.status === 'completed' && (
                    <Button icon={<DownloadOutlined />} size="small"
                      onClick={() => handleExport(bookDetail.id, bookDetail.title)}>
                      导出 Markdown
                    </Button>
                  )}
                </Space>
              </div>

              {(bookDetail.status === 'compiling' || bookDetail.status === 'planning') && (
                <div style={{ marginBottom: 16 }}>
                  <Spin size="small" style={{ marginRight: 8 }} />
                  <Text type="secondary">正在编译教材... {Math.round(bookDetail.progress * 100)}%</Text>
                  <Progress percent={Math.round(bookDetail.progress * 100)} status="active" />
                </div>
              )}

              {bookDetail.error && <div role="alert" style={{ color: '#ff4d4f', marginBottom: 16 }}>{bookDetail.error}</div>}

              {bookDetail.chapters && bookDetail.chapters.length > 0 && (
                <Collapse
                  defaultActiveKey={bookDetail.chapters[0]?.id}
                  items={bookDetail.chapters.map((ch, i) => ({
                    key: ch.id,
                    label: <Text strong>第{i + 1}章 {ch.title}</Text>,
                    children: ch.sections.map(sec => (
                      <Card key={sec.id} size="small" title={sec.title} style={{ marginBottom: 12 }}
                        bodyStyle={{ padding: '12px 16px' }}>
                        {sec.blocks
                          .sort((a, b) => a.order_index - b.order_index)
                          .map(block => (
                            <div key={block.id} style={{ marginBottom: 12 }}>
                              {renderBlock(block)}
                            </div>
                          ))
                        }
                      </Card>
                    )),
                  }))}
                />
              )}
            </>
          )}
        </Card>
      </div>

      {/* Create Modal */}
      <Modal
        title="创建教材"
        open={createModalOpen}
        onOk={handleCreate}
        onCancel={() => setCreateModalOpen(false)}
        confirmLoading={creating}
        okText="开始编译"
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Input
            placeholder="教材标题（例如：操作系统原理）"
            value={newTitle}
            onChange={e => setNewTitle(e.target.value)}
          />
          <Input.TextArea
            placeholder="教材描述（可选）"
            value={newDesc}
            onChange={e => setNewDesc(e.target.value)}
            autoSize={{ minRows: 2, maxRows: 4 }}
          />
        </div>
      </Modal>
    </div>
  )
}
