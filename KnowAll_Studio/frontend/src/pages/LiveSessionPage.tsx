import { useState, useEffect, useRef, useCallback } from 'react'
import {
  Card, Button, Input, Space, Tag, Table, Modal, App, Spin, Result,
  Radio, Statistic, Row, Col, Badge, Divider, Typography,
} from 'antd'
import {
  CopyOutlined, PlayCircleOutlined, ThunderboltOutlined, TrophyOutlined,
  TeamOutlined, ReloadOutlined, StopOutlined, QuestionCircleOutlined,
  SendOutlined, UserSwitchOutlined,
} from '@ant-design/icons'
import App_ from 'antd/es/app'
import VoiceInput from '../components/VoiceInput'
import {
  createLiveSession, getLiveSessionByCode, joinLiveSession,
  pushLiveQuestions, spotlightLiveQuestion, submitLiveAnswer,
  getLiveLeaderboard, endLiveSession, getLiveSessionQuestions,
  searchCards,
} from '../api'
import { useAuthStore } from '../stores'
import type { LiveSession, LiveParticipant, LiveQuestion, LiveAnswerResult } from '../types'

const { Title, Text } = Typography

type View = 'landing' | 'host' | 'participant'

export default function LiveSessionPage() {
  const { message: msg } = App_.useApp()
  const user = useAuthStore(s => s.user)

  const [view, setView] = useState<View>('landing')
  const [session, setSession] = useState<LiveSession | null>(null)
  const [participants, setParticipants] = useState<LiveParticipant[]>([])
  const [questions, setQuestions] = useState<LiveQuestion[]>([])
  const [leaderboard, setLeaderboard] = useState<LiveParticipant[]>([])
  const [currentQuestion, setCurrentQuestion] = useState<LiveQuestion | null>(null)
  const [userAnswer, setUserAnswer] = useState('')
  const [answerResult, setAnswerResult] = useState<LiveAnswerResult | null>(null)
  const [isHost, setIsHost] = useState(false)
  const [loading, setLoading] = useState(false)
  const [wsConnected, setWsConnected] = useState(false)

  // 创建表单
  const [createTitle, setCreateTitle] = useState('')
  // 加入表单
  const [joinCode, setJoinCode] = useState('')
  const [joinName, setJoinName] = useState('')

  // WebSocket ref
  const wsRef = useRef<WebSocket | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval>>()
  const heartbeatCleanupRef = useRef<(() => void) | null>(null)

  // Keep the latest message handler in a ref so the WS onmessage closure never
  // captures stale state (currentQuestion/user) from the first render.
  const handleWSMessageRef = useRef<(msg: any) => void>(() => {})

  // ===== WebSocket 连接 =====
  const connectWS = useCallback((sessionId: string, userId: string, userName: string) => {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const host = window.location.host
    const token = localStorage.getItem('knowall_token') || ''
    const url = `${proto}://${host}/ws/sync?doc_id=${sessionId}&user_id=${userId}&user_name=${encodeURIComponent(userName)}&token=${token}`

    // Tear down any previous connection/heartbeat before opening a new one.
    if (heartbeatCleanupRef.current) { heartbeatCleanupRef.current(); heartbeatCleanupRef.current = null }
    wsRef.current?.close()

    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      setWsConnected(true)
      ws.send(JSON.stringify({ type: 'reconnect', local_version: 0 }))
    }

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data)
        handleWSMessageRef.current(msg)
      } catch {}
    }

    ws.onclose = () => setWsConnected(false)
    ws.onerror = () => setWsConnected(false)

    // 心跳
    const heartbeat = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'heartbeat' }))
      }
    }, 15000)
    heartbeatCleanupRef.current = () => clearInterval(heartbeat)
  }, [])

  const handleWSMessage = useCallback((msg: any) => {
    const { type, data } = msg
    switch (type) {
      case 'live_question_push':
        // 新考题推送
        if (data.questions) {
          setQuestions(prev => [...prev, ...data.questions])
          if (!currentQuestion && data.questions.length > 0) {
            setCurrentQuestion(data.questions[0])
            setUserAnswer('')
            setAnswerResult(null)
          }
        }
        break
      case 'live_spotlight':
        // 被抽查
        setCurrentQuestion(data.question || data)
        setUserAnswer('')
        setAnswerResult(null)
        if (data.target_user_id === user?.id) {
          msg.info?.('你被抽中回答问题！')
        }
        break
      case 'live_answer_submitted':
        // 有人作答
        setLeaderboard(data.leaderboard || [])
        break
      case 'live_leaderboard_update':
        setLeaderboard(data.leaderboard || [])
        break
      case 'live_join':
        // 新参与者
        if (data.participant) {
          setParticipants(prev => {
            if (prev.find(p => p.id === data.participant.id)) return prev
            return [...prev, data.participant]
          })
        }
        break
      case 'live_ended':
        setView('landing')
        setSession(null)
        break
      case 'presence':
        // 在线成员更新
        break
      case 'heartbeat_ack':
        break
    }
  }, [currentQuestion, user])

  // Keep the handler ref fresh on every render so the WS closure always sees
  // the latest currentQuestion/user, not the values from first mount.
  handleWSMessageRef.current = handleWSMessage

  // 清理 WebSocket
  useEffect(() => {
    return () => {
      if (heartbeatCleanupRef.current) { heartbeatCleanupRef.current(); heartbeatCleanupRef.current = null }
      wsRef.current?.close()
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [])

  // ===== 创建会话 =====
  const handleCreate = async () => {
    if (!createTitle.trim()) { msg.warning('请输入会话标题'); return }
    setLoading(true)
    try {
      const data = await createLiveSession({ title: createTitle })
      setSession(data)
      setIsHost(true)
      setView('host')
      connectWS(data.session_id, user?.id || 'local_user', '主持人')
      msg.success(`会话已创建，房间码: ${data.room_code}`)
    } catch (e: any) {
      msg.error(e.response?.data?.detail || '创建失败')
    }
    setLoading(false)
  }

  // ===== 加入会话 =====
  const handleJoin = async () => {
    if (!joinCode.trim()) { msg.warning('请输入房间码'); return }
    setLoading(true)
    try {
      const sessionData = await getLiveSessionByCode(joinCode)
      const joinData = await joinLiveSession(sessionData.session_id, {
        user_name: joinName || user?.username || '匿名用户',
      })
      setSession(sessionData)
      setParticipants(sessionData.participants || [])
      setIsHost(sessionData.host_id === user?.id)
      setView('participant')
      connectWS(sessionData.session_id, user?.id || 'local_user', joinName || user?.username || '匿名')

      // 加载已有题目
      try {
        const qData = await getLiveSessionQuestions(sessionData.session_id)
        setQuestions(qData.questions || [])
        if (qData.questions?.length > 0) {
          setCurrentQuestion(qData.questions[qData.questions.length - 1])
        }
      } catch {}

      // 加载排行榜
      try {
        const lb = await getLiveLeaderboard(sessionData.session_id)
        setLeaderboard(lb.leaderboard || [])
      } catch {}

      msg.success('已加入会话')
    } catch (e: any) {
      msg.error(e.response?.data?.detail || '加入失败')
    }
    setLoading(false)
  }

  // ===== 主持人推送题目 =====
  const [pushModalOpen, setPushModalOpen] = useState(false)
  const [pushSearchQ, setPushSearchQ] = useState('')
  const [searchResults, setSearchResults] = useState<any[]>([])
  const [selectedQIds, setSelectedQIds] = useState<string[]>([])

  const handleSearchQuestions = async () => {
    try {
      const data = await searchCards(pushSearchQ, 20)
      setSearchResults(data.results || data || [])
    } catch { msg.error('搜索失败') }
  }

  const handlePush = async () => {
    if (selectedQIds.length === 0) { msg.warning('请选择题目'); return }
    if (!session) return
    setLoading(true)
    try {
      const data = await pushLiveQuestions(session.session_id, {
        question_ids: selectedQIds,
        mode: 'broadcast',
      })
      setQuestions(prev => [...prev, ...(data.questions || [])])
      if (!currentQuestion && data.questions?.length > 0) {
        setCurrentQuestion(data.questions[0])
      }
      // WebSocket 广播
      wsRef.current?.send(JSON.stringify({
        type: 'live_question_push',
        data: { questions: data.questions },
      }))
      setPushModalOpen(false)
      setSelectedQIds([])
      msg.success(`已推送 ${data.pushed_count} 道题`)
    } catch (e: any) {
      msg.error(e.response?.data?.detail || '推送失败')
    }
    setLoading(false)
  }

  // ===== 随机抽查 =====
  const handleSpotlight = async () => {
    if (!session) return
    setLoading(true)
    try {
      const data = await spotlightLiveQuestion(session.session_id, {})
      setCurrentQuestion(data)
      setUserAnswer('')
      setAnswerResult(null)
      // WebSocket 广播
      wsRef.current?.send(JSON.stringify({
        type: 'live_spotlight',
        data,
      }))
      msg.success(`抽查: ${data.target_user_name}`)
    } catch (e: any) {
      msg.error(e.response?.data?.detail || '抽查失败')
    }
    setLoading(false)
  }

  // ===== 提交答案 =====
  const handleAnswer = async () => {
    if (!currentQuestion || !userAnswer.trim() || !session) return
    setLoading(true)
    try {
      const result = await submitLiveAnswer(session.session_id, {
        live_question_id: currentQuestion.live_question_id,
        user_answer: userAnswer,
      })
      setAnswerResult(result)
      // WebSocket 广播
      wsRef.current?.send(JSON.stringify({
        type: 'live_answer_submitted',
        data: { question_id: currentQuestion.live_question_id, is_correct: result.is_correct },
      }))
      // 刷新排行榜
      try {
        const lb = await getLiveLeaderboard(session.session_id)
        setLeaderboard(lb.leaderboard || [])
        wsRef.current?.send(JSON.stringify({
          type: 'live_leaderboard_update',
          data: { leaderboard: lb.leaderboard },
        }))
      } catch {}
    } catch (e: any) {
      msg.error(e.response?.data?.detail || '提交失败')
    }
    setLoading(false)
  }

  // ===== 结束会话 =====
  const handleEnd = async () => {
    if (!session) return
    try {
      await endLiveSession(session.session_id)
      wsRef.current?.send(JSON.stringify({ type: 'live_ended', data: {} }))
      msg.success('会话已结束')
      setView('landing')
      setSession(null)
    } catch { msg.error('结束失败') }
  }

  // ===== 复制房间码 =====
  const copyCode = () => {
    if (session?.room_code) {
      navigator.clipboard.writeText(session.room_code)
      msg.success('已复制房间码')
    }
  }

  // ===== 渲染 =====

  // 着陆页（创建/加入）
  if (view === 'landing') {
    return (
      <div style={{ maxWidth: 600, margin: '0 auto', paddingTop: 40 }}>
        <Title level={2} style={{ textAlign: 'center', marginBottom: 40 }}>
          ⚡ 实时对话考试
        </Title>
        <Row gutter={24}>
          <Col span={12}>
            <Card title="🎤 创建会话" style={{ height: '100%' }}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <Input
                  placeholder="会话标题"
                  value={createTitle}
                  onChange={e => setCreateTitle(e.target.value)}
                />
                <Button type="primary" block icon={<PlayCircleOutlined />}
                  loading={loading} onClick={handleCreate}>
                  创建
                </Button>
              </Space>
            </Card>
          </Col>
          <Col span={12}>
            <Card title="🚪 加入会话" style={{ height: '100%' }}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <Input
                  placeholder="6位房间码"
                  value={joinCode}
                  onChange={e => setJoinCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                  maxLength={6}
                  style={{ fontSize: 20, textAlign: 'center', letterSpacing: 8 }}
                />
                <Input
                  placeholder="你的昵称（可选）"
                  value={joinName}
                  onChange={e => setJoinName(e.target.value)}
                />
                <Button type="primary" block icon={<TeamOutlined />}
                  loading={loading} onClick={handleJoin}>
                  加入
                </Button>
              </Space>
            </Card>
          </Col>
        </Row>
      </div>
    )
  }

  // 主持人视图 / 参与者视图
  return (
    <div>
      {/* 顶部信息栏 */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Space size="large">
            <span>
              <Text strong style={{ fontSize: 18 }}>{session?.title}</Text>
              <Tag color={wsConnected ? 'success' : 'error'} style={{ marginLeft: 8 }}>
                {wsConnected ? '已连接' : '未连接'}
              </Tag>
            </span>
            <span>
              <Text type="secondary">房间码: </Text>
              <Text strong style={{ fontSize: 20, letterSpacing: 4, color: '#7c3aed' }}>
                {session?.room_code}
              </Text>
              <Button size="small" icon={<CopyOutlined />} onClick={copyCode} style={{ marginLeft: 4 }} />
            </span>
            <Tag icon={<TeamOutlined />}>{participants.length} 人</Tag>
          </Space>
          <Space>
            {isHost && (
              <>
                <Button icon={<SendOutlined />} type="primary" onClick={() => setPushModalOpen(true)}>
                  推送考题
                </Button>
                <Button icon={<UserSwitchOutlined />} onClick={handleSpotlight} loading={loading}>
                  随机抽查
                </Button>
                <Button icon={<StopOutlined />} danger onClick={handleEnd}>
                  结束
                </Button>
              </>
            )}
            <Button icon={<TrophyOutlined />} onClick={async () => {
              if (!session) return
              const lb = await getLiveLeaderboard(session.session_id)
              setLeaderboard(lb.leaderboard || [])
            }}>
              排行榜
            </Button>
          </Space>
        </div>
      </Card>

      <Row gutter={16}>
        {/* 左侧：考题区 */}
        <Col span={16}>
          {currentQuestion ? (
            <Card
              title={
                <Space>
                  <QuestionCircleOutlined />
                  <span>第 {currentQuestion.question_index} 题</span>
                  {currentQuestion.mode === 'spotlight' && (
                    <Tag color="volcano">抽查: {currentQuestion.target_user_name}</Tag>
                  )}
                </Space>
              }
            >
              <div style={{ fontSize: 16, lineHeight: 2, marginBottom: 20 }}>
                {currentQuestion.question_text}
                <VoiceInput onResult={() => {}} readAloud={currentQuestion.question_text} compact />
              </div>

              {/* 选项（选择题） */}
              {currentQuestion.options?.length > 0 && (
                <Radio.Group
                  value={userAnswer}
                  onChange={e => setUserAnswer(e.target.value)}
                  style={{ width: '100%', marginBottom: 16 }}
                >
                  <Space direction="vertical" style={{ width: '100%' }}>
                    {currentQuestion.options.map((opt: any, i: number) => (
                      <Radio key={i} value={opt.label || opt.text} style={{ fontSize: 15, lineHeight: 2 }}>
                        <strong>{opt.label}.</strong> {opt.text}
                      </Radio>
                    ))}
                  </Space>
                </Radio.Group>
              )}

              {/* 非选择题输入 */}
              {(!currentQuestion.options || currentQuestion.options.length === 0) && (
                <div>
                  <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 4 }}>
                    <VoiceInput
                      onResult={(text, isFinal) => { if (isFinal) setUserAnswer(text) }}
                      mode="server"
                      readAloud={currentQuestion.question_text}
                    />
                  </div>
                  <Input.TextArea
                    value={userAnswer}
                    onChange={e => setUserAnswer(e.target.value)}
                    placeholder="输入你的答案（或点击🎤语音输入）..."
                  autoSize={{ minRows: 3 }}
                  style={{ marginBottom: 16, fontSize: 15 }}
                />
                </div>
              )}

              {/* 提交按钮 */}
              {!answerResult && (
                <Button type="primary" icon={<SendOutlined />} block size="large"
                  loading={loading} onClick={handleAnswer} disabled={!userAnswer.trim()}>
                  提交答案
                </Button>
              )}

              {/* 结果展示 */}
              {answerResult && (
                <Card
                  size="small"
                  style={{
                    marginTop: 16,
                    background: answerResult.is_correct ? '#f6ffed' : '#fff2f0',
                    borderColor: answerResult.is_correct ? '#b7eb8f' : '#ffa39e',
                  }}
                >
                  <Statistic
                    title={answerResult.is_correct ? '✅ 回答正确！' : '❌ 回答错误'}
                    value={answerResult.score}
                    suffix="分"
                    valueStyle={{ color: answerResult.is_correct ? '#3f8600' : '#cf1322' }}
                  />
                  {!answerResult.is_correct && answerResult.correct_answer && (
                    <div style={{ marginTop: 8 }}>
                      <Text type="secondary">正确答案: </Text>
                      <Text strong>{answerResult.correct_answer}</Text>
                      <VoiceInput onResult={() => {}} readAloud={answerResult.correct_answer!} compact />
                    </div>
                  )}
                  {answerResult.analysis && (
                    <div style={{ marginTop: 8 }}>
                      <Text type="secondary">解析: </Text>
                      <Text>{answerResult.analysis}</Text>
                    </div>
                  )}
                  {/* 下一题按钮 */}
                  {isHost && (
                    <Button style={{ marginTop: 12 }} onClick={() => {
                      const idx = questions.findIndex(q => q.live_question_id === currentQuestion.live_question_id)
                      if (idx < questions.length - 1) {
                        setCurrentQuestion(questions[idx + 1])
                        setUserAnswer('')
                        setAnswerResult(null)
                      }
                    }}>
                      下一题
                    </Button>
                  )}
                </Card>
              )}
            </Card>
          ) : (
            <Card>
              <Result
                icon={<QuestionCircleOutlined style={{ color: '#d9d9d9' }} />}
                title="暂无考题"
                subTitle={isHost ? '点击「推送考题」开始考试' : '等待主持人推送考题...'}
              />
            </Card>
          )}
        </Col>

        {/* 右侧：排行榜 */}
        <Col span={8}>
          <Card title={<><TrophyOutlined /> 排行榜</>} size="small">
            {leaderboard.length === 0 ? (
              <Text type="secondary">暂无数据</Text>
            ) : (
              <div>
                {leaderboard.map((p, i) => (
                  <div key={i} style={{
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    padding: '6px 0', borderBottom: '1px solid #f0f0f0',
                  }}>
                    <Space>
                      <Badge
                        count={i + 1}
                        style={{
                          backgroundColor: i === 0 ? '#ffd700' : i === 1 ? '#c0c0c0' : i === 2 ? '#cd7f32' : '#d9d9d9',
                        }}
                      />
                      <Text>{p.user_name}</Text>
                    </Space>
                    <Space>
                      <Text strong>{p.score}分</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {p.correct_count}/{p.total_answered}
                      </Text>
                    </Space>
                  </div>
                ))}
              </div>
            )}
          </Card>

          {/* 已推题目列表 */}
          <Card title="已推题目" size="small" style={{ marginTop: 16 }}>
            {questions.length === 0 ? (
              <Text type="secondary">暂无</Text>
            ) : (
              questions.map((q, i) => (
                <div key={q.live_question_id} style={{
                  padding: '4px 0', cursor: 'pointer',
                  color: currentQuestion?.live_question_id === q.live_question_id ? '#7c3aed' : '#333',
                  fontWeight: currentQuestion?.live_question_id === q.live_question_id ? 600 : 400,
                }} onClick={() => { setCurrentQuestion(q); setUserAnswer(''); setAnswerResult(null) }}>
                  {i + 1}. {q.question_text?.slice(0, 40)}...
                </div>
              ))
            )}
          </Card>
        </Col>
      </Row>

      {/* 推送考题弹窗 */}
      <Modal
        title="推送考题"
        open={pushModalOpen}
        onCancel={() => setPushModalOpen(false)}
        onOk={handlePush}
        okText={`推送 (${selectedQIds.length})`}
        width={700}
      >
        <Space style={{ marginBottom: 12, width: '100%' }}>
          <Input.Search
            placeholder="搜索题目..."
            value={pushSearchQ}
            onChange={e => setPushSearchQ(e.target.value)}
            onSearch={handleSearchQuestions}
            style={{ width: 400 }}
          />
        </Space>
        <div style={{ maxHeight: 400, overflow: 'auto' }}>
          {(searchResults.length > 0 ? searchResults : []).map((q: any) => (
            <Card
              key={q.id}
              size="small"
              style={{
                marginBottom: 8,
                borderColor: selectedQIds.includes(q.id) ? '#7c3aed' : '#f0f0f0',
                cursor: 'pointer',
              }}
              onClick={() => {
                setSelectedQIds(prev =>
                  prev.includes(q.id) ? prev.filter(id => id !== q.id) : [...prev, q.id]
                )
              }}
            >
              <Space>
                <Tag>{q.question_type || q.card_type}</Tag>
                <Text>{(q.question_text || q.front || '').slice(0, 80)}</Text>
              </Space>
            </Card>
          ))}
        </div>
      </Modal>
    </div>
  )
}
