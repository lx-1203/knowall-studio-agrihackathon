// Interactive quiz page - 互动问答（逐题即时反馈）

import { useEffect, useState } from 'react'
import { View, StyleSheet, ScrollView, Alert } from 'react-native'
import { Text, Surface, Button, RadioButton, Checkbox, TextInput, ActivityIndicator, ProgressBar, Chip, Snackbar } from 'react-native-paper'
import { useRouter, useLocalSearchParams } from 'expo-router'
import { startInteractiveQuiz, submitInteractiveAnswer, recordActivity } from '../src/api'
import type { Question } from '../src/types'

interface SessionQuestion {
  id: string
  question_type: string
  difficulty: string
  difficulty_score?: number
  cognitive_level?: string
  tags?: string[]
  question_text: string
  options: { label: string; text: string }[]
  knowledge_points?: { id: string; title: string }[]
}

interface SubmitResult {
  is_correct: boolean
  correct_answer: string
  analysis: string
  stats: { total_answered: number; correct: number; accuracy: number }
}

export default function InteractiveQuizPage() {
  const router = useRouter()
  const params = useLocalSearchParams<{ summaryId?: string }>()

  const [sessionId, setSessionId] = useState('')
  const [questions, setQuestions] = useState<SessionQuestion[]>([])
  const [idx, setIdx] = useState(0)
  const [answer, setAnswer] = useState('')
  const [startedAt, setStartedAt] = useState(0)
  const [result, setResult] = useState<SubmitResult | null>(null)
  const [correctCount, setCorrectCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [finished, setFinished] = useState(false)
  const [snack, setSnack] = useState('')

  const start = async () => {
    if (!params.summaryId) { setSnack('缺少摘要 ID'); setLoading(false); return }
    setLoading(true)
    try {
      const res = await startInteractiveQuiz({ summary_id: params.summaryId, count: 10, mode: 'sequential' })
      if (!res?.questions?.length) {
        setSnack(res?.message || '暂无题目，请先运行 Agent 编排生成题目')
        setFinished(true)
        return
      }
      setSessionId(res.session_id || '')
      setQuestions(res.questions)
      setIdx(0)
      setResult(null)
      setCorrectCount(0)
      setStartedAt(Date.now())
    } catch (e: any) {
      setSnack(e?.response?.data?.detail || '初始化失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { start() }, [])

  const toggleMulti = (label: string) => {
    setAnswer(prev => {
      const arr = prev.split(',').filter(Boolean)
      const i = arr.indexOf(label)
      if (i >= 0) arr.splice(i, 1)
      else arr.push(label)
      return arr.join(',')
    })
  }

  const handleSubmit = async () => {
    const q = questions[idx]
    if (!answer.trim()) { Alert.alert('提示', '请先作答'); return }
    setSubmitting(true)
    try {
      const res = await submitInteractiveAnswer({
        question_id: q.id,
        user_answer: answer,
        time_spent_ms: Date.now() - startedAt,
        session_id: sessionId || undefined,
      })
      setResult(res)
      if (res?.is_correct) setCorrectCount(c => c + 1)
    } catch (e: any) {
      setSnack(e?.response?.data?.detail || '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  const next = () => {
    setAnswer('')
    setResult(null)
    setStartedAt(Date.now())
    if (idx + 1 >= questions.length) {
      setFinished(true)
      recordActivity('quiz_interactive', questions.length).catch(() => {})
      return
    }
    setIdx(idx + 1)
  }

  // ===== Loading =====
  if (loading) {
    return (
      <View style={[styles.container, styles.centered]}>
        <ActivityIndicator size="large" color="#7C3AED" />
        <Text style={{ marginTop: 12, color: '#6B7280' }}>正在生成互动题目…</Text>
      </View>
    )
  }

  // ===== Finished =====
  if (finished) {
    const answered = questions.length
    const accuracy = answered > 0 ? Math.round((correctCount / answered) * 100) : 0
    return (
      <ScrollView style={styles.container}>
        <Surface style={styles.resultCard}>
          <Text variant="headlineMedium" style={{ color: '#7C3AED', fontWeight: 'bold' }}>
            答对 {correctCount}/{answered}
          </Text>
          <Text variant="headlineSmall" style={{ marginTop: 8, color: accuracy >= 70 ? '#10B981' : accuracy >= 40 ? '#F59E0B' : '#EF4444' }}>
            正确率 {accuracy}%
          </Text>
          <Button mode="contained" onPress={start} buttonColor="#7C3AED" style={{ marginTop: 20 }}>
            再来一轮
          </Button>
          <Button mode="outlined" onPress={() => router.back()} style={{ marginTop: 10 }}>
            返回
          </Button>
        </Surface>
        <View style={{ height: 32 }} />
      </ScrollView>
    )
  }

  const q = questions[idx]
  if (!q) {
    return (
      <View style={[styles.container, styles.centered]}>
        <Text style={{ color: '#9CA3AF' }}>无可用题目</Text>
        <Button mode="outlined" onPress={() => router.back()} style={{ marginTop: 12 }}>返回</Button>
      </View>
    )
  }

  // ===== Question view =====
  return (
    <View style={styles.container}>
      <ProgressBar progress={(idx + (result ? 1 : 0)) / questions.length} color="#7C3AED" style={{ marginBottom: 10 }} />
      <Text variant="bodySmall" style={{ color: '#6B7280', marginBottom: 12 }}>
        第 {idx + 1} / {questions.length} 题
      </Text>

      <ScrollView style={{ flex: 1 }} showsVerticalScrollIndicator={false}>
        <Surface style={styles.qCard}>
          <Text variant="titleMedium" style={{ lineHeight: 24 }}>{q.question_text}</Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
            <Chip compact style={{ height: 24 }} textStyle={{ fontSize: 10 }}>{q.question_type}</Chip>
            <Chip compact style={{ height: 24 }} textStyle={{ fontSize: 10 }}>{q.difficulty}</Chip>
            {q.cognitive_level ? <Chip compact style={{ height: 24 }} textStyle={{ fontSize: 10 }}>{q.cognitive_level}</Chip> : null}
          </View>

          <View style={{ marginTop: 16 }}>
            {['single_choice', 'true_false'].includes(q.question_type) ? (
              <RadioButton.Group value={answer} onValueChange={setAnswer}>
                {q.options.map((opt, i) => (
                  <RadioButton.Item key={i} label={`${opt.label}. ${opt.text}`} value={opt.label} disabled={!!result} />
                ))}
              </RadioButton.Group>
            ) : q.question_type === 'multi_choice' ? (
              <View>
                {q.options.map((opt, i) => {
                  const selected = answer.split(',').includes(opt.label)
                  return (
                    <Checkbox.Item
                      key={i}
                      label={`${opt.label}. ${opt.text}`}
                      status={selected ? 'checked' : 'unchecked'}
                      disabled={!!result}
                      onPress={() => toggleMulti(opt.label)}
                    />
                  )
                })}
              </View>
            ) : (
              <TextInput
                mode="outlined"
                value={answer}
                onChangeText={setAnswer}
                editable={!result}
                placeholder={q.question_type === 'calculation' ? '输入计算结果...' :
                  q.question_type === 'coding' ? '输入代码...' :
                  q.question_type === 'formula' ? '输入公式...' : '输入答案...'}
                multiline={['short_answer', 'coding', 'material_analysis'].includes(q.question_type)}
                numberOfLines={3}
                style={{ marginBottom: 8 }}
              />
            )}
          </View>

          {/* Immediate feedback */}
          {result ? (
            <Surface style={[styles.feedback, result.is_correct ? styles.fbCorrect : styles.fbWrong]}>
              <Text style={{ fontWeight: 'bold', color: result.is_correct ? '#047857' : '#B91C1C' }}>
                {result.is_correct ? '✓ 回答正确' : `✗ 回答错误 · 正确答案: ${result.correct_answer || '—'}`}
              </Text>
              {result.analysis ? (
                <Text style={{ marginTop: 6, lineHeight: 20, color: '#374151' }}>{result.analysis}</Text>
              ) : null}
            </Surface>
          ) : null}
        </Surface>
      </ScrollView>

      <View style={styles.navRow}>
        {!result ? (
          <Button mode="contained" onPress={handleSubmit} loading={submitting} buttonColor="#7C3AED" style={{ flex: 1 }}>
            提交答案
          </Button>
        ) : (
          <Button mode="contained" onPress={next} buttonColor={idx + 1 >= questions.length ? '#10B981' : '#7C3AED'} style={{ flex: 1 }}>
            {idx + 1 >= questions.length ? '查看结果' : '下一题'}
          </Button>
        )}
      </View>
      <Snackbar visible={!!snack} onDismiss={() => setSnack('')}>{snack}</Snackbar>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F5F3FF', padding: 16 },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  qCard: { padding: 20, borderRadius: 12, backgroundColor: '#fff', elevation: 2 },
  feedback: { marginTop: 16, padding: 12, borderRadius: 8 },
  fbCorrect: { backgroundColor: '#F0FDF4', borderLeftWidth: 4, borderLeftColor: '#10B981' },
  fbWrong: { backgroundColor: '#FEF2F2', borderLeftWidth: 4, borderLeftColor: '#EF4444' },
  navRow: { marginTop: 14 },
  resultCard: { padding: 32, borderRadius: 12, alignItems: 'center', backgroundColor: '#fff', elevation: 2, marginTop: 60 },
})
