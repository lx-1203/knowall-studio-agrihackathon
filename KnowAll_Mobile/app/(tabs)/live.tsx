// 实时对话考试页面 - 创建/加入会话，实时答题，排行榜

import { useEffect, useState, useRef, useCallback } from 'react'
import { View, StyleSheet, ScrollView, Alert } from 'react-native'
import { Text, Surface, Button, TextInput, Chip, ActivityIndicator, Snackbar, Icon, Divider, Badge } from 'react-native-paper'
import {
  createLiveSession, getLiveSessionByCode, joinLiveSession,
  submitLiveAnswer, getLiveLeaderboard, endLiveSession,
  getLiveSessionQuestions, spotlightLiveQuestion, recordActivity,
} from '../../src/api'
import { WS_BASE_URL } from '../../src/api'
import { useAuthStore } from '../../src/stores'
import type { LiveSession, LiveParticipant, LiveQuestion, LiveAnswerResult } from '../../src/types'

type View_ = 'landing' | 'host' | 'participant'

export default function LivePage() {
  const user = useAuthStore(s => s.user)
  const [view, setView] = useState<View_>('landing')
  const [session, setSession] = useState<LiveSession | null>(null)
  const [participants, setParticipants] = useState<LiveParticipant[]>([])
  const [questions, setQuestions] = useState<LiveQuestion[]>([])
  const [leaderboard, setLeaderboard] = useState<LiveParticipant[]>([])
  const [currentQ, setCurrentQ] = useState<LiveQuestion | null>(null)
  const [userAnswer, setUserAnswer] = useState('')
  const [answerResult, setAnswerResult] = useState<LiveAnswerResult | null>(null)
  const [isHost, setIsHost] = useState(false)
  const [loading, setLoading] = useState(false)
  const [snackMsg, setSnackMsg] = useState('')
  const [wsConnected, setWsConnected] = useState(false)

  const [createTitle, setCreateTitle] = useState('')
  const [joinCode, setJoinCode] = useState('')
  const [joinName, setJoinName] = useState('')

  const wsRef = useRef<WebSocket | null>(null)

  // WebSocket
  const connectWS = useCallback((sessionId: string, userId: string, userName: string) => {
    const proto = WS_BASE_URL.startsWith('https') ? 'wss' : 'ws'
    const host = WS_BASE_URL.replace(/^ws(s?):\/\//, '')
    const token = ''
    const url = `${proto}://${host}/ws/sync?doc_id=${sessionId}&user_id=${userId}&user_name=${encodeURIComponent(userName)}&token=${token}`
    const ws = new WebSocket(url)
    wsRef.current = ws
    ws.onopen = () => {
      setWsConnected(true)
      ws.send(JSON.stringify({ type: 'reconnect', local_version: 0 }))
    }
    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data)
        if (msg.type === 'live_question_push' && msg.data?.questions) {
          setQuestions(prev => [...prev, ...msg.data.questions])
          setCurrentQ(msg.data.questions[0])
          setUserAnswer('')
          setAnswerResult(null)
        }
        if (msg.type === 'live_spotlight') {
          setCurrentQ(msg.data)
          setUserAnswer('')
          setAnswerResult(null)
        }
        if (msg.type === 'live_leaderboard_update') {
          setLeaderboard(msg.data.leaderboard || [])
        }
        if (msg.type === 'live_join' && msg.data?.participant) {
          setParticipants(prev => [...prev, msg.data.participant])
        }
        if (msg.type === 'live_ended') {
          setView('landing')
          setSession(null)
          setSnackMsg('会话已结束')
        }
      } catch {}
    }
    ws.onclose = () => setWsConnected(false)
    ws.onerror = () => setWsConnected(false)
  }, [])

  useEffect(() => () => { wsRef.current?.close() }, [])

  // 创建会话
  const handleCreate = async () => {
    if (!createTitle.trim()) { setSnackMsg('请输入标题'); return }
    setLoading(true)
    try {
      const data = await createLiveSession({ title: createTitle })
      setSession(data)
      setIsHost(true)
      setView('host')
      connectWS(data.session_id, user?.id || 'local_user', '主持人')
      setSnackMsg(`房间码: ${data.room_code}`)
    } catch { setSnackMsg('创建失败') }
    setLoading(false)
  }

  // 加入会话
  const handleJoin = async () => {
    if (!joinCode.trim()) { setSnackMsg('请输入房间码'); return }
    setLoading(true)
    try {
      const sData = await getLiveSessionByCode(joinCode)
      await joinLiveSession(sData.session_id, { user_name: joinName || user?.username || '匿名' })
      setSession(sData)
      setParticipants(sData.participants || [])
      setIsHost(sData.host_id === user?.id)
      setView('participant')
      connectWS(sData.session_id, user?.id || 'local_user', joinName || '匿名')
      // 加载已有题目
      try {
        const qData = await getLiveSessionQuestions(sData.session_id)
        setQuestions(qData.questions || [])
        if (qData.questions?.length) setCurrentQ(qData.questions[qData.questions.length - 1])
      } catch {}
      // 加载排行榜
      try {
        const lb = await getLiveLeaderboard(sData.session_id)
        setLeaderboard(lb.leaderboard || [])
      } catch {}
      setSnackMsg('已加入')
    } catch { setSnackMsg('加入失败') }
    setLoading(false)
  }

  // 随机抽查
  const handleSpotlight = async () => {
    if (!session) return
    setLoading(true)
    try {
      const data = await spotlightLiveQuestion(session.session_id, {})
      setCurrentQ(data)
      setUserAnswer('')
      setAnswerResult(null)
      wsRef.current?.send(JSON.stringify({ type: 'live_spotlight', data }))
      setSnackMsg(`抽查: ${data.target_user_name}`)
    } catch { setSnackMsg('抽查失败') }
    setLoading(false)
  }

  // 提交答案
  const handleAnswer = async () => {
    if (!currentQ || !userAnswer.trim() || !session) return
    setLoading(true)
    try {
      const result = await submitLiveAnswer(session.session_id, {
        live_question_id: currentQ.live_question_id,
        user_answer: userAnswer,
      })
      setAnswerResult(result)
      recordActivity('live_quiz', 1).catch(() => {})
      // 刷新排行榜
      try {
        const lb = await getLiveLeaderboard(session.session_id)
        setLeaderboard(lb.leaderboard || [])
      } catch {}
      setSnackMsg(result.is_correct ? '✅ 正确！' : '❌ 错误')
    } catch { setSnackMsg('提交失败') }
    setLoading(false)
  }

  // 结束会话
  const handleEnd = async () => {
    if (!session) return
    Alert.alert('确认', '确定要结束会话吗？', [
      { text: '取消' },
      { text: '确定', onPress: async () => {
        try {
          await endLiveSession(session.session_id)
          wsRef.current?.send(JSON.stringify({ type: 'live_ended', data: {} }))
          setView('landing'); setSession(null)
          setSnackMsg('已结束')
        } catch { setSnackMsg('结束失败') }
      }},
    ])
  }

  // ===== 着陆页 =====
  if (view === 'landing') {
    return (
      <View style={styles.container}>
        <ScrollView contentContainerStyle={styles.content}>
          <Text variant="headlineMedium" style={styles.pageTitle}>⚡ 实时对话考试</Text>

          <Surface style={styles.card} elevation={1}>
            <Text variant="titleMedium" style={{ marginBottom: 12 }}>🎤 创建会话</Text>
            <TextInput label="会话标题" value={createTitle} onChangeText={setCreateTitle} mode="outlined" style={{ marginBottom: 12 }} />
            <Button mode="contained" loading={loading} onPress={handleCreate} buttonColor="#7C3AED" icon="play">
              创建
            </Button>
          </Surface>

          <Surface style={styles.card} elevation={1}>
            <Text variant="titleMedium" style={{ marginBottom: 12 }}>🚪 加入会话</Text>
            <TextInput
              label="6位房间码"
              value={joinCode}
              onChangeText={v => setJoinCode(v.replace(/\D/g, '').slice(0, 6))}
              mode="outlined"
              keyboardType="numeric"
              maxLength={6}
              style={{ marginBottom: 8, fontSize: 20, textAlign: 'center' }}
            />
            <TextInput label="你的昵称" value={joinName} onChangeText={setJoinName} mode="outlined" style={{ marginBottom: 12 }} />
            <Button mode="contained" loading={loading} onPress={handleJoin} buttonColor="#7C3AED" icon="account-plus">
              加入
            </Button>
          </Surface>
        </ScrollView>
        <Snackbar visible={!!snackMsg} onDismiss={() => setSnackMsg('')} duration={2000}>{snackMsg}</Snackbar>
      </View>
    )
  }

  // ===== 进行中 =====
  return (
    <View style={styles.container}>
      <ScrollView contentContainerStyle={styles.content}>
        {/* 信息栏 */}
        <Surface style={styles.card} elevation={1}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
            <View>
              <Text variant="titleMedium">{session?.title}</Text>
              <View style={{ flexDirection: 'row', gap: 8, marginTop: 4 }}>
                <Chip compact style={{ backgroundColor: wsConnected ? '#D1FAE5' : '#FEE2E2' }}>
                  {wsConnected ? '已连接' : '离线'}
                </Chip>
                <Chip compact icon="account-group">{participants.length} 人</Chip>
              </View>
            </View>
            <View style={{ alignItems: 'center' }}>
              <Text variant="bodySmall" style={{ color: '#9CA3AF' }}>房间码</Text>
              <Text variant="headlineSmall" style={{ color: '#7C3AED', fontWeight: '700', letterSpacing: 4 }}>
                {session?.room_code}
              </Text>
            </View>
          </View>
        </Surface>

        {/* 主持人操作 */}
        {isHost && (
          <View style={styles.hostActions}>
            <Button mode="contained" compact icon="account-switch" onPress={handleSpotlight} loading={loading} buttonColor="#F59E0B">
              随机抽查
            </Button>
            <Button mode="contained" compact icon="stop" onPress={handleEnd} buttonColor="#EF4444">
              结束
            </Button>
          </View>
        )}

        {/* 当前考题 */}
        {currentQ ? (
          <Surface style={styles.card} elevation={2}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 12 }}>
              <Text variant="titleMedium">第 {currentQ.question_index} 题</Text>
              {currentQ.mode === 'spotlight' && (
                <Chip compact style={{ backgroundColor: '#FEF3C7' }} textStyle={{ color: '#92400E' }}>
                  🔦 抽查: {currentQ.target_user_name}
                </Chip>
              )}
            </View>

            <Text variant="bodyLarge" style={{ lineHeight: 28, marginBottom: 16 }}>
              {currentQ.question_text}
            </Text>

            {/* 选项 */}
            {currentQ.options?.length > 0 && currentQ.options.map((opt: any, i: number) => (
              <Surface
                key={i}
                style={[styles.optionCard, userAnswer === (opt.label || opt.text) && styles.optionSelected]}
                elevation={userAnswer === (opt.label || opt.text) ? 2 : 0}
                onTouchEnd={() => setUserAnswer(opt.label || opt.text)}
              >
                <Text variant="bodyMedium">
                  <Text style={{ fontWeight: '700' }}>{opt.label}.</Text> {opt.text}
                </Text>
              </Surface>
            ))}

            {/* 简答输入 */}
            {(!currentQ.options || currentQ.options.length === 0) && (
              <TextInput
                label="你的答案"
                value={userAnswer}
                onChangeText={setUserAnswer}
                mode="outlined"
                multiline
                style={{ marginBottom: 12 }}
              />
            )}

            {/* 提交 */}
            {!answerResult && (
              <Button mode="contained" icon="send" onPress={handleAnswer} loading={loading}
                disabled={!userAnswer.trim()} buttonColor="#7C3AED" style={{ marginTop: 8 }}>
                提交答案
              </Button>
            )}

            {/* 结果 */}
            {answerResult && (
              <Surface style={[styles.resultCard, { backgroundColor: answerResult.is_correct ? '#D1FAE5' : '#FEE2E2' }]} elevation={0}>
                <Text variant="titleLarge" style={{ color: answerResult.is_correct ? '#065F46' : '#991B1B', fontWeight: '700' }}>
                  {answerResult.is_correct ? '✅ 正确！' : '❌ 错误'}
                </Text>
                <Text variant="headlineMedium" style={{ color: answerResult.is_correct ? '#065F46' : '#991B1B', fontWeight: '700', marginTop: 4 }}>
                  {answerResult.score} 分
                </Text>
                {!answerResult.is_correct && answerResult.correct_answer && (
                  <Text variant="bodyMedium" style={{ marginTop: 8, color: '#991B1B' }}>
                    正确答案: {answerResult.correct_answer}
                  </Text>
                )}
                {answerResult.analysis && (
                  <Text variant="bodySmall" style={{ marginTop: 4, color: '#6B7280' }}>
                    {answerResult.analysis}
                  </Text>
                )}
              </Surface>
            )}
          </Surface>
        ) : (
          <Surface style={styles.card} elevation={1}>
            <View style={{ alignItems: 'center', padding: 20 }}>
              <Icon source="help-circle-outline" size={48} color="#D1D5DB" />
              <Text variant="bodyLarge" style={{ color: '#9CA3AF', marginTop: 12 }}>
                {isHost ? '点击推送考题开始' : '等待主持人推送考题...'}
              </Text>
            </View>
          </Surface>
        )}

        {/* 排行榜 */}
        <Surface style={styles.card} elevation={1}>
          <Text variant="titleMedium" style={{ marginBottom: 12 }}>🏆 排行榜</Text>
          {leaderboard.length === 0 ? (
            <Text variant="bodySmall" style={{ color: '#9CA3AF' }}>暂无数据</Text>
          ) : (
            leaderboard.map((p, i) => (
              <View key={i} style={styles.leaderRow}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <View style={[styles.rankBadge, i === 0 && { backgroundColor: '#FFD700' }, i === 1 && { backgroundColor: '#C0C0C0' }, i === 2 && { backgroundColor: '#CD7F32' }]}>
                    <Text style={styles.rankText}>{i + 1}</Text>
                  </View>
                  <Text variant="bodyMedium">{p.user_name}</Text>
                </View>
                <View style={{ alignItems: 'flex-end' }}>
                  <Text variant="titleSmall" style={{ fontWeight: '700' }}>{p.score}分</Text>
                  <Text variant="bodySmall" style={{ color: '#9CA3AF' }}>
                    {p.correct_count}/{p.total_answered}
                  </Text>
                </View>
              </View>
            ))
          )}
        </Surface>

        {/* 已推题目列表 */}
        {questions.length > 0 && (
          <Surface style={styles.card} elevation={1}>
            <Text variant="titleMedium" style={{ marginBottom: 8 }}>📋 已推题目 ({questions.length})</Text>
            {questions.map((q, i) => (
              <Surface
                key={q.live_question_id}
                style={[styles.questionRow, currentQ?.live_question_id === q.live_question_id && { backgroundColor: '#EDE9FE' }]}
                elevation={0}
                onTouchEnd={() => { setCurrentQ(q); setUserAnswer(''); setAnswerResult(null) }}
              >
                <Text variant="bodySmall" style={{ color: '#374151' }}>
                  {i + 1}. {q.question_text?.slice(0, 50)}...
                </Text>
              </Surface>
            ))}
          </Surface>
        )}
      </ScrollView>
      <Snackbar visible={!!snackMsg} onDismiss={() => setSnackMsg('')} duration={2000}>{snackMsg}</Snackbar>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F5F3FF' },
  content: { padding: 16, paddingBottom: 32 },
  pageTitle: { textAlign: 'center', fontWeight: '700', color: '#374151', marginBottom: 20, marginTop: 8 },
  card: { padding: 16, borderRadius: 12, backgroundColor: '#fff', marginBottom: 12 },
  hostActions: { flexDirection: 'row', justifyContent: 'center', gap: 12, marginBottom: 12 },
  optionCard: { padding: 14, borderRadius: 10, backgroundColor: '#F9FAFB', marginBottom: 8, borderWidth: 1, borderColor: '#E5E7EB' },
  optionSelected: { backgroundColor: '#EDE9FE', borderColor: '#7C3AED' },
  resultCard: { padding: 16, borderRadius: 10, marginTop: 12 },
  leaderRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: '#F3F4F6' },
  rankBadge: { width: 28, height: 28, borderRadius: 14, backgroundColor: '#D1D5DB', justifyContent: 'center', alignItems: 'center' },
  rankText: { color: '#fff', fontWeight: '700', fontSize: 13 },
  questionRow: { padding: 10, borderRadius: 8, marginBottom: 4, backgroundColor: '#FAFAFA' },
})
