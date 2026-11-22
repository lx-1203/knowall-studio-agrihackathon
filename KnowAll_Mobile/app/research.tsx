// Research page - 深度研究（主题 → 子课题分解 → 报告）

import { useEffect, useState, useCallback, useRef } from 'react'
import { View, StyleSheet, ScrollView, Alert, RefreshControl } from 'react-native'
import { Text, Surface, Button, Chip, ActivityIndicator, TextInput, ProgressBar, Snackbar, Divider } from 'react-native-paper'
import { startResearch, getResearchStatus, listResearchTasks } from '../src/api'
import type { ResearchTask } from '../src/types'

const STATUS_LABELS: Record<string, string> = {
  pending: '等待中', rephrasing: '细化主题', decomposing: '分解子课题',
  researching: '研究中', reporting: '撰写报告', completed: '已完成', error: '失败',
}

export default function ResearchPage() {
  const [topic, setTopic] = useState('')
  const [loading, setLoading] = useState(false)
  const [current, setCurrent] = useState<ResearchTask | null>(null)
  const [tasks, setTasks] = useState<ResearchTask[]>([])
  const [tasksLoading, setTasksLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [snack, setSnack] = useState('')

  const loadTasks = useCallback(async () => {
    try {
      const res = await listResearchTasks()
      setTasks(res?.tasks || [])
    } catch {
      setSnack('加载历史研究失败')
    } finally {
      setTasksLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    loadTasks()
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  const handleStart = async () => {
    if (!topic.trim()) { Alert.alert('提示', '请输入研究主题'); return }
    setLoading(true)
    try {
      const res = await startResearch({ topic: topic.trim(), max_subtopics: 8 })
      setTopic('')
      setSnack('研究任务已启动')
      const taskId = res.task_id
      if (pollRef.current) clearInterval(pollRef.current)
      pollRef.current = setInterval(async () => {
        try {
          const status = await getResearchStatus(taskId)
          setCurrent(status)
          if (status.status === 'completed' || status.status === 'error') {
            if (pollRef.current) clearInterval(pollRef.current)
            loadTasks()
            setSnack(status.status === 'completed' ? '研究完成' : `研究失败: ${status.error || ''}`)
          }
        } catch { /* ignore */ }
      }, 2000)
      // Immediately load initial status
      getResearchStatus(taskId).then(setCurrent).catch(() => {})
    } catch (e: any) {
      setSnack(e?.response?.data?.detail || '启动失败')
    } finally {
      setLoading(false)
    }
  }

  const viewTask = async (taskId: string) => {
    try {
      setCurrent(await getResearchStatus(taskId))
    } catch {
      setSnack('获取任务详情失败')
    }
  }

  return (
    <ScrollView style={styles.container} showsVerticalScrollIndicator={false}>
      {/* Start card */}
      <Surface style={styles.startCard}>
        <Text variant="titleMedium" style={{ marginBottom: 8 }}>深度研究</Text>
        <Text variant="bodySmall" style={{ color: '#6B7280', marginBottom: 12 }}>
          输入主题，AI 自动分解子课题、全网调研并撰写结构化报告
        </Text>
        <TextInput
          value={topic}
          onChangeText={setTopic}
          placeholder="例如：CRISPR 技术在基因治疗中的最新进展"
          mode="outlined"
          multiline
          numberOfLines={2}
          style={{ marginBottom: 12 }}
        />
        <Button mode="contained" onPress={handleStart} loading={loading} buttonColor="#7C3AED" icon="flask-outline">
          开始研究
        </Button>
      </Surface>

      {/* Current task progress */}
      {current && (
        <Surface style={styles.taskCard}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
            <Text variant="titleSmall" style={{ flex: 1, marginRight: 8 }}>{current.topic}</Text>
            <Chip compact style={{ height: 24, backgroundColor: current.status === 'completed' ? '#D1FAE5' : current.status === 'error' ? '#FEE2E2' : '#FEF3C7' }}
              textStyle={{ fontSize: 10 }}>
              {STATUS_LABELS[current.status] || current.status}
            </Chip>
          </View>
          {current.refined_topic ? (
            <Text variant="bodySmall" style={{ color: '#6B7280', marginTop: 4 }}>细化主题: {current.refined_topic}</Text>
          ) : null}
          <ProgressBar progress={Math.min(1, current.progress || 0)} color="#7C3AED" style={{ height: 8, borderRadius: 4, marginTop: 12 }} />
          <Text variant="labelSmall" style={{ color: '#6B7280', marginTop: 4 }}>
            {Math.round((current.progress || 0) * 100)}%
          </Text>

          {(current.sub_topics || []).length > 0 && (
            <View style={{ marginTop: 12 }}>
              {current.sub_topics.map((st, i) => (
                <View key={i} style={styles.subRow}>
                  <Text variant="bodySmall" style={{ flex: 1, color: st.status === 'completed' ? '#374151' : '#9CA3AF' }}>
                    {i + 1}. {st.title}
                  </Text>
                  <Chip compact style={{ height: 22, backgroundColor: st.status === 'completed' ? '#D1FAE5' : '#F3F4F6' }}
                    textStyle={{ fontSize: 9, color: st.status === 'completed' ? '#047857' : '#6B7280' }}>
                    {STATUS_LABELS[st.status] || st.status}{st.finding_count ? ` · ${st.finding_count}条` : ''}
                  </Chip>
                </View>
              ))}
            </View>
          )}

          {/* Final report */}
          {current.report ? (
            <>
              <Divider style={{ marginVertical: 12 }} />
              <Text variant="titleSmall" style={{ marginBottom: 6 }}>研究报告</Text>
              <Text style={styles.reportText}>{current.report}</Text>
            </>
          ) : null}

          {(current.sources || []).length > 0 && (
            <>
              <Divider style={{ marginVertical: 12 }} />
              <Text variant="titleSmall" style={{ marginBottom: 6 }}>参考来源 ({(current.sources || []).length})</Text>
              {(current.sources || []).map((s, i) => (
                <View key={i} style={{ marginBottom: 4 }}>
                  <Text variant="bodySmall" style={{ color: '#374151' }}>{i + 1}. {s.title}</Text>
                  <Text variant="labelSmall" style={{ color: '#6D28D9' }} numberOfLines={1}>{s.url}</Text>
                </View>
              ))}
            </>
          )}
        </Surface>
      )}

      <Divider style={{ marginVertical: 14 }} />
      <Text variant="titleMedium" style={{ marginBottom: 8 }}>历史研究 ({tasks.length})</Text>

      {tasksLoading ? (
        <View style={styles.centered}>
          <ActivityIndicator size="large" color="#7C3AED" />
        </View>
      ) : tasks.length === 0 ? (
        <Surface style={styles.emptyCard}>
          <Text variant="bodyLarge" style={{ color: '#9CA3AF' }}>暂无研究任务</Text>
        </Surface>
      ) : (
        <ScrollView
          nestedScrollEnabled
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); loadTasks() }} colors={['#7C3AED']} />}
        >
          {tasks.map(t => (
            <Surface key={t.task_id} style={styles.historyCard} onTouchEnd={() => viewTask(t.task_id)}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
                <Text variant="bodyMedium" style={{ flex: 1, marginRight: 8 }} numberOfLines={1}>{t.topic}</Text>
                <Chip compact style={{ height: 22, backgroundColor: t.status === 'completed' ? '#D1FAE5' : '#F3F4F6' }}
                  textStyle={{ fontSize: 9, color: t.status === 'completed' ? '#047857' : '#6B7280' }}>
                  {STATUS_LABELS[t.status] || t.status}
                </Chip>
              </View>
              <Text variant="labelSmall" style={{ color: '#9CA3AF', marginTop: 4 }}>
                {t.task_id.slice(0, 8)} · {Math.round((t.progress || 0) * 100)}%
              </Text>
            </Surface>
          ))}
          <View style={{ height: 24 }} />
        </ScrollView>
      )}

      <Snackbar visible={!!snack} onDismiss={() => setSnack('')}>{snack}</Snackbar>
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F5F3FF', padding: 16 },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: 24 },
  emptyCard: { padding: 24, borderRadius: 12, alignItems: 'center', backgroundColor: '#fff' },
  startCard: { padding: 20, borderRadius: 12, backgroundColor: '#fff', elevation: 2, marginBottom: 12 },
  taskCard: { padding: 16, borderRadius: 12, backgroundColor: '#fff', elevation: 2, marginBottom: 12 },
  historyCard: { padding: 14, borderRadius: 12, backgroundColor: '#fff', elevation: 1, marginBottom: 8 },
  subRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 5 },
  reportText: { lineHeight: 22, fontSize: 14, color: '#374151' },
})
