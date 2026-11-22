// Coverage report page - 知识覆盖率报告

import { useEffect, useState } from 'react'
import { View, StyleSheet, ScrollView, RefreshControl, Alert, ActivityIndicator } from 'react-native'
import { Text, Surface, Chip, Button, Dialog, TextInput, Snackbar, ProgressBar } from 'react-native-paper'
import { useRouter, useLocalSearchParams } from 'expo-router'
import { getCoverageReport, refreshCoverage } from '../src/api'
import type { CoverageReport } from '../src/types'

function pct(v: number | undefined | null) {
  return v == null ? '—' : `${Math.round(v * 100)}%`
}

function RateRow({ label, value, color }: { label: string; value: number | undefined | null; color: string }) {
  return (
    <View style={{ marginBottom: 12 }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 }}>
        <Text variant="bodyMedium">{label}</Text>
        <Text variant="bodyMedium" style={{ color, fontWeight: 'bold' }}>{pct(value)}</Text>
      </View>
      <ProgressBar progress={Math.min(1, value || 0)} color={color} />
    </View>
  )
}

export default function CoveragePage() {
  const router = useRouter()
  const params = useLocalSearchParams<{ summaryId?: string }>()

  const [report, setReport] = useState<CoverageReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [showRefresh, setShowRefresh] = useState(false)
  const [docId, setDocId] = useState('')
  const [refreshingCoverage, setRefreshingCoverage] = useState(false)
  const [snack, setSnack] = useState('')

  const fetchReport = async (silent = false) => {
    if (!params.summaryId) { setSnack('缺少摘要 ID'); setLoading(false); return }
    if (!silent) setLoading(true)
    try {
      setReport(await getCoverageReport(params.summaryId))
    } catch (e: any) {
      setSnack(e?.response?.data?.detail || '加载覆盖率失败')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useEffect(() => { fetchReport() }, [params.summaryId])

  const handleRefresh = async () => {
    if (!docId) { Alert.alert('提示', '请输入文档 ID'); return }
    setRefreshingCoverage(true)
    try {
      const res = await refreshCoverage({ summary_id: params.summaryId, document_id: docId })
      setShowRefresh(false)
      setDocId('')
      if (res?.status === 'already_covered') {
        setSnack('知识点已全覆盖，无需补充')
      } else {
        setSnack(`已自动补充: ${res?.generated_questions ?? 0} 题, ${res?.generated_cards ?? 0} 张闪卡`)
      }
      if (res?.report) setReport(res.report)
      else fetchReport(true)
    } catch (e: any) {
      Alert.alert('补充失败', e?.response?.data?.detail || '请确认文档 ID 正确')
    } finally {
      setRefreshingCoverage(false)
    }
  }

  if (loading) {
    return (
      <View style={[styles.container, styles.centered]}>
        <ActivityIndicator size="large" color="#7C3AED" />
      </View>
    )
  }

  if (!report) {
    return (
      <View style={[styles.container, styles.centered]}>
        <Text style={{ color: '#9CA3AF' }}>暂无覆盖率数据</Text>
      </View>
    )
  }

  return (
    <ScrollView
      style={styles.container}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); fetchReport(true) }} colors={['#7C3AED']} />
      }
    >
      <Surface style={styles.headerCard}>
        <Text variant="titleMedium" style={{ marginBottom: 16 }}>知识覆盖率</Text>
        <RateRow label="题目覆盖率" value={report.coverage_rate_questions} color="#7C3AED" />
        <RateRow label="闪卡覆盖率" value={report.coverage_rate_flashcards} color="#0EA5E9" />
        <RateRow label="完全覆盖" value={report.full_coverage_rate} color="#10B981" />
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
          <Chip compact style={{ height: 24 }} textStyle={{ fontSize: 10 }}>
            知识点 {report.total_knowledge_points}
          </Chip>
          <Chip compact style={{ height: 24, backgroundColor: '#EDE9FE' }} textStyle={{ fontSize: 10 }}>
            有题 {report.covered_by_questions}
          </Chip>
          <Chip compact style={{ height: 24, backgroundColor: '#E0F2FE' }} textStyle={{ fontSize: 10 }}>
            有卡 {report.covered_by_flashcards}
          </Chip>
          <Chip compact style={{ height: 24, backgroundColor: '#D1FAE5' }} textStyle={{ fontSize: 10 }}>
            全覆盖 {report.full_coverage}
          </Chip>
        </View>
      </Surface>

      {(report.uncovered_points?.length || 0) > 0 ? (
        <Surface style={styles.sectionCard}>
          <Text variant="titleMedium" style={{ marginBottom: 10 }}>未覆盖知识点 ({report.uncovered_points.length})</Text>
          {report.uncovered_points.map(p => (
            <View key={p.id} style={styles.rowItem}>
              <Text variant="bodyMedium" numberOfLines={2}>{p.title}</Text>
              <Chip compact style={{ height: 22 }} textStyle={{ fontSize: 10 }}>L{p.level}</Chip>
            </View>
          ))}
        </Surface>
      ) : null}

      {(report.weak_points?.length || 0) > 0 ? (
        <Surface style={styles.sectionCard}>
          <Text variant="titleMedium" style={{ marginBottom: 10 }}>薄弱知识点 ({report.weak_points.length})</Text>
          {report.weak_points.map(p => (
            <View key={p.id} style={styles.rowItem}>
              <View style={{ flex: 1 }}>
                <Text variant="bodyMedium">{p.title}</Text>
                {p.recommendation ? (
                  <Text variant="bodySmall" style={{ color: '#6B7280', marginTop: 2 }}>{p.recommendation}</Text>
                ) : null}
              </View>
              <Chip compact style={{ height: 22, backgroundColor: '#FEE2E2' }} textStyle={{ fontSize: 10, color: '#991B1B' }}>
                {pct(p.accuracy)}
              </Chip>
            </View>
          ))}
        </Surface>
      ) : null}

      <Button mode="outlined" icon="auto-fix" onPress={() => setShowRefresh(true)} style={{ marginBottom: 24 }}>
        自动补充题目与闪卡
      </Button>

      <Dialog visible={showRefresh} onDismiss={() => setShowRefresh(false)}>
        <Dialog.Title>自动补充覆盖率</Dialog.Title>
        <Dialog.Content>
          <Text variant="bodySmall" style={{ marginBottom: 8 }}>
            将为未覆盖的知识点自动生成题目和闪卡。请输入来源文档 ID（可留空使用摘要默认文档）。
          </Text>
          <TextInput
            mode="outlined"
            value={docId}
            onChangeText={setDocId}
            placeholder="文档 ID"
            autoCapitalize="none"
          />
        </Dialog.Content>
        <Dialog.Actions>
          <Button onPress={() => setShowRefresh(false)}>取消</Button>
          <Button mode="contained" onPress={handleRefresh} loading={refreshingCoverage} buttonColor="#7C3AED">
            开始补充
          </Button>
        </Dialog.Actions>
      </Dialog>

      <Snackbar visible={!!snack} onDismiss={() => setSnack('')}>{snack}</Snackbar>
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F5F3FF', padding: 16 },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  headerCard: { padding: 20, borderRadius: 12, marginBottom: 12, backgroundColor: '#fff', elevation: 2 },
  sectionCard: { padding: 16, borderRadius: 12, marginBottom: 12, backgroundColor: '#fff', elevation: 1 },
  rowItem: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 8, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: '#E5E7EB' },
})
