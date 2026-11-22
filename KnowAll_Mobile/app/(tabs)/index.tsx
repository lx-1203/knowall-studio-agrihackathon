import { useCallback, useState } from 'react'
import { View, StyleSheet, ScrollView, RefreshControl } from 'react-native'
import { useFocusEffect, useRouter } from 'expo-router'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { ActivityIndicator, Button, Chip, Icon, Surface, Text, TouchableRipple, useTheme } from 'react-native-paper'
import { listDocuments } from '../../src/api'
import { useAuthStore } from '../../src/stores'
import { storage } from '../../src/services/storage'
import type { Document } from '../../src/types'
import DailyCheckInCard from '../../src/components/DailyCheckInCard'

const kinds = [['all', '全部'], ['knowledge', '知识资料'], ['question_bank', '题库资料'], ['mixed', '混合资料']] as const
export default function MaterialsHome() {
  const router = useRouter()
  const theme = useTheme()
  const insets = useSafeAreaInsets()
  const userId = useAuthStore(s => s.user?.id)
  const [documents, setDocuments] = useState<Document[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [kind, setKind] = useState('all')
  const [subject, setSubject] = useState('')
  const [recentId, setRecentId] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [attempt, setAttempt] = useState(0)
  useFocusEffect(useCallback(() => {
    let active = true
    setLoading(true); setError(false)
    Promise.all([listDocuments(1000), storage.getItem(`material-recent:${userId}`).catch(() => null)])
      .then(([result, recent]) => {
        const docs = Array.isArray(result) ? result : result?.documents
        if (!Array.isArray(docs)) throw new Error('Invalid document list')
        if (active) { setDocuments(docs); setRecentId(recent) }
      }).catch(() => { if (active) setError(true) })
      .finally(() => { if (active) { setLoading(false); setRefreshing(false) } })
    return () => { active = false }
  }, [userId, attempt]))
  const open = (doc: Document) => router.push({ pathname: '/material', params: { id: doc.id } })
  const subjects = [...new Set(documents.map(doc => doc.subject).filter((value): value is string => !!value))]
  const filtered = documents.filter(doc => (kind === 'all' || (doc.material_kind || 'knowledge') === kind)
    && (!subject || doc.subject === subject))
  const recent = documents.find(doc => doc.id === recentId)
  return <ScrollView style={{ backgroundColor: theme.colors.background }} contentContainerStyle={[styles.content, { paddingTop: insets.top + 20 }]}
    refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); setAttempt(n => n + 1) }} tintColor={theme.colors.primary} />}>
    <View style={styles.heading}><View style={{ flex: 1 }}>
      <Text variant="labelLarge" style={{ color: theme.colors.onSurfaceVariant, marginBottom: 8 }}>学习，从这里继续</Text>
      <Text variant="headlineLarge" style={styles.title}>我的资料</Text></View>
      <Button mode="contained" icon="plus" compact contentStyle={{ minHeight: 44 }} style={{ borderRadius: 12 }} onPress={() => router.push('/(tabs)/upload')}>上传</Button></View>
    <DailyCheckInCard />
    {!loading && !error && recent && <Surface elevation={0} style={[styles.resume, { backgroundColor: theme.colors.primaryContainer }]}>
      <View style={styles.row}><Icon source="bookmark-outline" size={26} color={theme.colors.primary} />
        <View style={{ flex: 1 }}><Text variant="labelMedium" style={{ color: theme.colors.onPrimaryContainer }}>继续阅读</Text>
          <Text variant="titleMedium" numberOfLines={2} style={{ marginTop: 6, color: theme.colors.onPrimaryContainer }}>{recent.filename}</Text></View></View>
      <Button mode="text" onPress={() => open(recent)} contentStyle={{ minHeight: 44 }} style={{ alignSelf: 'flex-end' }}>继续学习</Button>
    </Surface>}
    <TouchableRipple accessibilityRole="button" accessibilityLabel="搜索学习内容" onPress={() => router.push('/search')} style={[styles.search, { backgroundColor: theme.colors.surfaceVariant, justifyContent: 'center', paddingHorizontal: 16 }]}><View style={styles.row}><Icon source="magnify" size={22} color={theme.colors.onSurfaceVariant} /><Text style={{ color: theme.colors.onSurfaceVariant }}>搜索资料、知识或术语</Text></View></TouchableRipple>
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filters}>
      {kinds.map(([value, label]) => <Chip key={value} selected={kind === value} showSelectedCheck={false} onPress={() => setKind(value)} style={{ backgroundColor: kind === value ? theme.colors.primaryContainer : 'transparent' }} textStyle={{ color: kind === value ? theme.colors.primary : theme.colors.onSurfaceVariant }}>{label}</Chip>)}
    </ScrollView>
    {subjects.length > 0 && <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.subjects}>
      <Chip mode="outlined" selected={!subject} showSelectedCheck={false} onPress={() => setSubject('')}>全部科目</Chip>
      {subjects.map(value => <Chip mode="outlined" key={value} selected={subject === value} showSelectedCheck={false} onPress={() => setSubject(value)}>{value}</Chip>)}
    </ScrollView>}
    {loading ? <ActivityIndicator style={{ padding: 40 }} /> : error ? <View style={styles.empty}>
      <Icon source="wifi-alert" size={36} color={theme.colors.onSurfaceVariant} /><Text>资料加载失败，请检查网络后重试。</Text><Button onPress={() => setAttempt(n => n + 1)}>重新加载</Button>
    </View> : <>
      <View style={styles.listHeading}><Text variant="titleSmall">{kind === 'all' ? '全部资料' : kinds.find(([value]) => value === kind)?.[1]}</Text><Text variant="labelMedium" style={{ color: theme.colors.onSurfaceVariant }}>{filtered.length} 份</Text></View>
      {filtered.map(doc => <TouchableRipple key={doc.id} accessibilityRole="button" accessibilityLabel="打开学习" accessibilityHint={doc.filename} onPress={() => open(doc)} style={[styles.document, { borderColor: theme.colors.outlineVariant }]}>
        <View style={styles.row}><View style={[styles.cover, { backgroundColor: theme.colors.surfaceVariant }]}>
          <Icon source={doc.material_kind === 'question_bank' ? 'clipboard-text-outline' : 'book-open-outline'} size={24} color={theme.colors.primary} />
          <Text variant="labelSmall" style={{ marginTop: 8, color: theme.colors.onSurfaceVariant }}>{doc.file_type?.toUpperCase()}</Text></View>
          <View style={{ flex: 1 }}><Text variant="titleMedium" style={styles.documentTitle} numberOfLines={2}>{doc.filename}</Text>
            <Text variant="labelMedium" style={{ color: theme.colors.onSurfaceVariant, marginBottom: 6 }}>{doc.subject || '未分类'} · {kinds.find(([value]) => value === (doc.material_kind || 'knowledge'))?.[1]}</Text>
            <Text variant="bodySmall" style={{ color: theme.colors.onSurfaceVariant }}>{doc.status === 'ready' ? `${doc.page_count ? `${doc.page_count} 页 · ` : ''}可以直接阅读` : doc.status === 'error' ? '解析失败，打开查看详情' : '资料正在处理'}</Text>
          </View><Icon source="chevron-right" size={22} color={theme.colors.onSurfaceVariant} /></View>
      </TouchableRipple>)}
      {!filtered.length && <View style={styles.empty}><Icon source="book-open-outline" size={42} color={theme.colors.primary} />
        <Text variant="titleMedium">{documents.length ? '没有符合筛选的资料' : '把第一份教材放进来'}</Text>
        <Text style={{ color: theme.colors.onSurfaceVariant, textAlign: 'center' }}>教材、笔记和试卷分别保存，上传后即可学习。</Text>
        <Button mode="outlined" onPress={() => { if (documents.length) { setKind('all'); setSubject('') } else router.push('/(tabs)/upload') }}>{documents.length ? '清除筛选' : '上传资料'}</Button></View>}
    </>}
    <Button icon="book-lock-outline" onPress={() => router.push('/kaf')}>打开加密资料库</Button>
  </ScrollView>
}
const styles = StyleSheet.create({
  content: { padding: 24, paddingBottom: 32 }, title: { fontWeight: '700', letterSpacing: -0.8 },
  heading: { flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 24 },
  resume: { padding: 16, borderRadius: 14, marginBottom: 24 },
  search: { borderRadius: 12, height: 48 },
  filters: { gap: 4, paddingVertical: 14 }, subjects: { gap: 8, paddingBottom: 20 },
  listHeading: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 },
  document: { borderBottomWidth: StyleSheet.hairlineWidth, paddingVertical: 20 },
  documentTitle: { fontWeight: '600', marginBottom: 6, lineHeight: 23 },
  row: { flexDirection: 'row', gap: 14, alignItems: 'center' },
  cover: { width: 48, minHeight: 68, borderRadius: 8, alignItems: 'center', justifyContent: 'center' },
  empty: { paddingVertical: 32, alignItems: 'center', gap: 16 },
})
