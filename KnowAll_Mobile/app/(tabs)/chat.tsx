import { useCallback, useEffect, useRef, useState } from 'react'
import { FlatList, KeyboardAvoidingView, Platform, ScrollView, View } from 'react-native'
import { ActivityIndicator, Button, Dialog, Divider, IconButton, List, Portal, Text, TextInput, useTheme } from 'react-native-paper'
import { useFocusEffect, useRouter } from 'expo-router'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useAuthStore } from '../../src/stores'
import api from '../../src/api'
import { ScreenNotice, calm } from '../../src/components/CalmScreen'
import { noReplay, screenError } from '../../src/services/mobileScreenActions'
import type { ChatConversation, ChatMessage } from '../../src/types'

export default function ChatPage() {
  const theme = useTheme(), insets = useSafeAreaInsets(), router = useRouter(), userId = useAuthStore(s => s.user?.id)
  const lock = useRef(false), revision = useRef(0), list = useRef<FlatList<ChatMessage>>(null)
  const [conversations, setConversations] = useState<ChatConversation[]>([]), [active, setActive] = useState<string | null>(null), [messages, setMessages] = useState<ChatMessage[]>([])
  const [drafts, setDrafts] = useState<Record<string, string>>({}), [historyOpen, setHistoryOpen] = useState(false), [loading, setLoading] = useState(false), [sending, setSending] = useState(false)
  const [error, setError] = useState(''), [historyError, setHistoryError] = useState('')
  const draftKey = active || 'new', input = drafts[draftKey] || ''
  const fetchHistory = useCallback(async () => {
    try { const data = (await api.get('/chat/conversations')).data; if (!Array.isArray(data)) throw new Error(); if (useAuthStore.getState().user?.id === userId) { setConversations(data); setHistoryError('') } }
    catch { setHistoryError('历史对话没有读取成功，请重试。') }
  }, [userId])
  useFocusEffect(useCallback(() => { fetchHistory(); return () => { revision.current++ } }, [fetchHistory]))
  useEffect(() => { revision.current++; setMessages([]); setActive(null); setDrafts({}); setError('') }, [userId])
  const choose = async (id: string) => {
    if (lock.current) return
    const v = ++revision.current; setLoading(true); setError(''); setHistoryOpen(false)
    try {
      const data = (await api.get('/chat/conversations/' + encodeURIComponent(id))).data
      if (data.conversation_id !== id || !Array.isArray(data.messages)) throw new Error()
      if (v !== revision.current) return
      setActive(id); setMessages(data.messages)
    } catch { if (v === revision.current) setError('这段对话没有加载成功，当前对话会保留。请从历史对话重试。') }
    finally { if (v === revision.current) setLoading(false) }
  }
  const fresh = () => {
    if (lock.current) return
    revision.current++; setLoading(false); setActive(null); setMessages([]); setError('')
  }
  const send = async () => {
    const question = input.trim()
    if (!question || lock.current || loading) return
    lock.current = true; setSending(true); setError(''); const v = revision.current, oldKey = draftKey
    try {
      const data = (await api.post('/chat/assistant', { conversation_id: active, message: question }, noReplay)).data
      if (typeof data.message !== 'string' || !data.conversation_id) throw new Error()
      if (v !== revision.current || useAuthStore.getState().user?.id !== userId) return
      setMessages(prev => [...prev, { id: 'u-' + Date.now(), role: 'user', content: question, created_at: new Date().toISOString() }, { id: 'a-' + Date.now(), role: 'assistant', content: data.message, created_at: new Date().toISOString() }])
      setDrafts(prev => ({ ...prev, [oldKey]: '' })); setActive(data.conversation_id)
      void fetchHistory()
    } catch (e) { if (v === revision.current) setError(screenError(e, '没有收到回复，问题已保留。若刚才网络中断，先查看历史对话核对，再决定是否重新发送。')) }
    finally { lock.current = false; setSending(false) }
  }
  const setInput = (value: string) => setDrafts(prev => ({ ...prev, [draftKey]: value }))
  return <KeyboardAvoidingView style={{ flex: 1, backgroundColor: theme.colors.background, paddingTop: insets.top + 12 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
    <View style={[calm.row, { paddingHorizontal: 10 }]}><IconButton icon="arrow-left" accessibilityLabel="返回复习" disabled={sending} onPress={() => router.replace('/review')} /><Text variant="titleLarge" accessibilityRole="header" style={{ flex: 1, fontWeight: '600' }}>学习问答</Text><IconButton icon="history" accessibilityLabel="历史对话" disabled={sending} onPress={() => { setHistoryOpen(true); fetchHistory() }} /><IconButton icon="plus" accessibilityLabel="新对话" disabled={sending} onPress={fresh} /></View>
    <Text numberOfLines={2} style={{ paddingHorizontal: 20, paddingBottom: 14, color: theme.colors.onSurfaceVariant }}>{active ? conversations.find(c => c.id === active)?.title || '当前对话' : '把不明白的地方，问清楚。'}</Text>
    <Divider />
    {loading && <ActivityIndicator style={{ marginVertical: 16 }} />}
    <FlatList ref={list} data={messages} keyExtractor={item => item.id} style={{ flex: 1 }} contentContainerStyle={{ padding: 20, flexGrow: 1 }} keyboardShouldPersistTaps="handled"
      onContentSizeChange={() => list.current?.scrollToEnd({ animated: false })}
      ListEmptyComponent={!loading ? <View style={{ paddingTop: 24 }}><Text variant="headlineSmall">今天想弄懂什么？</Text><Text style={[calm.body, { color: theme.colors.onSurfaceVariant }]}>写下具体问题，或先从下面的方向开始。</Text>{['用例子解释一个概念', '帮我梳理复习思路'].map(text => <Button key={text} mode="outlined" disabled={sending} style={{ marginTop: 14 }} onPress={() => setInput(text)}>{text}</Button>)}</View> : null}
      renderItem={({ item }) => <View style={{ marginBottom: 24, padding: 16, borderRadius: 12, backgroundColor: item.role === 'user' ? theme.colors.primaryContainer : theme.colors.surface, borderWidth: item.role === 'user' ? 0 : 1, borderColor: theme.colors.outlineVariant }}>
        <Text variant="labelMedium" style={{ color: theme.colors.onSurfaceVariant, marginBottom: 8 }}>{item.role === 'user' ? '你' : '学习助手'}</Text><Text selectable style={{ color: item.role === 'user' ? theme.colors.onPrimaryContainer : theme.colors.onSurface, lineHeight: 27 }}>{item.content}</Text>
      </View>} />
    {sending && <Text accessibilityLiveRegion="polite" style={{ color: theme.colors.onSurfaceVariant, paddingHorizontal: 20, paddingVertical: 8 }}>正在等待回复…</Text>}
    {!!error && <ScrollView style={{ maxHeight: 120 }} contentContainerStyle={{ paddingHorizontal: 20 }}><ScreenNotice text={error} /></ScrollView>}
    <View style={{ padding: 16, backgroundColor: theme.colors.surface, borderTopWidth: 1, borderColor: theme.colors.outlineVariant }}>
      <TextInput label="你的问题" accessibilityLabel="你的问题" placeholder="输入具体的学习问题" mode="outlined" value={input} onChangeText={setInput} multiline maxLength={2000} disabled={sending || loading} style={{ maxHeight: 140 }} />
      <View style={[calm.row, { marginTop: 8 }]}><Text style={{ flex: 1, color: theme.colors.onSurfaceVariant }}>{input.length} / 2000</Text><Button mode="contained" onPress={send} disabled={sending || loading || !input.trim()} loading={sending}>发送问题</Button></View>
    </View>
    <Portal><Dialog visible={historyOpen} onDismiss={() => setHistoryOpen(false)} style={{ width: '90%', maxWidth: 460, alignSelf: 'center' }}><Dialog.Title>历史对话</Dialog.Title><Dialog.ScrollArea><ScrollView contentContainerStyle={{ paddingVertical: 8 }}>
      {!!historyError && <ScreenNotice text={historyError} onRetry={fetchHistory} />}
      {!historyError && !conversations.length && <Text style={calm.body}>还没有历史对话。</Text>}
      {conversations.map(c => <List.Item key={c.id} title={c.title} titleNumberOfLines={3} onPress={() => choose(c.id)} right={p => <List.Icon {...p} icon="chevron-right" />} />)}
    </ScrollView></Dialog.ScrollArea><Dialog.Actions><Button onPress={() => setHistoryOpen(false)}>关闭</Button></Dialog.Actions></Dialog></Portal>
  </KeyboardAvoidingView>
}
