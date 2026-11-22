import { useState, useEffect, useRef } from 'react'
import { Card, Input, Button, List, Tag, Progress, Typography, Space, Spin, Empty, message, Collapse } from 'antd'
import { ExperimentOutlined, SearchOutlined, FileTextOutlined, LinkOutlined, ReloadOutlined } from '@ant-design/icons'
import { startResearch, getResearchStatus, listResearchTasks } from '../api'
import { materialPath } from '../materials'

const { Title, Paragraph, Text } = Typography
const { TextArea } = Input

interface SubTopic {
  title: string
  description?: string
  status: string
  finding_count?: number
}

interface ResearchTask {
  task_id: string
  topic: string
  refined_topic?: string
  status: string
  progress: number
  sub_topics: SubTopic[]
  report?: string
  sources?: { title: string; url: string; source: string; source_id?: number; doc_id?: string; chunk_id?: string; page_range?: string }[]
  error?: string
}

export default function ResearchPage() {
  const [topic, setTopic] = useState('')
  const [loading, setLoading] = useState(false)
  const [currentTask, setCurrentTask] = useState<ResearchTask | null>(null)
  const [tasks, setTasks] = useState<any[]>([])
  const [tasksLoading, setTasksLoading] = useState(false)
  const [pollTaskId, setPollTaskId] = useState<string | null>(null)
  const selection = useRef(0)

  useEffect(() => {
    loadTasks()
    return () => { selection.current++ }
  }, [])

  useEffect(() => {
    if (!pollTaskId) return
    const request = selection.current
    let stopped = false
    let busy = false
    const interval = setInterval(async () => {
      if (busy) return
      busy = true
      try {
        const status = await getResearchStatus(pollTaskId)
        if (stopped || request !== selection.current) return
        setCurrentTask(status)
        if (status.status === 'completed' || status.status === 'error') {
          setPollTaskId(null)
          loadTasks()
        }
      } catch {
        if (!stopped && request === selection.current) {
          setPollTaskId(null)
          message.error('获取任务状态失败，请重新打开任务')
        }
      } finally { busy = false }
    }, 2000)
    return () => { stopped = true; clearInterval(interval) }
  }, [pollTaskId])

  const loadTasks = async () => {
    setTasksLoading(true)
    try {
      const data = await listResearchTasks()
      setTasks(data.tasks || [])
    } catch { /* ignore */ }
    setTasksLoading(false)
  }

  const handleStart = async () => {
    if (loading) return
    if (!topic.trim()) return message.warning('请输入研究主题')
    const request = ++selection.current
    setPollTaskId(null)
    setLoading(true)
    try {
      const res = await startResearch({ topic: topic.trim() })
      if (request !== selection.current) return
      message.success('研究任务已启动')
      setTopic('')
      setCurrentTask({ ...res, sub_topics: [] })
      if (res.status !== 'completed' && res.status !== 'error') setPollTaskId(res.task_id)
    } catch (err: any) {
      if (request === selection.current) message.error('启动失败: ' + (err.message || '未知错误'))
    } finally { setLoading(false) }
  }

  const handleViewTask = async (taskId: string) => {
    const request = ++selection.current
    setPollTaskId(null)
    try {
      const status = await getResearchStatus(taskId)
      if (request !== selection.current) return
      setCurrentTask(status)
      if (status.status !== 'completed' && status.status !== 'error') setPollTaskId(taskId)
    } catch {
      if (request === selection.current) message.error('获取任务详情失败')
    }
  }

  const statusColors: Record<string, string> = {
    pending: 'default', rephrasing: 'processing', decomposing: 'processing',
    researching: 'processing', reporting: 'processing', completed: 'success', error: 'error',
  }
  const statusLabels: Record<string, string> = {
    pending: '等待中', rephrasing: '细化主题', decomposing: '分解子课题',
    researching: '研究中', reporting: '撰写报告', completed: '已完成', error: '失败',
  }

  return (
    <div style={{ maxWidth: 960, margin: '0 auto' }}>
      <Title level={3}><ExperimentOutlined /> 深度研究</Title>
      <Paragraph type="secondary">输入研究主题，AI 将自动分解子课题、多源搜索、生成带引用的研究报告。</Paragraph>

      {/* Input */}
      <Card style={{ marginBottom: 16 }}>
        <Space.Compact style={{ width: '100%' }}>
          <TextArea
            value={topic}
            onChange={e => setTopic(e.target.value)}
            placeholder="例如：大语言模型在教育领域的应用现状与未来趋势"
            autoSize={{ minRows: 1, maxRows: 3 }}
            onPressEnter={e => { if (!e.shiftKey) { e.preventDefault(); handleStart() } }}
            style={{ flex: 1 }}
          />
          <Button type="primary" icon={<SearchOutlined />} loading={loading} onClick={handleStart}
            style={{ height: 'auto' }}>
            开始研究
          </Button>
        </Space.Compact>
      </Card>

      {/* Current Task Progress */}
      {currentTask && (
        <Card
          title={<><FileTextOutlined /> {currentTask.refined_topic || currentTask.topic}</>}
          extra={<Tag color={statusColors[currentTask.status]}>{statusLabels[currentTask.status]}</Tag>}
          style={{ marginBottom: 16 }}
        >
          <Progress percent={Math.round(currentTask.progress * 100)} status={
            currentTask.status === 'error' ? 'exception' : currentTask.status === 'completed' ? 'success' : 'active'
          } />

          {/* Sub-topics */}
          {currentTask.sub_topics?.length > 0 && (
            <div style={{ margin: '16px 0' }}>
              <Text strong>子课题：</Text>
              <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {currentTask.sub_topics.map((st, i) => (
                  <Tag key={i} color={st.status === 'done' ? 'green' : st.status === 'researching' ? 'blue' : 'default'}>
                    {st.title}
                    {st.finding_count ? ` (${st.finding_count})` : ''}
                  </Tag>
                ))}
              </div>
            </div>
          )}

          {/* Error */}
          {currentTask.error && (
            <div style={{ color: '#ff4d4f', marginTop: 12 }}>
              错误: {currentTask.error}
            </div>
          )}

          {/* Report */}
          {currentTask.report && (
            <Collapse defaultActiveKey={['report']} style={{ marginTop: 16 }}
              items={[{
                key: 'report',
                label: <Text strong>研究报告</Text>,
                children: (
                  <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.8, maxHeight: 600, overflow: 'auto' }}>
                    {currentTask.report}
                  </div>
                ),
              }]}
            />
          )}

          {/* Sources */}
          {currentTask.sources && currentTask.sources.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <Text strong>参考来源：</Text>
              <List
                size="small"
                dataSource={currentTask.sources}
                renderItem={(s: any, index: number) => (
                  <List.Item>
                    <LinkOutlined style={{ marginRight: 8 }} />
                    <Tag>来源{s.source_id || index + 1}</Tag>
                    {s.doc_id
                      ? <a href={materialPath(s.doc_id)}>{s.title || s.doc_id}</a>
                      : <a href={s.url} target="_blank" rel="noopener noreferrer">{s.title || s.url}</a>}
                    {s.page_range && <Tag style={{ marginLeft: 8 }}>{s.page_range}</Tag>}
                    <Tag style={{ marginLeft: 8 }} color="blue">{s.source}</Tag>
                  </List.Item>
                )}
              />
            </div>
          )}
        </Card>
      )}

      {/* History */}
      <Card title="研究历史" extra={<Button icon={<ReloadOutlined />} size="small" onClick={loadTasks} loading={tasksLoading} />}>
        {tasks.length === 0 ? (
          <Empty description="暂无研究记录" />
        ) : (
          <List
            dataSource={tasks}
            renderItem={(t: any) => (
              <List.Item
                actions={[<Button type="link" onClick={() => handleViewTask(t.task_id)}>查看</Button>]}
              >
                <List.Item.Meta
                  title={t.refined_topic || t.topic}
                  description={
                    <Space>
                      <Tag color={statusColors[t.status]}>{statusLabels[t.status]}</Tag>
                      <Text type="secondary">{t.sub_topic_count} 个子课题 · {t.source_count} 个来源</Text>
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Card>
    </div>
  )
}
