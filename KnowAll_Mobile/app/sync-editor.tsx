// Sync editor page - 实时协同编辑（WebSocket + 服务器端 OT 变换）
// 协议：/ws/sync?doc_id=&user_id=&user_name=  |  reconnect/sync_full/operation/presence/heartbeat

import { useEffect, useState, useCallback, useRef } from 'react'
import { View, StyleSheet, ScrollView, Alert, RefreshControl } from 'react-native'
import { Text, Surface, Button, TextInput, ActivityIndicator, Chip, Snackbar, Divider } from 'react-native-paper'
import { WS_BASE_URL, listDocuments } from '../src/api'
import { storage } from '../src/services/storage'
import type { Document } from '../src/types'

interface PresenceMsg {
  online_users: { user_id: string; user_name: string }[]
  total_online: number
}

export default function SyncEditorPage() {
  const [view, setView] = useState<'rooms' | 'editor'>('rooms')
  const [docs, setDocs] = useState<Document[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [customRoom, setCustomRoom] = useState('')

  // Editor state
  const [roomId, setRoomId] = useState('')
  const [text, setText] = useState('')
  const [connected, setConnected] = useState(false)
  const [online, setOnline] = useState<PresenceMsg | null>(null)
  const [version, setVersion] = useState(0)
  const [syncing, setSyncing] = useState(false)
  const [snack, setSnack] = useState('')

  const textRef = useRef('')
  const versionRef = useRef(0)
  const wsRef = useRef<WebSocket | null>(null)
  const userNameRef = useRef('我')
  const userIdRef = useRef(`mobile_${Date.now().toString(36)}`)
  const heartbeatRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchDocs = useCallback(async () => {
    try {
      const res = await listDocuments(50, 0)
      setDocs(res?.documents || res || [])
    } catch {
      setSnack('加载文档失败')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => { fetchDocs() }, [])

  // Load user name from auth store
  useEffect(() => {
    storage.getJSON('knowall_user').then(u => {
      const user = (u || {}) as { username?: string }
      if (user.username) userNameRef.current = user.username
    }).catch(() => {})
  }, [])

  const closeWS = useCallback(() => {
    if (heartbeatRef.current) clearInterval(heartbeatRef.current)
    heartbeatRef.current = null
    if (wsRef.current) {
      wsRef.current.onclose = null
      wsRef.current.close()
      wsRef.current = null
    }
    setConnected(false)
  }, [])

  useEffect(() => () => closeWS(), [closeWS])

  const connect = useCallback((docId: string) => {
    closeWS()
    setRoomId(docId)
    setText('')
    textRef.current = ''
    versionRef.current = 0
    setVersion(0)
    setSyncing(true)
    setView('editor')

    const proto = WS_BASE_URL.startsWith('https') ? 'wss' : 'ws'
    const host = WS_BASE_URL.replace(/^ws(s?):\/\//, '')
    const connectUrl = async (tok: string) => {
      const url = `${proto}://${host}/ws/sync?doc_id=${encodeURIComponent(docId)}&user_id=${userIdRef.current}&user_name=${encodeURIComponent(userNameRef.current)}&token=${encodeURIComponent(tok)}`
      const ws = new WebSocket(url)
      wsRef.current = ws
      setupWS(ws)
    }
    storage.getItem('knowall_token').then(tok => connectUrl(tok || '')).catch(() => connectUrl(''))
  }, [closeWS])

  function setupWS(ws: WebSocket) {
    ws.onopen = () => {
      setConnected(true)
      ws.send(JSON.stringify({ type: 'reconnect', local_version: 0 }))
      // Heartbeat every 25s
      heartbeatRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'heartbeat' }))
        }
      }, 25000)
    }

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data)
        const d = msg.data || {}
        switch (msg.type) {
          case 'sync_full':
            textRef.current = d.content?.filename ? `文档 ${d.content.filename}\n` : ''
            setText(textRef.current)
            versionRef.current = d.version || 0
            setVersion(versionRef.current)
            setSyncing(false)
            break
          case 'sync_diff':
            if (Array.isArray(d.ops)) {
              for (const op of d.ops) {
                applyRemoteOp(op.data || op)
              }
            }
            versionRef.current = d.version || versionRef.current
            setVersion(versionRef.current)
            setSyncing(false)
            break
          case 'ack':
            versionRef.current = d.new_version ?? versionRef.current
            setVersion(versionRef.current)
            setSyncing(false)
            break
          case 'operation':
            applyRemoteOp(d)
            versionRef.current = msg.version ?? versionRef.current
            setVersion(versionRef.current)
            break
          case 'presence':
            setOnline(d)
            break
          case 'heartbeat_ack':
            break
          case 'error':
            setSnack(d.msg || '服务器错误')
            break
        }
      } catch {}
    }

    ws.onclose = () => { setConnected(false); setOnline(null) }
    ws.onerror = () => setConnected(false)
  }

  // Apply a remote operation to the local text (server already OT-transformed)
  const applyRemoteOp = (op: any) => {
    if (!op || typeof op.operation !== 'string') return
    let t = textRef.current
    const pos = Math.min(op.position || 0, t.length)
    if (op.operation === 'insert') {
      t = t.slice(0, pos) + (op.value || '') + t.slice(pos)
    } else if (op.operation === 'delete') {
      const len = Math.min(op.length || 0, t.length - pos)
      t = t.slice(0, pos) + t.slice(pos + len)
    } else {
      return
    }
    textRef.current = t
    setText(t)
  }

  // Diff local edit → send operation
  const handleChange = (newText: string) => {
    const oldText = textRef.current
    if (newText === oldText) return

    // Compute minimal diff: common prefix + common suffix
    let p = 0
    const minLen = Math.min(oldText.length, newText.length)
    while (p < minLen && oldText[p] === newText[p]) p++
    let s = 0
    while (
      s < minLen - p &&
      oldText[oldText.length - 1 - s] === newText[newText.length - 1 - s]
    ) s++

    textRef.current = newText
    setText(newText)

    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return

    let op: any
    if (newText.length >= oldText.length) {
      op = { operation: 'insert', position: p, value: newText.slice(p, newText.length - s), path: [] }
    } else {
      op = { operation: 'delete', position: p, length: oldText.length - p - s, path: [] }
    }
    ws.send(JSON.stringify({ type: 'operation', version: versionRef.current, data: op }))
  }

  return (
    <View style={styles.container}>
      {view === 'editor' ? (
        <>
          <Surface style={styles.editorHeader}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <Text variant="titleSmall" style={{ flex: 1 }} numberOfLines={1}>房间: {roomId}</Text>
              <Chip compact style={{ height: 24, backgroundColor: connected ? '#D1FAE5' : '#FEE2E2' }} textStyle={{ fontSize: 10 }}>
                {connected ? `在线 ${online?.total_online ?? 1}` : '已断开'}
              </Chip>
            </View>
            <Text variant="labelSmall" style={{ color: '#6B7280', marginTop: 4 }}>v{version} · 支持多人实时编辑</Text>
            <Button mode="outlined" compact onPress={() => { closeWS(); setView('rooms') }} style={{ marginTop: 8 }}>
              返回房间列表
            </Button>
          </Surface>

          {syncing ? (
            <View style={styles.centered}>
              <ActivityIndicator size="large" color="#7C3AED" />
              <Text style={{ marginTop: 8, color: '#6B7280' }}>正在同步…</Text>
            </View>
          ) : (
            <TextInput
              value={text}
              onChangeText={handleChange}
              mode="outlined"
              multiline
              style={styles.editorInput}
              placeholder="在此输入文本，与其他成员实时同步…"
              editable={connected}
            />
          )}
        </>
      ) : (
        <>
          <Surface style={styles.roomCard}>
            <Text variant="titleMedium" style={{ marginBottom: 8 }}>实时协同</Text>
            <Text variant="bodySmall" style={{ color: '#6B7280', marginBottom: 12 }}>
              选择一篇文档作为协作房间，或输入自定义房间号创建临时房间
            </Text>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              <View style={{ flex: 1 }}>
                <TextInput value={customRoom} onChangeText={setCustomRoom} placeholder="自定义房间号" autoCapitalize="none" />
              </View>
              <Button mode="contained" onPress={() => {
                if (!customRoom.trim()) { Alert.alert('提示', '请输入房间号'); return }
                connect(customRoom.trim())
              }} buttonColor="#7C3AED" style={{ alignSelf: 'center' }}>进入</Button>
            </View>
          </Surface>

          <Divider style={{ marginVertical: 12 }} />
          <Text variant="titleMedium" style={{ marginBottom: 8 }}>文档房间</Text>

          {loading ? (
            <View style={styles.centered}>
              <ActivityIndicator size="large" color="#7C3AED" />
            </View>
          ) : docs.length === 0 ? (
            <Surface style={styles.emptyCard}>
              <Text variant="bodyLarge" style={{ color: '#9CA3AF' }}>暂无文档</Text>
              <Text variant="bodySmall" style={{ color: '#9CA3AF', marginTop: 4 }}>可先用自定义房间号协作</Text>
            </Surface>
          ) : (
            <ScrollView
              showsVerticalScrollIndicator={false}
              refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); fetchDocs() }} colors={['#7C3AED']} />}
            >
              {docs.map(d => (
                <Surface key={d.id} style={styles.docCard} onTouchEnd={() => connect(d.id)}>
                  <Text variant="bodyMedium" numberOfLines={1}>{d.filename}</Text>
                  <Text variant="labelSmall" style={{ color: '#9CA3AF', marginTop: 2 }}>
                    {d.file_type} · {d.status === 'ready' ? '已解析' : d.status}
                  </Text>
                </Surface>
              ))}
              <View style={{ height: 24 }} />
            </ScrollView>
          )}
        </>
      )}
      <Snackbar visible={!!snack} onDismiss={() => setSnack('')}>{snack}</Snackbar>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F5F3FF', padding: 16 },
  centered: { flex: 1, justifyContent: 'center', alignItems: 'center' },
  emptyCard: { padding: 24, borderRadius: 12, alignItems: 'center', backgroundColor: '#fff' },
  roomCard: { padding: 16, borderRadius: 12, backgroundColor: '#fff', elevation: 2 },
  docCard: { padding: 14, borderRadius: 12, backgroundColor: '#fff', elevation: 1, marginBottom: 8 },
  editorHeader: { padding: 12, borderRadius: 12, backgroundColor: '#fff', elevation: 2, marginBottom: 12 },
  editorInput: { flex: 1, backgroundColor: '#fff', fontFamily: undefined, fontSize: 14 },
})
