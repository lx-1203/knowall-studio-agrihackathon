import { useEffect, useRef, useState } from 'react'
import { Alert, KeyboardAvoidingView, Platform, ScrollView, StyleSheet, View } from 'react-native'
import { Redirect, Stack, useLocalSearchParams, useNavigation, useRouter } from 'expo-router'
import { usePreventRemove } from 'expo-router/react-navigation'
import { ActivityIndicator, Button, IconButton, Modal, Portal, Text, TextInput, useTheme } from 'react-native-paper'
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context'
import { getDocumentChunks, getDocumentDetail } from '../src/api'
import { useAuthStore } from '../src/stores'
import type { Document, DocumentChunk } from '../src/types'
import { findSourceIndex } from '../src/materialLearning'
import MaterialLearningTools from '../src/components/MaterialLearningTools'
import { useMaterialNote } from '../src/hooks/useMaterialNote'
import MaterialOriginalReader from '../src/components/MaterialOriginalReader'
import { originalKind } from '../src/materialOriginal'
import MaterialRichText from '../src/components/MaterialRichText'
import { useMaterialProgress } from '../src/hooks/useMaterialProgress'
import { chapterLabel, materialLabel } from '../src/materialReaderNavigation'

export default function MaterialReader() {
  const { id, source, from, returnQ, returnMode } = useLocalSearchParams<{ id: string; source?: string; from?: string; returnQ?: string; returnMode?: string }>()
  const router = useRouter()
  const navigation = useNavigation()
  const theme = useTheme()
  const insets = useSafeAreaInsets()
  const { user, isAuthenticated, authReady } = useAuthStore()
  const [doc, setDoc] = useState<Document | null>(null)
  const [chunks, setChunks] = useState<DocumentChunk[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [noteOpen, setNoteOpen] = useState(false)
  const [toolsOpen, setToolsOpen] = useState(false)
  const [viewMode, setViewMode] = useState<'original' | 'text'>('text')
  const [textError, setTextError] = useState('')
  const [textLoading, setTextLoading] = useState(true)
  const [rawText, setRawText] = useState(false)
  const [progressResetOpen, setProgressResetOpen] = useState(false)
  const [directoryOpen, setDirectoryOpen] = useState(false)
  const note = useMaterialNote(authReady && isAuthenticated ? user?.id : undefined, id)
  const progress = useMaterialProgress(authReady && isAuthenticated ? user?.id : undefined, id, chunks.length, !!doc && doc.id === id && !loading && !error && !textLoading && chunks.length > 0)
  const [sourceActive, setSourceActive] = useState(!!source)
  const sourceIndex = source ? findSourceIndex(chunks, [source]) : -1
  const index = sourceActive && sourceIndex >= 0 ? sourceIndex : progress.index
  const { fontSize } = progress
  const leaving = useRef(false)
  const readerHref = useRef('')
  const scope = `${user?.id || ''}:${id}`
  const currentScope = useRef(scope)
  currentScope.current = scope
  const scroll = useRef<ScrollView>(null)
  useEffect(() => {
    setNoteOpen(false); setToolsOpen(false); setRawText(false); setProgressResetOpen(false); setDirectoryOpen(false)
    if (Platform.OS === 'web') readerHref.current = window.location.href
  }, [id, user?.id])
  useEffect(() => { setSourceActive(!!source) }, [id, user?.id, source])
  useEffect(() => {
    if (!sourceActive || sourceIndex < 0 || !progress.ready || !doc || doc.id !== id || textLoading) return
    setViewMode('text')
    // A search click is an explicit new reading intent. Keep its visible anchor
    // while initial cloud restoration settles; manual navigation releases it.
    if (progress.index !== sourceIndex) progress.update({ index: sourceIndex })
  }, [sourceActive, sourceIndex, progress.ready, progress.index, doc?.id, textLoading])
  const closeNote = async () => {
    const saved = !(note.dirty || note.saving) || await note.save()
    if (saved && currentScope.current === scope) setNoteOpen(false)
  }
  usePreventRemove(note.dirty || note.saving, ({ data }) => {
    if (leaving.current) return
    leaving.current = true
    void note.save().then(saved => {
      if (currentScope.current !== scope) return
      if (saved) navigation.dispatch(data.action)
      else {
        setNoteOpen(true)
        // Browser Back moves the URL before navigation can be cancelled. Restore that entry on failure.
        if (Platform.OS === 'web' && readerHref.current && window.location.href !== readerHref.current) window.history.forward()
      }
    }).finally(() => { leaving.current = false })
  })
  const noteMessage = note.error || (note.saving ? '正在保存到本机…' : note.dirty ? '尚未保存 · 停止输入后自动保存' : note.persisted ? '已保存到本机' : '停止输入后自动保存到本机，与当前账号和资料关联。')
  useEffect(() => {
    if (!authReady || !isAuthenticated) return
    if (!id) { setError('没有找到这份资料，请返回资料库重新打开。'); setLoading(false); return }
    let active = true
    setLoading(true); setTextLoading(true); setError(''); setTextError(''); setDoc(null); setChunks([])
    // Original-file reading must not wait for slow/failed parsed-text requests.
    getDocumentDetail(id).then(detail => {
      if (!active) return
      setDoc(detail); setViewMode(originalKind(detail.file_type) ? 'original' : 'text')
    }).catch(() => { if (active) setError('资料暂时无法打开，请检查网络后重试。') })
      .finally(() => { if (active) setLoading(false) })
    getDocumentChunks(id).then(result => ({ result, ok: true })).catch(() => ({ result: null, ok: false }))
      .then(content => {
        const sections = Array.isArray(content.result) ? content.result : content.result?.chunks
        if (!active) return
        const validSections = Array.isArray(sections) ? sections : []
        setChunks(validSections)
        if (!content.ok || !Array.isArray(sections)) setTextError('文字加载失败，原件仍可阅读，请重试文字加载。')
      }).catch(() => { if (active) setTextError('文字加载失败，原件仍可阅读，请重试文字加载。') })
      .finally(() => { if (active) setTextLoading(false) })
    return () => { active = false }
  }, [id, user?.id, attempt, authReady, isAuthenticated])
  const move = (next: number) => { setSourceActive(false); if (source) router.setParams({ source: '' }); progress.update({ index: next }); scroll.current?.scrollTo({ y: 0, animated: false }) }
  const locateSource = (ids: string[]) => {
    const position = findSourceIndex(chunks, ids)
    if (position < 0) { Alert.alert('来源暂不可定位', '资料可能已更新，未找到对应正文片段。'); return }
    setViewMode('text'); move(position); setToolsOpen(false)
  }
  const textEmptyTitle = textError && !originalKind(doc?.file_type || '')
    ? '文字加载失败，请检查网络后重试。'
    : textError || (doc?.status === 'error' ? '资料解析失败' : '正文还没有准备好')
  let progressMessage = '阅读进度保存在本机 · 原资料不受影响'
  if (progress.saving) progressMessage = '正在保存本机阅读进度…'
  if (!progress.ready) progressMessage = '正在读取本机阅读进度…'
  if (progress.recentError) progressMessage = '最近阅读入口更新失败，阅读位置单独保存。'
  if (progress.error) progressMessage = progress.error
  if (authReady && !isAuthenticated) return <Redirect href="/login" />
  return <SafeAreaView edges={['top', 'bottom']} style={{ flex: 1, backgroundColor: theme.colors.surface }}>
    <Stack.Screen options={{ headerShown: false }} />
    <View style={[styles.header, { borderBottomColor: theme.colors.outlineVariant, borderBottomWidth: StyleSheet.hairlineWidth }]}><IconButton icon="arrow-left" accessibilityLabel={from === 'search' ? '返回搜索结果' : '返回我的资料'} onPress={() => {
      if (router.canGoBack()) router.back()
      else if (from === 'search') router.replace({ pathname: '/search', params: { q: returnQ || '', mode: returnMode || 'keyword' } })
      else router.replace('/(tabs)')
    }} />
      <Text variant="titleMedium" numberOfLines={2} style={{ flex: 1 }}>{doc ? materialLabel(doc.filename) : '资料学习'}</Text>
      <IconButton icon="note-edit-outline" accessibilityLabel="当前资料笔记" disabled={!doc} onPress={() => setNoteOpen(true)} /></View>
    {loading ? <ActivityIndicator style={{ marginTop: 60 }} /> : error ? <View style={styles.empty}><Text>{error}</Text><Button onPress={() => setAttempt(n => n + 1)}>重试</Button></View> : <>
      {!!doc && originalKind(doc.file_type) && <View style={{ flexDirection: 'row', paddingHorizontal: 16, gap: 8, paddingBottom: 4 }}>
        <Button mode={viewMode === 'original' ? 'contained-tonal' : 'text'} onPress={() => setViewMode('original')}>原件</Button>
        <Button mode={viewMode === 'text' ? 'contained-tonal' : 'text'} onPress={() => setViewMode('text')}>文字</Button>
      </View>}
      {viewMode === 'original' && doc && user?.id ? <MaterialOriginalReader key={scope} documentId={doc.id} accountId={user.id} /> : <>
      {sourceActive && !textLoading && <Text accessibilityLiveRegion="polite" style={{ color: theme.colors.onSurfaceVariant, paddingHorizontal: 20, paddingVertical: 8 }}>{sourceIndex >= 0 ? '已定位搜索结果' : '资料已更新，未找到命中片段；已打开当前资料。'}</Text>}
      <View style={styles.toolbar}><Text variant="labelMedium" numberOfLines={1} style={{ color: theme.colors.onSurfaceVariant, flex: 1 }}>{doc?.subject || '未分类'} · 正文</Text>
        <View style={{ flexDirection: 'row' }}><IconButton icon="format-font-size-decrease" accessibilityLabel="缩小字号" disabled={fontSize <= 16} onPress={() => progress.update({ fontSize: fontSize - 2 })} />
          <IconButton icon="format-font-size-increase" accessibilityLabel="放大字号" disabled={fontSize >= 26} onPress={() => progress.update({ fontSize: fontSize + 2 })} /></View></View>
      {!!chunks.length && <View style={styles.readingOptions}><Text variant="labelMedium" numberOfLines={1} style={{ flex: 1, color: theme.colors.onSurfaceVariant }}>{chapterLabel(chunks[index]?.text || '', index)}</Text><Button compact mode="text" onPress={() => setRawText(value => !value)}>{rawText ? '排版阅读' : '原始文字'}</Button></View>}
      <ScrollView ref={scroll} testID="material-reading-content" style={{ flex: 1, backgroundColor: theme.colors.surface }} contentContainerStyle={styles.reading}>
        {textLoading ? <View style={styles.empty}><ActivityIndicator /><Text>正在加载文字…</Text></View> : chunks.length ? <MaterialRichText key={`${scope}:${chunks[index]?.id}:${rawText}`} text={chunks[index]?.text || ''} fontSize={fontSize} raw={rawText} /> : <View style={styles.empty}>
          <Text variant="titleMedium">{textEmptyTitle}</Text>
          <Text>{doc && originalKind(doc.file_type) ? '可切换到原件直接阅读，无需生成学习内容。' : '无需生成知识卡。资料解析完成后即可在这里阅读。'}</Text><Button onPress={() => setAttempt(n => n + 1)}>重新加载正文</Button></View>}
      </ScrollView>
      {!!chunks.length && <View style={[styles.pagination, { borderTopColor: theme.colors.outlineVariant }]}><Button compact disabled={index <= 0} onPress={() => move(index - 1)}>上一段</Button>
        <Text variant="labelMedium" style={{ color: theme.colors.onSurfaceVariant }}>{index + 1} / {chunks.length}</Text><Button compact disabled={index >= chunks.length - 1} onPress={() => move(index + 1)}>下一段</Button></View>}
      </>}
      <View style={[styles.readerActions, { borderTopColor: theme.colors.outlineVariant }]}>
        <Button compact icon="format-list-bulleted" disabled={!chunks.length || textLoading} onPress={() => setDirectoryOpen(true)} contentStyle={styles.actionTouch}>目录</Button>
        <Button compact icon="note-edit-outline" accessibilityLabel="打开资料笔记" disabled={!doc} onPress={() => setNoteOpen(true)} contentStyle={styles.actionTouch}>笔记</Button>
        <Button compact mode="contained" icon="book-open-page-variant-outline" disabled={!doc} onPress={() => setToolsOpen(true)} contentStyle={styles.actionTouch} style={{ borderRadius: 10 }}>学习工具</Button>
      </View>
      <View style={styles.progressStatus}>
        <Text variant="labelSmall" accessibilityLiveRegion="polite" style={{ textAlign: 'center', color: progress.error || progress.recentError ? theme.colors.error : theme.colors.onSurfaceVariant }}>{progressMessage}</Text>
        {!!progress.error && <Button onPress={() => { if (progress.ready) void progress.save(); else void progress.reload() }} disabled={progress.saving}>{progress.ready ? '重试保存进度' : '重试读取进度'}</Button>}
        {!!progress.error && !progress.ready && chunks.length > 0 && <Button onPress={() => setProgressResetOpen(true)}>用当前位置重新保存</Button>}
        {progress.recentError && <Button onPress={progress.retryRecent}>重试更新最近阅读</Button>}
      </View>
    </>}
    <Portal><Modal visible={directoryOpen} onDismiss={() => setDirectoryOpen(false)} contentContainerStyle={[styles.directory, { backgroundColor: theme.colors.surface }]}>
      <View style={styles.header}><Text variant="titleLarge" style={{ flex: 1, paddingLeft: 20 }}>目录</Text><IconButton icon="close" accessibilityLabel="关闭目录" onPress={() => setDirectoryOpen(false)} /></View>
      <ScrollView contentContainerStyle={{ paddingHorizontal: 12, paddingBottom: 20 }}>
        {chunks.map((chunk, position) => <Button key={chunk.id || position} accessibilityLabel={`目录第 ${position + 1} 段`} accessibilityState={{ selected: index === position }}
          mode={index === position ? 'contained-tonal' : 'text'} style={{ marginBottom: 4, borderRadius: 8 }} contentStyle={{ minHeight: 52, justifyContent: 'flex-start' }}
          onPress={() => { setViewMode('text'); move(position); setDirectoryOpen(false) }}>{chapterLabel(chunk.text, position)}{chunk.page_range ? ` · ${chunk.page_range} 页` : ''}</Button>)}
      </ScrollView>
    </Modal></Portal>
    <Portal><Modal visible={progressResetOpen} onDismiss={() => { if (!progress.saving) setProgressResetOpen(false) }} contentContainerStyle={[styles.note, { backgroundColor: theme.colors.surface }]}>
      <ScrollView contentContainerStyle={{ gap: 16 }}>
        <Text variant="titleLarge">替换本机阅读进度</Text>
        <Text>将记录第 {index + 1} 段、字号 {fontSize}。旧阅读位置会被替换，原资料和笔记不受影响。</Text>
        {!!progress.error && <Text accessibilityLiveRegion="polite" style={{ color: theme.colors.error }}>{progress.error}</Text>}
        <Button mode="contained" loading={progress.saving} disabled={progress.saving} onPress={() => { void progress.replaceWithCurrent().then(saved => { if (saved && currentScope.current === scope) setProgressResetOpen(false) }) }}>确认替换进度</Button>
        <Button disabled={progress.saving} onPress={() => setProgressResetOpen(false)}>{progress.ready ? '返回阅读' : '保留旧记录'}</Button>
      </ScrollView>
    </Modal></Portal>
    <Portal><Modal visible={noteOpen} onDismiss={() => { void closeNote() }} contentContainerStyle={[styles.note, { backgroundColor: theme.colors.surface }]}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
      <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ gap: 12 }}>
      <Text variant="titleLarge">这份资料的笔记</Text>
      <Text variant="bodySmall" style={{ marginVertical: 12 }}>{doc?.filename}</Text>
      <TextInput accessibilityLabel="我的学习笔记" mode="outlined" multiline numberOfLines={8} label="记下你的理解" value={note.text} disabled={!note.ready}
        onChangeText={note.update} style={{ maxHeight: 240 }} />
      <Text accessibilityLiveRegion="polite" style={{ color: note.error ? theme.colors.error : theme.colors.onSurfaceVariant }}>{noteMessage}</Text>
      {!!note.error && !note.ready && <Button onPress={note.reload}>重试读取笔记</Button>}
      <Button mode="contained" loading={note.saving} disabled={note.saving || !note.ready} onPress={note.save}>保存笔记</Button>
      <Button disabled={note.saving} onPress={() => { void closeNote() }}>返回阅读</Button>
      </ScrollView></KeyboardAvoidingView>
    </Modal></Portal>
    <Portal><Modal visible={toolsOpen} onDismiss={() => setToolsOpen(false)} contentContainerStyle={[styles.tools, { backgroundColor: theme.colors.surface, paddingTop: insets.top, paddingBottom: insets.bottom }]}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={{ flex: 1 }}>
        <View style={[styles.header, { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: theme.colors.outlineVariant }]}><IconButton icon="arrow-left" accessibilityLabel="关闭学习工具" onPress={() => setToolsOpen(false)} /><View style={{ flex: 1 }}><Text variant="labelMedium" style={{ color: theme.colors.onSurfaceVariant }}>当前资料学习工具</Text><Text variant="titleMedium" numberOfLines={1}>{doc ? materialLabel(doc.filename) : '学习工具'}</Text></View></View>
        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ padding: 24, paddingBottom: 40 }}>
          <MaterialLearningTools key={scope} documentId={id} title={doc?.filename || '当前资料'} chunks={chunks} chunk={chunks[index]} onSource={locateSource} />
        </ScrollView>
      </KeyboardAvoidingView>
    </Modal></Portal>
  </SafeAreaView>
}
const styles = StyleSheet.create({
  header: { flexDirection: 'row', alignItems: 'center', paddingRight: 20, minHeight: 60 },
  toolbar: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingLeft: 20, paddingRight: 8 },
  readingOptions: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 24, gap: 8 },
  reading: { paddingHorizontal: 24, paddingTop: 8, paddingBottom: 48 }, empty: { padding: 24, alignItems: 'center', gap: 16 },
  pagination: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 12, borderTopWidth: StyleSheet.hairlineWidth },
  readerActions: { flexDirection: 'row', justifyContent: 'space-between', paddingHorizontal: 12, paddingVertical: 8, borderTopWidth: StyleSheet.hairlineWidth, gap: 2 },
  actionTouch: { minHeight: 44 },
  progressStatus: { paddingHorizontal: 16, paddingBottom: 8 },
  directory: { margin: 16, borderRadius: 16, maxHeight: '75%' },
  note: { margin: 20, padding: 24, borderRadius: 20, maxHeight: '85%' },
  tools: { margin: 0, height: '100%', width: '100%', overflow: 'hidden' },
})
