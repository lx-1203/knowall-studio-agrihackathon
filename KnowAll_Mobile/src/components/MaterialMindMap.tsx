import { useMemo, useState } from 'react'
import { View, StyleSheet } from 'react-native'
import { Button, Icon, Searchbar, Surface, Text, TouchableRipple, useTheme } from 'react-native-paper'
import type { DocumentChunk } from '../types'
import type { MaterialSection } from '../materialLearning'
import { filterMindmap, mindmapBranches } from '../materialMindmap'

export default function MaterialMindMap({ title, sections, chunks, activeId, onSource }: {
  title: string; sections: MaterialSection[]; chunks: DocumentChunk[]; activeId?: string; onSource: (ids: string[]) => void
}) {
  const theme = useTheme()
  const [query, setQuery] = useState('')
  const [findOpen, setFindOpen] = useState(false)
  const [closed, setClosed] = useState<string[]>([])
  const [revealed, setRevealed] = useState<string[]>([])
  const branches = useMemo(() => mindmapBranches(sections, chunks), [sections, chunks])
  const visible = useMemo(() => filterMindmap(branches, query), [branches, query])
  const toggle = (key: string) => setClosed(values => values.includes(key) ? values.filter(value => value !== key) : [...values, key])
  return <View style={styles.root}>
    <Surface elevation={0} style={[styles.title, { backgroundColor: theme.colors.primaryContainer }]}>
      <Icon source="file-tree-outline" size={24} color={theme.colors.onPrimaryContainer} />
      <Text variant="titleMedium" style={{ color: theme.colors.onPrimaryContainer, flex: 1 }}>{title}</Text>
    </Surface>
    <Text variant="bodySmall">{sections.length ? '章节 → 知识卡 · 点击分支展开' : '原文结构 · 无需生成即可浏览'} · {branches.length} 个分支</Text>
    <Button icon="magnify" onPress={() => { setFindOpen(value => !value); setQuery('') }} style={{ alignSelf: 'flex-end' }}>{findOpen ? '收起查找' : '查找当前导图'}</Button>
    {findOpen && <Searchbar placeholder="查找当前导图中的内容" accessibilityLabel="查找当前导图关键词" value={query} onChangeText={setQuery} style={{ backgroundColor: theme.colors.surfaceVariant }} />}
    <View style={styles.actions}><Button compact onPress={() => { setQuery(''); setClosed([]) }}>全部展开</Button><Button compact onPress={() => { setQuery(''); setClosed(branches.map(branch => branch.key)) }}>全部收起</Button></View>
    {!visible.length && <View style={styles.empty}><Text>{branches.length ? '没有匹配的分支' : '资料解析完成后显示导图'}</Text>{!!query && <Button onPress={() => setQuery('')}>清空搜索</Button>}</View>}
    <View style={[styles.tree, { borderLeftColor: theme.colors.outlineVariant }]}>{visible.map(branch => {
      const open = !!query.trim() || !closed.includes(branch.key)
      return <View key={branch.key} style={styles.branch}>
        <View style={[styles.connector, { backgroundColor: theme.colors.outlineVariant }]} />
        <Surface elevation={0} style={[styles.node, { backgroundColor: theme.colors.surfaceVariant }]}>
          <TouchableRipple accessibilityRole="button" accessibilityLabel={`${open ? '收起' : '展开'}分支 ${branch.title}`} accessibilityState={{ expanded: open }} onPress={() => toggle(branch.key)} disabled={!!query.trim()}>
            <View style={styles.nodeHeading}><Icon source={open ? 'chevron-down' : 'chevron-right'} size={22} /><View style={{ flex: 1 }}><Text variant="titleSmall">{branch.title}</Text><Text variant="bodySmall" style={{ color: theme.colors.onSurfaceVariant }}>{branch.cards.length} 张知识卡{activeId && branch.source.includes(activeId) ? ' · 正在阅读' : ''}</Text></View></View>
          </TouchableRipple>
          {open && <View style={styles.detail}>
            <Text selectable numberOfLines={5} style={{ lineHeight: 24 }}>{branch.notes || '本章讲解尚未生成'}</Text>
            {branch.cards.map((card, position) => {
              const key = `${branch.key}:${card.id}:${position}`
              return <View key={key} style={[styles.card, { borderLeftColor: theme.colors.primary }]}>
                <Text selectable>{card.front}</Text>{revealed.includes(key) && <Text selectable style={{ color: theme.colors.onSurfaceVariant }}>{card.back}</Text>}
                <Button compact onPress={() => setRevealed(values => values.includes(key) ? values.filter(value => value !== key) : [...values, key])}>{revealed.includes(key) ? '收起卡片答案' : '查看卡片答案'}</Button>
              </View>
            })}
            <Button icon="book-open-outline" disabled={!branch.source.length} onPress={() => onSource(branch.source)}>阅读分支原文</Button>
            {!branch.source.length && <Text variant="bodySmall">来源已变化，暂不可定位</Text>}
          </View>}
        </Surface>
      </View>
    })}</View>
  </View>
}
const styles = StyleSheet.create({
  root: { gap: 14 }, title: { padding: 16, borderRadius: 16, flexDirection: 'row', alignItems: 'center', gap: 12 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between' },
  tree: { marginLeft: 8, paddingLeft: 16, borderLeftWidth: 2, gap: 14 }, branch: { position: 'relative' },
  connector: { position: 'absolute', left: -16, top: 26, width: 16, height: 2 }, node: { borderRadius: 14, overflow: 'hidden' },
  nodeHeading: { padding: 12, flexDirection: 'row', alignItems: 'center', gap: 8 }, detail: { padding: 14, paddingTop: 0, gap: 12 },
  card: { borderLeftWidth: 2, paddingLeft: 12, gap: 8 }, empty: { padding: 16, gap: 8 },
})
