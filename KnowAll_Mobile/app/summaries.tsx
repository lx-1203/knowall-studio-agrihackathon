// Summaries page - 学习摘要中心
// 列表 -> 详情(Markdown 阅读) -> 互动问答 / 覆盖率报告 / 思维导图 入口

import { useEffect, useState, useCallback } from 'react'
import { View, StyleSheet, ScrollView, RefreshControl, Alert } from 'react-native'
import { Text, Surface, ActivityIndicator, Button, Chip, Dialog, Checkbox, Divider, Snackbar } from 'react-native-paper'
import { useRouter, useLocalSearchParams } from 'expo-router'
import { listSummaries, getSummary, generateSummary, listDocuments, getSummaryMindmap } from '../src/api'
import type { Document, MindMapNode } from '../src/types'

interface SummaryItem {
  summary_id: string
  document_ids: string[]
  node_count: number
  level_stats: Record<string, number>
  generated_at: string | null
  created_at: string | null
}

function formatDate(iso: string | null | undefined) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export default function SummariesPage() {
  const router = useRouter()
  const params = useLocalSearchParams<{ id?: string }>()

  const [items, setItems] = useState<SummaryItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)

  // Detail state (?id=)
  const [contentMd, setContentMd] = useState('')
  const [detailLoading, setDetailLoading] = useState(false)
  const [mindmap, setMindmap] = useState<MindMapNode[] | null>(null)
  const [mindmapLoading, setMindmapLoading] = useState(false)
  const [snack, setSnack] = useState('')

  // Generate dialog
  const [showGenerate, setShowGenerate] = useState(false)
  const [docs, setDocs] = useState<Document[]>([])
  const [selectedDocs, setSelectedDocs] = useState<string[]>([])
  const [generating, setGenerating] = useState(false)

  const fetchList = useCallback(async () => {
    try {
      const res = await listSummaries(50, 0)
      setItems(res?.items || [])
      setTotal(res?.total || 0)
    } catch {
      setSnack('加载摘要失败')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => { fetchList() }, [])

  // Fetch detail when ?id= changes
  useEffect(() => {
    if (params.id) {
      setDetailLoading(true)
      setContentMd('')
      setMindmap(null)
      getSummary(params.id)
        .then(s => setContentMd(s?.content_md || ''))
        .catch(() => setSnack('加载摘要详情失败'))
        .finally(() => setDetailLoading(false))
    }
  }, [params.id])

  const onRefresh = () => { setRefreshing(true); fetchList() }

  const openGenerate = async () => {
    setShowGenerate(true)
    try {
      const res = await listDocuments(100, 0)
      setDocs(res?.documents || res || [])
    } catch {
      setSnack('加载文档列表失败')
    }
  }

  const toggleDoc = (id: string) => {
    setSelectedDocs(prev =>
      prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]
    )
  }

  const handleGenerate = async () => {
    if (selectedDocs.length === 0) { Alert.alert('提示', '请至少选择一篇文档'); return }
    setGenerating(true)
    try {
      const res = await generateSummary({ document_ids: selectedDocs })
      setShowGenerate(false)
      setSelectedDocs([])
      if (res?.summary_id) {
        router.push({ pathname: '/summaries', params: { id: res.summary_id } })
      } else {
        fetchList()
      }
    } catch (e: any) {
      Alert.alert('生成失败', e?.response?.data?.detail || e?.message || '请重试')
    } finally {
      setGenerating(false)
    }
  }

  const loadMindmap = async () => {
    if (!params.id) return
    setMindmapLoading(true)
    try {
      const res = await getSummaryMindmap(params.id)
      setMindmap(res?.nodes || res || [])
    } catch (e: any) {
      Alert.alert('思维导图生成失败', e?.response?.data?.detail || '可能需要先运行 Agent 编排')
    } finally {
      setMindmapLoading(false)
    }
  }

  const openInteractive = () => router.push({ pathname: '/interactive-quiz', params: { summaryId: params.id } })
  const openCoverage = () => router.push({ pathname: '/coverage', params: { summaryId: params.id } })

  // ===================== Detail view =====================
  if (params.id) {
    if (detailLoading) {
      return (
        <View style={[styles.container, styles.centered]}>
          <ActivityIndicator size="large" color="#7C3AED" />
        </View>
      )
    }
    return (
      <ScrollView style={styles.container}>
        <Surface style={styles.actionBar}>
          <Button mode="contained" icon="pencil-box-outline" onPress={openInteractive} buttonColor="#7C3AED" style={{ flex: 1 }}>
            互动问答
          </Button>
          <Button mode="outlined" icon="chart-donut" onPress={openCoverage} style={{ flex: 1, marginHorizontal: 8 }}>
            覆盖率
          </Button>
          <Button mode="outlined" icon="graph-outline" loading={mindmapLoading} onPress={loadMindmap} style={{ flex: 1 }}>
            思维导图
          </Button>
        </Surface>

        {mindmap ? (
          <Surface style={styles.sectionCard}>
            <Text variant="titleMedium" style={{ marginBottom: 10 }}>知识结构</Text>
            {mindmap.map((n, i) => <MindNodeView key={n.id || i} node={n} depth={0} />)}
          </Surface>
        ) : null}

        <Surface style={styles.sectionCard}>
          <Text variant="titleMedium" style={{ marginBottom: 10 }}>摘要内容</Text>
          <Text style={styles.mdText}>{contentMd || '（暂无内容）'}</Text>
        </Surface>
        <View style={{ height: 32 }} />
      </ScrollView>
    )
  }

  // ===================== List view =====================
  return (
    <View style={styles.container}>
      <Button mode="contained" icon="plus" onPress={openGenerate} buttonColor="#7C3AED" style={{ marginBottom: 12 }}>
        从文档生成摘要
      </Button>

      {loading ? (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color="#7C3AED" />
        </View>
      ) : items.length === 0 ? (
        <Surface style={styles.emptyCard}>
          <Text variant="bodyLarge" style={{ color: '#9CA3AF' }}>暂无摘要</Text>
          <Text variant="bodySmall" style={{ color: '#9CA3AF', marginTop: 4 }}>选择文档生成一篇学习摘要</Text>
        </Surface>
      ) : (
        <ScrollView
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} colors={['#7C3AED']} />}
        >
          <Text variant="bodySmall" style={{ color: '#6B7280', marginBottom: 8 }}>共 {total} 篇</Text>
          {items.map(s => (
            <Surface key={s.summary_id} style={styles.card}
              onTouchEnd={() => router.push({ pathname: '/summaries', params: { id: s.summary_id } })}>
              <Text variant="titleSmall" style={{ color: '#7C3AED' }}>
                摘要 {s.summary_id.slice(0, 8)}…
              </Text>
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
                <Chip compact style={{ height: 24 }} textStyle={{ fontSize: 10 }}>{s.node_count || 0} 知识点</Chip>
                {Object.entries(s.level_stats || {}).slice(0, 3).map(([k, v]) => (
                  <Chip key={k} compact style={{ height: 24 }} textStyle={{ fontSize: 10 }}>L{k}: {v}</Chip>
                ))}
              </View>
              <Text variant="bodySmall" style={{ color: '#9CA3AF', marginTop: 8 }}>
                生成于 {formatDate(s.generated_at || s.created_at)}
              </Text>
            </Surface>
          ))}
          <View style={{ height: 24 }} />
        </ScrollView>
      )}

      {/* Generate dialog */}
      <Dialog visible={showGenerate} onDismiss={() => setShowGenerate(false)}>
        <Dialog.Title>选择文档生成摘要</Dialog.Title>
        <Dialog.ScrollArea style={{ maxHeight: 320 }}>
          {docs.length === 0 ? (
            <Text style={{ padding: 16, color: '#9CA3AF' }}>暂无文档</Text>
          ) : docs.map(d => (
            <Checkbox.Item
              key={d.id}
              label={d.filename || d.id}
              status={selectedDocs.includes(d.id) ? 'checked' : 'unchecked'}
              onPress={() => toggleDoc(d.id)}
            />
          ))}
        </Dialog.ScrollArea>
        <Dialog.Actions>
          <Button onPress={() => setShowGenerate(false)}>取消</Button>
          <Button mode="contained" onPress={handleGenerate} loading={generating} buttonColor="#7C3AED">
            生成
          </Button>
        </Dialog.Actions>
      </Dialog>

      <Snackbar visible={!!snack} onDismiss={() => setSnack('')}>{snack}</Snackbar>
    </View>
  )
}

