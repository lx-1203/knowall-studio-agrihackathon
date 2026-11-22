// Language learning reading page - 语言学习阅读（中英混合）

import { useEffect, useState, useCallback } from 'react'
import { View, StyleSheet, ScrollView, Alert, RefreshControl } from 'react-native'
import { Text, Surface, Button, Chip, SegmentedButtons, ActivityIndicator, TextInput, Dialog, Snackbar, Divider } from 'react-native-paper'
import { getReadingArticles, getReadingArticle, convertReadingText, uploadReadingArticle, deleteReadingArticle } from '../src/api'

interface ReadingArticle {
  id: string
  title: string
  content: string
  difficulty: number
  tags: string[]
  source: string
}

interface VocabularyWord {
  cn: string
  en: string
}

interface ConvertResult {
  result: string
  vocabulary: VocabularyWord[]
  max_new: number
  known_count: number
  seen_count: number
  source: string
}

export default function LanguagePage() {
  const [articles, setArticles] = useState<ReadingArticle[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)

  // Reader state
  const [current, setCurrent] = useState<ReadingArticle | null>(null)
  const [content, setContent] = useState('')
  const [vocabulary, setVocabulary] = useState<VocabularyWord[]>([])
  const [knownWords, setKnownWords] = useState<VocabularyWord[]>([])
  const [seenWords, setSeenWords] = useState<VocabularyWord[]>([])
  const [level, setLevel] = useState<1 | 2 | 3>(1)
  const [converting, setConverting] = useState(false)
  const [convertSource, setConvertSource] = useState('')

  // Upload dialog
  const [showUpload, setShowUpload] = useState(false)
  const [upTitle, setUpTitle] = useState('')
  const [upContent, setUpContent] = useState('')
  const [uploading, setUploading] = useState(false)
  const [snack, setSnack] = useState('')

  const fetchArticles = useCallback(async () => {
    try {
      const res = await getReadingArticles()
      setArticles(res || [])
    } catch {
      setSnack('加载文章失败')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => { fetchArticles() }, [])

  const openArticle = async (id: string) => {
    setLoading(true)
    try {
      const a = await getReadingArticle(id)
      setCurrent(a)
      setContent(a?.content || '')
      setVocabulary([])
      setKnownWords([])
      setSeenWords([])
      setConvertSource('')
    } catch (e: any) {
      Alert.alert('加载失败', e?.response?.data?.detail || '')
    } finally {
      setLoading(false)
    }
  }

  const doConvert = async () => {
    if (!current) return
    if (!content.trim()) { setSnack('内容为空'); return }
    setConverting(true)
    try {
      const res: ConvertResult = await convertReadingText(content, level, knownWords, seenWords)
      if (res.result) {
        setContent(res.result)
        setVocabulary(res.vocabulary || [])
        setConvertSource(res.source || '')
        setSnack(`已转换·新增 ${res.max_new ?? 0} 词${res.source === 'cache' ? ' (缓存)' : res.source === 'dictionary' ? ' (离线词典)' : ''}`)
      } else {
        setSnack('转换失败')
      }
    } catch (e: any) {
      setSnack(e?.response?.data?.detail || '转换失败,请重试')
    } finally {
      setConverting(false)
    }
  }

  const toggleKnown = (w: VocabularyWord) => {
    setKnownWords(prev =>
      prev.some(x => x.cn === w.cn) ? prev.filter(x => x.cn !== w.cn) : [...prev, w]
    )
    setSeenWords(prev => prev.filter(x => x.cn !== w.cn))
  }
  const toggleSeen = (w: VocabularyWord) => {
    setSeenWords(prev =>
      prev.some(x => x.cn === w.cn) ? prev.filter(x => x.cn !== w.cn) : [...prev, w]
    )
    setKnownWords(prev => prev.filter(x => x.cn !== w.cn))
  }

  const handleUpload = async () => {
    if (!upTitle.trim() || !upContent.trim()) { Alert.alert('提示', '请填写标题和内容'); return }
    setUploading(true)
    try {
      await uploadReadingArticle(upTitle.trim(), upContent.trim())
      setShowUpload(false)
      setUpTitle('')
      setUpContent('')
      setSnack('发布成功')
      fetchArticles()
    } catch (e: any) {
      Alert.alert('发布失败', e?.response?.data?.detail || '')
    } finally {
      setUploading(false)
    }
  }

  const handleDelete = async (id: string) => {
    if (!id.startsWith('u')) { setSnack('内置文章不可删除'); return }
    Alert.alert('删除文章', '确定删除这篇文章？', [
      { text: '取消' },
      { text: '删除', style: 'destructive', onPress: async () => {
        try { await deleteReadingArticle(id); fetchArticles(); setSnack('已删除') }
        catch { setSnack('删除失败') }
      } },
    ])
  }

  // ===== Reader view =====
  if (current) {
    return (
      <View style={styles.container}>
        <Surface style={styles.readerHeader}>
          <Text variant="titleMedium" numberOfLines={2}>{current.title}</Text>
          <View style={{ flexDirection: 'row', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
            {(current.tags || []).map(t => <Chip key={t} compact style={{ height: 24 }} textStyle={{ fontSize: 10 }}>{t}</Chip>)}
          </View>
          <Divider style={{ marginVertical: 12 }} />
          <Text variant="bodySmall" style={{ marginBottom: 8 }}>替换等级</Text>
          <SegmentedButtons
            value={String(level)}
            onValueChange={v => setLevel(Number(v) as 1 | 2 | 3)}
            buttons={[{ value: '1', label: '基础(20%)' }, { value: '2', label: '中级(40%)' }, { value: '3', label: '高级(65%)' }]}
            style={{ marginBottom: 12 }}
          />
          <Button mode="contained" onPress={doConvert} loading={converting} buttonColor="#7C3AED" icon="translate">
            转换难度
          </Button>
          {convertSource ? (
            <Text variant="labelSmall" style={{ color: '#9CA3AF', marginTop: 6 }}>
              来源: {convertSource === 'deepseek' ? 'AI' : convertSource === 'cache' ? '缓存' : convertSource === 'dictionary' ? '离线词典' : convertSource}
            </Text>
          ) : null}
        </Surface>

        <ScrollView style={{ flex: 1 }} showsVerticalScrollIndicator={false}>
          <Surface style={styles.readerCard}>
            <Text style={{ lineHeight: 26, fontSize: 15 }}>{content}</Text>
          </Surface>

          {vocabulary.length > 0 && (
            <Surface style={styles.readerCard}>
              <Text variant="titleMedium" style={{ marginBottom: 4 }}>生词 {vocabulary.length}</Text>
              <Text variant="bodySmall" style={{ color: '#6B7280', marginBottom: 10 }}>
                点击标记「认识」后不再替换，标记「已见」下次优先保留
              </Text>
              {vocabulary.map((w, i) => {
                const isKnown = knownWords.some(x => x.cn === w.cn)
                const isSeen = seenWords.some(x => x.cn === w.cn)
                return (
                  <View key={i} style={styles.wordRow}>
                    <View style={{ flex: 1 }}>
                      <Text variant="bodyMedium">{w.cn} <Text style={{ color: '#6D28D9' }}>{w.en}</Text></Text>
                    </View>
                    <Chip compact style={{ height: 26, backgroundColor: isKnown ? '#D1FAE5' : '#F3F4F6' }}
                      textStyle={{ fontSize: 10 }}
                      onPress={() => toggleKnown(w)}>
                      {isKnown ? '✓ 认识' : '认识'}
                    </Chip>
                    <Chip compact style={{ height: 26, backgroundColor: isSeen ? '#FEF3C7' : '#F3F4F6' }}
                      textStyle={{ fontSize: 10 }}
                      onPress={() => toggleSeen(w)}>
                      {isSeen ? '★ 已见' : '已见'}
                    </Chip>
                  </View>
                )
              })}
            </Surface>
          )}

          <Button mode="outlined" onPress={() => setCurrent(null)} style={{ marginBottom: 24 }}>
            返回文章列表
          </Button>
        </ScrollView>
        <Snackbar visible={!!snack} onDismiss={() => setSnack('')}>{snack}</Snackbar>
      </View>
    )
  }

  // ===== Article list view =====
  return (
    <View style={styles.container}>
      <Button mode="contained" icon="plus" onPress={() => setShowUpload(true)} buttonColor="#7C3AED" style={{ marginBottom: 12 }}>
        发布阅读文章
      </Button>

      {loading ? (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color="#7C3AED" />
        </View>
      ) : articles.length === 0 ? (
        <Surface style={styles.emptyCard}>
          <Text variant="bodyLarge" style={{ color: '#9CA3AF' }}>暂无文章</Text>
          <Text variant="bodySmall" style={{ color: '#9CA3AF', marginTop: 4 }}>发布一篇文章开始中英混合阅读</Text>
        </Surface>
      ) : (
        <ScrollView
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); fetchArticles() }} colors={['#7C3AED']} />}
        >
          {articles.map(a => (
            <Surface key={a.id} style={styles.articleCard} onTouchEnd={() => openArticle(a.id)}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <Text variant="titleSmall" style={{ flex: 1, marginRight: 8 }}>{a.title}</Text>
                {a.source === 'upload' && (
                  <Button compact onPress={(e: any) => { e?.stopPropagation?.(); handleDelete(a.id) }} textColor="#EF4444" style={{ height: 24 }}>
                    删除
                  </Button>
                )}
              </View>
              <View style={{ flexDirection: 'row', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
                {(a.tags || []).map(t => <Chip key={t} compact style={{ height: 24 }} textStyle={{ fontSize: 10 }}>{t}</Chip>)}
                {a.difficulty > 0 && <Chip compact style={{ height: 24 }} textStyle={{ fontSize: 10 }}>难度 {a.difficulty}</Chip>}
              </View>
              <Text variant="bodySmall" style={{ color: '#6B7280', marginTop: 6, lineHeight: 20 }} numberOfLines={3}>
                {a.content}
              </Text>
            </Surface>
          ))}
          <View style={{ height: 24 }} />
        </ScrollView>
      )}

      <Dialog visible={showUpload} onDismiss={() => setShowUpload(false)}>
        <Dialog.Title>发布阅读文章</Dialog.Title>
        <Dialog.ScrollArea>
          <View style={{ paddingHorizontal: 8 }}>
            <TextInput label="标题" value={upTitle} onChangeText={setUpTitle} mode="outlined" style={{ marginBottom: 12 }} />
            <TextInput label="正文（至少 20 个中文字符）" value={upContent} onChangeText={setUpContent} mode="outlined" multiline numberOfLines={8} style={{ marginBottom: 12 }} />
          </View>
        </Dialog.ScrollArea>
        <Dialog.Actions>
          <Button onPress={() => setShowUpload(false)}>取消</Button>
          <Button mode="contained" onPress={handleUpload} loading={uploading} buttonColor="#7C3AED">发布</Button>
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
  articleCard: { padding: 16, borderRadius: 12, marginBottom: 10, backgroundColor: '#fff', elevation: 1 },
  readerCard: { padding: 16, borderRadius: 12, marginBottom: 12, backgroundColor: '#fff', elevation: 1 },
  readerHeader: { padding: 16, borderRadius: 12, marginBottom: 12, backgroundColor: '#fff', elevation: 2 },
  wordRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: '#E5E7EB' },
})
