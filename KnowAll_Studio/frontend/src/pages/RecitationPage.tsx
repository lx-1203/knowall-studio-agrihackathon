import { useEffect, useState, useRef } from 'react'
import {
  Card, Button, Tabs, Slider, Space, Modal, Form, Input, Select,
  Table, Tag, Empty, Spin, message, Popconfirm, Statistic, Row, Col,
} from 'antd'
import {
  PlusOutlined, EyeOutlined, EditOutlined, DeleteOutlined,
  ReloadOutlined, TrophyOutlined, BookOutlined,
} from '@ant-design/icons'
import App from 'antd/es/app'
import TextHider from '../components/TextHider'
import DictationCompare from '../components/DictationCompare'
import { useRecitationStore, useAuthStore } from '../stores'
import type { RecitationPassage as Passage, DictationResult } from '../types'
import {
  listPassages, createPassage, deletePassage, getHidePlan,
  submitDictation, reviewRecitation, getDueRecitations, getRecitationStats,
} from '../api'

export default function RecitationPage() {
  const accountId = useAuthStore(state => state.user?.id)
  const accountRequests = useRef<AbortController | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    accountRequests.current = controller
    return () => { controller.abort(); if (accountRequests.current === controller) accountRequests.current = null }
  }, [accountId])
  const accountCurrent = () => useAuthStore.getState().isAuthenticated && useAuthStore.getState().user?.id === accountId
    && Boolean(accountRequests.current && !accountRequests.current.signal.aborted)
  const requestConfig = () => ({ signal: accountRequests.current?.signal })
  const { message: msg } = App.useApp()
  const [passages, setPassages] = useState<Passage[]>([])
  const [loading, setLoading] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const [addForm] = Form.useForm()
  const [submitting, setSubmitting] = useState(false)
  const [stats, setStats] = useState<{ total_passages: number; total_records: number; avg_accuracy: number; mastered: number } | null>(null)
  const [dueItems, setDueItems] = useState<any[]>([])
  const startTimeRef = useRef<number>(0)

  const store = useRecitationStore()

  // 加载数据
  const loadPassages = async () => {
    setLoading(true)
    try {
      const data = await listPassages({ limit: 200 }, requestConfig())
      if (!accountCurrent()) return
      setPassages(data.passages || [])
    } catch { if (!accountCurrent()) return; msg.error('加载段落失败') }
    setLoading(false)
  }

  const loadStats = async () => {
    try { const data = await getRecitationStats(requestConfig()); if (accountCurrent()) setStats(data) } catch { if (!accountCurrent()) return;}
  }

  const loadDue = async () => {
    try {
      const data = await getDueRecitations(20, requestConfig())
      if (!accountCurrent()) return
      setDueItems(data.due_items || [])
    } catch { if (!accountCurrent()) return;}
  }

  useEffect(() => {
    loadPassages()
    loadStats()
    loadDue()
  }, [accountId])

  // 选择段落后生成遮字方案
  useEffect(() => {
    if (store.currentPassage && store.mode === 'hide') {
      generateHidePlan()
    }
  }, [store.currentPassage, store.mode, store.hideLevel, accountId])

  const generateHidePlan = async () => {
    if (!store.currentPassage) return
    try {
      const plan = await getHidePlan(store.currentPassage.content, store.hideLevel, requestConfig())
      if (!accountCurrent()) return
      store.setHidePlan(plan)
    } catch { if (!accountCurrent()) return; msg.error('生成隐藏方案失败') }
  }

  // 创建段落
  const handleCreate = async () => {
    try {
      const values = await addForm.validateFields()
      if (!accountCurrent()) return
      await createPassage({
        title: values.title,
        content: values.content,
        source: values.source || undefined,
        category: values.category || 'custom',
        difficulty: values.difficulty || 1,
      }, requestConfig())
      if (!accountCurrent()) return
      msg.success('段落创建成功')
      setAddOpen(false)
      addForm.resetFields()
      loadPassages()
      loadStats()
    } catch (e: any) { if (!accountCurrent()) return;
      if (!e.errorFields) msg.error(`创建失败：${e.response?.data?.detail || '请重试'}`)
    }
  }

  // 删除段落
  const handleDelete = async (id: string) => {
    try {
      await deletePassage(id, requestConfig())
      if (!accountCurrent()) return
      msg.success('已删除')
      if (store.currentPassage?.id === id) store.reset()
      loadPassages()
      loadStats()
    } catch { if (!accountCurrent()) return; msg.error('删除失败') }
  }

  // 开始背诵（选择段落）
  const startPractice = (passage: Passage, mode: 'hide' | 'dictation') => {
    store.setCurrentPassage(passage)
    store.setMode(mode)
    startTimeRef.current = Date.now()
  }

  // 提交默写
  const handleDictationSubmit = async () => {
    if (!store.currentPassage || !store.dictationInput.trim()) {
      msg.warning('请输入默写内容')
      return
    }
    setSubmitting(true)
    try {
      const timeSpent = Date.now() - startTimeRef.current
      const result = await submitDictation({
        passage_id: store.currentPassage.id,
        user_input: store.dictationInput,
        time_spent_ms: timeSpent,
      }, requestConfig())
      if (!accountCurrent()) return
      store.setDictationResult(result)
      msg.success(`准确率: ${Math.round(result.accuracy * 100)}%`)
      loadStats()
      loadDue()
    } catch { if (!accountCurrent()) return; msg.error('提交失败') }
    setSubmitting(false)
  }

  // FSRS 复习评级
  const handleReview = async (recordId: string, rating: number) => {
    try {
      await reviewRecitation(recordId, rating, requestConfig())
      if (!accountCurrent()) return
      msg.success(['', '已标记：再来一次', '已标记：有点难', '已标记：记住', '已标记：很简单'][rating])
      loadDue()
    } catch { if (!accountCurrent()) return; msg.error('评级失败') }
  }

  // 段落列表表格列
  const columns = [
    { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true },
    {
      title: '分类', dataIndex: 'category', key: 'category', width: 80,
      render: (v: string) => {
        const map: Record<string, { color: string; text: string }> = {
          poetry: { color: 'purple', text: '古诗词' },
          textbook: { color: 'blue', text: '课文' },
          custom: { color: 'default', text: '自定义' },
        }
        const tag = map[v] || map.custom
        return <Tag color={tag.color}>{tag.text}</Tag>
      },
    },
    {
      title: '难度', dataIndex: 'difficulty', key: 'difficulty', width: 80,
      render: (d: number) => <Tag>{'⭐'.repeat(Math.max(0, Math.min(Math.round(d || 0), 5)))}</Tag>,
    },
    {
      title: '字数', key: 'length', width: 70,
      render: (_: any, r: Passage) => r.content.length,
    },
    {
      title: '操作', key: 'action', width: 220,
      render: (_: any, record: Passage) => (
        <Space size="small">
          <Button size="small" icon={<EyeOutlined />} onClick={() => startPractice(record, 'hide')}>
            遮字背诵
          </Button>
          <Button size="small" icon={<EditOutlined />} onClick={() => startPractice(record, 'dictation')}>
            默写
          </Button>
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  // ========== 主渲染 ==========

  // 如果正在背诵某个段落
  if (store.currentPassage) {
    return (
      <div>
        <Card
          title={
            <Space>
              <Button onClick={() => store.reset()}>← 返回列表</Button>
              <span>{store.currentPassage.title}</span>
              <Tag color={store.mode === 'hide' ? 'blue' : 'orange'}>
                {store.mode === 'hide' ? '📖 遮字背诵' : '✍️ 默写模式'}
              </Tag>
            </Space>
          }
        >
          {/* 遮字背诵模式 */}
          {store.mode === 'hide' && (
            <div>
              <div style={{ marginBottom: 16 }}>
                <span style={{ marginRight: 12, fontSize: 14 }}>隐藏等级：</span>
                <Slider
                  min={1} max={5} value={store.hideLevel}
                  onChange={v => store.setHideLevel(v)}
                  style={{ width: 300, display: 'inline-block' }}
                  marks={{ 1: '初级', 2: '二级', 3: '三级', 4: '四级', 5: '全隐' }}
                />
                <Button size="small" icon={<ReloadOutlined />} onClick={generateHidePlan} style={{ marginLeft: 12 }}>
                  重新生成
                </Button>
              </div>

              {store.hidePlan ? (
                <TextHider
                  segments={store.hidePlan.segments}
                  revealedIndices={store.hideRevealedSegments}
                  onToggleReveal={store.toggleHideSegment}
                  onRevealAll={store.revealAllHideSegments}
                  allRevealed={false}
                />
              ) : (
                <Spin tip="生成中..." />
              )}

              {/* 显示原文参考（折叠） */}
              <details style={{ marginTop: 20 }}>
                <summary style={{ cursor: 'pointer', color: '#999', fontSize: 13 }}>查看完整原文</summary>
                <div style={{ marginTop: 8, padding: 12, background: '#f5f5f5', borderRadius: 6, fontSize: 16, lineHeight: 2 }}>
                  {store.currentPassage.content}
                </div>
              </details>
            </div>
          )}

          {/* 默写模式 */}
          {store.mode === 'dictation' && (
            <div>
              {!store.dictationResult ? (
                <div>
                  <div style={{ marginBottom: 12, color: '#666', fontSize: 14 }}>
                    请在下方输入框中默写全文，完成后点击"检查"：
                  </div>
                  <Input.TextArea
                    value={store.dictationInput}
                    onChange={e => store.setDictationInput(e.target.value)}
                    placeholder="在此输入你的默写内容..."
                    autoSize={{ minRows: 8, maxRows: 20 }}
                    style={{ fontSize: 16, lineHeight: 2, letterSpacing: '0.05em' }}
                  />
                  <div style={{ marginTop: 16, display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: '#999', fontSize: 13 }}>
                      已输入 {store.dictationInput.length} 字（原文 {store.currentPassage.content.length} 字）
                    </span>
                    <Space>
                      <Button onClick={() => store.setDictationInput('')}>清空</Button>
                      <Button type="primary" loading={submitting} onClick={handleDictationSubmit}>
                        检查
                      </Button>
                    </Space>
                  </div>
                </div>
              ) : (
                <div>
                  <DictationCompare
                    diffs={store.dictationResult.diffs}
                    accuracy={store.dictationResult.accuracy}
                    correctChars={store.dictationResult.correct_chars}
                    missingChars={store.dictationResult.missing_chars}
                    extraChars={store.dictationResult.extra_chars}
                    originalLength={store.dictationResult.original_length}
                    inputLength={store.dictationResult.input_length}
                  />

                  {/* FSRS 复习评级 */}
                  <Card size="small" title="记忆评级" style={{ marginTop: 20 }}>
                    <div style={{ marginBottom: 8, color: '#666' }}>
                      根据你的记忆情况，选择本次评级以安排下次复习：
                    </div>
                    <Space size="middle">
                      <Button danger onClick={() => handleReview(store.dictationResult!.record_id, 1)}>
                        😫 忘了
                      </Button>
                      <Button style={{ borderColor: '#faad14', color: '#faad14' }} onClick={() => handleReview(store.dictationResult!.record_id, 2)}>
                        😕 有点难
                      </Button>
                      <Button type="primary" onClick={() => handleReview(store.dictationResult!.record_id, 3)}>
                        😊 记住了
                      </Button>
                      <Button style={{ background: '#52c41a', borderColor: '#52c41a', color: '#fff' }} onClick={() => handleReview(store.dictationResult!.record_id, 4)}>
                        🤩 很简单
                      </Button>
                    </Space>
                  </Card>

                  <div style={{ marginTop: 16 }}>
                    <Space>
                      <Button onClick={() => { store.setDictationResult(null); store.setDictationInput(''); startTimeRef.current = Date.now() }}>
                        再试一次
                      </Button>
                      <Button onClick={() => store.reset()}>返回列表</Button>
                    </Space>
                  </div>
                </div>
              )}
            </div>
          )}
        </Card>
      </div>
    )
  }

  // 主列表页
  return (
    <div>
      {/* 统计卡 */}
      {stats && (
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col xs={12} sm={6}>
            <Card size="small"><Statistic title="段落数" value={stats.total_passages} prefix={<BookOutlined />} /></Card>
          </Col>
          <Col xs={12} sm={6}>
            <Card size="small"><Statistic title="练习次数" value={stats.total_records} /></Card>
          </Col>
          <Col xs={12} sm={6}>
            <Card size="small">
              <Statistic
                title="平均准确率"
                value={Math.round(stats.avg_accuracy * 100)}
                suffix="%"
                valueStyle={{ color: stats.avg_accuracy >= 0.9 ? '#3f8600' : stats.avg_accuracy >= 0.6 ? '#faad14' : '#cf1322' }}
              />
            </Card>
          </Col>
          <Col xs={12} sm={6}>
            <Card size="small"><Statistic title="已掌握" value={stats.mastered} prefix={<TrophyOutlined />} valueStyle={{ color: '#3f8600' }} /></Card>
          </Col>
        </Row>
      )}

      <Card
        title="📖 背诵练习"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => { loadPassages(); loadStats(); loadDue() }}>刷新</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setAddOpen(true)}>
              新建段落
            </Button>
          </Space>
        }
      >
        <Tabs
          items={[
            {
              key: 'list',
              label: '全部段落',
              children: (
                <Table
                  dataSource={passages}
                  columns={columns}
                  rowKey="id"
                  loading={loading}
                  pagination={{ pageSize: 10 }}
                  locale={{ emptyText: <Empty description={'暂无段落，点击"新建段落"开始'} /> }}
                  size="middle"
                />
              ),
            },
            {
              key: 'due',
              label: `待复习 ${dueItems.length > 0 ? `(${dueItems.length})` : ''}`,
              children: (
                dueItems.length === 0 ? (
                  <Empty description="暂无需要复习的段落 🎉" />
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    {dueItems.map((item: any) => (
                      <Card key={item.record_id} size="small">
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <div>
                            <strong>{item.passage_title}</strong>
                            <Tag style={{ marginLeft: 8 }}>{item.mode === 'hide' ? '遮字' : '默写'}</Tag>
                            <span style={{ color: '#999', marginLeft: 8 }}>
                              上次准确率: {Math.round((item.accuracy || 0) * 100)}%
                            </span>
                          </div>
                          <Space>
                            <Button size="small" onClick={() => handleReview(item.record_id, 1)}>😫 忘了</Button>
                            <Button size="small" onClick={() => handleReview(item.record_id, 2)}>😕 难</Button>
                            <Button size="small" type="primary" onClick={() => handleReview(item.record_id, 3)}>😊 记住</Button>
                            <Button size="small" style={{ background: '#52c41a', borderColor: '#52c41a', color: '#fff' }} onClick={() => handleReview(item.record_id, 4)}>🤩 简单</Button>
                          </Space>
                        </div>
                      </Card>
                    ))}
                  </div>
                )
              ),
            },
          ]}
        />
      </Card>

      {/* 新建段落弹窗 */}
      <Modal
        title="新建背诵段落"
        open={addOpen}
        onOk={handleCreate}
        onCancel={() => setAddOpen(false)}
        okText="创建"
        width={600}
      >
        <Form form={addForm} layout="vertical">
          <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入标题' }]}>
            <Input placeholder="如：将进酒、岳阳楼记" />
          </Form.Item>
          <Form.Item name="content" label="原文内容" rules={[{ required: true, message: '请输入背诵内容' }]}>
            <Input.TextArea autoSize={{ minRows: 6 }} placeholder="粘贴要背诵的文本..." />
          </Form.Item>
          <Form.Item name="source" label="来源">
            <Input placeholder="如：唐诗三百首、高中语文必修一" />
          </Form.Item>
          <Space size="large">
            <Form.Item name="category" label="分类" initialValue="custom">
              <Select style={{ width: 120 }}>
                <Select.Option value="poetry">古诗词</Select.Option>
                <Select.Option value="textbook">课文</Select.Option>
                <Select.Option value="custom">自定义</Select.Option>
              </Select>
            </Form.Item>
            <Form.Item name="difficulty" label="难度" initialValue={1}>
              <Slider min={1} max={5} style={{ width: 160 }} marks={{ 1: '简', 3: '中', 5: '难' }} />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  )
}
