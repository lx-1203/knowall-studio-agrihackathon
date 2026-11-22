import { useEffect, useRef, useState } from 'react'
import { ScrollView, View } from 'react-native'
import { Redirect, useLocalSearchParams, useRouter } from 'expo-router'
import { ActivityIndicator, Button, Chip, Modal, Portal, SegmentedButtons, Text, TextInput, useTheme } from 'react-native-paper'
import api from '../src/api'
import { useAuthStore } from '../src/stores'
import { CalmPanel, CalmScreen, calm } from '../src/components/CalmScreen'
import { normalizeCards, normalizeDocumentHits, resultArray, type DiscoveryCard, type DocumentHit } from '../src/materialDiscovery'
import { noReplay, screenError } from '../src/services/mobileScreenActions'
import { normalizeSearchKind, normalizeSearchScope, searchPlan, type SearchKind, type SearchScope } from '../src/unifiedSearch'

type Point = { id: string; title: string; content: string; category: string }
type Term = { id: string; term: string; definition: string; full_explanation?: string; subject: string }
type SharedHit = { document_id: string; chunk_id: string; filename: string; excerpt: string; page_range?: string; added: boolean }
type Results = { docs: DocumentHit[]; points: Point[]; cards: DiscoveryCard[]; terms: Term[]; shared: SharedHit[] }
const emptyResults = (): Results => ({ docs: [], points: [], cards: [], terms: [], shared: [] })
const kinds = [['all', '全部'], ['docs', '资料'], ['points', '知识点'], ['cards', '知识卡'], ['terms', '术语']] as const
const names = { docs: '资料', points: '知识点', cards: '知识卡', terms: '术语库', shared: '共享库' }

