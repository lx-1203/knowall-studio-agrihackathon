// KnowAll_Studio/frontend/src/pages/GlossaryDetailPage.tsx
import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Divider, Empty, Space, Spin, Tag, Typography } from 'antd'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { getGlossaryTerm } from '../api'
import { useGlossary } from '../components/GlossaryContext'
import { SourceBadge } from '../components/TermPopup'
import type { GlossaryItem } from '../types'

const { Title, Paragraph, Text } = Typography

/** 安全解码：useParams 已做过一次 decode，此处兼容含 % 的术语名，避免 URIError。 */
function safeDecode(s: string): string {
  try { return decodeURIComponent(s) } catch { return s }
}

export default function GlossaryDetailPage() {
  const { term } = useParams<{ term: string }>()
  const [searchParams] = useSearchParams()
  const subject = searchParams.get('subject') || undefined
  const navigate = useNavigate()
  const { terms, loading: glossaryLoading } = useGlossary()

  const [item, setItem] = useState<GlossaryItem | null>(null)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)

  const decode = safeDecode(term || '')

  const load = useCallback(async () => {
    setLoading(true)
    setNotFound(false)
    setItem(null)
    try {
      const data = await getGlossaryTerm(decode, subject)
      setItem(data)
    } catch {
      // 回退：本地词库中按规范名/别名查找
      const local = terms.find(t => t.term === decode)
        || terms.find(t => (t.aliases || []).includes(decode))
      if (local) {
        setItem(local)
      } else {
        setNotFound(true)
      }
    } finally {
      setLoading(false)
    }
  }, [decode, subject, terms])

  useEffect(() => { load() }, [load])

  if (loading || glossaryLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '60vh' }}>
        <Spin size="large" />
      </div>
    )
  }

  if (notFound || !item) {
    return (
      <div style={{ maxWidth: 720, margin: '0 auto', paddingTop: 48, textAlign: 'center' }}>
        <Empty description={<>未找到术语「{decode}」{subject ? `（学科：${subject}）` : ''}</>} />
        <Button type="primary" style={{ marginTop: 16 }} onClick={() => navigate('/glossary')}>
          返回名词解释库
        </Button>
      </div>
    )
  }

  const relTerms: string[] = item.related_terms || []

  return (
    <div style={{ maxWidth: 760, margin: '0 auto' }}>
      <Button type="link" style={{ paddingLeft: 0, marginBottom: 8 }} onClick={() => navigate('/glossary')}>
        ← 返回词库
      </Button>

      <Card>
        <Title level={3} style={{ marginBottom: 8 }}>
          {item.term}
          <Space size={6} style={{ marginLeft: 12 }}>
            {(item.tags || []).map((t, i) => <Tag key={i} color="purple">{t}</Tag>)}
          </Space>
        </Title>
        <Space size={6} wrap>
          <Tag color="blue">{item.subject}</Tag>
          {item.chapter && <Tag color="cyan">{item.chapter}</Tag>}
          {item.updated_at && <Text type="secondary" style={{ fontSize: 12 }}>更新: {item.updated_at.slice(0, 10)}</Text>}
        </Space>

        <Divider orientation="left" plain>名词解释</Divider>
        <Paragraph style={{ fontSize: 15, lineHeight: 1.8, whiteSpace: 'pre-wrap' }}>
          {item.definition || '暂无解释'}
        </Paragraph>

        {(item.aliases || []).length > 0 && (
          <>
            <Divider orientation="left" plain>别名 / 同义词</Divider>
            <Space size={6} wrap>
              {(item.aliases || []).map((a, i) => <Tag key={i}>{a}</Tag>)}
            </Space>
          </>
        )}

        {relTerms.length > 0 && (
          <>
            <Divider orientation="left" plain>关联术语</Divider>
            <Space size={6} wrap>
              {relTerms.map((rel, i) => (
                <Button key={i} size="small" onClick={() => navigate(`/glossary/${encodeURIComponent(rel)}`)}>
                  {rel}
                </Button>
              ))}
            </Space>
          </>
        )}

        {item.full_explanation && (
          <>
            <Divider orientation="left" plain>完整解释</Divider>
            <Paragraph style={{ fontSize: 14, lineHeight: 1.9, whiteSpace: 'pre-wrap', color: '#555' }}>
              {item.full_explanation}
            </Paragraph>
          </>
        )}

        {(item.source_document_id || item.source_node_id || item.source_summary_id || item.source_type || item.source_label) && (
          <>
            <Divider orientation="left" plain>来源出处</Divider>
            <Space direction="vertical" size={4}>
              <SourceBadge type={item.source_type} label={item.source_label} />
              <Text type="secondary" style={{ fontSize: 12 }}>
                来源出处：{item.source_document_id ? `文档 ${item.source_document_id.slice(0, 8)}…` : '知识资料库'}
                {item.source_summary_id && ` · 总纲 ${item.source_summary_id.slice(0, 8)}…`}
              </Text>
            </Space>
            {item.source_document_id && (
              <Text type="secondary" style={{ display: 'block', fontSize: 12 }}>
                来源文档: {item.source_document_id}
              </Text>
            )}
            {item.source_node_id && (
              <Text type="secondary" style={{ display: 'block', fontSize: 12 }}>
                来源节点: {item.source_node_id}
              </Text>
            )}
            {item.source_summary_id && (
              <Button
                type="link"
                style={{ paddingLeft: 0, marginTop: 4 }}
                onClick={() => navigate(`/summary/${item.source_summary_id}`)}
              >
                查看原文位置（知识总纲） →
              </Button>
            )}
          </>
        )}
      </Card>
    </div>
  )
}
