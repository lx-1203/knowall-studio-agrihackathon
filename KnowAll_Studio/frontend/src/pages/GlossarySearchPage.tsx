// KnowAll_Studio/frontend/src/pages/GlossarySearchPage.tsx
import { useCallback, useEffect, useState } from 'react'
import { Card, Empty, Input, Space, Table, Tag, Typography, message } from 'antd'
import { useNavigate } from 'react-router-dom'
import { getGlossaryStats, getGlossaryTerms } from '../api'
import type { GlossaryItem } from '../types'

const { Title, Text } = Typography

interface GlossaryStats {
  total: number
  subjects: string[]
}

export default function GlossarySearchPage() {
  const navigate = useNavigate()
  const [items, setItems] = useState<GlossaryItem[]>([])
  const [total, setTotal] = useState(0)
  const [stats, setStats] = useState<GlossaryStats>({ total: 0, subjects: [] })
  const [loading, setLoading] = useState(false)
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)

  const load = useCallback(async (query: string, p: number) => {
    setLoading(true)
    try {
      const data = await getGlossaryTerms({ q: query || undefined, page: p, page_size: 20 })
      setItems(data.items || [])
      setTotal(data.total || 0)
    } catch (err: any) {
      message.error('加载词库失败: ' + (err?.response?.data?.detail || err?.message || '未知错误'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load('', 1) }, [load])

  useEffect(() => {
    getGlossaryStats().then(setStats).catch(() => { /* 统计失败不阻塞列表 */ })
  }, [])

  const gotoDetail = (item: GlossaryItem) => {
    navigate(`/glossary/${encodeURIComponent(item.term)}${item.subject ? `?subject=${encodeURIComponent(item.subject)}` : ''}`)
  }

  const columns = [
    {
      title: '术语', dataIndex: 'term', width: 180,
      render: (v: string, r: GlossaryItem) => (
        <a onClick={() => gotoDetail(r)} style={{ fontWeight: 600, color: '#4f46e5' }}>{v}</a>
      ),
    },
    {
      title: '学科', dataIndex: 'subject', width: 110,
      render: (v: string) => (v ? <Tag color="blue">{v}</Tag> : '-'),
    },
    {
      title: '章节', dataIndex: 'chapter', width: 150,
      render: (v: string) => v || '-',
    },
    {
      title: '解释', dataIndex: 'definition', ellipsis: true,
      render: (v: string) => (
        <Text ellipsis style={{ maxWidth: 380 }}>{v}</Text>
      ),
    },
    {
      title: '标签', dataIndex: 'tags', width: 180,
      render: (tags: string[]) =>
        (tags || []).map((t, i) => <Tag key={i} className="glossary-tag">{t}</Tag>),
    },
  ]

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto' }}>
      <Title level={3} style={{ margin: 0 }}>📖 名词解释库</Title>
      <Text type="secondary" style={{ display: 'block', marginTop: 4 }}>
        收录课程核心术语，点击术语查看完整解释；支持按术语 / 别名 / 解释全文搜索。
      </Text>

      <Card style={{ marginTop: 16, marginBottom: 16 }}>
        <Space style={{ width: '100%', flexWrap: 'wrap' }} size={12}>
          <Input.Search
            placeholder="搜索术语 / 别名 / 解释…"
            allowClear
            enterButton
            loading={loading}
            style={{ maxWidth: 480 }}
            onSearch={(v) => { setQ(v); setPage(1); load(v, 1) }}
          />
          <Text type="secondary">共 {stats.total || total} 个词条</Text>
          {stats.subjects.map(s => <Tag key={s} color="purple">{s}</Tag>)}
        </Space>
      </Card>

      <Card>
        <Table<GlossaryItem>
          rowKey="id"
          size="middle"
          columns={columns as any}
          dataSource={items}
          loading={loading}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无词条" /> }}
          pagination={{
            current: page,
            pageSize: 20,
            total,
            showSizeChanger: false,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p) => { setPage(p); load(q, p) },
          }}
        />
      </Card>
    </div>
  )
}
