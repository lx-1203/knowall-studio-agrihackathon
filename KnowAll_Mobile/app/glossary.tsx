import { useEffect, useState } from 'react'
import { View } from 'react-native'
import { Redirect, Stack, useLocalSearchParams, useRouter } from 'expo-router'
import { ActivityIndicator, Button, Chip, Text } from 'react-native-paper'
import { glossaryGetTerm, glossaryGetVariants } from '../src/api'
import { CalmPanel, CalmScreen, ScreenNotice, calm } from '../src/components/CalmScreen'
import type { GlossaryTerm } from '../src/types'

// Lists use the unified search; term deep links retain their detail and variants.
export default function GlossaryPage() {
  const router = useRouter(), params = useLocalSearchParams<{ term?: string; subject?: string; q?: string }>()
  const [entry, setEntry] = useState<GlossaryTerm | null>(null), [variants, setVariants] = useState<GlossaryTerm[]>([])
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!params.term) return
    let active = true
    setBusy(true); setError(''); setEntry(null); setVariants([])
    void Promise.allSettled([glossaryGetTerm(params.term, params.subject), glossaryGetVariants(params.term)]).then(results => {
      if (!active) return
      if (results[0].status === 'fulfilled' && results[0].value?.term) setEntry(results[0].value)
      else setError('暂时无法打开这条解释，请稍后重试。')
      if (results[1].status === 'fulfilled' && Array.isArray(results[1].value?.items)) setVariants(results[1].value.items)
      setBusy(false)
    })
    return () => { active = false }
  }, [params.term, params.subject, attempt])
  if (!params.term) return <Redirect href={{ pathname: '/search', params: { q: params.q || '', source: 'mine', kind: 'terms' } }} />
  const back = () => router.canGoBack() ? router.back() : router.replace({ pathname: '/search', params: { q: params.term, source: 'mine', kind: 'terms' } })
  return <><Stack.Screen options={{ headerShown: false }} /><CalmScreen title="术语解释" onBack={back} backLabel="返回搜索">
    {busy && <ActivityIndicator style={{ margin: 24 }} />}
    {!!error && <ScreenNotice text={error} onRetry={() => setAttempt(value => value + 1)} />}
    {entry && <>
      <CalmPanel><Text variant="headlineSmall">{entry.term}</Text><Text style={calm.body}>{entry.subject}{entry.chapter ? ` · ${entry.chapter}` : ''}</Text>
        {!!entry.aliases?.length && <Text style={calm.body}>别名：{entry.aliases.join('、')}</Text>}
        {!!entry.definition && <Text selectable style={calm.body}>{entry.definition}</Text>}
        {!!entry.full_explanation && entry.full_explanation !== entry.definition && <Text selectable style={calm.body}>{entry.full_explanation}</Text>}
        {!!entry.tags?.length && <View style={calm.wrap}>{entry.tags.map(tag => <Chip key={tag}>{tag}</Chip>)}</View>}
        {!!entry.source_label && <Text style={calm.body}>{entry.source_label}</Text>}
      </CalmPanel>
      {variants.some(variant => variant.id !== entry.id) && <CalmPanel><Text variant="titleMedium">其他学科的解释</Text>{variants.filter(variant => variant.id !== entry.id).map(variant => <Button key={variant.id} onPress={() => router.push({ pathname: '/glossary', params: { term: variant.term, subject: variant.subject } })}>{variant.subject}{variant.chapter ? ` · ${variant.chapter}` : ''}</Button>)}</CalmPanel>}
      {!!entry.related_terms?.length && <CalmPanel><Text variant="titleMedium">相关术语</Text><View style={[calm.wrap, { marginTop: 12 }]}>{entry.related_terms.map(term => <Chip key={term} onPress={() => router.push({ pathname: '/glossary', params: { term } })}>{term}</Chip>)}</View></CalmPanel>}
    </>}
  </CalmScreen></>
}
