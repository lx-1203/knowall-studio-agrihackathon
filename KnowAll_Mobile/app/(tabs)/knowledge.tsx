import { Redirect, useLocalSearchParams } from 'expo-router'

export default function KnowledgeSearchEntry() {
  const { q } = useLocalSearchParams<{ q?: string }>()
  return <Redirect href={{ pathname: '/search', params: { q: q || '', source: 'mine', kind: 'points' } }} />
}
