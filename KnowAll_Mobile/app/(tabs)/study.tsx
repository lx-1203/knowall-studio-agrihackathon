import { useCallback, useRef, useState } from 'react'
import { Pressable, ScrollView, View } from 'react-native'
import { ActivityIndicator, Button, Checkbox, Dialog, Divider, List, Portal, ProgressBar, Text, TextInput, useTheme } from 'react-native-paper'
import { useFocusEffect, useRouter } from 'expo-router'
import api from '../../src/api'
import type { StudyPlanDayHour, StudyPlanWeekDay } from '../../src/types'
import { CalmScreen, CalmPanel, ScreenNotice, calm } from '../../src/components/CalmScreen'
import { noReplay, screenError } from '../../src/services/mobileScreenActions'
import DailyCheckInCard from '../../src/components/DailyCheckInCard'

type Goal = { id: string; title: string; completed: boolean; due_date?: string }
type Plan = { id?: string; plan_id?: string; name: string; description?: string; progress?: number; status?: string; target_end_date?: string; goals?: Goal[]; short_term_plan?: StudyPlanDayHour[]; long_term_plan?: StudyPlanWeekDay[]; ebbinghaus_nodes?: { day: number; review: boolean }[] }
const idOf = (plan: Plan) => plan.id || plan.plan_id || ''
export default function StudyPage() {
  const theme = useTheme(), router = useRouter(), lock = useRef(false), revision = useRef(0), listRevision = useRef(0)
  const [plans, setPlans] = useState<Plan[]>([]), [reminders, setReminders] = useState<{ id: string; message: string }[]>([])
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [remError, setRemError] = useState('')
  const [selected, setSelected] = useState(''), [detail, setDetail] = useState<Plan | null>(null), [detailLoading, setDetailLoading] = useState(false), [detailError, setDetailError] = useState('')
  const [dialog, setDialog] = useState<'create' | 'delete' | 'goal' | null>(null), [name, setName] = useState(''), [description, setDescription] = useState(''), [goal, setGoal] = useState('')
  const [busy, setBusy] = useState(false), [formError, setFormError] = useState(''), [allDays, setAllDays] = useState(false)
  const load = useCallback(async () => {
    const v = ++listRevision.current; setLoading(true)
    const results = await Promise.allSettled([api.get('/study/plans', { params: { limit: 200, offset: 0 } }), api.get('/study/reminders/due')])
    if (v !== listRevision.current) return
    const [p, r] = results
    if (p.status === 'fulfilled' && Array.isArray(p.value.data) && p.value.data.every((item: Plan) => idOf(item) && typeof item.name === 'string')) { setPlans(p.value.data); setError('') }
    else setError('计划列表没有加载成功，已显示的计划会保留。')
    if (r.status === 'fulfilled' && Array.isArray(r.value.data?.reminders)) { setReminders(r.value.data.reminders); setRemError('') }
    else setRemError('今日提醒暂时无法读取。')
    setLoading(false)
  }, [])
  useFocusEffect(useCallback(() => {
    load()
    return () => { listRevision.current++; revision.current++; setSelected(''); setDetail(null) }
  }, [load]))
  const readDetail = async (id: string) => {
    const v = ++revision.current; setDetailLoading(true); setDetailError('')
    try {
      const data = (await api.get('/study/plans/' + encodeURIComponent(id))).data
      if (idOf(data) !== id || !Array.isArray(data.goals)) throw new Error()
      if (v === revision.current) {
        setDetail(data)
        setPlans(prev => prev.map(p => idOf(p) === id ? { ...p, progress: data.progress, status: data.status } : p))
      }
    } catch { if (v === revision.current) setDetailError('计划详情没有加载成功，请重试。') }
    finally { if (v === revision.current) setDetailLoading(false) }
  }
  const view = (plan: Plan) => {
    if (busy) return
    const id = idOf(plan)
    revision.current++; setDetail(null); setAllDays(false); setDetailError('')
    if (selected === id) { setSelected(''); setDetailLoading(false); return }
    setSelected(id); readDetail(id)
  }
  const open = (value: 'create' | 'delete' | 'goal') => { setDialog(value); setFormError(''); setName(''); setDescription(''); setGoal('') }
  const mutate = async () => {
    if (lock.current) return
    if (dialog === 'create' && !name.trim()) { setFormError('请给计划取一个名字。'); return }
    if (dialog === 'goal' && !goal.trim()) { setFormError('请输入要完成的目标。'); return }
    lock.current = true; setBusy(true); setFormError('')
    try {
      if (dialog === 'create') {
        const res = (await api.post('/study/plans', { name: name.trim(), description: description.trim(), goals: goal.trim() ? [{ title: goal.trim() }] : [] }, noReplay)).data
        if (!res.plan_id) throw new Error()
      } else if (dialog === 'goal') {
        const res = (await api.post('/study/goals', { plan_id: selected, title: goal.trim() }, noReplay)).data
        if (!res.goal_id) throw new Error()
        await readDetail(selected)
      } else if (dialog === 'delete') {
        const res = (await api.delete('/study/plans/' + encodeURIComponent(selected), noReplay)).data
        if (res.status !== 'deleted') throw new Error()
        revision.current++; setSelected(''); setDetail(null)
      }
      setDialog(null); await load()
    } catch (e) { setFormError(screenError(e, '没有收到保存回执，输入已保留。请先取消并刷新计划核对，再决定是否重试。')) }
    finally { lock.current = false; setBusy(false) }
  }
  const toggle = async (item: Goal) => {
    if (lock.current) return
    lock.current = true; setBusy(true); setDetailError('')
    try {
      const res = (await api.put('/study/goals/' + encodeURIComponent(item.id) + '/toggle', null, noReplay)).data
      if (res.goal_id !== item.id || typeof res.completed !== 'boolean') throw new Error()
      setDetail(prev => prev ? { ...prev, progress: res.plan_progress, goals: prev.goals?.map(g => g.id === item.id ? { ...g, completed: res.completed } : g) } : prev)
      setPlans(prev => prev.map(p => idOf(p) === selected ? { ...p, progress: res.plan_progress } : p))
    } catch { setDetailError('目标状态未确认。请重试读取详情核对状态，避免重复勾选。') }
    finally { lock.current = false; setBusy(false) }
  }
  const days = detail?.short_term_plan || [], weeks = detail?.long_term_plan || []
  return <CalmScreen title="学习计划" subtitle="把要做的事分成小步，一点点完成。" refreshing={loading} onRefresh={busy ? undefined : load} action={<Button mode="contained-tonal" onPress={() => open('create')} disabled={busy}>新建</Button>}>
    <DailyCheckInCard />
    {reminders.length > 0 && <CalmPanel><Text variant="titleMedium">今日提醒</Text>{reminders.map(r => <Text key={r.id} style={calm.body}>{r.message}</Text>)}</CalmPanel>}
    {!!remError && <ScreenNotice text={remError} onRetry={load} />}
    <Text variant="titleMedium" style={calm.section}>{error && !plans.length ? '我的计划' : '我的计划 · ' + plans.length}</Text>
    {!!error && <ScreenNotice text={error} onRetry={load} />}
    {loading && !plans.length ? <ActivityIndicator style={{ marginVertical: 24 }} /> : !plans.length && !error && <CalmPanel><Text style={calm.body}>还没有学习计划。先写下一个小目标，也可以从资料开始。</Text><Button mode="outlined" onPress={() => open('create')}>创建第一个计划</Button></CalmPanel>}
    {plans.map(plan => <CalmPanel key={idOf(plan)}>
      <List.Item title={plan.name} titleNumberOfLines={3} description={plan.target_end_date ? '目标日期 ' + plan.target_end_date.slice(0, 10) : plan.description || '按自己的节奏学习'} descriptionNumberOfLines={3}
        onPress={() => view(plan)} disabled={busy} style={{ margin: -12 }}
        right={p => <List.Icon {...p} icon={selected === idOf(plan) ? 'chevron-up' : 'chevron-down'} />} />
      {typeof plan.progress === 'number' && <View style={{ marginTop: 16 }}><ProgressBar progress={Math.max(0, Math.min(1, plan.progress / 100))} /><Text style={{ color: theme.colors.onSurfaceVariant, marginTop: 8 }}>已完成 {Math.round(plan.progress)}%</Text></View>}
      {selected === idOf(plan) && <>
        <Divider style={{ marginVertical: 18 }} />
        {detailLoading ? <ActivityIndicator /> : detail && <>
          {!!detail.description && <Text style={calm.body}>{detail.description}</Text>}
          <Text variant="titleMedium" style={calm.section}>学习目标</Text>
          {!detail.goals?.length && <Text style={calm.body}>还没有目标，添加一件可以完成的小事。</Text>}
          {detail.goals?.map(g => <Pressable key={g.id} accessibilityRole="checkbox" accessibilityLabel={g.title} accessibilityState={{ checked: g.completed, disabled: busy || !!detailError }} aria-checked={g.completed} aria-disabled={busy || !!detailError} disabled={busy || !!detailError} onPress={() => toggle(g)} style={[calm.row, { minHeight: 48, paddingVertical: 6 }]}>
            <View accessibilityElementsHidden importantForAccessibility="no-hide-descendants" aria-hidden><Checkbox status={g.completed ? 'checked' : 'unchecked'} /></View><Text style={{ flex: 1, lineHeight: 24 }}>{g.title}</Text>
          </Pressable>)}
          <Button disabled={busy} onPress={() => open('goal')} style={{ alignSelf: 'flex-start' }}>添加目标</Button>
          {(days.length > 0 || weeks.length > 0) && <><Text variant="titleMedium" style={calm.section}>学习日程</Text>
            {(allDays ? days : days.slice(0, 3)).map((d, i) => <View key={i} style={{ marginBottom: 14 }}><Text style={{ color: theme.colors.primary }}>第 {d.day} 天 · {d.date}</Text>{(d.hours || []).map((h, j) => <Text key={j} style={calm.body}>{h.hour} · {h.topic}{h.is_review ? '（复习）' : ''}</Text>)}</View>)}
            {(allDays ? weeks : weeks.slice(0, 1)).map((w, i) => <View key={i}><Text style={{ color: theme.colors.primary }}>第 {w.week} 周</Text>{(w.days || []).map((d, j) => <Text key={j} style={calm.body}>{d.date || '第' + d.day + '天'} · {(d.topics || []).join('、')} · {d.hours} 小时{d.is_review_day ? '（复习）' : ''}</Text>)}</View>)}
            {(days.length > 3 || weeks.length > 1) && <Button onPress={() => setAllDays(v => !v)}>{allDays ? '收起日程' : '展开全部日程'}</Button>}
          </>}
          {!!detail.ebbinghaus_nodes?.length && <Text style={[calm.body, { color: theme.colors.onSurfaceVariant }]}>复习日：{detail.ebbinghaus_nodes.filter(n => n.review).map(n => '第' + n.day + '天').join('、') || '未设置'}</Text>}
        </>}
        {!!detailError && <ScreenNotice text={detailError} onRetry={busy ? undefined : () => readDetail(selected)} />}
        <Button textColor={theme.colors.error} disabled={busy || detailLoading} onPress={() => open('delete')} style={{ alignSelf: 'flex-end', marginTop: 12 }}>删除计划</Button>
      </>}
    </CalmPanel>)}
    <Button icon="book-open-outline" onPress={() => router.push('/')} style={{ marginTop: 8 }}>从资料开始学习</Button>
    <Portal><Dialog visible={!!dialog} onDismiss={() => { if (!busy) setDialog(null) }} style={{ maxWidth: 460, width: '90%', alignSelf: 'center' }}>
      <Dialog.Title>{dialog === 'create' ? '新建学习计划' : dialog === 'goal' ? '添加学习目标' : '删除这个计划？'}</Dialog.Title>
      <Dialog.ScrollArea><ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingVertical: 12 }}>
        {dialog === 'delete' ? <Text style={calm.body}>计划和其中的目标会一起删除。</Text> : <>
          {dialog === 'create' && <><TextInput label="计划名称" accessibilityLabel="计划名称" value={name} onChangeText={setName} maxLength={200} mode="outlined" disabled={busy} /><TextInput label="计划说明（选填）" accessibilityLabel="计划说明（选填）" value={description} onChangeText={setDescription} maxLength={1000} mode="outlined" disabled={busy} style={{ marginTop: 12 }} /></>}
          <TextInput label={dialog === 'create' ? '第一个目标（选填）' : '目标内容'} accessibilityLabel={dialog === 'create' ? '第一个目标（选填）' : '目标内容'} value={goal} onChangeText={setGoal} maxLength={200} mode="outlined" disabled={busy} style={{ marginTop: 12 }} />
        </>}
        {!!formError && <ScreenNotice text={formError} />}
      </ScrollView></Dialog.ScrollArea>
      <Dialog.Actions><Button disabled={busy} onPress={() => setDialog(null)}>取消</Button><Button loading={busy} disabled={busy} onPress={mutate}>{dialog === 'delete' ? '确认删除' : dialog === 'goal' ? '添加' : '创建'}</Button></Dialog.Actions>
    </Dialog></Portal>
  </CalmScreen>
}
