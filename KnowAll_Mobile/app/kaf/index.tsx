// KAF 加密资料库页: 列出 → 打开 → 解密 → 章节阅读

import { useEffect, useState, useCallback } from 'react'
import {
  View, StyleSheet, ScrollView, RefreshControl, TouchableOpacity, ActivityIndicator,
} from 'react-native'
import { Text, Card, Surface, Chip, Icon } from 'react-native-paper'
import { useRouter } from 'expo-router'
import { listKafFiles } from '../../src/api'

interface KafItem {
  file_id: string
  title: string
  topic: string
  file_size: number
  node_count: number
  created_at?: string
}

export default function KafLibraryPage() {
  const [items, setItems] = useState<KafItem[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const router = useRouter()

  const fetchData = useCallback(async () => {
    try {
      const res = await listKafFiles()
      setItems(res.items || [])
    } catch {
      setItems([])
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => { fetchData() }, [])
  const onRefresh = () => { setRefreshing(true); fetchData() }

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" />
        <Text style={styles.muted}>加载资料库…</Text>
      </View>
    )
  }

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
    >
      <Text variant="headlineSmall" style={styles.pageTitle}>加密资料库</Text>
      <Text variant="bodySmall" style={styles.muted}>
        解密密钥由服务端按你的会员等级下发，转发文件无法打开
      </Text>

      {items.length === 0 && (
        <Surface style={styles.empty}>
          <Icon source="book-lock" size={48} />
          <Text style={styles.muted}>暂无加密资料</Text>
        </Surface>
      )}

      {items.map(item => (
        <Card key={item.file_id} style={styles.card} mode="elevated"
          onPress={() => router.push(`/kaf/reader?fileId=${item.file_id}`)}>
          <Card.Content>
            <View style={styles.cardHeader}>
              <Text variant="titleMedium" style={styles.cardTitle} numberOfLines={2}>
                {item.title}
              </Text>
              <Icon source="lock" size={18} />
            </View>
            <View style={styles.cardMeta}>
              <Chip compact>{item.topic || '未分类'}</Chip>
              <Text variant="bodySmall" style={styles.muted}>
                {item.node_count} 章 · {(item.file_size / 1024).toFixed(0)} KB
              </Text>
            </View>
          </Card.Content>
        </Card>
      ))}
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f5f5' },
  content: { padding: 16, paddingBottom: 40 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  pageTitle: { marginBottom: 4 },
  muted: { color: '#888', marginVertical: 4 },
  empty: { padding: 48, alignItems: 'center', borderRadius: 12, marginTop: 24 },
  card: { marginTop: 12 },
  cardHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  cardTitle: { flex: 1, marginRight: 8 },
  cardMeta: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 8 },
})
