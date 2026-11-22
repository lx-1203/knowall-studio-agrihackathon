import { useEffect, useRef, useState } from 'react'
import { isAxiosError } from 'axios'
import { View, StyleSheet, ScrollView } from 'react-native'
import { ActivityIndicator, Button, Surface, Text, TextInput, TouchableRipple, useTheme } from 'react-native-paper'
import { chatWithAssistant, getMaterialLearningPack, reviewMaterialCard, startMaterialPractice, submitMaterialAnswer } from '../api'
import { materialContext, type MaterialPack, type MaterialPracticeItem, type MaterialAnswerResult } from '../materialLearning'
import type { DocumentChunk } from '../types'
import MaterialMindMap from './MaterialMindMap'
import MaterialAnswerInput from './MaterialAnswerInput'
import MaterialGeneration from './MaterialGeneration'
import { answerReady, formatCorrectAnswer, initialAnswer, inputIssue } from '../materialPractice'

const tabs = [['knowledge', '知识点'], ['map', '导图'], ['cards', '知识卡'], ['practice', '练习'], ['assistant', '问答']] as const

export default function MaterialLearningTools({ documentId, title, chunks, chunk, onSource }: {
  documentId: string; title: string; chunks: DocumentChunk[]; chunk?: DocumentChunk; onSource: (ids: string[]) => void
}) {
  const theme = useTheme()
  const [tab, setTab] = useState('knowledge')
  const [pack, setPack] = useState<MaterialPack | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [revealed, setRevealed] = useState<string[]>([])
  const [question, setQuestion] = useState('')
  const [reply, setReply] = useState('')
  const [askError, setAskError] = useState('')
  const [asking, setAsking] = useState(false)
  const revision = useRef(0)
  const sourceRevision = useRef(0)
  const [items, setItems] = useState<MaterialPracticeItem[] | null>(null)
  const [index, setIndex] = useState(0)
  const [answer, setAnswer] = useState('')
  const [feedback, setFeedback] = useState<MaterialAnswerResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [practiceError, setPracticeError] = useState('')
  const started = useRef(Date.now())
  const operation = useRef(false)
  const [mode, setMode] = useState<'practice' | 'smart'>('practice')
  const [blocked, setBlocked] = useState(false)
  const [reviewRevealed, setReviewRevealed] = useState(false)
  const [ratedCards, setRatedCards] = useState<string[]>([])
  const [cardErrors, setCardErrors] = useState<Record<string, string>>({})
  const [generationOpen, setGenerationOpen] = useState(false)
  const [generationNotice, setGenerationNotice] = useState('')

  useEffect(() => {
    const current = ++revision.current
    setPack(null); setLoading(true); setError(''); setRevealed([])
    setItems(null); setFeedback(null); setAnswer(''); setBusy(false); setPracticeError('')
    operation.current = false; setBlocked(false); setReviewRevealed(false); setRatedCards([]); setCardErrors({})
    setGenerationOpen(false); setGenerationNotice('')
    getMaterialLearningPack(documentId).then(value => { if (revision.current === current) setPack(value) })
      .catch(() => { if (revision.current === current) setError('学习内容加载失败，原资料仍可阅读。') })
      .finally(() => { if (revision.current === current) setLoading(false) })
    return () => { revision.current++ }
  }, [documentId, attempt])
  useEffect(() => {
    sourceRevision.current++; setReply(''); setQuestion(''); setAskError(''); setAsking(false)
    return () => { sourceRevision.current++ }
  }, [documentId, chunk?.id])

  const ask = async () => {
    if (!chunk || !question.trim() || asking) return
    const current = sourceRevision.current
    setAsking(true); setAskError('')
    try {
      const data = await chatWithAssistant({ message: question.trim(), role_preset: 'tutor', knowledge_context: materialContext(chunk) })
      if (current === sourceRevision.current) {
        const response = data?.message || data?.reply
        if (typeof response !== 'string' || !response.trim()) throw new Error('Empty reply')
        setReply(response)
      }
    } catch { if (current === sourceRevision.current) setAskError('回答未完成，问题已保留，请检查模型设置后重试。') }
    finally { if (current === sourceRevision.current) setAsking(false) }
  }
  const begin = async (sessionMode: 'practice' | 'smart' = 'practice') => {
    if (!pack || pack.stale || blocked || operation.current) return
    operation.current = true
    const current = revision.current
    setBusy(true); setPracticeError('')
    try {
      const data = await startMaterialPractice(pack, sessionMode)
      if (current !== revision.current) return
      if (!Array.isArray(data.items)) throw new Error('Invalid session')
      setMode(sessionMode); setItems(data.items); setIndex(0); setAnswer(data.items[0]?.question ? initialAnswer(data.items[0].question) : ''); setFeedback(null); setReviewRevealed(false); started.current = Date.now()
    } catch (reason) { if (current === revision.current) {
      if (isAxiosError(reason) && reason.response?.status === 409) setBlocked(true)
      setPracticeError('练习加载失败，请重试；如果资料已更新，请先重新整理学习内容。')
    } }
    finally { if (current === revision.current) { operation.current = false; setBusy(false) } }
  }
  const item = items?.[index]
  const next = () => {
    const nextItem = items?.[index + 1]
    setIndex(index + 1); setAnswer(nextItem?.question ? initialAnswer(nextItem.question) : ''); setFeedback(null); setReviewRevealed(false); setPracticeError(''); started.current = Date.now()
  }
  const submit = async () => {
    if (!pack || pack.stale || blocked || !item?.question || !answerReady(item.question, answer) || operation.current || feedback) return
    operation.current = true
    const current = revision.current
    setBusy(true); setPracticeError('')
    try {
      const data = await submitMaterialAnswer(pack.id, item.question.id, answer, Date.now() - started.current)
      if (typeof data?.is_correct !== 'boolean') throw new Error('Invalid grading result')
      if (current === revision.current) setFeedback(data)
    } catch (reason) { if (current === revision.current) {
      if (isAxiosError(reason) && reason.response?.status === 409) setBlocked(true)
      setPracticeError('提交失败，答案已保留，请重试。')
    } }
    finally { if (current === revision.current) { operation.current = false; setBusy(false) } }
  }
  const rate = async (cardId: string, rating: number, advance: boolean) => {
    // A due card in a new session can be reviewed again; the knowledge-card receipt only locks its own widget.
    if (!pack || pack.stale || blocked || operation.current || (!advance && ratedCards.includes(cardId))) return
    operation.current = true
    const current = revision.current
    setBusy(true); setPracticeError(''); setCardErrors(errors => ({ ...errors, [cardId]: '' }))
    try {
      const result = await reviewMaterialCard(pack, cardId, rating)
      if (result?.card_id !== cardId) throw new Error('Invalid review receipt')
      if (current !== revision.current) return
      setRatedCards(cards => cards.includes(cardId) ? cards : [...cards, cardId])
      if (advance) next()
    } catch (reason) { if (current === revision.current) {
      if (isAxiosError(reason) && reason.response?.status === 409) setBlocked(true)
      const message = '评分保存失败，答案已保留，请重试。'
      if (advance) setPracticeError(message)
      else setCardErrors(errors => ({ ...errors, [cardId]: message }))
    } }
    finally { if (current === revision.current) { operation.current = false; setBusy(false) } }
  }
  const source = (ids?: string[]) => onSource(ids || [])
  const hasContent = pack && pack.sections.length > 0
  return <View style={styles.root}>
    {!generationOpen && <View style={styles.toolHeading}><Text variant="titleLarge" style={{ fontWeight: '600' }}>学习内容</Text><Button compact mode="text" disabled={loading || busy || asking || !chunks.some(chunk => !!chunk.text.trim())} onPress={() => setGenerationOpen(true)}>整理学习内容</Button></View>}
    <MaterialGeneration active={generationOpen} documentId={documentId} title={title} previousId={pack?.id} onCancel={() => setGenerationOpen(false)} onResume={() => setGenerationOpen(true)} onGenerated={(value, message) => {
      setPack(value); setError(''); setGenerationOpen(false); setGenerationNotice(message)
      setItems(null); setFeedback(null); setAnswer(''); setRevealed([]); setRatedCards([]); setCardErrors({}); setBlocked(false); setPracticeError('')
    }} />
    {!generationOpen && <>
    {!!generationNotice && <Text accessibilityLiveRegion="polite" style={{ color: theme.colors.primary }}>{generationNotice}</Text>}
    <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ flexGrow: 0 }} contentContainerStyle={styles.tabs}>
      {tabs.map(([value, label]) => <TouchableRipple key={value} accessibilityRole="tab" accessibilityState={{ selected: tab === value }} onPress={() => setTab(value)}
        style={[styles.tab, { borderBottomColor: tab === value ? theme.colors.primary : 'transparent' }]}>
        <Text variant="titleSmall" style={{ color: tab === value ? theme.colors.primary : theme.colors.onSurfaceVariant }}>{label}</Text>
      </TouchableRipple>)}
    </ScrollView>
    {blocked && <View style={styles.stack}><Text style={{ color: theme.colors.error }}>资料或题目已更新，请刷新学习内容后再继续。</Text><Button disabled={busy} onPress={() => setAttempt(n => n + 1)}>刷新学习内容</Button></View>}
    {tab === 'map' ? <View style={styles.stack}>
      {pack?.stale && <Text style={{ color: theme.colors.error }}>资料已更新，章节内容可能过期。</Text>}
      {!!error && <><Text style={{ color: theme.colors.error }}>{error} 以下显示原文结构。</Text><Button onPress={() => setAttempt(n => n + 1)}>重试学习内容</Button></>}
      {loading && <ActivityIndicator />}
      <MaterialMindMap title={title} sections={pack?.sections || []} chunks={chunks} activeId={chunk?.id} onSource={onSource} />
    </View> : tab === 'assistant' ? <View style={styles.stack}>
      <Text>当前依据：{chunk?.page_range ? `${chunk.page_range} 页` : chunk ? `阅读片段 ${chunk.index + 1}` : '正文未就绪'}</Text>
      <Text variant="bodySmall">点击发送会使用你配置的 AI 模型。{(chunk?.text.length || 0) > 20000 ? '当前片段仅使用前 20,000 字。' : ''}</Text>
      <TextInput label="向当前资料提问" multiline value={question} onChangeText={setQuestion} disabled={asking || !chunk} />
      <Button mode="contained" loading={asking} disabled={asking || !chunk || !question.trim()} onPress={ask}>发送问题</Button>
      {!!askError && <Text style={{ color: theme.colors.error }}>{askError}</Text>}
      {!!reply && <Text selectable style={styles.body}>{reply}</Text>}
    </View> : loading ? <ActivityIndicator /> : error ? <View style={styles.stack}><Text style={{ color: theme.colors.error }}>{error}</Text><Button onPress={() => setAttempt(n => n + 1)}>重试</Button></View> : !hasContent ? <View style={styles.stack}>
      <Text>这份资料还没有整理学习内容。</Text><Text variant="bodySmall">可以继续直接阅读，或点击上方整理学习内容。独立试卷仍保留在阅读区。</Text><Button onPress={() => setAttempt(n => n + 1)}>刷新学习内容</Button>
    </View> : <>
      {pack.stale && <Text style={{ color: theme.colors.error }}>资料已更新。以下内容可能过期，请重新整理后练习。</Text>}
      {tab === 'knowledge' && pack.sections.map((section, position) => <Surface key={section.id || position} elevation={0} style={[styles.section, { backgroundColor: theme.colors.surface, borderBottomColor: theme.colors.outlineVariant }]}>
        <Text variant="labelMedium" style={{ color: theme.colors.onSurfaceVariant }}>章节 {position + 1}</Text><Text variant="titleLarge" style={{ fontWeight: '600' }}>{section.title || `章节 ${position + 1}`}</Text><Text selectable style={styles.body}>{section.notes || '本章讲解尚未生成'}</Text>
        <Button onPress={() => source(section.chunk_ids)}>阅读对应原文</Button></Surface>)}
      {tab === 'cards' && (pack.sections.some(section => section.flashcards.length) ? pack.sections.map(section => section.flashcards.map(card => <Surface key={card.id} testID={`material-card-${card.id}`} elevation={0} style={[styles.section, { backgroundColor: theme.colors.surface, borderBottomColor: theme.colors.outlineVariant }]}>
        <Text variant="labelMedium">{section.title}</Text><Text style={styles.body}>{card.front}</Text>
        {revealed.includes(card.id) && <Text selectable style={styles.body}>{card.back}</Text>}
        <Button onPress={() => setRevealed(values => values.includes(card.id) ? values.filter(id => id !== card.id) : [...values, card.id])}>{revealed.includes(card.id) ? '收起答案' : '显示答案'}</Button>
        {revealed.includes(card.id) && <>
          <View style={styles.cardStatus}>
            <Text variant="bodySmall" accessibilityLiveRegion="polite">{ratedCards.includes(card.id) ? '评分已保存到复习记录' : '根据回忆情况评分，保存到当前账号的复习记录。'}</Text>
            {!!cardErrors[card.id] && <Text variant="bodySmall" style={{ color: theme.colors.error }}>{cardErrors[card.id]}</Text>}
          </View>
          <View style={styles.row}>{['重来', '困难', '良好', '简单'].map((label, i) => <Button key={label} mode="outlined" contentStyle={{ minHeight: 48 }} disabled={busy || pack.stale || blocked || ratedCards.includes(card.id)} onPress={() => rate(card.id, i + 1, false)}>{label}</Button>)}</View>
        </>}
        <Button onPress={() => source(section.chunk_ids)}>查看来源原文</Button></Surface>)) : <Text>这份资料尚没有知识卡。</Text>)}
      {tab === 'practice' && <View style={styles.stack}>
        {!!practiceError && <Text style={{ color: theme.colors.error }}>{practiceError}</Text>}
        {items === null ? <><Button mode="contained" loading={busy} disabled={busy || pack.stale || blocked} onPress={() => begin('practice')}>开始当前资料练习</Button>
          <Button mode="outlined" disabled={busy || pack.stale || blocked} onPress={() => begin('smart')}>智能复习</Button><Text variant="bodySmall">复习当前资料的错题与到期知识卡。</Text></> : !item ? <><Text>{mode === 'smart' ? '本轮复习结束。' : '本轮练习结束。'}</Text><Button disabled={busy || pack.stale || blocked} onPress={() => begin(mode)}>再练一轮</Button>
          <Button disabled={busy || pack.stale || blocked} onPress={() => begin(mode === 'practice' ? 'smart' : 'practice')}>{mode === 'practice' ? '智能复习' : '章节练习'}</Button></> : <>
          <Text>{index + 1} / {items.length} · {item.title || '当前资料练习'}</Text>
          {item.question ? <>
            <Text selectable style={styles.body}>{item.question.question_text}</Text>
            <MaterialAnswerInput question={item.question} value={answer} onChange={setAnswer} disabled={busy || !!feedback || pack.stale || blocked} />
              {!feedback ? <Button mode="contained" loading={busy} disabled={busy || pack.stale || blocked || !answerReady(item.question, answer)} onPress={submit}>提交答案</Button> : <>
                <Text style={{ color: feedback.is_correct ? theme.colors.primary : theme.colors.error }}>{feedback.is_correct ? '回答正确' : '继续巩固'}</Text>
                <Text selectable>正确答案：{formatCorrectAnswer(item.question, feedback.correct_answer)}</Text>
                {typeof feedback.score_ratio === 'number' && Number.isFinite(feedback.score_ratio) && <Text>得分：{Math.round(Math.max(0, Math.min(1, feedback.score_ratio)) * 100)}%</Text>}
                <Text selectable style={styles.body}>{feedback.analysis || ''}</Text><Button disabled={busy} onPress={next}>下一题</Button>
              </>}
              {!!inputIssue(item.question) && <Button disabled={busy} onPress={next}>跳过此题</Button>}
          </> : item.card ? <>
            <Text style={styles.body}>{item.card.front}</Text>
            {!reviewRevealed ? <Button onPress={() => setReviewRevealed(true)}>显示答案</Button> : <>
              <Text selectable style={styles.body}>{item.card.back}</Text>
              <View style={styles.row}>{['重来', '困难', '良好', '简单'].map((label, i) => <Button key={label} mode="outlined" contentStyle={{ minHeight: 48 }} disabled={busy || pack.stale || blocked} onPress={() => rate(item.card!.id, i + 1, true)}>{label}</Button>)}</View>
            </>}
          </> : <><Text>当前项内容不完整，请刷新学习内容。</Text><Button disabled={busy} onPress={next}>下一项</Button></>}
          <Button onPress={() => source(feedback?.source?.chunk_ids || item.source?.chunk_ids)}>定位来源原文</Button>
        </>}
      </View>}
    </>}
    </>}
  </View>
}
const styles = StyleSheet.create({ root: { gap: 12 }, toolHeading: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 4 }, tabs: { flexDirection: 'row', gap: 2 }, tab: { minWidth: 48, minHeight: 48, alignItems: 'center', justifyContent: 'center', borderBottomWidth: 2, paddingHorizontal: 6 }, row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 }, stack: { gap: 14 }, cardStatus: { minHeight: 96, gap: 8 }, section: { paddingVertical: 20, borderBottomWidth: StyleSheet.hairlineWidth, gap: 14 }, body: { fontSize: 16, lineHeight: 30 } })
