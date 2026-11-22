// Share page - 分享中心（创建分享链接 + 我的分享 + 查看分享）

import { useEffect, useState, useCallback } from 'react'
import { View, StyleSheet, ScrollView, Alert, RefreshControl } from 'react-native'
import { Text, Surface, Button, Chip, ActivityIndicator, Dialog, TextInput, SegmentedButtons, Snackbar, Divider } from 'react-native-paper'
import { createShareLink, listShareLinks, deleteShareLink, viewSharedResource, listTrees, listQuestions, listDecks } from '../src/api'
import type { ShareLinkInfo, SharedResourceData } from '../src/types'

type ResourceType = 'knowledge_tree' | 'question_bank' | 'flashcard_deck'

interface ResourceOption {
  id: string
  label: string
}

export default function SharePage() {
  const [links, setLinks] = useState<ShareLinkInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)

  // Create dialog
  const [showCreate, setShowCreate] = useState(false)
  const [resType, setResType] = useState<ResourceType>('knowledge_tree')
  const [options, setOptions] = useState<ResourceOption[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [days, setDays] = useState('7')
  const [creating, setCreating] = useState(false)
  const [createdLink, setCreatedLink] = useState<ShareLinkInfo | null>(null)
  const [snack, setSnack] = useState('')

  // View dialog
  const [showView, setShowView] = useState(false)
  const [viewId, setViewId] = useState('')
  const [viewCode, setViewCode] = useState('')
  const [viewing, setViewing] = useState(false)
  const [viewed, setViewed] = useState<SharedResourceData | null>(null)

  const fetchLinks = useCallback(async () => {
    try {
      const res = await listShareLinks(200, 0)
      setLinks(res || [])
    } catch {
      setSnack('加载分享列表失败')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => { fetchLinks() }, [])

  const openCreate = async (type: ResourceType) => {
    setResType(type)
    setSelectedId('')
    setShowCreate(true)
    setOptions([])
    try {
      if (type === 'knowledge_tree') {
        const res = await listTrees(100, 0)
        const trees = res?.trees || res || []
        setOptions(trees.map((t: any) => ({ id: t.tree_id, label: t.name || t.tree_id })))
      } else if (type === 'question_bank') {
        const res = await listQuestions({})
        const qs = res?.questions || res || []
        setOptions(qs.slice(0, 50).map((q: any) => ({ id: q.id, label: q.question_text?.slice(0, 30) || q.id })))
      } else {
        const res = await listDecks(100, 0)
        const decks = res?.decks || res || []
        setOptions(decks.map((d: any) => ({ id: d.deck_id || d.id, label: (d.name || d.deck_name || '').slice(0, 30) || d.id })))
      }
    } catch {
      setSnack('加载资源列表失败')
    }
  }

  const handleCreate = async () => {
    if (!selectedId) { Alert.alert('提示', '请选择要分享的资源'); return }
    setCreating(true)
    try {
      const res = await createShareLink({
        resource_type: resType,
        resource_id: selectedId,
        expires_in_days: days ? Number(days) : null,
      })
      setCreatedLink(res)
      setShowCreate(false)
      fetchLinks()
    } catch (e: any) {
      setSnack(e?.response?.data?.detail || '创建失败')
    } finally {
      setCreating(false)
    }
  }

  const handleView = async () => {
    if (!viewId.trim()) { Alert.alert('提示', '请输入分享 ID'); return }
    setViewing(true)
    setViewed(null)
    try {
      setViewed(await viewSharedResource(viewId.trim(), viewCode.trim()))
    } catch (e: any) {
      setSnack(e?.response?.data?.detail || '查看失败（检查分享 ID 和访问码）')
    } finally {
      setViewing(false)
    }
  }

  const fmtDate = (iso: string | null) => {
    if (!iso) return '永久有效'
    const d = new Date(iso)
    if (isNaN(d.getTime())) return iso
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit' })
  }

  const typeLabel: Record<ResourceType, string> = {
    knowledge_tree: '知识树',
    question_bank: '题库',
    flashcard_deck: '闪卡牌组',
  }

  return (
    <View style={styles.container}>
      <Button mode="contained" icon="share-variant" onPress={() => openCreate('knowledge_tree')} buttonColor="#7C3AED" style={{ marginBottom: 12 }}>
        创建分享链接
      </Button>

      <Surface style={styles.viewCard}>
        <Text variant="titleMedium" style={{ marginBottom: 8 }}>查看分享</Text>
        <Text variant="bodySmall" style={{ color: '#6B7280', marginBottom: 10 }}>
          输入好友分享给你的分享 ID 和 6 位访问码
        </Text>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <View style={{ flex: 1 }}>
            <TextInput value={viewId} onChangeText={setViewId} placeholder="分享 ID" autoCapitalize="none" />
          </View>
          <View style={{ width: 90 }}>
            <TextInput value={viewCode} onChangeText={setViewCode} placeholder="访问码" keyboardType="number-pad" maxLength={6} />
          </View>
          <Button mode="contained" style={{ alignSelf: 'center' }} onPress={handleView} loading={viewing} buttonColor="#7C3AED">
            查看
          </Button>
        </View>
      </Surface>

      {/* Viewed result */}
      {viewed && (
        <Surface style={styles.viewResult}>
          <Text variant="titleSmall" style={{ color: '#6D28D9' }}>分享内容 · {typeLabel[viewed.type as ResourceType] || viewed.type}</Text>
          {viewed.type === 'knowledge_tree' && (
            <>
              <Text variant="bodyMedium" style={{ marginTop: 8 }}>{viewed.data.name}</Text>
              {viewed.data.tree_data?.tree?.nodes?.length ? (
                <Text variant="bodySmall" style={{ color: '#6B7280', marginTop: 4 }}>
                  {viewed.data.tree_data.tree.nodes.length} 个顶级知识点
                </Text>
              ) : null}
            </>
          )}
          {viewed.type === 'flashcard_deck' && (
            <>
              <Text variant="bodyMedium" style={{ marginTop: 8 }}>{viewed.data.name}</Text>
              <Text variant="bodySmall" style={{ color: '#6B7280', marginTop: 4 }}>共 {viewed.data.card_count} 张卡片</Text>
              <View style={{ marginTop: 8 }}>
                {(viewed.data.cards || []).slice(0, 5).map((c, i) => (
                  <Text key={c.id || i} variant="bodySmall" numberOfLines={2} style={{ marginBottom: 4 }}>
                    {i + 1}. {c.front} → {c.back}
                  </Text>
                ))}
              </View>
            </>
          )}
          {viewed.type === 'question_bank' && (
            <View style={{ marginTop: 8 }}>
              {(viewed.data.questions || []).map((q, i) => (
                <View key={q.id || i} style={{ marginBottom: 6 }}>
                  <Text variant="bodySmall">{i + 1}. {q.question_text}</Text>
                  {(q.options || []).map((o: any, oi: number) => (
                    <Text key={oi} variant="labelSmall" style={{ color: '#6B7280', marginLeft: 12 }}>
                      {o.label}. {o.text}
                    </Text>
                  ))}
                </View>
              ))}
            </View>
          )}
        </Surface>
      )}

      <Divider style={{ marginVertical: 14 }} />
      <Text variant="titleMedium" style={{ marginBottom: 8 }}>我的分享 ({links.length})</Text>

      {loading ? (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color="#7C3AED" />
        </View>
      ) : links.length === 0 ? (
        <Surface style={styles.emptyCard}>
          <Text variant="bodyLarge" style={{ color: '#9CA3AF' }}>暂无分享链接</Text>
        </Surface>
      ) : (
        <ScrollView
          showsVerticalScrollIndicator={false}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); fetchLinks() }} colors={['#7C3AED']} />}
        >
          {links.map(l => (
            <Surface key={l.share_id} style={styles.linkCard}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
                <Chip compact style={{ backgroundColor: '#EDE9FE' }} textStyle={{ color: '#6D28D9', fontSize: 10 }}>
                  {typeLabel[l.resource_type] || l.resource_type}
                </Chip>
                <Text variant="labelSmall" style={{ color: '#9CA3AF' }}>浏览 {l.view_count} 次</Text>
              </View>
              <View style={styles.codeRow}>
                <Text variant="headlineSmall" style={{ color: '#7C3AED', letterSpacing: 4, fontWeight: 'bold' }}>
                  {l.access_code}
                </Text>
                <Button compact onPress={() => setSnack(`分享 ID: ${l.share_id}`)} textColor="#6D28D9">复制 ID</Button>
              </View>
              <Text variant="bodySmall" style={{ color: '#6B7280' }}>
                {fmtDate(l.expires_at)} · 创建于 {new Date(l.created_at).toLocaleDateString('zh-CN')}
              </Text>
              <View style={{ flexDirection: 'row', justifyContent: 'flex-end', marginTop: 6 }}>
                <Button compact textColor="#EF4444" onPress={() => {
                  Alert.alert('删除分享', '确定删除该分享链接？', [
                    { text: '取消' },
                    { text: '删除', style: 'destructive', onPress: async () => {
                      try { await deleteShareLink(l.share_id); fetchLinks(); setSnack('已删除') }
                      catch { setSnack('删除失败') }
                    } },
                  ])
                }}>删除</Button>
              </View>
            </Surface>
          ))}
          <View style={{ height: 24 }} />
        </ScrollView>
      )}

      {/* Create dialog */}
      <Dialog visible={showCreate} onDismiss={() => setShowCreate(false)}>
        <Dialog.Title>创建分享</Dialog.Title>
        <Dialog.Content>
          <SegmentedButtons
            value={resType}
            onValueChange={v => openCreate(v as ResourceType)}
            buttons={[
              { value: 'knowledge_tree', label: '知识树' },
              { value: 'question_bank', label: '题库' },
              { value: 'flashcard_deck', label: '牌组' },
            ]}
            style={{ marginBottom: 12 }}
          />
          {options.length === 0 ? (
            <Text variant="bodySmall" style={{ color: '#9CA3AF', marginBottom: 8 }}>该类型暂无可分享资源</Text>
          ) : (
            <ScrollView style={{ maxHeight: 200 }} nestedScrollEnabled>
              {options.map(o => (
                <Chip key={o.id} compact style={{ marginBottom: 6 }}
                  selected={selectedId === o.id}
                  onPress={() => setSelectedId(o.id)}>
                  {o.label}
                </Chip>
              ))}
            </ScrollView>
          )}
          <TextInput
            label="有效期（天，留空永久）"
            value={days}
            onChangeText={setDays}
            keyboardType="number-pad"
            mode="outlined"
            style={{ marginTop: 8 }}
          />
        </Dialog.Content>
        <Dialog.Actions>
          <Button onPress={() => setShowCreate(false)}>取消</Button>
          <Button mode="contained" onPress={handleCreate} loading={creating} buttonColor="#7C3AED">创建</Button>
        </Dialog.Actions>
      </Dialog>

      {/* Created result dialog */}
      <Dialog visible={!!createdLink} onDismiss={() => setCreatedLink(null)}>
        <Dialog.Title>分享已创建</Dialog.Title>
        <Dialog.Content>
          <Text variant="bodyMedium">访问码</Text>
          <Text variant="headlineMedium" style={{ color: '#7C3AED', letterSpacing: 6, fontWeight: 'bold', textAlign: 'center', marginVertical: 8 }}>
            {createdLink?.access_code}
          </Text>
          <Text variant="bodySmall" style={{ color: '#6B7280', textAlign: 'center' }}>
            分享 ID: {createdLink?.share_id}
          </Text>
          <Text variant="bodySmall" style={{ color: '#9CA3AF', textAlign: 'center', marginTop: 4 }}>
            {createdLink?.expires_at ? `有效期至 ${new Date(createdLink.expires_at).toLocaleDateString('zh-CN')}` : '永久有效'}
          </Text>
        </Dialog.Content>
        <Dialog.Actions>
          <Button onPress={() => setCreatedLink(null)}>完成</Button>
        </Dialog.Actions>
      </Dialog>

      <Snackbar visible={!!snack} onDismiss={() => setSnack('')}>{snack}</Snackbar>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F5F3FF', padding: 16 },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  emptyCard: { padding: 24, borderRadius: 12, alignItems: 'center', backgroundColor: '#fff' },
  viewCard: { padding: 16, borderRadius: 12, marginBottom: 12, backgroundColor: '#fff', elevation: 2 },
  viewResult: { padding: 16, borderRadius: 12, marginBottom: 12, backgroundColor: '#fff', elevation: 1 },
  linkCard: { padding: 16, borderRadius: 12, marginBottom: 10, backgroundColor: '#fff', elevation: 1 },
  codeRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginVertical: 6 },
})
