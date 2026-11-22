import { createElement, useState, useEffect, useMemo, useRef, useId } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Layout, Card, Button, Spin, App, Tabs, Space, Tag, Typography, Progress, List, Statistic, Row, Col, Empty } from 'antd'
import {
  ArrowLeftOutlined, ThunderboltOutlined, FileTextOutlined, FormOutlined,
  ApartmentOutlined, IdcardOutlined, ScheduleOutlined, ReadOutlined,
  PieChartOutlined, LoadingOutlined, CheckCircleOutlined, CloseCircleOutlined,
  RocketOutlined,
} from '@ant-design/icons'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { isCancel } from 'axios'
import {
  getSummary, getSummaryNodes, getSummaryMindmap, orchestrateAgents,
  getCoverageReport, getReviewQueue, getMemoryStats, startInteractiveQuiz,
} from '../api'
import type { MindMapData, CoverageReport, ReviewQueueItem, MemoryStats, Question } from '../types'
import MarkdownTOC from '../components/MarkdownTOC'
import { headingId, parseMarkdownHeadings } from '../components/markdownHeadings'
import ReviewQueuePanel from '../components/ReviewQueuePanel'
import LoadFailure from '../components/LoadFailure'

const { Sider, Content } = Layout
const { Title, Text } = Typography
type AgentProgress = 'pending' | 'running' | 'done' | 'error' | 'skipped'

const AGENTS = ['question_bank', 'mindmap', 'study_plan', 'language']
type Auxiliary = 'coverage' | 'quiz' | 'mindmap' | 'memory'
type AuxiliaryState = { loading: boolean; loaded: boolean; error: unknown | null }
const emptyAuxiliary = (): Record<Auxiliary, AuxiliaryState> => ({
  coverage: { loading: false, loaded: false, error: null }, quiz: { loading: false, loaded: false, error: null },
  mindmap: { loading: false, loaded: false, error: null }, memory: { loading: false, loaded: false, error: null },
})
const AGENT_LABELS: Record<string, string> = {
  question_bank: '题库生成',
  mindmap: '思维导图',
  study_plan: '学习计划',
  language: '生词表',
}
const AGENT_TAB_MAP: Record<string, string> = {
  question_bank: 'quiz',
  mindmap: 'mindmap',
  study_plan: 'plan',
  language: 'vocab',
}

