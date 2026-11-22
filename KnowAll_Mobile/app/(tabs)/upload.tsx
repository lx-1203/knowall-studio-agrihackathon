import { useRef, useState } from 'react'
import { View } from 'react-native'
import { Button, Chip, Divider, ProgressBar, Text, TextInput, useTheme } from 'react-native-paper'
import * as DocumentPicker from 'expo-document-picker'
import { useRouter } from 'expo-router'
import api from '../../src/api'
import { CalmScreen, CalmPanel, ScreenNotice, calm } from '../../src/components/CalmScreen'
import { noReplay, screenError, uploadPickedFile } from '../../src/services/mobileScreenActions'

const types = ['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'application/vnd.openxmlformats-officedocument.presentationml.presentation', 'text/plain', 'text/markdown', 'image/png', 'image/jpeg']
type Receipt = { id: string; name: string; subject: string; kind: string; organized: boolean }
export default function UploadPage() {
  const router = useRouter(), theme = useTheme(), lock = useRef(false)
  const [mode, setMode] = useState<'file' | 'url'>('file'), [file, setFile] = useState<DocumentPicker.DocumentPickerAsset | null>(null)
  const [url, setUrl] = useState(''), [subject, setSubject] = useState(''), [kind, setKind] = useState('knowledge')
  const [busy, setBusy] = useState(false), [progress, setProgress] = useState(0), [error, setError] = useState('')
  const [receipt, setReceipt] = useState<Receipt | null>(null)
  const organize = async (value: Receipt) => {
    const res = (await api.patch('/documents/' + encodeURIComponent(value.id) + '/organization', { subject: value.subject, material_kind: value.kind }, noReplay)).data
    if (res.id !== value.id || res.subject !== value.subject || res.material_kind !== value.kind) throw new Error('分类保存回执不完整')
    setReceipt({ ...value, organized: true }); setError('')
  }
  const pick = async () => {
    if (lock.current) return
    try {
      const result = await DocumentPicker.getDocumentAsync({ type: types, copyToCacheDirectory: true })
      if (!result.canceled && result.assets?.[0]) { setFile(result.assets[0]); setError('') }
    } catch { setError('文件没有选好，请重新选择。') }
  }
  const submit = async () => {
    if (lock.current) return
    let normalized = url.trim()
    if (!receipt) {
      if (mode === 'file' && !file) { setError('先选择一个文件。'); return }
      if (mode === 'url') {
        try { const parsed = new URL(normalized); if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname) throw new Error(); normalized = parsed.href }
        catch { setError('请输入完整的 http:// 或 https:// 网页链接。'); return }
      }
    }
    lock.current = true; setBusy(true); setError(''); setProgress(0)
    let saved = receipt
    try {
      if (!saved) {
        const res = mode === 'file' ? await uploadPickedFile(file!, setProgress) : (await api.post('/documents/import-url', { url: normalized }, noReplay)).data
        if (typeof res.document_id !== 'string' || !res.document_id || res.status === 'error') throw new Error()
        saved = { id: res.document_id, name: mode === 'file' ? file!.name : normalized, subject: subject.trim(), kind, organized: false }
        setReceipt(saved)
      }
      await organize(saved)
    } catch (e) {
      setError(saved ? '资料已保存，分类暂未保存。重试只会保存分类，不会重复上传。' : screenError(e, '没有收到成功回执。先到资料页检查是否已保存，再决定是否重试；当前输入已保留。'))
    } finally { lock.current = false; setBusy(false) }
  }
  const frozen = busy || !!receipt
  return <CalmScreen title="添加资料" subtitle="把讲义与题库收好，随时继续学习。" onBack={() => router.replace('/')}>
    {!receipt && <>
      <View style={[calm.wrap, { marginBottom: 18 }]}>
        <Chip selected={mode === 'file'} onPress={() => { setMode('file'); setError('') }} disabled={busy}>本地文件</Chip>
        <Chip selected={mode === 'url'} onPress={() => { setMode('url'); setError('') }} disabled={busy}>网页链接</Chip>
      </View>
      <CalmPanel>
        <Text variant="titleMedium">{mode === 'file' ? '选择学习资料' : '保存网页内容'}</Text>
        {mode === 'file' ? <>
          <Text style={[calm.body, { color: theme.colors.onSurfaceVariant }]}>支持 PDF、Word、Excel、PPT、TXT、Markdown 和图片</Text>
          {file && <Text style={calm.body} testID="picked-file">{file.name}{file.size != null ? ' · ' + Math.max(1, Math.round(file.size / 1024)) + ' KB' : ''}</Text>}
          <Button mode="outlined" onPress={pick} disabled={busy}>{file ? '重新选择文件' : '选择文件'}</Button>
        </> : <TextInput label="网页链接" accessibilityLabel="网页链接" value={url} onChangeText={setUrl} mode="outlined" keyboardType="url" autoCapitalize="none" disabled={busy} style={{ marginTop: 16 }} />}
      </CalmPanel>
    </>}
    <CalmPanel>
      <Text variant="titleMedium">{receipt ? '保存结果' : '归到哪里'}</Text>
      {receipt ? <>
        <Text style={calm.body}>{receipt.name}</Text>
        <Text style={{ color: theme.colors.primary }}>{receipt.organized ? '资料和分类已保存' : '资料已保存 · 分类待保存'}</Text>
        <Text style={calm.body}>{receipt.subject || '未分科目'} · {({ knowledge: '知识资料', question_bank: '题库资料', mixed: '混合资料' } as Record<string, string>)[receipt.kind]}</Text>
      </> : <>
        <TextInput label="科目（可留空）" accessibilityLabel="科目（可留空）" value={subject} onChangeText={setSubject} maxLength={80} disabled={frozen} mode="outlined" style={{ marginVertical: 16 }} />
        <View style={calm.wrap}>{[['knowledge', '知识资料'], ['question_bank', '题库资料'], ['mixed', '混合资料']].map(([value, label]) => <Chip key={value} selected={kind === value} disabled={frozen} onPress={() => setKind(value)}>{label}</Chip>)}</View>
        <Text style={[calm.body, { color: theme.colors.onSurfaceVariant }]}>{kind === 'question_bank' ? '试卷、习题集请选题库资料，方便和讲义分开管理。' : '讲义、教材和阅读材料可选知识资料。'}</Text>
      </>}
      {busy && <><ProgressBar progress={mode === 'file' && !receipt ? progress : undefined} indeterminate={mode === 'url' || !!receipt} style={{ marginTop: 16 }} /><Text style={calm.body}>{receipt ? '正在保存分类…' : mode === 'file' ? '正在上传文件…' : '正在导入网页…'}</Text></>}
      {!!error && <ScreenNotice text={error} />}
      {!receipt?.organized && <Button mode="contained" onPress={submit} loading={busy} disabled={busy} style={{ marginTop: 12 }}>{receipt ? '重试保存分类' : mode === 'file' ? '上传资料' : '导入网页'}</Button>}
      {receipt && <Button mode={receipt.organized ? 'contained' : 'outlined'} disabled={busy} onPress={() => router.push({ pathname: '/material', params: { id: receipt.id } })} style={{ marginTop: 12 }}>打开资料开始学习</Button>}
    </CalmPanel>
    {receipt?.organized && <Button onPress={() => { setReceipt(null); setFile(null); setUrl(''); setError('') }}>继续添加资料</Button>}
    <Divider /><Button onPress={() => router.replace('/')} style={{ marginTop: 12 }}>查看我的资料</Button>
  </CalmScreen>
}

