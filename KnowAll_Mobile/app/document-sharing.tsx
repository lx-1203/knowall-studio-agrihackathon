import { useEffect, useRef, useState } from 'react'
import { View } from 'react-native'
import { Redirect, useRouter } from 'expo-router'
import { ActivityIndicator, Button, Dialog, Portal, Switch, Text } from 'react-native-paper'
import api from '../src/api'
import { useAuthStore } from '../src/stores'
import { CalmPanel, CalmScreen, ScreenNotice, calm } from '../src/components/CalmScreen'
import { noReplay, screenError } from '../src/services/mobileScreenActions'

type OwnedDocument = { id: string; filename: string; status: string; shared: boolean }
export default function DocumentSharingPage() {
  const router = useRouter(), { user, authReady, isAuthenticated } = useAuthStore()
  const [docs, setDocs] = useState<OwnedDocument[]>([]), [busy, setBusy] = useState(false), [more, setMore] = useState(false), [saving, setSaving] = useState(''), [error, setError] = useState('')
  const [confirm, setConfirm] = useState<OwnedDocument | null>(null)
  const revision = useRef(0), lock = useRef(false)
  const load = async (append = false) => {
    const current = ++revision.current; setBusy(true); setError('')
    try {
      const result = (await api.get('/shared-library/mine', { params: { offset: append ? docs.length : 0, limit: 30 } })).data
      if (!Array.isArray(result.items)) throw new Error('Invalid documents')
      if (current === revision.current) { setDocs(old => append ? [...old, ...result.items] : result.items); setMore(result.has_more === true) }
    } catch (e) { if (current === revision.current) setError(screenError(e, '共享设置暂时无法加载。')) }
    finally { if (current === revision.current) setBusy(false) }
  }
  useEffect(() => { setDocs([]); setConfirm(null); if (authReady && isAuthenticated) void load(); return () => { revision.current++ } }, [user?.id, authReady, isAuthenticated])
  const save = async (doc: OwnedDocument, shared: boolean) => {
    if (lock.current) return
    lock.current = true; setSaving(doc.id); setError(''); const current = revision.current
    try {
      const result = (await api.patch(`/shared-library/documents/${encodeURIComponent(doc.id)}/sharing`, { shared }, noReplay)).data
      if (result.id !== doc.id || result.shared !== shared) throw new Error('Unconfirmed sharing')
      if (current === revision.current) { setDocs(old => old.map(item => item.id === doc.id ? { ...item, shared } : item)); setConfirm(null) }
    } catch (e) { if (current === revision.current) setError(screenError(e, '共享设置没有确认，请重新加载后核对。')) }
    finally { lock.current = false; setSaving('') }
  }
  if (authReady && !isAuthenticated) return <Redirect href="/login" />
  return <CalmScreen title="我的文档共享" subtitle="只有主动开启共享的文档，才会进入共享知识库。" onBack={() => router.canGoBack() ? router.back() : router.replace('/')}>
    <Text style={[calm.body, { marginBottom: 20 }]}>关闭共享会停止后续检索和添加；其他人已保存的知识副本会保留。</Text>
    {!!error && <ScreenNotice text={error} onRetry={saving ? undefined : () => { void load() }} />}
    {busy && <ActivityIndicator style={{ margin: 20 }} />}
    {!busy && !error && !docs.length && <CalmPanel><Text variant="titleMedium">还没有自己上传的文档</Text><Text style={calm.body}>先上传一份资料，解析完成后可以选择是否共享。</Text><Button mode="outlined" onPress={() => router.push('/upload')}>上传资料</Button></CalmPanel>}
    {docs.map(doc => <CalmPanel key={doc.id}><View style={calm.row}><View style={{ flex: 1 }}><Text variant="titleMedium">{doc.filename}</Text><Text style={calm.body}>{doc.shared ? '已共享 · 可被其他人检索和添加' : '未共享 · 仅自己使用'}{doc.status !== 'ready' ? ' · 等待解析完成' : ''}</Text></View>
      <Switch accessibilityLabel={'共享文档 '+doc.filename} value={doc.shared} disabled={!!saving || busy || (doc.status !== 'ready' && !doc.shared)} onValueChange={shared => { if (shared) setConfirm(doc); else void save(doc, false) }} /></View></CalmPanel>)}
    {more && <Button disabled={busy || !!saving} onPress={() => { void load(true) }}>加载更多我的文档</Button>}
    <Portal><Dialog visible={!!confirm} onDismiss={() => { if (!saving) setConfirm(null) }}><Dialog.Title>共享这份文档？</Dialog.Title><Dialog.Content><Text style={calm.body}>{confirm?.filename}</Text><Text style={calm.body}>其他用户将能检索文档片段，并保存为自己的知识。你随时可以取消共享。</Text>{!!error && <ScreenNotice text={error} />}</Dialog.Content><Dialog.Actions><Button disabled={!!saving} onPress={() => setConfirm(null)}>取消</Button><Button disabled={!!saving} loading={!!saving} onPress={() => { if (confirm) void save(confirm, true) }}>确认共享</Button></Dialog.Actions></Dialog></Portal>
  </CalmScreen>
}