export default function SummaryPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { message } = App.useApp()

  const [summary, setSummary] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<unknown | null>(null)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [activeTab, setActiveTab] = useState('summary')
  const headingPrefix = `summary-${useId()}`
  const bodyRef = useRef<HTMLDivElement>(null)
  const content = summary?.content_md || ''
  const headings = useMemo(() => parseMarkdownHeadings(content, headingPrefix), [content, headingPrefix])
  const [headingSelection, setHeadingSelection] = useState<{ id: string; content: string } | null>(null)
  const headingComponents = useMemo(() => {
    const components: Components = {}
    for (const tag of ['h1', 'h2', 'h3', 'h4', 'h5', 'h6'] as const) {
      components[tag] = ({ node, children, ...props }) => createElement(tag, {
        ...props, id: node?.position?.start.offset === undefined ? undefined : headingId(headingPrefix, node.position.start.offset),
        tabIndex: -1,
      }, children)
    }
    return components
  }, [headingPrefix])

  useEffect(() => {
    if (!headingSelection || headingSelection.content !== content || activeTab !== 'summary') return
    // rc-tabs reveals its retained panel through native CSSMotion after this commit.
    const frame = requestAnimationFrame(() => {
      const target = Array.from(bodyRef.current?.querySelectorAll<HTMLElement>('h1,h2,h3,h4,h5,h6') || [])
        .find(el => el.id === headingSelection.id)
      target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      target?.focus({ preventScroll: true })
    })
    return () => cancelAnimationFrame(frame)
  }, [headingSelection, content, activeTab])

  // Agent orchestration state
  const [agentResults, setAgentResults] = useState<Record<string, any>>({})
  const [agentProgress, setAgentProgress] = useState<Record<string, AgentProgress>>({})
  const [orchestrating, setOrchestrating] = useState(false)
  const [orchestrateDone, setOrchestrateDone] = useState(false)

  // Preloaded data for tabs
  const [mindmapData, setMindmapData] = useState<MindMapData | null>(null)
  const [coverageReport, setCoverageReport] = useState<CoverageReport | null>(null)
  const [previewQuestions, setPreviewQuestions] = useState<Question[]>([])
  const [memoryStats, setMemoryStats] = useState<MemoryStats | null>(null)
  const [auxiliary, setAuxiliary] = useState(emptyAuxiliary)
  const auxiliaryRevision = useRef({ quiz: 0, mindmap: 0, memory: 0 })
  const requestScope = useRef<{ id: string; controller: AbortController; generating: boolean; coverageRevision: number; pending: Record<Auxiliary, boolean> } | null>(null)

  const loadAuxiliary = async (key: Auxiliary, scope = requestScope.current, replace = false) => {
    if (!scope || scope.controller.signal.aborted || (scope.pending[key] && !replace)) return
    scope.pending[key] = true
    const revision = key === 'coverage' ? ++scope.coverageRevision : ++auxiliaryRevision.current[key]
    const current = () => requestScope.current === scope && !scope.controller.signal.aborted
      && revision === (key === 'coverage' ? scope.coverageRevision : auxiliaryRevision.current[key])
    setAuxiliary(prev => ({ ...prev, [key]: { ...prev[key], loading: true, error: null } }))
    const config = { signal: scope.controller.signal }
    try {
      const data = key === 'coverage' ? await getCoverageReport(scope.id, config)
        : key === 'quiz' ? await startInteractiveQuiz({ summary_id: scope.id, count: 5 }, config)
        : key === 'mindmap' ? await getSummaryMindmap(scope.id, config) : await getMemoryStats(config)
      if (!current()) return
      if (key === 'coverage') setCoverageReport(data)
      else if (key === 'quiz') setPreviewQuestions(data?.questions || [])
      else if (key === 'mindmap') setMindmapData(data)
      else setMemoryStats(data)
      setAuxiliary(prev => ({ ...prev, [key]: { loading: false, loaded: true, error: null } }))
    } catch (error) {
      if (current() && !isCancel(error)) setAuxiliary(prev => ({ ...prev, [key]: { ...prev[key], loading: false, error } }))
    } finally {
      if (current()) scope.pending[key] = false
    }
  }

  useEffect(() => {
    setSummary(null); setCoverageReport(null); setMemoryStats(null); setMindmapData(null)
    setLoadError(null); setAuxiliary(emptyAuxiliary())
    setPreviewQuestions([]); setAgentResults({}); setAgentProgress({})
    setOrchestrating(false); setOrchestrateDone(false); setHeadingSelection(null); setActiveTab('summary')
    if (!id) { setLoading(false); return }
    const scope = { id, controller: new AbortController(), generating: false, coverageRevision: 0, pending: { coverage: false, quiz: false, mindmap: false, memory: false } }
    requestScope.current = scope
    const current = () => requestScope.current === scope && !scope.controller.signal.aborted
    const config = { signal: scope.controller.signal }
    setLoading(true)
    void (async () => {
      try {
        const data = await getSummary(id, config)
        if (!current()) return
        setSummary(data)
        // Auxiliary reads do not delay usable source content. A generation owns
        // newer coverage, so a slow pre-generation read cannot replace it.
        void loadAuxiliary('coverage', scope)
        void loadAuxiliary('memory', scope)
      } catch (e: any) {
        if (current() && !isCancel(e)) setLoadError(e)
      } finally {
        if (current()) setLoading(false)
      }
    })()
    return () => {
      scope.controller.abort()
      if (requestScope.current === scope) requestScope.current = null
    }
  }, [id, loadAttempt])

  const handleOrchestrate = async () => {
    const scope = requestScope.current
    if (!id || !summary || !scope || scope.id !== id || scope.controller.signal.aborted || scope.generating) return
    const current = () => requestScope.current === scope && !scope.controller.signal.aborted
    const config = { signal: scope.controller.signal }
    const docIds = summary.document_ids || []
    if (docIds.length === 0) {
      message.warning('缺少文档关联信息')
      return
    }

    scope.generating = true
    setOrchestrating(true)
    setOrchestrateDone(false)
    setAgentResults({})
    const initialProgress: Record<string, AgentProgress> = {}
    AGENTS.forEach(a => { initialProgress[a] = 'pending' })
    setAgentProgress(initialProgress)

    try {
      const result = await orchestrateAgents({
        summary_id: id,
        document_id: docIds[0],
        config: { question_count: 30 },
      }, config)
      if (!current()) return

      // Process results
      const newResults: Record<string, any> = {}
      const newProgress: Record<string, AgentProgress> = {}
      for (const [name, r] of Object.entries(result.results || {}) as [string, any][]) {
        newResults[name] = r
        if (r.status === 'success') newProgress[name] = 'done'
        else if (r.status === 'skipped') newProgress[name] = 'skipped'
        else newProgress[name] = 'error'
      }
      setAgentResults(newResults)
      setAgentProgress(newProgress)
      setOrchestrateDone(true)

      // Auto-load coverage report after orchestration
      await loadAuxiliary('coverage', scope, true)
      if (!current()) return

      // Auto-load mindmap data
      if (newResults.mindmap?.status === 'success') {
        await loadAuxiliary('mindmap', scope)
      }
      if (!current()) return

      // Load preview questions
      if (newResults.question_bank?.status === 'success') {
        await loadAuxiliary('quiz', scope)
      }
      if (!current()) return

      const successCount = Object.values(newProgress).filter(p => p === 'done').length
      const failedCount = Object.values(newProgress).filter(p => p === 'error').length
      const skippedCount = Object.values(newProgress).filter(p => p === 'skipped').length
      if (failedCount > 0) {
        if (successCount > 0) message.warning(`配套内容部分生成：成功 ${successCount} 项，失败 ${failedCount} 项`)
        else message.error('配套内容生成失败')
      } else if (successCount > 0) {
        message.success(`配套内容生成完成：成功 ${successCount} 项，跳过 ${skippedCount} 项`)
      } else if (skippedCount > 0) {
        message.info(`配套内容已跳过：${skippedCount} 项`)
      } else {
        message.warning('未返回生成结果，请重试')
      }
    } catch (e: any) {
      if (current() && !isCancel(e)) message.error('生成失败: ' + (e.response?.data?.detail || e.message))
    } finally {
      scope.generating = false
      if (current()) setOrchestrating(false)
    }
  }

  if (loading) return <div style={{ textAlign: 'center', padding: 100 }}><Spin size="large" /><div style={{ marginTop: 16, color: '#999' }}>加载知识总纲...</div></div>
  if (loadError) return <LoadFailure title="知识总纲加载失败" error={loadError} onRetry={() => setLoadAttempt(n => n + 1)} onBack={() => navigate('/materials')} />
  if (!summary) return <div style={{ textAlign: 'center', padding: 100 }}>知识总纲未找到</div>

  let planDescription = '尚未生成学习计划'
  if (agentResults.study_plan?.status === 'error') {
    planDescription = `学习计划生成失败：${agentResults.study_plan.error || '请重试'}`
  } else if (agentResults.study_plan?.status === 'skipped') {
    planDescription = '已跳过学习计划生成'
  }

  const tabItems = [
    {
      key: 'summary',
      label: <span><FileTextOutlined /> 知识总纲</span>,
      children: (
        <Card>
          <div ref={bodyRef} className="markdown-body" style={{ maxWidth: 900, margin: '0 auto' }}>
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={headingComponents}>
              {summary.content_md}
            </ReactMarkdown>
          </div>
        </Card>
      ),
    },
    {
      key: 'quiz',
      label: <span><FormOutlined /> 题库 ({previewQuestions.length || '?'})</span>,
      children: (
        <Card
          title="题库概览"
          extra={
            <Button type="link" onClick={() => navigate(`/quiz/interactive/${id}`)}>
              进入答题 →
            </Button>
          }
        >
          {auxiliary.quiz.error != null && <LoadFailure inline title="题库预览加载失败" error={auxiliary.quiz.error} retryLabel="重新加载题库预览" onRetry={() => { void loadAuxiliary('quiz') }} pending={auxiliary.quiz.loading} />}
          {auxiliary.quiz.loading && <Spin><div style={{ padding: 16 }}>正在加载题库预览...</div></Spin>}
          {previewQuestions.length > 0 ? (
            <List
              dataSource={previewQuestions.slice(0, 10)}
              renderItem={(q: any) => (
                <List.Item>
                  <List.Item.Meta
                    title={q.question_text?.slice(0, 80) + (q.question_text?.length > 80 ? '...' : '')}
                    description={
                      <Space>
                        <Tag>{q.question_type === 'single_choice' ? '单选题' : q.question_type}</Tag>
                        <Tag color={q.difficulty === 'hard' ? 'red' : q.difficulty === 'easy' ? 'green' : 'blue'}>{q.difficulty}</Tag>
                      </Space>
                    }
                  />
                </List.Item>
              )}
            />
          ) : !auxiliary.quiz.loading && auxiliary.quiz.error == null ? (
            <Empty description={auxiliary.quiz.loaded ? '暂无可用题目' : '尚未生成题目'}>
              {!orchestrateDone && (
                <Button type="primary" onClick={handleOrchestrate}>一键生成全部配套</Button>
              )}
            </Empty>
          ) : null}
        </Card>
      ),
    },
    {
      key: 'mindmap',
      label: <span><ApartmentOutlined /> 思维导图</span>,
      children: (
        <Card
          title="思维导图概览"
          extra={
            <Button type="link" onClick={() => navigate(`/mindmap/${id}`)}>
              查看完整导图 →
            </Button>
          }
        >
          {auxiliary.mindmap.error != null && <LoadFailure inline title="思维导图预览加载失败" error={auxiliary.mindmap.error} retryLabel="重新加载思维导图预览" onRetry={() => { void loadAuxiliary('mindmap') }} pending={auxiliary.mindmap.loading} />}
          {auxiliary.mindmap.loading && <Spin><div style={{ padding: 16 }}>正在加载思维导图预览...</div></Spin>}
          {mindmapData ? (
            <div style={{ textAlign: 'center', padding: 24 }}>
              <Row gutter={16}>
                <Col span={8}><Statistic title="节点数" value={mindmapData.nodes?.length || 0} /></Col>
                <Col span={8}><Statistic title="连线数" value={mindmapData.edges?.length || 0} /></Col>
                <Col span={8}>
                  <Statistic
                    title="BOIS 评分"
                    value={mindmapData.bois_metrics?.score || 0}
                    suffix="分"
                    valueStyle={{ color: (mindmapData.bois_metrics?.score || 0) >= 75 ? '#52c41a' : '#faad14' }}
                  />
                </Col>
              </Row>
              <Button style={{ marginTop: 16 }} onClick={() => navigate(`/mindmap/${id}`)}>
                打开思维导图
              </Button>
            </div>
          ) : !auxiliary.mindmap.loading && auxiliary.mindmap.error == null ? (
            <Empty description={auxiliary.mindmap.loaded ? '暂无思维导图' : '尚未生成思维导图'}>
              {!orchestrateDone && (
                <Button type="primary" onClick={handleOrchestrate}>一键生成全部配套</Button>
              )}
            </Empty>
          ) : null}
        </Card>
      ),
    },
    {
      key: 'flashcards',
      label: <span><IdcardOutlined /> 记忆卡</span>,
      children: (
        <Card
          title="记忆卡概览"
          extra={
            <Button type="link" onClick={() => navigate('/flashcards')}>
              进入闪卡复习 →
            </Button>
          }
        >
          {auxiliary.memory.error != null && <LoadFailure inline title="记忆卡统计加载失败" error={auxiliary.memory.error} retryLabel="重新加载记忆卡统计" onRetry={() => { void loadAuxiliary('memory') }} pending={auxiliary.memory.loading} />}
          {auxiliary.memory.loading && <Spin><div style={{ padding: 16 }}>正在加载记忆卡统计...</div></Spin>}
          {memoryStats ? (
            <div style={{ textAlign: 'center', padding: 24 }}>
              <Row gutter={16}>
                <Col span={6}><Statistic title="总卡片数" value={memoryStats.total_cards} /></Col>
                <Col span={6}><Statistic title="待复习" value={memoryStats.due_today} valueStyle={{ color: '#faad14' }} /></Col>
                <Col span={6}><Statistic title="平均正确率" value={memoryStats.average_accuracy * 100} suffix="%" precision={1} /></Col>
                <Col span={6}><Statistic title="复习队列" value={memoryStats.review_queue_count} valueStyle={{ color: '#ff4d4f' }} /></Col>
              </Row>
            </div>
          ) : !auxiliary.memory.loading && auxiliary.memory.error == null ? <Empty description="暂无记忆卡统计" /> : null}
        </Card>
      ),
    },
    {
      key: 'plan',
      label: <span><ScheduleOutlined /> 学习计划</span>,
      children: (
        <Card
          title="学习计划"
          extra={
            <Button type="link" onClick={() => navigate('/study')}>
              管理学习计划 →
            </Button>
          }
        >
          {agentResults.study_plan?.status === 'success' ? (
            <div style={{ padding: 16 }}>
              <Text strong>计划名称: </Text>
              <Text>{agentResults.study_plan.result?.name || '学习计划'}</Text>
              <br /><br />
              {agentResults.study_plan.result?.ebbinghaus_nodes && (
                <div>
                  <Text strong>艾宾浩斯复习节点: </Text>
                  <Space wrap>
                    {agentResults.study_plan.result.ebbinghaus_nodes.map((n: any) => (
                      <Tag key={n.day} color="blue">第{n.day}天复习</Tag>
                    ))}
                  </Space>
                </div>
              )}
            </div>
          ) : (
            <Empty description={planDescription}>
              {!orchestrateDone && (
                <Button type="primary" onClick={handleOrchestrate}>一键生成全部配套</Button>
              )}
            </Empty>
          )}
        </Card>
      ),
    },
    {
      key: 'vocab',
      label: <span><ReadOutlined /> 生词表</span>,
      children: (
        <Card
          title="生词表"
          extra={
            <Button type="link" onClick={() => navigate(`/language?docId=${summary.document_ids?.[0] || ''}`)}>
              查看完整生词表 →
            </Button>
          }
        >
          {agentResults.language?.status === 'success' ? (
            <div style={{ textAlign: 'center', padding: 24 }}>
              <Statistic title="提取生词数" value={agentResults.language.result?.total_words || 0} />
            </div>
          ) : agentResults.language?.status === 'skipped' ? (
            <Empty description="非语言类材料，已跳过生词提取" />
          ) : (
            <Empty description="尚未提取生词">
              {!orchestrateDone && (
                <Button type="primary" onClick={handleOrchestrate}>一键生成全部配套</Button>
              )}
            </Empty>
          )}
        </Card>
      ),
    },
    {
      key: 'coverage',
      label: <span><PieChartOutlined /> 覆盖率</span>,
      children: (
        <Card
          title="覆盖率报告"
          extra={
            <Button type="link" onClick={() => navigate(`/coverage/${id}`)}>
              查看详细报告 →
            </Button>
          }
        >
          {auxiliary.coverage.error != null && <LoadFailure inline title="覆盖率加载失败" error={auxiliary.coverage.error} retryLabel="重新加载覆盖率" onRetry={() => { void loadAuxiliary('coverage') }} pending={auxiliary.coverage.loading} />}
          {auxiliary.coverage.loading && <Spin><div style={{ padding: 16 }}>正在加载覆盖率...</div></Spin>}
          {coverageReport ? (
            <div style={{ textAlign: 'center', padding: 24 }}>
              <Row gutter={16}>
                <Col span={6}>
                  <Progress type="circle" percent={Math.round(coverageReport.full_coverage_rate * 100)} size={80} />
                  <div style={{ marginTop: 8 }}><Text>全覆盖率</Text></div>
                </Col>
                <Col span={6}><Statistic title="知识点总数" value={coverageReport.total_knowledge_points} /></Col>
                <Col span={6}><Statistic title="题目覆盖" value={coverageReport.covered_by_questions} /></Col>
                <Col span={6}><Statistic title="记忆卡覆盖" value={coverageReport.covered_by_flashcards} /></Col>
              </Row>
              {coverageReport.uncovered_points.length > 0 && (
                <Tag color="warning" style={{ marginTop: 16 }}>
                  还有 {coverageReport.uncovered_points.length} 个知识点未覆盖
                </Tag>
              )}
            </div>
          ) : !auxiliary.coverage.loading && auxiliary.coverage.error == null ? <Empty description="暂无覆盖率报告" /> : null}
        </Card>
      ),
    },
  ]

  // Agent progress indicators
  const agentStatusTags = AGENTS.map(name => {
    const status = agentProgress[name]
    if (!status || status === 'pending') return <Tag key={name}>{AGENT_LABELS[name]}: 等待中</Tag>
    if (status === 'running') return <Tag key={name} color="processing" icon={<LoadingOutlined />}>{AGENT_LABELS[name]}: 生成中</Tag>
    if (status === 'done') return <Tag key={name} color="success" icon={<CheckCircleOutlined />}>{AGENT_LABELS[name]}: 完成</Tag>
    if (status === 'skipped') return <Tag key={name}>{AGENT_LABELS[name]}: 已跳过</Tag>
    return <Tag key={name} color="error" icon={<CloseCircleOutlined />}>{AGENT_LABELS[name]}: 失败</Tag>
  })

  return (
    <Layout style={{ minHeight: '100%', background: 'transparent' }}>
      {/* Left Sidebar */}
      <Sider width={260} style={{ background: 'transparent', paddingRight: 16 }}>
        <Card size="small" title="知识总纲目录" style={{ position: 'sticky', top: 0 }}>
          <MarkdownTOC headings={headings}
            selectedId={headingSelection && headingSelection.content === content ? headingSelection.id : undefined}
            onSelect={id => { setHeadingSelection({ id, content }); setActiveTab('summary') }} />
        </Card>
        <Card size="small" title="统计" style={{ marginTop: 12 }}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <Text>知识点总数: <Text strong>{summary.node_count}</Text></Text>
            {summary.level_stats && Object.entries(summary.level_stats).map(([k, v]) => (
              <Tag key={k}>{k}: {v as number}个</Tag>
            ))}
            {summary.document_ids?.length > 0 && (
              <Text type="secondary">来源: {summary.document_ids.length} 个文档</Text>
            )}
          </Space>
        </Card>
        <Card size="small" title="快捷操作" style={{ marginTop: 12 }}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <Button
              block
              icon={<RocketOutlined />}
              type="primary"
              onClick={handleOrchestrate}
              loading={orchestrating}
              disabled={orchestrating}
            >
              {orchestrateDone ? '重新生成全部配套' : '一键生成全部配套'}
            </Button>
            <Button block icon={<FormOutlined />} onClick={() => navigate(`/quiz/interactive/${id}`)}>
              开始交互答题
            </Button>
            <Button block icon={<ApartmentOutlined />} onClick={() => navigate(`/mindmap/${id}`)}>
              查看思维导图
            </Button>
            <Button block icon={<IdcardOutlined />} onClick={() => navigate('/flashcards')}>
              复习记忆卡
            </Button>
            <Button block icon={<PieChartOutlined />} onClick={() => navigate(`/coverage/${id}`)}>
              覆盖率报告
            </Button>
            <Button block icon={<ScheduleOutlined />} onClick={() => navigate('/study')}>
              学习计划
            </Button>
          </Space>
        </Card>
        <Card size="small" title="复习队列" style={{ marginTop: 12 }}>
          <ReviewQueuePanel />
        </Card>
      </Sider>

      {/* Main Content */}
      <Content style={{ paddingLeft: 24 }}>
        <div style={{ marginBottom: 16 }}>
          <Space>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/upload')} type="text">
              返回资料库
            </Button>
          </Space>
          <Title level={3} style={{ marginTop: 8 }}>
            <RocketOutlined style={{ marginRight: 8, color: '#4f46e5' }} />
            知识中枢
          </Title>
          <div style={{ marginBottom: 12 }}>
            <Space wrap>{agentStatusTags}</Space>
          </div>
        </div>

        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={tabItems}
          size="large"
        />
      </Content>
    </Layout>
  )
}
