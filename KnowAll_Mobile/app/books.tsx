// Books page - 教材编译（选文档 → 编译 → 轮询进度 → 阅读）

import { useEffect, useState, useCallback, useRef } from 'react'
import { View, StyleSheet, ScrollView, Alert, RefreshControl } from 'react-native'
import { Text, Surface, Button, Chip, ActivityIndicator, Dialog, TextInput, Checkbox, ProgressBar, Snackbar, Divider } from 'react-native-paper'
import { createBook, listBooks, getBook, deleteBook, listDocuments } from '../src/api'
import type { BookItem, Document } from '../src/types'

export default function BooksPage() {
  const [books, setBooks] = useState<BookItem[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)

  // Create dialog
  const [showCreate, setShowCreate] = useState(false)
  const [docs, setDocs] = useState<Document[]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [title, setTitle] = useState('')
  const [desc, setDesc] = useState('')
  const [creating, setCreating] = useState(false)

  // Reader state
  const [current, setCurrent] = useState<BookItem | null>(null)
  const [readingChapter, setReadingChapter] = useState<number | null>(null)
  const [pollId, setPollId] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [snack, setSnack] = useState('')

  const fetchBooks = useCallback(async () => {
    try {
      const res = await listBooks()
      setBooks(res?.books || [])
    } catch {
      setSnack('加载教材失败')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => { fetchBooks() }, [])

  // Poll compiling books
  useEffect(() => {
    if (!pollId) return
    pollRef.current = setInterval(async () => {
      try {
        const detail = await getBook(pollId)
        setBooks(prev => prev.map(b => b.id === pollId ? { ...b, ...detail } : b))
        if (detail.status === 'completed' || detail.status === 'error') {
          if (pollRef.current) clearInterval(pollRef.current)
          setPollId(null)
          fetchBooks()
          setSnack(detail.status === 'completed' ? '教材编译完成' : '教材编译失败')
        }
      } catch { /* ignore */ }
    }, 3000)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [pollId])

  const openCreate = async () => {
    setShowCreate(true)
    setSelectedIds([])
    setTitle('')
    setDesc('')
    try {
      const res = await listDocuments(100, 0)
      setDocs(res?.documents || res || [])
    } catch {
      setSnack('加载文档列表失败')
    }
  }

  const toggleDoc = (id: string) => {
    setSelectedIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])
  }

  const handleCreate = async () => {
    if (!title.trim()) { Alert.alert('提示', '请输入教材标题'); return }
    if (selectedIds.length === 0) { Alert.alert('提示', '请至少选择一篇文档'); return }
    setCreating(true)
    try {
      const res = await createBook({ title: title.trim(), description: desc.trim(), source_doc_ids: selectedIds })
      setShowCreate(false)
      fetchBooks()
      if (res?.id) {
        setPollId(res.id)
        setSnack('教材编译已启动')
      }
    } catch (e: any) {
      setSnack(e?.response?.data?.detail || '创建失败')
    } finally {
      setCreating(false)
    }
  }

  const openBook = async (id: string) => {
    try {
      const detail = await getBook(id)
      setCurrent(detail)
      setReadingChapter(null)
    } catch (e: any) {
      setSnack(e?.response?.data?.detail || '打开失败')
    }
  }

  const handleDelete = (id: string) => {
    Alert.alert('删除教材', '确定删除该教材？', [
      { text: '取消' },
      { text: '删除', style: 'destructive', onPress: async () => {
        try { await deleteBook(id); fetchBooks(); setSnack('已删除') }
        catch { setSnack('删除失败') }
      } },
    ])
  }

  const statusColor: Record<string, string> = {
    planning: '#F59E0B', compiling: '#F59E0B', completed: '#10B981', error: '#EF4444',
  }
  const statusLabel: Record<string, string> = {
    planning: '规划中', compiling: '编译中', completed: '已完成', error: '失败',
  }

  // ===== Reader view =====
  if (current) {
    const chapter = readingChapter != null ? current.chapters?.[readingChapter] : null
    return (
      <View style={styles.container}>
        <Surface style={styles.readerHeader}>
          <Text variant="titleMedium">{current.title}</Text>
          {current.subtitle ? <Text variant="bodySmall" style={{ color: '#6B7280' }}>{current.subtitle}</Text> : null}
          {current.description ? <Text variant="bodySmall" style={{ color: '#6B7280', marginTop: 4 }}>{current.description}</Text> : null}
          <View style={{ flexDirection: 'row', gap: 6, marginTop: 8 }}>
            <Chip compact style={{ height: 24 }} textStyle={{ fontSize: 10 }}>{statusLabel[current.status] || current.status}</Chip>
            <Chip compact style={{ height: 24 }} textStyle={{ fontSize: 10 }}>{current.chapters?.length || 0} 章</Chip>
          </View>
        </Surface>

        <ScrollView style={{ flex: 1 }} showsVerticalScrollIndicator={false}>
          {(current.chapters || []).map((ch, i) => (
            <Surface key={ch.id} style={styles.chapterCard} onTouchEnd={() => setReadingChapter(readingChapter === i ? null : i)}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <Text style={{ color: '#7C3AED', fontWeight: 'bold' }}>{i + 1}</Text>
                <Text variant="titleSmall" style={{ flex: 1 }}>{ch.title}</Text>
              </View>
              {ch.description ? <Text variant="bodySmall" style={{ color: '#6B7280', marginTop: 4 }}>{ch.description}</Text> : null}
              {readingChapter === i && (
                <View style={{ marginTop: 10 }}>
                  {(ch.sections || []).map(sec => (
                    <View key={sec.id} style={{ marginTop: 8 }}>
                      <Text variant="bodyMedium" style={{ fontWeight: '600' }}>{sec.title}</Text>
                      {(sec.blocks || []).map(b => (
                        <Text key={b.id} variant="bodySmall" style={{ lineHeight: 20, marginTop: 4, color: '#374151' }}>
                          {b.content}
                        </Text>
                      ))}
                    </View>
                  ))}
                </View>
              )}
            </Surface>
          ))}
          <Button mode="outlined" onPress={() => setCurrent(null)} style={{ marginBottom: 24 }}>
            返回教材列表
          </Button>
        </ScrollView>
      </View>
    )
  }

  // ===== List view =====
  return (
    <View style={styles.container}>
      <Button mode="contained" icon="book-plus" onPress={openCreate} buttonColor="#7C3AED" style={{ marginBottom: 12 }}>
        创建教材
      </Button>

      {loading ? (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color="#7C3AED" />
        </View>
      ) : books.length === 0 ? (
        <Surface style={styles.emptyCard}>
          <Text variant="bodyLarge" style={{ color: '#9CA3AF' }}>暂无教材</Text>
          <Text variant="bodySmall" style={{ color: '#9CA3AF', marginTop: 4 }}>选择文档编译成结构化教材</Text>
        </Surface>
      ) : (
        <ScrollView
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); fetchBooks() }} colors={['#7C3AED']} />}
        >
          {books.map(b => (
            <Surface key={b.id} style={styles.bookCard} onTouchEnd={() => openBook(b.id)}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <Text variant="titleSmall" style={{ flex: 1, marginRight: 8 }}>{b.title}</Text>
                <Chip compact style={{ height: 24, backgroundColor: (statusColor[b.status] || '#9CA3AF') + '22' }}
                  textStyle={{ fontSize: 10, color: statusColor[b.status] || '#6B7280' }}>
                  {statusLabel[b.status] || b.status}
                </Chip>
              </View>
              {b.description ? <Text variant="bodySmall" style={{ color: '#6B7280', marginTop: 4 }} numberOfLines={2}>{b.description}</Text> : null}
              {(b.status === 'compiling' || b.status === 'planning') ? (
                <View style={{ marginTop: 8 }}>
                  <ProgressBar progress={b.progress || 0.05} color="#7C3AED" style={{ height: 6, borderRadius: 3 }} />
                  <Text variant="labelSmall" style={{ color: '#6B7280', marginTop: 4 }}>
                    {Math.round((b.progress || 0) * 100)}%
                  </Text>
                </View>
              ) : b.status === 'completed' ? (
                <View style={{ flexDirection: 'row', gap: 6, marginTop: 8 }}>
                  <Chip compact style={{ height: 24 }} textStyle={{ fontSize: 10 }}>{b.chapters?.length || b.chapter_count || 0} 章</Chip>
                  {b.source_kb ? <Chip compact style={{ height: 24 }} textStyle={{ fontSize: 10 }}>{b.source_kb}</Chip> : null}
                </View>
              ) : null}
              <View style={{ flexDirection: 'row', justifyContent: 'flex-end', marginTop: 4 }}>
                <Button compact onPress={(e: any) => { e?.stopPropagation?.(); handleDelete(b.id) }} textColor="#EF4444" style={{ height: 24 }}>
                  删除
                </Button>
              </View>
            </Surface>
          ))}
          <View style={{ height: 24 }} />
        </ScrollView>
      )}

      {/* Create dialog */}
      <Dialog visible={showCreate} onDismiss={() => setShowCreate(false)}>
        <Dialog.Title>创建教材</Dialog.Title>
        <Dialog.ScrollArea style={{ maxHeight: 340 }}>
          <View style={{ paddingHorizontal: 4 }}>
            <TextInput label="教材标题" value={title} onChangeText={setTitle} mode="outlined" style={{ marginBottom: 8 }} />
            <TextInput label="描述（可选）" value={desc} onChangeText={setDesc} mode="outlined" style={{ marginBottom: 12 }} />
            <Text variant="bodySmall" style={{ marginBottom: 6 }}>源文档（{selectedIds.length} 已选）</Text>
            {docs.length === 0 ? (
              <Text variant="bodySmall" style={{ color: '#9CA3AF' }}>暂无文档，请先导入资料</Text>
            ) : docs.map(d => (
              <Checkbox.Item
                key={d.id}
                label={d.filename || d.id}
                status={selectedIds.includes(d.id) ? 'checked' : 'unchecked'}
                onPress={() => toggleDoc(d.id)}
              />
            ))}
          </View>
        </Dialog.ScrollArea>
        <Dialog.Actions>
          <Button onPress={() => setShowCreate(false)}>取消</Button>
          <Button mode="contained" onPress={handleCreate} loading={creating} buttonColor="#7C3AED">编译</Button>
        </Dialog.Actions>
      </Dialog>

      <Snackbar visible={!!snack} onDismiss={() => setSnack('')}>{snack}</Snackbar>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F5F3FF', padding: 16 },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  emptyCard: { padding: 32, borderRadius: 12, alignItems: 'center', backgroundColor: '#fff', marginTop: 20 },
  bookCard: { padding: 16, borderRadius: 12, marginBottom: 10, backgroundColor: '#fff', elevation: 1 },
  readerHeader: { padding: 16, borderRadius: 12, marginBottom: 12, backgroundColor: '#fff', elevation: 2 },
  chapterCard: { padding: 16, borderRadius: 12, marginBottom: 10, backgroundColor: '#fff', elevation: 1 },
})
