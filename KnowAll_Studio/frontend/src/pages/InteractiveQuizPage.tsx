import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Card, Button, Spin, App, Progress, Space, Typography, Tag, Result, Drawer, Tooltip } from 'antd'
import { ArrowLeftOutlined, OrderedListOutlined, SyncOutlined } from '@ant-design/icons'
import { startInteractiveQuiz, submitInteractiveAnswer, getInteractiveSessionStats } from '../api'
import { COGNITIVE_LEVEL_LABELS, COGNITIVE_LEVEL_COLORS, type CognitiveLevel, type Question } from '../types'
import InteractiveQuestionCard from '../components/InteractiveQuestionCard'
import LearningContent from '../components/LearningContent'
import { isCancel } from 'axios'
import LoadFailure from '../components/LoadFailure'
import { useAuthStore } from '../stores'

const { Title, Text } = Typography

// --- Completion thresholds & motivational copy (same spirit as the single-page quiz) ---
function completionCopy(pct: number, done: number, correct: number, total: number) {
  if (done === 0) return { title: '征程待启', msg: '做完第 1 题立个旗' }
  if (pct < 10) return { title: '🚀 发动机点火', msg: `已冲过 ${done} 题，前 10% 就是最难的一关` }
  if (pct < 40) return { title: '🏃 渐入佳境', msg: `完成 ${done} 题，正确率 ${correct}/${done}——手感来了` }
  if (pct < 75) return { title: '💪 过半在望', msg: `已刷 ${done} 题，还剩 ${total - done} 题到终点` }
  if (pct < 100) return { title: '🥇 冲刺阶段', msg: `只剩 ${total - done} 题，胜利触手可及` }
  return { title: '🏆 全员通关', msg: `${total} 题全部刷完，你已经是分子生物学大佬了！` }
}

function diffLabel(v: number) {
  if (v <= 0.25) return '很简单'; if (v <= 0.45) return '偏简单'
  if (v <= 0.60) return '中等'; if (v <= 0.80) return '偏困难'; return '困难'
}

