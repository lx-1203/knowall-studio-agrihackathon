import { useEffect, useRef, useState } from 'react'
import { Image, ScrollView, StyleSheet, View } from 'react-native'
import { ActivityIndicator, Button, IconButton, Modal, Portal, Text, TextInput, useTheme } from 'react-native-paper'
import { getOriginalPage, getOriginalPreview } from '../api'
import { useOriginalProgress } from '../hooks/useOriginalProgress'

export default function MaterialOriginalReader({ documentId, accountId }: { documentId: string; accountId: string }) {
  const theme = useTheme()
  const [meta, setMeta] = useState<{ count: number } | null>(null)
  const progress = useOriginalProgress(accountId, documentId, meta?.count || 0)
  const { page, zoom } = progress
  const [resetOpen, setResetOpen] = useState(false)
  const [jump, setJump] = useState('')
  const [jumpError, setJumpError] = useState('')
  const [width, setWidth] = useState(320)
  const [image, setImage] = useState<{ page: number; uri: string; width: number; height: number } | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(true)
  const [attempt, setAttempt] = useState(0)
  const [pageAttempt, setPageAttempt] = useState(0)
  const vertical = useRef<ScrollView>(null)
  const horizontal = useRef<ScrollView>(null)
  const scope = `${accountId}:${documentId}`
  const currentScope = useRef(scope)
  currentScope.current = scope
  useEffect(() => { setResetOpen(false) }, [scope])
  useEffect(() => {
    const request = new AbortController()
    let active = true
    setMeta(null); setImage(null); setError(''); setBusy(true)
    getOriginalPreview(documentId, request.signal)
      .then(preview => {
        if (!Number.isInteger(preview.page_count) || preview.page_count < 1) throw new Error('原件没有可阅读的页面')
        if (!active) return
        setMeta({ count: preview.page_count })
      }).catch(reason => { if (active) { setError(previewError(reason)); setBusy(false) } })
    return () => { active = false; request.abort() }
  }, [documentId, accountId, attempt])
  useEffect(() => {
    if (!meta) return
    const request = new AbortController()
    let active = true
    setBusy(true); setImage(null); setError('')
    vertical.current?.scrollTo({ y: 0, animated: false })
    horizontal.current?.scrollTo({ x: 0, animated: false })
    getOriginalPage(documentId, page, request.signal).then(uri => {
      if (!active) return
      Image.getSize(uri, (w, h) => {
        if (!active) return
        if (!(w > 0 && h > 0)) { setError('页面图片无法显示，请重试'); setBusy(false); return }
        setImage({ page, uri, width: w, height: h }); setBusy(false)
      }, () => { if (active) { setError('页面图片无法显示，请重试'); setBusy(false) } })
    }).catch(reason => { if (active) { setError(previewError(reason)); setBusy(false) } })
    return () => { active = false; request.abort() }
  }, [documentId, meta, page, pageAttempt])
  const go = (next: number) => { progress.update({ page: next }); setJump(''); setJumpError('') }
  const jumpTo = () => {
    const next = Number(jump.trim())
    if (!/^\d+$/.test(jump.trim()) || !Number.isSafeInteger(next) || next < 1 || next > (meta?.count || 0)) {
      setJumpError(`请输入1至${meta?.count || 1}的页码`); return
    }
    go(next)
  }
  const current = image?.page === page ? image : null
  const imageWidth = Math.max(1, width - 24) * zoom
  return <View style={{ flex: 1 }} onLayout={event => setWidth(event.nativeEvent.layout.width)}>
    <View style={styles.zoom}>
      <Text style={{ flex: 1, color: theme.colors.onSurfaceVariant }}>原件 · {Math.round(zoom * 100)}%{zoom > 1 ? ' · 可横向拖动' : ''}</Text>
      <IconButton icon="minus" accessibilityLabel="缩小原件" disabled={zoom <= 1} onPress={() => progress.update({ zoom: zoom === 2 ? 1.5 : 1 })} />
      <IconButton icon="plus" accessibilityLabel="放大原件" disabled={zoom >= 2} onPress={() => progress.update({ zoom: zoom === 1 ? 1.5 : 2 })} />
      <Button compact disabled={zoom === 1} onPress={() => progress.update({ zoom: 1 })}>适宽</Button>
    </View>
    <ScrollView ref={vertical} style={{ flex: 1 }} keyboardShouldPersistTaps="handled" contentContainerStyle={{ flexGrow: 1, paddingBottom: 12 }}>
      {busy ? <ActivityIndicator accessibilityLabel="正在加载原件" style={{ marginTop: 60 }} /> : error ? <View style={styles.empty}>
        <Text accessibilityLiveRegion="polite" style={{ color: theme.colors.error }}>{error}</Text>
        <Button onPress={() => meta ? setPageAttempt(n => n + 1) : setAttempt(n => n + 1)}>重试原件</Button>
        <Text style={{ color: theme.colors.onSurfaceVariant }}>也可以切换“文字”阅读已解析内容。</Text>
      </View> : current && <ScrollView ref={horizontal} horizontal contentContainerStyle={{ paddingHorizontal: 12 }}>
        <Image key={`${scope}:${page}`} accessibilityLabel={`原件第${page}页`} accessible source={{ uri: current.uri }} resizeMode="contain" onLoad={() => progress.markRendered(page)}
          onError={() => { setError('页面图片无法显示，请重试'); setImage(null); setBusy(false) }}
          style={{ width: imageWidth, height: imageWidth * current.height / current.width, backgroundColor: '#fff' }} />
      </ScrollView>}
    </ScrollView>
    {!!meta && meta.count > 1 && <>
      <View style={styles.pagination}><Button compact disabled={page <= 1} onPress={() => go(page - 1)}>上一页</Button>
        <Text accessibilityLiveRegion="polite">{page} / {meta.count} 页</Text><Button compact disabled={page >= meta.count} onPress={() => go(page + 1)}>下一页</Button></View>
      <View style={styles.jump}><TextInput mode="outlined" dense accessibilityLabel="跳转页码" placeholder="页码" keyboardType="number-pad" value={jump} onChangeText={setJump} onSubmitEditing={jumpTo} style={{ width: 76 }} />
        <Button compact onPress={jumpTo}>跳转</Button>{!!jumpError && <Text style={{ flex: 1, color: theme.colors.error }}>{jumpError}</Text>}</View>
    </>}
    {!!progress.error && meta && <View style={styles.warning}><Text accessibilityLiveRegion="polite" style={{ color: theme.colors.error }}>{progress.error}</Text>
      <Button compact disabled={progress.saving || (progress.ready && !current)} onPress={() => { if (progress.ready) void progress.save(); else void progress.reload() }}>{progress.ready ? '重试保存' : '重试读取'}</Button>
      {!progress.ready && <Button compact disabled={!current || busy} onPress={() => setResetOpen(true)}>用当前原件位置保存</Button>}
    </View>}
    <Portal><Modal visible={resetOpen} onDismiss={() => { if (!progress.saving) setResetOpen(false) }} contentContainerStyle={[styles.confirm, { backgroundColor: theme.colors.surface }]}>
      <ScrollView contentContainerStyle={{ gap: 16 }}><Text variant="titleLarge">替换原件阅读进度</Text>
        <Text>将记录第 {page} 页、缩放 {Math.round(zoom * 100)}%。旧原件位置会被替换，原文件和笔记不受影响。</Text>
        {!!progress.error && <Text style={{ color: theme.colors.error }} accessibilityLiveRegion="polite">{progress.error}</Text>}
        <Button mode="contained" loading={progress.saving} disabled={progress.saving || !current || busy} onPress={() => { void progress.replaceWithCurrent().then(saved => { if (saved && currentScope.current === scope) setResetOpen(false) }) }}>确认保存原件位置</Button>
        <Button disabled={progress.saving} onPress={() => setResetOpen(false)}>{progress.ready ? '返回阅读' : '保留原件旧记录'}</Button>
      </ScrollView>
    </Modal></Portal>
  </View>
}

function previewError(reason: any): string {
  if (reason?.response?.status === 404) return '原文件暂不可用，请确认已上传并有访问权限'
  // Binary responses cannot expose the server JSON detail, so use status-based copy.
  if (reason?.response?.status === 409) return 'PDF有密码，请先上传解密后的文件'
  if (reason?.response?.status === 413) return '原文件尺寸过大，请使用电脑阅读'
  if (reason?.response?.status === 415) return '此格式暂不支持原件预览，可切换文字阅读'
  if (reason?.response?.status === 422) return '原文件损坏或无法预览，请重新上传'
  return '原件加载失败，请检查网络后重试'
}

const styles = StyleSheet.create({
  zoom: { paddingLeft: 16, paddingRight: 8, flexDirection: 'row', alignItems: 'center', minHeight: 48 },
  empty: { alignItems: 'center', padding: 24, gap: 16 },
  pagination: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 8 },
  jump: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, gap: 8, paddingBottom: 8 },
  warning: { paddingHorizontal: 16, gap: 4, paddingBottom: 8 },
  confirm: { margin: 20, padding: 24, borderRadius: 20, maxHeight: '85%' },
})