// Simple recursive mindmap renderer (outline style)
function MindNodeView({ node, depth }: { node: MindMapNode; depth: number }) {
  return (
    <View style={{ marginLeft: depth > 0 ? 14 : 0 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
        <View style={[styles.dot, { backgroundColor: depth === 0 ? '#7C3AED' : depth < 3 ? '#A78BFA' : '#DDD6FE' }]} />
        <Text style={[styles.mdText, depth === 0 && { fontWeight: 'bold' }]}>{node.label}</Text>
        {node.tag ? <Chip compact style={{ height: 22 }} textStyle={{ fontSize: 10 }}>{node.tag}</Chip> : null}
      </View>
      {node.summary ? (
        <Text style={[styles.mdText, { color: '#6B7280', marginLeft: 18, marginTop: 2 }]} numberOfLines={3}>
          {node.summary}
        </Text>
      ) : null}
      <View style={{ marginTop: 6 }}>
        {(node.children || []).map((c, i) => <MindNodeView key={c.id || i} node={c} depth={depth + 1} />)}
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F5F3FF', padding: 16 },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  emptyCard: { padding: 32, borderRadius: 12, alignItems: 'center', backgroundColor: '#fff', marginTop: 20 },
  card: { padding: 16, borderRadius: 12, marginBottom: 10, backgroundColor: '#fff', elevation: 1 },
  actionBar: { padding: 12, borderRadius: 12, marginBottom: 12, backgroundColor: '#fff', elevation: 2, flexDirection: 'row' },
  sectionCard: { padding: 16, borderRadius: 12, marginBottom: 12, backgroundColor: '#fff', elevation: 1 },
  mdText: { lineHeight: 22, fontSize: 14, color: '#374151' },
  dot: { width: 8, height: 8, borderRadius: 4 },
})