export default function InteractiveQuizPage() {
  const { summaryId } = useParams<{ summaryId: string }>()
  const accountId = useAuthStore(state => state.user?.id ?? null)
  const navigate = useNavigate()
  const { message } = App.useApp()
  const [sessionId, setSessionId] = useState('')
  const [questions, setQuestions] = useState<Question[]>([])
  const [currentIndex, setCurrentIndex] = useState(0)
  const [results, setResults] = useState<Record<string, any>>({})
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [completed, setCompleted] = useState(false)
  const [loadError, setLoadError] = useState<unknown | null>(null)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [sheetOpen, setSheetOpen] = useState(false)
  const autoAdvanceRef = useRef<ReturnType<typeof setTimeout>>()
  const activeRequestRef = useRef<AbortController | null>(null)
  const inFlightRef = useRef<object | null>(null)
  const currentIndexRef = useRef(currentIndex)
  const contextRef = useRef({ summaryId, accountId })
  const loadedContextRef = useRef({ summaryId, accountId })
  currentIndexRef.current = currentIndex
  contextRef.current = { summaryId, accountId }
  const isCurrentRequest = useCallback((controller: AbortController, summary: typeof summaryId, account: typeof accountId) =>
    activeRequestRef.current === controller && !controller.signal.aborted &&
    contextRef.current.summaryId === summary && contextRef.current.accountId === account &&
    (useAuthStore.getState().user?.id ?? null) === account, [])

  useEffect(() => {
    if (!summaryId) { setLoading(false); return }
    const controller = new AbortController()
    activeRequestRef.current = controller
    inFlightRef.current = null; setSubmitting(false)
    setLoading(true); setLoadError(null)
    const oldContext = loadedContextRef.current
    if (oldContext.summaryId !== summaryId || oldContext.accountId !== accountId) {
      setSessionId(''); setQuestions([]); setResults({}); setCurrentIndex(0)
      setCompleted(false); setSheetOpen(false)
    }
    loadedContextRef.current = { summaryId, accountId }
    // A manual restart does not erase previous answers unless the new session loads.
    if (autoAdvanceRef.current) clearTimeout(autoAdvanceRef.current)
    void startInteractiveQuiz({ summary_id: summaryId, count: 20 }, { signal: controller.signal }).then(data => {
      if (!isCurrentRequest(controller, summaryId, accountId)) return
      setResults({}); setCurrentIndex(0); setCompleted(false)
      setSessionId(data?.session_id || ''); setQuestions(data?.questions || [])
    }).catch(error => {
      if (isCurrentRequest(controller, summaryId, accountId) && !isCancel(error)) setLoadError(error)
    }).finally(() => { if (isCurrentRequest(controller, summaryId, accountId)) setLoading(false) })
    return () => {
      controller.abort()
      if (autoAdvanceRef.current) clearTimeout(autoAdvanceRef.current)
      if (activeRequestRef.current === controller) { activeRequestRef.current = null; inFlightRef.current = null }
    }
  }, [summaryId, loadAttempt, accountId, isCurrentRequest])
  const startQuiz = () => setLoadAttempt(n => n + 1)

  const submitAnswer = useCallback(async (answer: string, timeMs: number) => {
    const q = questions[currentIndex]
    const controller = activeRequestRef.current
    if (!sessionId || !q || !controller || !isCurrentRequest(controller, summaryId, accountId)) return
    // The ref also guards clicks in the same turn, before React renders submitting.
    if (results[q.id] || inFlightRef.current) return
    const flight = {}; inFlightRef.current = flight
    if (autoAdvanceRef.current) clearTimeout(autoAdvanceRef.current)
    setSubmitting(true)
    try {
      let result, savedAnswer = answer, savedTimeMs = timeMs
      try {
        result = await submitInteractiveAnswer({
          question_id: q.id, user_answer: answer, time_spent_ms: timeMs, session_id: sessionId,
        }, { signal: controller.signal })
      } catch (error: any) {
        if (!isCurrentRequest(controller, summaryId, accountId) || isCancel(error)) return
        const status = error.response?.status
        if (status != null && status !== 409 && status < 500) throw error
        // A lost response does not imply that the write failed. Read the owned
        // session before permitting a manual retry; never repeat POST here.
        try {
          const stats = await getInteractiveSessionStats(sessionId, { signal: controller.signal })
          if (!isCurrentRequest(controller, summaryId, accountId)) return
          const saved = Array.isArray(stats?.questions_detail)
            ? stats.questions_detail.find((a: any) => a?.question_id === q.id) : undefined
          if (!saved || typeof saved.is_correct !== 'boolean' || typeof saved.user_answer !== 'string' ||
            typeof saved.correct_answer !== 'string' || (saved.analysis != null && typeof saved.analysis !== 'string') ||
            !Number.isFinite(saved.time_spent_ms) || saved.time_spent_ms < 0) throw error
          result = { ...saved, stats }
          savedAnswer = saved.user_answer; savedTimeMs = saved.time_spent_ms
          message.info('已恢复之前提交的评分')
        } catch { throw error }
      }
      if (!isCurrentRequest(controller, summaryId, accountId)) return
      const entry = { ...result, question: q, user_answer: savedAnswer, time_spent_ms: savedTimeMs }
      setResults(prev => ({ ...prev, [q.id]: entry }))
      const next = currentIndex + 1
      if (currentIndexRef.current === currentIndex) {
        autoAdvanceRef.current = setTimeout(() => {
          if (!isCurrentRequest(controller, summaryId, accountId) || currentIndexRef.current !== currentIndex) return
          if (next < questions.length) setCurrentIndex(next)
          else setCompleted(true)
        }, 1500)
      }
    } catch (e: any) {
      if (isCurrentRequest(controller, summaryId, accountId) && !isCancel(e)) message.error('提交失败: ' + (e.response?.data?.detail || e.message))
    } finally {
      if (inFlightRef.current === flight) {
        inFlightRef.current = null
        if (isCurrentRequest(controller, summaryId, accountId)) setSubmitting(false)
      }
    }
  }, [sessionId, questions, currentIndex, results, message, summaryId, accountId, isCurrentRequest])

  const total = questions.length
  const answered = Object.keys(results).length
  const correct = Object.values(results).filter((r: any) => r.is_correct).length
  const accuracy = answered > 0 ? (correct / answered * 100).toFixed(1) : '0'
  const pct = total > 0 ? Math.round(answered / total * 100) : 0
  const copy = completionCopy(pct, answered, correct, total)

  // Group questions by their first knowledge point for the answer-sheet quick nav
  const grouped: { kp: string; items: number[] }[] = []
  const kpIndex: Record<string, number> = {}
  questions.forEach((q, i) => {
    const kp = q.knowledge_points?.[0]?.title || '未关联知识点'
    if (!(kp in kpIndex)) { kpIndex[kp] = grouped.length; grouped.push({ kp, items: [] }) }
    grouped[kpIndex[kp]].items.push(i)
  })

  if (loading) return <div style={{ textAlign: 'center', padding: 100 }}><Spin size="large" /></div>
  if (loadError && questions.length === 0) return <LoadFailure title="题目加载失败" error={loadError} onRetry={startQuiz} onBack={() => navigate('/materials')} />

  if (completed) {
    return (
      <div style={{ maxWidth: 640, margin: '40px auto' }}>
        {loadError != null && <LoadFailure inline title="题目加载失败" error={loadError} onRetry={startQuiz} />}
        <Result
          status={correct / Math.max(1, answered) > 0.6 ? 'success' : 'info'}
          title="答题完成！"
          subTitle={`正确率: ${accuracy}% (${correct}/${answered})`}
          extra={[
            <Button key="retry" type="primary" icon={<SyncOutlined />} onClick={startQuiz}>重新开始</Button>,
            <Button key="back" onClick={() => navigate(-1)}>返回</Button>,
          ]}
        />
        {/* Wrong-question recap + retry, grouped by knowledge point */}
        <Card title="答题详情 · 错题重练" style={{ marginTop: 16 }}>
          {Object.entries(results).map(([qid, r]: [string, any]) => (
            <Card key={qid} size="small" style={{ marginBottom: 8 }}
              extra={r.is_correct ? <Tag color="success">正确</Tag> : <Tag color="error">错误</Tag>}>
              <div style={{ fontWeight: 600 }}><LearningContent text={r.question?.question_text || ''} /></div>
              {typeof r.partial_score === 'number' && <Tag color={r.partial_score === 1 ? 'success' : 'orange'}>得分比例: {(r.partial_score * 100).toFixed(0)}%</Tag>}
              {!r.is_correct && (
                <>
                  <div style={{ marginTop: 8 }}><Text type="secondary">正确答案: {r.correct_answer}</Text></div>
                  {r.analysis && <div>解析: <LearningContent text={r.analysis} /></div>}
                  {r.question?.knowledge_points?.length > 0 && (
                    <div style={{ marginTop: 8, fontSize: 12, color: '#888' }}>
                      知识点: {r.question.knowledge_points.map((k: any) => k.title).join('、')}
                    </div>
                  )}
                </>
              )}
            </Card>
          ))}
        </Card>
      </div>
    )
  }

  if (questions.length === 0) {
    return (
      <div style={{ maxWidth: 500, margin: '100px auto', textAlign: 'center' }}>
        <Title level={4}>暂无可用题目</Title>
        <Text type="secondary">请先在知识点总结页面运行 "并行生成所有内容" 来生成题库</Text>
        <br /><br />
        <Button type="primary" onClick={() => navigate(-1)}>返回</Button>
      </div>
    )
  }

  const currentQuestion = questions[currentIndex]

  return (
    <div style={{ maxWidth: 800, margin: '0 auto' }}>
      {loadError != null && <LoadFailure inline title="题目加载失败" error={loadError} onRetry={startQuiz} />}
      {/* Header */}
      <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)} type="text">返回</Button>
          <Title level={4} style={{ margin: 0 }}>交互式刷题</Title>
        </Space>
        <Space>
          <Button icon={<OrderedListOutlined />} onClick={() => setSheetOpen(true)}>
            答题卡 ({answered}/{total})
          </Button>
          <Tag color="blue">{accuracy}% 正确率</Tag>
        </Space>
      </div>

      {/* Big completion panel */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 20, padding: '16px 22px',
        background: 'linear-gradient(135deg,#eef0ff,#fff)', border: '1px solid #e6e8f2', borderRadius: 16, marginBottom: 16,
      }}>
        <div style={{
          fontSize: 52, fontWeight: 800, lineHeight: 1,
          background: 'linear-gradient(90deg,#5b5bd6,#8b8bf0)', WebkitBackgroundClip: 'text', backgroundClip: 'text',
          color: 'transparent', minWidth: 100, textAlign: 'center', letterSpacing: -2,
        }}>
          {pct}%
        </div>
        <div>
          <b style={{ fontSize: 16, display: 'block', marginBottom: 5 }}>{copy.title}</b>
          <span style={{ fontSize: 13, color: '#67708a' }}>{copy.msg}</span>
        </div>
      </div>

      <Progress percent={pct} style={{ marginBottom: 16 }} />

      {/* Question meta: cognitive level + difficulty + knowledge provenance */}
      {currentQuestion && (
        <div style={{ marginBottom: 12, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          <Tag color={COGNITIVE_LEVEL_COLORS[currentQuestion.cognitive_level as CognitiveLevel] || 'default'}>
            {COGNITIVE_LEVEL_LABELS[currentQuestion.cognitive_level as CognitiveLevel] || currentQuestion.cognitive_level}
          </Tag>
          <Tag>{diffLabel(currentQuestion.difficulty_score ?? 0.5)}</Tag>
          {currentQuestion.knowledge_points?.map((k: any, ki: number) => (
            <Tooltip key={ki} title={<div style={{ maxWidth: 300 }}>{k.explanation || '无解释'}{k.related_concepts ? `\n关联: ${k.related_concepts}` : ''}</div>}>
              <Tag color="geekblue">📚 {k.title}</Tag>
            </Tooltip>
          ))}
          {currentQuestion.knowledge_points?.length === 0 && currentQuestion.tags?.slice(0, 3).map((t, ti) => <Tag key={ti}>{t}</Tag>)}
        </div>
      )}

      <InteractiveQuestionCard
        question={currentQuestion}
        onSubmit={submitAnswer}
        submitting={submitting}
        lastResult={results[currentQuestion?.id]}
      />

      {/* Answer sheet grouped by knowledge point */}
      <Drawer
        title={<Space><OrderedListOutlined /> 答题卡 <Tag>{answered}/{total} 已答</Tag></Space>}
        placement="right" width={320} open={sheetOpen} onClose={() => setSheetOpen(false)}
      >
        {grouped.map((grp) => (
          <div key={grp.kp} style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: '#333' }}>{grp.kp}</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {grp.items.map((i) => {
                const q = questions[i]
                const r = results[q.id]
                let bg = '#f0f0f0'
                if (r && r.is_correct) bg = '#f6ffed'
                else if (r && !r.is_correct) bg = '#fff2f0'
                else if (i === currentIndex) bg = '#e6f7ff'
                return (
                  <Tooltip key={i} title={`第${i + 1}题 ${r ? (r.is_correct ? '✓' : '✗') : '未答'}`}>
                    <Button size="small" style={{ width: 36, height: 36, background: bg, borderColor: i === currentIndex ? '#5b5bd6' : undefined }}
                      onClick={() => {
                        if (autoAdvanceRef.current) clearTimeout(autoAdvanceRef.current)
                        currentIndexRef.current = i; setSheetOpen(false); setCurrentIndex(i)
                      }}>
                      {i + 1}
                    </Button>
                  </Tooltip>
                )
              })}
            </div>
          </div>
        ))}
        <div style={{ fontSize: 12, color: '#888' }}>
          <Tag color="green">✓ 正确</Tag> <Tag color="red">✗ 错误</Tag> <Tag color="blue">当前题</Tag> 点击题目快速切换
        </div>
      </Drawer>
    </div>
  )
}
