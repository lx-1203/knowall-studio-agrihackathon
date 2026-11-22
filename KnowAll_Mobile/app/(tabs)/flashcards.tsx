import { useCallback, useRef, useState } from 'react'
import { View } from 'react-native'
import { ActivityIndicator, Button, Chip, Divider, Text, useTheme } from 'react-native-paper'
import { useFocusEffect, useRouter } from 'expo-router'
import api from '../../src/api'
import { CalmScreen, CalmPanel, ScreenNotice, calm } from '../../src/components/CalmScreen'
import { noReplay, recordToolActivity } from '../../src/services/mobileScreenActions'

type Card = { id: string; front: string; back: string; hints?: string; card_type?: string }
type Deck = { id: string; name: string; card_count: number }
export default function FlashcardPage() {
  const router = useRouter(), theme = useTheme(), request = useRef(0), lock = useRef(false)
  const [decks, setDecks] = useState<Deck[]>([]), [selected, setSelected] = useState(''), [cards, setCards] = useState<Card[]>([])
  const [index, setIndex] = useState(0), [flipped, setFlipped] = useState(false), [done, setDone] = useState(0)
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(''), [deckError, setDeckError] = useState('')
  const load = useCallback(async (id: string) => {
    const version = ++request.current; setLoading(true); setError('')
    try {
      const data = (await api.get('/flashcards/due', { params: { limit: 50, deck_id: id || undefined } })).data
      if (!Array.isArray(data.cards)) throw new Error()
      if (version !== request.current) return
      setCards(data.cards); setIndex(0); setFlipped(false)
    } catch { if (version === request.current) setError('卡片没有加载成功，请重试。') }
    finally { if (version === request.current) setLoading(false) }
  }, [])
  const loadDecks = useCallback(async () => {
    try { const data = (await api.get('/flashcards/decks')).data; if (!Array.isArray(data)) throw new Error(); setDecks(data); setDeckError('') }
    catch { setDeckError('卡组暂时无法读取，仍可以复习全部卡片。') }
  }, [])
  useFocusEffect(useCallback(() => { load(selected); loadDecks(); return () => { request.current++ } }, [load, loadDecks, selected]))
  const choose = (id: string) => { if (!lock.current) { setSelected(id); setDone(0) } }
  const rate = async (rating: number) => {
    const card = cards[index]
    if (lock.current || !card || !flipped) return
    lock.current = true; setBusy(true); setError(''); const version = request.current
    try {
      const res = (await api.post('/flashcards/review', { card_id: card.id, rating }, noReplay)).data
      if (res.card_id !== card.id) throw new Error()
      if (version !== request.current) return
      setCards(prev => prev.filter(c => c.id !== card.id)); setIndex(Math.max(0, Math.min(index, cards.length - 2))); setFlipped(false); setDone(v => v + 1)
      void recordToolActivity('flashcard')
    } catch { if (version === request.current) setError('评分未确认，答案已保留。请重新加载核对排期，避免重复评分。') }
    finally { lock.current = false; setBusy(false) }
  }
  const card = cards[index]
  return <CalmScreen title="知识卡" subtitle="先想一想，再翻开答案。" onBack={() => router.replace('/review')} backLabel="返回复习" refreshing={loading} onRefresh={busy ? undefined : () => load(selected)}>
    <View style={[calm.wrap, { marginBottom: 20 }]}>
      <Chip selected={!selected} disabled={busy} onPress={() => choose('')}>全部卡组</Chip>
      {decks.map(d => <Chip key={d.id} selected={selected === d.id} disabled={busy} onPress={() => choose(d.id)}>{d.name} ({d.card_count})</Chip>)}
    </View>
    {!!deckError && <ScreenNotice text={deckError} onRetry={loadDecks} />}
    <CalmPanel>
      <Text style={{ color: theme.colors.onSurfaceVariant }}>{loading ? '正在读取卡片…' : '本组剩余 ' + cards.length + ' 张'}{done > 0 ? ' · 已完成 ' + done + ' 张' : ''}</Text>
      {loading ? <ActivityIndicator style={{ marginVertical: 28 }} /> : card ? <>
        <Text variant="titleLarge" style={{ lineHeight: 32, marginVertical: 28 }}>{card.card_type === 'cloze' ? card.front.replace(/\{\{c\d+::([^}]+)\}\}/g, '____') : card.front}</Text>
        {flipped ? <><Divider /><Text style={[calm.body, { marginVertical: 24 }]}>{card.back}</Text><Button onPress={() => setFlipped(false)} disabled={busy}>收起答案</Button>
          <Text style={[calm.body, { color: theme.colors.onSurfaceVariant }]}>这次记得怎么样？</Text><View style={calm.wrap}>{['再学一次', '有点难', '记住了', '很熟悉'].map((label, i) => <Button key={label} mode={i === 2 ? 'contained' : 'outlined'} contentStyle={{ minHeight: 48 }} style={{ width: '47%' }} disabled={busy || !!error} onPress={() => rate(i + 1)}>{label}</Button>)}</View>
        </> : <><Button mode="contained" onPress={() => setFlipped(true)}>查看答案</Button>{!!card.hints && <Text style={[calm.body, { color: theme.colors.onSurfaceVariant }]}>提示：{card.hints}</Text>}</>}
      </> : !error && <><Text variant="titleLarge" style={{ marginTop: 24 }}>{done ? '这一组复习完成了' : '当前卡组没有到期卡片'}</Text><Text style={calm.body}>可以换一个卡组，或稍后再回来。</Text><Button mode="outlined" onPress={() => load(selected)}>重新加载卡片</Button></>}
      {!!error && <ScreenNotice text={error} onRetry={busy ? undefined : () => load(selected)} />}
      {busy && <Text style={calm.body}>正在保存评分…</Text>}
    </CalmPanel>
    {!!card && cards.length > 1 && <View style={calm.wrap}><Button disabled={busy || index === 0} onPress={() => { setIndex(v => v - 1); setFlipped(false) }}>上一张</Button><Button disabled={busy || index === cards.length - 1} onPress={() => { setIndex(v => v + 1); setFlipped(false) }}>暂时跳过</Button></View>}
  </CalmScreen>
}
