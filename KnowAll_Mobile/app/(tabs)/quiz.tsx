import { useCallback, useEffect, useRef, useState } from 'react'
import { View } from 'react-native'
import { ActivityIndicator, Button, Dialog, Divider, Portal, Searchbar, Text, useTheme } from 'react-native-paper'
import { useRouter } from 'expo-router'
import api from '../../src/api'
import { useAuthStore } from '../../src/stores'
import { storage } from '../../src/services/storage'
import { queueMaterialStorage } from '../../src/materialStorageQueue.js'
import { CalmScreen, CalmPanel, ScreenNotice, calm } from '../../src/components/CalmScreen'
import MaterialAnswerInput, { MaterialChoice } from '../../src/components/MaterialAnswerInput'
import { answerReady, formatCorrectAnswer, initialAnswer } from '../../src/materialPractice'
import { decodeQuizSession, type QuizQuestion, type QuizSession } from '../../src/quizSession'
import { noReplay, recordToolActivity, screenError } from '../../src/services/mobileScreenActions'

const typeNames: Record<string, string> = { single_choice: '单选题', multi_choice: '多选题', true_false: '判断题', fill_blank: '填空题', short_answer: '简答题', matching: '匹配题', ordering: '排序题', classification: '分类题', calculation: '计算题', formula: '公式题', coding: '代码题', material_analysis: '材料分析', term_definition: '名词解释', cloze: '完形填空' }
export default function QuizPage() {
  const theme = useTheme(), router = useRouter(), user = useAuthStore(s => s.user), key = 'quiz-session:' + (user?.id || '')
  const lock = useRef(false), saveVersion = useRef(0), loadVersion = useRef(0)
  const [questions, setQuestions] = useState<QuizQuestion[]>([]), [selected, setSelected] = useState<string[]>([]), [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(''), [more, setMore] = useState(false)
  const [session, setSession] = useState<QuizSession | null>(null), [restored, setRestored] = useState<QuizSession | null>(null)
  const [ready, setReady] = useState(false), [readError, setReadError] = useState(''), [saveError, setSaveError] = useState(''), [saveState, setSaveState] = useState('')
  const [dialog, setDialog] = useState<'submit' | 'discard' | null>(null), [semantic, setSemantic] = useState(false)
  const [findOpen, setFindOpen] = useState(false)
  const load = useCallback(async (offset = 0) => {
    const v = ++loadVersion.current; setLoading(true)
    try {
      const data = (await api.get('/quiz/questions', { params: { limit: 100, offset } })).data
      if (!Array.isArray(data)) throw new Error()
      if (v !== loadVersion.current) return
      setQuestions(prev => offset ? [...prev, ...data] : data); setMore(data.length === 100); setError('')
    } catch { if (v === loadVersion.current) setError('题库没有加载成功，已有题目会保留。') }
    finally { if (v === loadVersion.current) setLoading(false) }
  }, [])
  const readDraft = useCallback(async () => {
    setReady(false); setReadError('')
    try {
      const raw = await queueMaterialStorage(storage, key, () => storage.getItem(key))
      if (useAuthStore.getState().user?.id !== user?.id) return
      if (raw) setRestored(decodeQuizSession(raw, user!.id))
      setReady(true)
    } catch { setReadError('练习草稿没有读好，原记录不会被覆盖。请重试，或确认后清除草稿。') }
  }, [key, user?.id])
  useEffect(() => { setSession(null); setRestored(null); readDraft(); load(); return () => { loadVersion.current++; saveVersion.current++ } }, [readDraft, load])
  const persist = useCallback(async (value: QuizSession) => {
    const v = ++saveVersion.current; setSaveState('正在保存到本机…')
    try {
      await queueMaterialStorage(storage, key, () => storage.setJSON(key, value))
      if (v === saveVersion.current) { setSaveError(''); setSaveState('本机进度已保存') }
    } catch { if (v === saveVersion.current) { setSaveError('本机进度没有保存成功，请重试后再离开。'); setSaveState('') } }
  }, [key])
  useEffect(() => { if (ready && session && session.accountId === user?.id) persist(session) }, [ready, session, persist, user?.id])
  const create = async () => {
    if (lock.current || !ready || restored) return
    const ids = selected.length ? selected : questions.filter(q => q.question_text.includes(query.trim())).slice(0, 10).map(q => q.id)
    if (!ids.length) { setError('先选择题目，或换一个搜索词。'); return }
    lock.current = true; setBusy(true); setError('')
    try {
      const exam = (await api.post('/quiz/exam/create', { title: '本次练习', question_ids: ids }, noReplay)).data
      const value: QuizSession = { version: 1, accountId: user!.id, exam, answers: {}, index: 0, result: null }
      decodeQuizSession(JSON.stringify(value), user!.id)
      setSession(value); setSaveError('')
    } catch (e) { setError(screenError(e, '练习没有创建成功，请核对后重试。')) }
    finally { lock.current = false; setBusy(false) }
  }
  const submit = async () => {
    if (lock.current || !session) return
    lock.current = true; setBusy(true); setError('')
    try {
      const answers = Object.fromEntries(session.exam.questions.map(q => [q.id, session.answers[q.id] || initialAnswer(q)]))
      const result = (await api.post('/quiz/exam/submit', { paper_id: session.exam.paper_id, answers, enable_semantic: semantic }, noReplay)).data
      if (!Array.isArray(result.details) || typeof result.total !== 'number') throw new Error()
      setSession(prev => prev ? { ...prev, result } : prev); setDialog(null)
      void recordToolActivity('quiz', Object.keys(answers).filter(id => !!answers[id]).length)
    } catch (e) { setError(screenError(e, '提交没有确认，答案已保留。没有自动再次提交；请核对后决定是否重试。')); setDialog(null) }
    finally { lock.current = false; setBusy(false) }
  }
  const discard = async () => {
    if (lock.current) return
    lock.current = true; setBusy(true); saveVersion.current++
    try { await queueMaterialStorage(storage, key, () => storage.removeItem(key)); setSession(null); setRestored(null); setReadError(''); setReady(true); setSaveError(''); setError(''); setDialog(null); setSelected([]) }
    catch { setSaveError('草稿没有清除成功，当前记录会保留。') }
    finally { lock.current = false; setBusy(false) }
  }
  const q = session?.exam.questions[session.index]
  const input = q ? { ...q, blank_count: q.blank_count || q.blanks?.length || undefined } : null
  const answered = session?.exam.questions.filter(q => answerReady({ ...q, blank_count: q.blank_count || q.blanks?.length }, session.answers[q.id] || initialAnswer(q))).length || 0
  const change = (answer: string) => { if (!busy && q) setSession(prev => prev ? { ...prev, answers: { ...prev.answers, [q.id]: answer } } : prev) }
  const title = session?.result ? '练习结果' : session ? '本次练习' : '题库'
  return <CalmScreen title={title} subtitle={session?.result ? '看看题目、答案和解析。' : session ? '按自己的节奏，想清楚再回答。' : '用几道题，检查一下今天学会了什么。'} onBack={() => router.replace('/review')} backLabel="返回复习">
    {!!readError && <CalmPanel><ScreenNotice text={readError} onRetry={readDraft} /><Button onPress={() => setDialog('discard')}>清除损坏草稿</Button></CalmPanel>}
    {restored && !session && <CalmPanel><Text variant="titleMedium">还有一份练习</Text><Text style={calm.body}>答案与题目位置已保存在本机。</Text><Button mode="contained" onPress={() => { setSession(restored); setRestored(null) }}>继续上次练习</Button><Button onPress={() => setDialog('discard')}>放弃旧练习</Button></CalmPanel>}
    {session?.result ? <>
      <CalmPanel><Text variant="headlineMedium">{Math.round(session.result.percentage)} 分</Text><Text style={calm.body}>正确 {session.result.correct} / {session.result.total}</Text></CalmPanel>
      {session.result.details.map((d, i) => {
        const question = session.exam.questions.find(q => q.id === d.question_id)
        return <CalmPanel key={d.question_id}><Text style={{ color: d.is_correct ? theme.colors.primary : theme.colors.error }}>第 {i + 1} 题 · {d.is_correct ? '答对了' : '再巩固一下'}</Text><Text variant="titleMedium" style={calm.body}>{question?.question_text}</Text>{question?.options?.map(o => <Text key={o.label} style={{ color: theme.colors.onSurfaceVariant, lineHeight: 24 }}>{o.label}. {o.text}</Text>)}<Text style={calm.body}>你的答案：{question ? formatCorrectAnswer(question, d.user_answer) : d.user_answer || '未作答'}</Text><Text style={calm.body}>参考答案：{question ? formatCorrectAnswer(question, d.correct_answer) : d.correct_answer}</Text><Divider /><Text style={calm.body}>{d.analysis || '本题没有提供解析。'}</Text>{d.partial_score != null && <Text style={calm.body}>部分得分：{d.partial_score}</Text>}</CalmPanel>
      })}
      <Button mode="contained" onPress={() => setDialog('discard')}>回到题库</Button>
    </> : session && q && input ? <>
      <Text style={{ color: theme.colors.onSurfaceVariant, marginBottom: 16 }}>第 {session.index + 1} / {session.exam.questions.length} 题 · 已答 {answered} 题</Text>
      <CalmPanel><Text style={{ color: theme.colors.primary }}>{typeNames[q.question_type] || q.question_type}</Text><Text variant="titleLarge" style={{ lineHeight: 30, marginVertical: 20 }}>{q.question_text}</Text><MaterialAnswerInput question={input} value={session.answers[q.id] || initialAnswer(input)} onChange={change} disabled={busy} /></CalmPanel>
      <View style={calm.wrap}><Button disabled={busy || session.index === 0} mode="outlined" onPress={() => setSession(prev => prev ? { ...prev, index: prev.index - 1 } : prev)}>上一题</Button>{session.index < session.exam.questions.length - 1 && <Button disabled={busy} mode="contained" onPress={() => setSession(prev => prev ? { ...prev, index: prev.index + 1 } : prev)}>下一题</Button>}<Button disabled={busy} onPress={() => setDialog('submit')}>交卷</Button></View>
      {session.exam.questions.some(q => ['short_answer', 'material_analysis', 'term_definition', 'calculation', 'formula', 'coding'].includes(q.question_type)) && <MaterialChoice label="启用 AI 批改主观题" name="启用 AI 批改主观题" multiple checked={semantic} onPress={() => setSemantic(v => !v)} disabled={busy} />}
      {semantic && <Text style={calm.body}>开启后会使用当前配置的模型。</Text>}
      <Text style={[calm.body, { color: theme.colors.onSurfaceVariant }]}>{saveState}</Text>
      <Button disabled={busy} onPress={() => setDialog('discard')}>结束并重新选题</Button>
    </> : !restored && ready && <>
      <Button icon="magnify" onPress={() => { setFindOpen(value => !value); setQuery('') }} style={{ alignSelf: 'flex-end', marginBottom: 12 }}>{findOpen ? '收起查找' : '查找当前题目'}</Button>
      {findOpen && <><Searchbar placeholder="仅查找已载入的题目" accessibilityLabel="查找当前题目关键词" value={query} onChangeText={setQuery} elevation={0} style={{ marginBottom: 8 }} /><Text style={calm.body}>只在当前已载入的题目中查找。</Text></>}
      <CalmPanel><Text variant="titleMedium">已载入 {questions.length} 题</Text><Text style={calm.body}>{selected.length ? '已选 ' + selected.length + ' 题' : '不选题时，从搜索结果开始最多 10 题。'}</Text><Button mode="contained" onPress={create} loading={busy} disabled={busy || loading}>开始练习</Button></CalmPanel>
      {loading && !questions.length && <ActivityIndicator />}
      {!loading && !questions.length && !error && <ScreenNotice text="题库还没有题目。请从资料页导入题库或生成练习。" />}
      {questions.filter(q => q.question_text.includes(query.trim())).map(q => <View key={q.id} style={{ marginBottom: 10 }}><Text style={{ color: theme.colors.primary, marginLeft: 12 }}>{typeNames[q.question_type] || q.question_type}</Text><MaterialChoice label={q.question_text} name={'选择题目 ' + q.id} multiple checked={selected.includes(q.id)} disabled={busy} onPress={() => setSelected(prev => prev.includes(q.id) ? prev.filter(id => id !== q.id) : [...prev, q.id])} /><Divider /></View>)}
      {more && <Button onPress={() => load(questions.length)} disabled={loading}>加载更多题目</Button>}
    </>}
    {!!error && <ScreenNotice text={error} onRetry={session ? undefined : () => load()} />}
    {!!saveError && <ScreenNotice text={saveError} onRetry={session ? () => persist(session) : undefined} />}
    <Portal><Dialog visible={!!dialog} onDismiss={() => { if (!busy) setDialog(null) }}>
      <Dialog.Title>{dialog === 'submit' ? '提交这份练习？' : '放下这份练习？'}</Dialog.Title><Dialog.Content><Text>{dialog === 'submit' ? '已答 ' + answered + ' / ' + session?.exam.questions.length + ' 题。未完成的题目也会计入本次结果。' : '本机的这份练习记录会清除，可以重新选题。'}</Text></Dialog.Content><Dialog.Actions><Button disabled={busy} onPress={() => setDialog(null)}>取消</Button><Button disabled={busy} loading={busy} onPress={dialog === 'submit' ? submit : discard}>{dialog === 'submit' ? '确认交卷' : '确认清除'}</Button></Dialog.Actions>
    </Dialog></Portal>
  </CalmScreen>
}
