import { useCallback, useRef, useState } from 'react'
import { View } from 'react-native'
import { ActivityIndicator, Button, Divider, List, Text, useTheme } from 'react-native-paper'
import { useRouter, useFocusEffect } from 'expo-router'
import api from '../../src/api'
import { CalmPanel, CalmScreen, ScreenNotice, calm } from '../../src/components/CalmScreen'
import { noReplay, screenError } from '../../src/services/mobileScreenActions'

type Card = { id: string; front: string; back: string }
export default function ReviewPage() {
  const theme = useTheme(), router = useRouter(), lock = useRef(false), revision = useRef(0)
  const [cards, setCards] = useState<Card[]>([]), [due, setDue] = useState(0), [revealed, setRevealed] = useState(false)
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(''), [saved, setSaved] = useState(0)
  const load = useCallback(async () => {
    const version = ++revision.current; setLoading(true); setError('')
    try {
      const res = (await api.get('/flashcards/due', { params: { limit: 20 } })).data
      if (!Array.isArray(res.cards) || typeof res.due_count !== 'number' || res.cards.some((c: Card) => !c.id || typeof c.front !== 'string' || typeof c.back !== 'string')) throw new Error()
      if (version !== revision.current) return
      setCards(res.cards); setDue(res.due_count); setRevealed(false)
    } catch { if (version === revision.current) setError('复习卡片没有加载成功，重试后继续。') }
    finally { if (version === revision.current) setLoading(false) }
  }, [])
  useFocusEffect(useCallback(() => { load(); return () => { revision.current++ } }, [load]))
  const rate = async (rating: number) => {
    if (lock.current || !cards[0]) return
    lock.current = true; setBusy(true); setError(''); const card = cards[0], version = revision.current
    try {
      const res = (await api.post('/flashcards/review', { card_id: card.id, rating }, noReplay)).data
      if (res.card_id !== card.id) throw new Error()
      if (version !== revision.current) return
      setCards(prev => prev.filter(c => c.id !== card.id)); setDue(prev => Math.max(0, prev - 1)); setRevealed(false); setSaved(prev => prev + 1)
    } catch (e) { if (version === revision.current) setError(screenError(e, '评分未确认，卡片已保留。可重新加载核对排期，避免重复评分。')) }
    finally { lock.current = false; setBusy(false) }
  }
  return <CalmScreen title="复习" subtitle="留一点时间，让学过的内容慢慢记牢。" onRefresh={busy ? undefined : load} refreshing={loading}>
    <CalmPanel>
      <Text variant="labelLarge" style={{ color: theme.colors.primary }}>今日知识卡</Text>
      {loading ? <ActivityIndicator style={{ marginVertical: 24 }} /> : <>
        <Text variant="headlineMedium" style={{ marginTop: 12 }}>{error && !cards.length ? '待加载' : due + ' 张待复习'}</Text>
        {saved > 0 && <Text style={calm.body}>本次已完成 {saved} 张</Text>}
        {cards[0] ? <>
          <Text variant="titleLarge" style={{ lineHeight: 30, marginVertical: 24 }}>{cards[0].front}</Text>
          {revealed ? <><Divider /><Text style={[calm.body, { marginVertical: 20 }]}>{cards[0].back}</Text><Text style={{ color: theme.colors.onSurfaceVariant, marginBottom: 12 }}>刚才记得怎么样？</Text>
            <View style={calm.wrap}>{['再学一次', '有点难', '记住了', '很熟悉'].map((label, i) => <Button key={label} mode={i === 2 ? 'contained' : 'outlined'} disabled={busy} onPress={() => rate(i + 1)} contentStyle={{ minHeight: 48 }} style={{ width: '47%' }}>{label}</Button>)}</View>
          </> : <Button mode="contained" onPress={() => setRevealed(true)}>查看答案</Button>}
        </> : !error && <><Text style={calm.body}>{saved ? '这一组已完成。可以加载下一组，或换一种方式练习。' : '当前没有到期知识卡。也可以从题库开始练习。'}</Text>{saved > 0 && <Button mode="outlined" onPress={load}>加载下一组</Button>}</>}
      </>}
      {busy && <Text style={calm.body}>正在保存评分…</Text>}
      {!!error && <ScreenNotice text={error} onRetry={busy ? undefined : load} />}
    </CalmPanel>
    <Text variant="titleMedium" style={calm.section}>练习与记忆</Text>
    {([['全部题库', '从题目中检查掌握情况', 'notebook-outline', '/quiz'], ['知识卡片库', '查看卡组和更多卡片', 'cards-outline', '/flashcards'], ['背诵练习', '复述、对照，再巩固一次', 'text-box-outline', '/recitation']] as const).map(([title, description, icon, href]) => <View key={title}><List.Item title={title} description={description} onPress={() => router.push(href)} left={p => <List.Icon {...p} icon={icon} />} right={p => <List.Icon {...p} icon="chevron-right" />} /><Divider /></View>)}
    <Text variant="titleMedium" style={[calm.section, { marginTop: 28 }]}>其他学习工具</Text>
    {([['知识检索', 'text-search', '/knowledge'], ['学习问答', 'message-text-outline', '/chat'], ['实时对话', 'microphone-outline', '/live']] as const).map(([title, icon, href]) => <List.Item key={title} title={title} onPress={() => router.push(href)} left={p => <List.Icon {...p} icon={icon} />} right={p => <List.Icon {...p} icon="chevron-right" />} />)}
  </CalmScreen>
}