export default function SearchPage() {
  const router = useRouter(), theme = useTheme(), params = useLocalSearchParams<{ q?: string; source?: string; kind?: string; mode?: string }>()
  const { user, authReady, isAuthenticated } = useAuthStore()
  const [query, setQuery] = useState(params.q || ''), [scope, setScope] = useState<SearchScope>(normalizeSearchScope(params.source))
  const [kind, setKind] = useState<SearchKind>(normalizeSearchKind(params.kind)), [mode, setMode] = useState(params.mode === 'related' ? 'related' : 'keyword')
  const [results, setResults] = useState<Results>(emptyResults), [busy, setBusy] = useState(false), [searched, setSearched] = useState(false)
  const [errors, setErrors] = useState<string[]>([]), [hasMore, setHasMore] = useState(false), [limit, setLimit] = useState(10), [optionsOpen, setOptionsOpen] = useState(false)
  const [selected, setSelected] = useState<DiscoveryCard | Point | null>(null), [revealed, setRevealed] = useState(false)
  const [adding, setAdding] = useState(''), [notice, setNotice] = useState(''), [syncing, setSyncing] = useState(false)
  const revision = useRef(0), request = useRef<AbortController | null>(null), addLock = useRef(false)
  const invalidate = () => { revision.current++; request.current?.abort(); setResults(emptyResults()); setBusy(false); setSearched(false); setErrors([]); setHasMore(false); setLimit(10); setNotice(''); setSelected(null) }
  useEffect(() => () => { revision.current++; request.current?.abort() }, [])
  useEffect(() => { invalidate() }, [user?.id])
  useEffect(() => {
    const nextQ = params.q || '', nextScope = normalizeSearchScope(params.source), nextKind = normalizeSearchKind(params.kind), nextMode = params.mode === 'related' ? 'related' : 'keyword'
    if (nextQ !== query || nextScope !== scope || nextKind !== kind || nextMode !== mode) {
      invalidate(); setQuery(nextQ); setScope(nextScope); setKind(nextKind); setMode(nextMode)
    }
  }, [params.q, params.source, params.kind, params.mode])
  const search = async (targetScope = scope, targetKind = kind, count = limit, targetMode = mode) => {
    const q = query.trim(), plan = searchPlan(q, targetScope, targetKind, count, targetMode)
    if (!plan.length || !isAuthenticated || !user?.id) return
    request.current?.abort(); const controller = new AbortController(); request.current = controller
    const current = ++revision.current
    setQuery(q); setBusy(true); setSearched(true); setResults(emptyResults()); setErrors([]); setHasMore(false); setLimit(count); setNotice('')
    router.setParams({ q, source: targetScope, kind: targetKind, mode: targetMode })
    const config = { ...noReplay, signal: controller.signal }
    const settled = await Promise.allSettled(plan.map(async item => {
      const response = item.method === 'post' ? await api.post(item.path, item.body, config) : await api.get(item.path, { ...config, params: item.params })
      const data = response.data, parsed: Partial<Results> = {}
      if (item.key === 'docs') parsed.docs = normalizeDocumentHits(targetMode === 'related' ? resultArray(data, 'sources') : data)
      else if (item.key === 'cards') parsed.cards = normalizeCards(data)
      else if (item.key === 'points') parsed.points = resultArray(data, 'items') as Point[]
      else if (item.key === 'terms') parsed.terms = resultArray(data, 'items') as Term[]
      else parsed.shared = resultArray(data, 'items') as SharedHit[]
      const length = Object.values(parsed)[0]?.length || 0
      return { parsed, more: data.has_more === true || length >= count || Number(data.total) > count }
    }))
    if (current !== revision.current) return
    const combined = emptyResults(), failures: string[] = []
    let more = false
    settled.forEach((item, index) => {
      if (item.status === 'fulfilled') { Object.assign(combined, item.value.parsed); more ||= item.value.more }
      else failures.push(names[plan[index].key] + '暂时无法搜索，请稍后重试。')
    })
    setResults(combined); setErrors(failures); setHasMore(more); setBusy(false)
  }
  const switchScope = (value: string) => {
    const next = normalizeSearchScope(value), shouldSearch = searched && !!query.trim()
    invalidate(); setScope(next); router.setParams({ q: query, source: next, kind, mode })
    if (shouldSearch) void search(next, kind, 10)
  }
  const switchKind = (next: SearchKind) => {
    const shouldSearch = searched && !!query.trim()
    invalidate(); setKind(next); router.setParams({ q: query, source: scope, kind: next, mode })
    if (shouldSearch) void search(scope, next, 10)
  }
  const add = async (hit: SharedHit) => {
    if (addLock.current || hit.added) return
    addLock.current = true; setAdding(hit.chunk_id); const current = revision.current
    try {
      const saved = (await api.post(`/shared-library/documents/${encodeURIComponent(hit.document_id)}/add`, { chunk_id: hit.chunk_id }, noReplay)).data
      if (!saved.knowledge_point_id) throw new Error('Missing knowledge receipt')
      if (current === revision.current) { setResults(old => ({ ...old, shared: old.shared.map(item => item.chunk_id === hit.chunk_id ? { ...item, added: true } : item) })); setNotice('已添加到我的知识。切换到“我的学习”即可搜索。') }
    } catch (error) { if (current === revision.current) setNotice(screenError(error, '添加结果暂未确认，请重新搜索核对“已添加”状态。')) }
    finally { addLock.current = false; setAdding('') }
  }
  const syncTerms = async () => {
    if (syncing) return
    setSyncing(true); const current = revision.current
    try { await api.post('/glossary/sync', {}, noReplay); if (current === revision.current) setNotice('术语库已同步，可以重新搜索。') }
    catch (error) { if (current === revision.current) setNotice(screenError(error, '术语库暂时无法同步，请稍后再试。')) }
    finally { setSyncing(false) }
  }
  const total = Object.values(results).reduce((sum, items) => sum + items.length, 0)
  if (authReady && !isAuthenticated) return <Redirect href="/login" />
  return <CalmScreen title="搜索" subtitle="资料、知识与术语，在这里一起找。" onBack={() => router.canGoBack() ? router.back() : router.replace('/')}>
    <TextInput accessibilityLabel="统一搜索关键词" mode="outlined" placeholder={scope === 'shared' ? '搜索大家共享的知识' : '搜索资料、知识或术语'} value={query} maxLength={200}
      onChangeText={value => { invalidate(); setQuery(value) }} onSubmitEditing={() => { void search() }} returnKeyType="search"
      right={query ? <TextInput.Icon icon="close" accessibilityLabel="清空搜索" onPress={() => { invalidate(); setQuery(''); router.setParams({ q: '' }) }} /> : undefined} />
    <SegmentedButtons value={scope} onValueChange={switchScope} buttons={[{ value: 'mine', label: '我的学习' }, { value: 'shared', label: '共享库' }]} style={{ marginTop: 16 }} />
    {scope === 'mine' && <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingVertical: 14 }}>
      {kinds.map(([value, label]) => <Chip key={value} selected={kind === value} showSelectedCheck={false} onPress={() => switchKind(value)}>{label}</Chip>)}
    </ScrollView>}
    <View style={[calm.row, { marginTop: scope === 'shared' ? 16 : 0, marginBottom: 16 }]}>
      <Button mode="contained" disabled={!query.trim() || busy} loading={busy} onPress={() => { void search() }}>搜索</Button>
      {scope === 'shared' ? <Button onPress={() => router.push('/document-sharing')}>管理我的共享</Button> : <Button onPress={() => setOptionsOpen(value => !value)}>{optionsOpen ? '收起选项' : '更多选项'}</Button>}
    </View>
    {scope === 'mine' && optionsOpen && <View style={[calm.wrap, { marginBottom: 18 }]}>
      {(['keyword', 'related'] as const).map(value => <Chip key={value} selected={mode === value} onPress={() => {
        const shouldSearch = searched && !!query.trim(); invalidate(); setMode(value); router.setParams({ q: query, source: scope, kind, mode: value }); if (shouldSearch) void search(scope, kind, 10, value)
      }}>{value === 'keyword' ? '按关键词查找' : '查找相关资料'}</Chip>)}
      {kind === 'terms' && <Button disabled={syncing} loading={syncing} onPress={() => { void syncTerms() }}>同步术语库</Button>}
    </View>}
    {!searched && <CalmPanel><Text variant="titleMedium">从一个关键词开始</Text><Text style={calm.body}>{scope === 'shared' ? '搜索大家主动共享的资料，选择需要的知识添加到自己的学习库。' : '输入例如“导数”或“细胞核”，再选择想查看的结果类型。'}</Text><Text style={{ color: theme.colors.onSurfaceVariant, lineHeight: 22 }}>输入并搜索后才展示结果，不会默认展开全部知识。</Text></CalmPanel>}
    {busy && <View accessibilityLiveRegion="polite"><ActivityIndicator /><Text style={calm.body}>正在查找“{query.trim()}”…</Text></View>}
    {!!notice && <Text accessibilityLiveRegion="polite" style={calm.body}>{notice}</Text>}
    {!!errors.length && <CalmPanel><Text variant="titleMedium">{total ? '部分内容暂时无法搜索' : '暂时无法完成搜索'}</Text>{errors.map(message => <Text key={message} style={calm.body}>{message}</Text>)}<Text style={{ color: theme.colors.onSurfaceVariant }}>关键词已保留。</Text><Button disabled={busy} onPress={() => { void search() }}>重新搜索</Button></CalmPanel>}
    {searched && !busy && !errors.length && !total && <CalmPanel><Text variant="titleMedium">没有找到相关内容</Text><Text style={calm.body}>试试其他关键词，或切换搜索范围。</Text></CalmPanel>}
    {results.docs.map(hit => <CalmPanel key={hit.key}><Text variant="labelMedium" style={{ color: theme.colors.primary }}>我的学习 · 资料{hit.pageRange ? ` · ${hit.pageRange} 页` : ''}</Text><Text variant="titleMedium" style={{ marginTop: 8 }}>{hit.title}</Text><Text selectable numberOfLines={5} style={calm.body}>{hit.text || '资料名称匹配，打开继续阅读。'}</Text>
      {!!hit.documentId && <Button onPress={() => router.push({ pathname: '/material', params: { id: hit.documentId, ...(hit.chunkId ? { source: hit.chunkId } : {}), from: 'search', returnQ: query, returnMode: mode } })}>打开资料</Button>}</CalmPanel>)}
    {results.points.map(point => <CalmPanel key={point.id}><Text variant="labelMedium" style={{ color: theme.colors.primary }}>我的知识点{point.category ? ` · ${point.category}` : ''}</Text><Text variant="titleMedium" style={{ marginTop: 8 }}>{point.title}</Text><Text selectable numberOfLines={4} style={calm.body}>{point.content}</Text><Button onPress={() => setSelected(point)}>查看知识点</Button></CalmPanel>)}
    {results.cards.map(card => <CalmPanel key={card.id}><Text variant="labelMedium" style={{ color: theme.colors.primary }}>知识卡</Text><Text variant="titleMedium" style={calm.body}>{card.front}</Text><Button onPress={() => { setSelected(card); setRevealed(false) }}>查看知识卡</Button></CalmPanel>)}
    {results.terms.map(term => <CalmPanel key={term.id}><Text variant="labelMedium" style={{ color: theme.colors.primary }}>术语库 · {term.subject}</Text><Text variant="titleMedium" style={{ marginTop: 8 }}>{term.term}</Text><Text selectable numberOfLines={4} style={calm.body}>{term.definition || term.full_explanation}</Text><Button onPress={() => router.push({ pathname: '/glossary', params: { term: term.term, subject: term.subject } })}>查看解释</Button></CalmPanel>)}
    {results.shared.map(hit => <CalmPanel key={hit.chunk_id}><Text variant="labelMedium" style={{ color: theme.colors.primary }}>共享库{hit.page_range ? ` · ${hit.page_range} 页` : ''}</Text><Text variant="titleMedium" style={{ marginTop: 8 }}>{hit.filename}</Text><Text selectable style={calm.body}>{hit.excerpt}</Text>
      <Button mode={hit.added ? 'outlined' : 'contained'} icon={hit.added ? 'check' : 'plus'} disabled={hit.added || !!adding} loading={adding === hit.chunk_id} onPress={() => { void add(hit) }}>{hit.added ? '已添加到我的知识' : '添加这条知识'}</Button></CalmPanel>)}
    {hasMore && !busy && (limit < 40 ? <Button onPress={() => { void search(scope, kind, Math.min(limit + 10, 40)) }}>查看更多结果</Button> : <Text style={calm.body}>结果较多，可以输入更具体的关键词。</Text>)}
    <Portal><Modal visible={!!selected} onDismiss={() => setSelected(null)} contentContainerStyle={{ margin: 16, padding: 20, borderRadius: 18, backgroundColor: theme.colors.surface, maxHeight: '90%' }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 16 }}>{selected && ('front' in selected ? <><Text variant="titleLarge">知识卡</Text><Text selectable style={calm.body}>{selected.front}</Text>{revealed && <><Text selectable style={calm.body}>{selected.back}</Text>{!!selected.hints && <Text style={calm.body}>{selected.hints}</Text>}</>}<Button onPress={() => setRevealed(value => !value)}>{revealed ? '收起答案' : '查看答案'}</Button></> : <><Text variant="titleLarge">{selected.title}</Text><Text selectable style={calm.body}>{selected.content}</Text></>)}</ScrollView><Button onPress={() => setSelected(null)}>返回搜索</Button>
    </Modal></Portal>
  </CalmScreen>
}
