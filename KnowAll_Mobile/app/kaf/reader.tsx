// KAF 阅读器页: 授权 → 下载 → 解密 → 渲染章节

import { useEffect, useState } from 'react'
import { View, StyleSheet, ScrollView, ActivityIndicator, TouchableOpacity } from 'react-native'
import { Text, Surface, Icon } from 'react-native-paper'
import { useRouter, useLocalSearchParams } from 'expo-router'
import { openKaf, type KafDecryptedPackage } from '../../src/services/kaf'

export default function KafReaderPage() {
  const { fileId } = useLocalSearchParams<{ fileId: string }>()
  const [pkg, setPkg] = useState<KafDecryptedPackage | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const router = useRouter()

  useEffect(() => {
    if (!fileId) return
    let cancelled = false
    ;(async () => {
      try {
        const result = await openKaf(fileId)
        if (!cancelled) setPkg(result)
      } catch (e: any) {
        if (!cancelled) setError(e?.message || '解密失败')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [fileId])

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" />
        <Text style={styles.muted}>授权并解密中…</Text>
      </View>
    )
  }

  if (error) {
    return (
      <View style={styles.center}>
        <Icon source="alert-circle-outline" size={56} />
        <Text style={styles.errorText}>{error}</Text>
        <TouchableOpacity onPress={() => router.back()}>
          <Text style={styles.link}>返回</Text>
        </TouchableOpacity>
      </View>
    )
  }

  if (!pkg) return null

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text variant="headlineSmall" style={styles.pageTitle}>{pkg.title}</Text>
      <Text variant="bodySmall" style={styles.muted}>{pkg.subject || ''}</Text>

      {pkg.chapters.map(ch => (
        <Surface key={ch.id} style={styles.chapter}>
          <Text variant="titleMedium" style={styles.chapterTitle}>{ch.title}</Text>
          <Text variant="bodyMedium" style={styles.chapterBody}>{ch.body}</Text>
        </Surface>
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
  chapter: { marginTop: 16, padding: 16, borderRadius: 12 },
  chapterTitle: { marginBottom: 8 },
  chapterBody: { lineHeight: 24, color: '#333' },
  errorText: { color: '#c0392b', textAlign: 'center', marginVertical: 8 },
  link: { color: '#1a73e8', marginTop: 12 },
})
