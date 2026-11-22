import { useEffect, useState } from 'react'
import { Pressable, View } from 'react-native'
import { Redirect, useRouter } from 'expo-router'
import { ActivityIndicator, Button, Icon, IconButton, Text, TextInput, useTheme } from 'react-native-paper'
import { CalmPanel, CalmScreen, ScreenNotice, calm } from '../src/components/CalmScreen'
import { useDailyCheckIn } from '../src/hooks/useDailyCheckIn'
import { beijingClock, monthCells, shiftMonth } from '../src/checkIn'
import { useAuthStore } from '../src/stores'

export default function CheckInPage() {
  const router = useRouter(), theme = useTheme(), { authReady, isAuthenticated } = useAuthStore()
  const [month, setMonth] = useState<string>(), [selected, setSelected] = useState(''), [note, setNote] = useState(''), [message, setMessage] = useState('')
  const { data, loading, busy, error, reload, submit } = useDailyCheckIn(month)
  useEffect(() => { if (data?.today) setSelected(data.today) }, [data?.today])
  const save = async () => {
    setMessage('')
    const result = await submit(note.trim())
    if (result) { setMonth(result.month); setSelected(result.today); setNote(''); setMessage(result.already_checked_in ? '今天已经打过卡了，记录已同步。' : '打卡成功，今天的坚持已保存。') }
  }
  if (authReady && !isAuthenticated) return <Redirect href="/login" />
  const records = new Map(data?.records.map(record => [record.date, record]) || [])
  const record = records.get(selected)
  let heading = '今天，也坚持一点'
  if (data?.checked_in_today) heading = '今天，也向前了一点'
  if (error) heading = '打卡状态待确认'
  const matchingMonth = data && (!month || data.month === month)
  const changeMonth = (offset: number) => {
    if (!data || busy || loading) return
    const next = shiftMonth(data.month, offset)
    setMonth(next); setSelected(next + '-01'); setMessage('')
  }
  return <CalmScreen title="每日打卡" subtitle="每天一点，把学习留在日历里。" onBack={() => router.canGoBack() ? router.back() : router.replace('/')} onRefresh={busy ? undefined : reload} refreshing={loading}>
    {!!error && <ScreenNotice text={error} onRetry={busy ? undefined : reload} />}
    {!data && !error && <ActivityIndicator style={{ marginVertical: 40 }} />}
    {data && <>
      <View style={{ padding: 24, borderRadius: 22, marginBottom: 20, backgroundColor: theme.colors.primaryContainer }}>
        <View style={calm.row}><Icon source={data.checked_in_today ? 'calendar-check-outline' : 'calendar-heart'} size={28} color={theme.colors.primary} /><Text style={{ color: theme.colors.onSurfaceVariant }}>{data.today.replaceAll('-', '.')} · 北京时间</Text></View>
        <Text variant="headlineMedium" style={{ fontWeight: '700', marginTop: 18 }}>{heading}</Text>
        <Text style={[calm.body, { color: theme.colors.onSurfaceVariant }]}>{data.checked_in_today ? '今日打卡已保存，明天再来继续。' : '完成今天的学习后，给自己留一个记录。'}</Text>
        {!data.checked_in_today && <><TextInput mode="outlined" label="今天学到了什么（选填）" accessibilityLabel="打卡心得" value={note} onChangeText={setNote} maxLength={300} multiline numberOfLines={3} disabled={busy} style={{ marginTop: 10, backgroundColor: theme.colors.surface }} /><Text style={{ textAlign: 'right', marginTop: 5, color: theme.colors.onSurfaceVariant }}>{note.length}/300</Text></>}
        <Button mode="contained" icon={data.checked_in_today ? 'check' : 'check-circle-outline'} accessibilityLabel={data.checked_in_today ? '今日已打卡' : '完成今日打卡'} disabled={busy || loading || !!error || data.checked_in_today} loading={busy} onPress={() => { void save() }} contentStyle={{ minHeight: 52 }} style={{ marginTop: 18, borderRadius: 14 }}>{data.checked_in_today ? '今日已打卡' : '完成今日打卡'}</Button>
        {!!message && <Text accessibilityLiveRegion="polite" style={[calm.body, { color: theme.colors.primary }]}>{message}</Text>}
      </View>
      <View style={{ flexDirection: 'row', marginBottom: 22 }}>{[[data.current_streak, '连续打卡'], [data.longest_streak, '最长连续'], [data.total_days, '累计打卡']].map(([value, label]) => <View key={label} style={{ flex: 1, alignItems: 'center' }}><Text variant="headlineSmall" style={{ fontWeight: '600' }}>{value}<Text variant="bodySmall"> 天</Text></Text><Text style={{ marginTop: 5, color: theme.colors.onSurfaceVariant }}>{label}</Text></View>)}</View>
      <CalmPanel>
        <View style={calm.row}><IconButton icon="chevron-left" accessibilityLabel="上个月" onPress={() => changeMonth(-1)} disabled={busy || loading || data.month === '1900-01'} style={{ marginLeft: -10 }} /><Text variant="titleMedium" style={{ flex: 1, textAlign: 'center' }}>{data.month.replace('-', ' 年 ')} 月</Text><IconButton icon="chevron-right" accessibilityLabel="下个月" onPress={() => changeMonth(1)} disabled={busy || loading || data.month >= data.today.slice(0, 7)} style={{ marginRight: -10 }} /></View>
        <View style={{ flexDirection: 'row', marginVertical: 10 }}>{['一', '二', '三', '四', '五', '六', '日'].map(day => <Text key={day} style={{ width: '14.285714%', textAlign: 'center', color: theme.colors.onSurfaceVariant }}>{day}</Text>)}</View>
        {!matchingMonth ? <ActivityIndicator style={{ margin: 30 }} /> : <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
          {monthCells(data.month).map((day, index) => {
            if (!day) return <View key={'empty-'+index} style={{ width: '14.285714%', height: 46 }} />
            const checked = records.has(day), today = day === data.today, future = day > data.today
            return <Pressable key={day} accessibilityRole="button" accessibilityLabel={`${day}${today ? '，今天' : ''}${checked ? '，已打卡' : '，未打卡'}`} accessibilityState={{ selected: selected === day, disabled: future }} disabled={future} onPress={() => setSelected(day)} style={{ width: '14.285714%', height: 46, justifyContent: 'center', alignItems: 'center' }}>
              <View style={{ width: 34, height: 34, borderRadius: 17, justifyContent: 'center', alignItems: 'center', backgroundColor: checked ? theme.colors.primary : 'transparent', borderWidth: today || selected === day ? 1.5 : 0, borderColor: theme.colors.primary, opacity: future ? 0.3 : 1 }}>
                <Text style={{ color: checked ? theme.colors.onPrimary : theme.colors.onSurface, fontWeight: today ? '700' : '400' }}>{Number(day.slice(-2))}</Text>
              </View>
            </Pressable>
          })}
        </View>}
        <View style={[calm.row, { marginTop: 12, justifyContent: 'space-between' }]}><View style={calm.row}><View style={{ width: 9, height: 9, borderRadius: 5, backgroundColor: theme.colors.primary }} /><Text style={{ color: theme.colors.onSurfaceVariant }}>已打卡 · 本月 {matchingMonth ? data.records.length : '…'} 天</Text></View><Button compact disabled={busy || loading} onPress={() => { setMonth(data.today.slice(0, 7)); setSelected(data.today) }}>回到今天</Button></View>
      </CalmPanel>
      {matchingMonth && !!selected && <CalmPanel><Text variant="titleMedium">{selected} · {record ? '打卡记录' : '未打卡'}</Text><Text selectable style={calm.body}>{record ? record.note || '这一天，你也坚持了学习。' : '这天还没有打卡记录。记录只在当天保存，不提供补签。'}</Text>{record && <Text style={{ color: theme.colors.onSurfaceVariant }}>保存于北京时间 {beijingClock(record.checked_at)}</Text>}</CalmPanel>}
      <Text style={{ color: theme.colors.onSurfaceVariant, lineHeight: 22 }}>按北京时间每日记录一次，手机和电脑使用同一账号同步。打卡不会增加做题数量或学习时长。</Text>
    </>}
  </CalmScreen>
}
