import { useCallback, useEffect, useRef, useState } from 'react'
import { ScrollView, View } from 'react-native'
import { ActivityIndicator, Avatar, Button, Dialog, Divider, Icon, List, Portal, Snackbar, Switch, Text, TextInput, useTheme } from 'react-native-paper'
import { useFocusEffect, useRouter } from 'expo-router'
import Constants from 'expo-constants'
import { useAuthStore } from '../../src/stores'
import { useAppTheme } from '../../src/components/ThemeProvider'
import api from '../../src/api'
import { CalmPanel, CalmScreen, ScreenNotice, calm } from '../../src/components/CalmScreen'
import { noReplay, screenError } from '../../src/services/mobileScreenActions'
import type { UserProfile, Notification, UserHistory, MasteryAnalysis } from '../../src/types'

type Feed<T> = { items: T[]; total: number; unread_count?: number }
export default function ProfilePage() {
  const theme = useTheme(), { isDark, toggle } = useAppTheme(), router = useRouter()
  const { user, logout } = useAuthStore(), lock = useRef(false), revision = useRef(0)
  const [profile, setProfile] = useState<UserProfile | null>(null), [mastery, setMastery] = useState<MasteryAnalysis | null>(null)
  const [notifications, setNotifications] = useState<Feed<Notification> | null>(null), [history, setHistory] = useState<Feed<UserHistory> | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({}), [loading, setLoading] = useState(false), [busy, setBusy] = useState(false)
  const [notifyOpen, setNotifyOpen] = useState(false), [historyOpen, setHistoryOpen] = useState(false)
  const [notifyPage, setNotifyPage] = useState(1), [historyPage, setHistoryPage] = useState(1)
  const [dialog, setDialog] = useState<'nickname' | 'password' | 'logout' | null>(null), [nickname, setNickname] = useState(''), [oldPwd, setOldPwd] = useState(''), [newPwd, setNewPwd] = useState(''), [confirmPwd, setConfirmPwd] = useState('')
  const [formError, setFormError] = useState(''), [message, setMessage] = useState('')
  const load = useCallback(async () => {
    const v = ++revision.current; setLoading(true)
    const keys = ['profile', 'notifications', 'history', 'mastery']
    const results = await Promise.allSettled([api.get('/user/profile'), api.get('/notifications', { params: { page: 1, page_size: 5 } }), api.get('/user/history', { params: { page: 1, page_size: 5 } }), api.get('/review/mastery')])
    if (v !== revision.current) return
    const next: Record<string, string> = {}
    results.forEach((r, i) => {
      const key = keys[i]
      if (r.status === 'rejected') { next[key] = ({ profile: '账户资料', notifications: '通知', history: '最近活动', mastery: '学习数据' } as Record<string, string>)[key] + '暂时无法读取。'; return }
      const data = r.value.data
      if ((i === 1 || i === 2) && (!Array.isArray(data.items) || typeof data.total !== 'number') || i === 0 && !data.id || i === 3 && typeof data.overall_mastery !== 'number') { next[key] = '返回的数据不完整，请重试。'; return }
      if (i === 0) setProfile(data)
      else if (i === 1) { setNotifications(data); setNotifyPage(1) }
      else if (i === 2) { setHistory(data); setHistoryPage(1) }
      else setMastery(data)
    })
    setErrors(next); setLoading(false)
  }, [user?.id])
  useEffect(() => { setProfile(null); setMastery(null); setNotifications(null); setHistory(null) }, [user?.id])
  useFocusEffect(useCallback(() => { load(); return () => { revision.current++ } }, [load]))
  const more = async (kind: 'notifications' | 'history') => {
    if (lock.current) return
    lock.current = true; setBusy(true); const v = revision.current
    const page = (kind === 'notifications' ? notifyPage : historyPage) + 1
    try {
      const data = (await api.get(kind === 'notifications' ? '/notifications' : '/user/history', { params: { page, page_size: 5 } })).data
      if (!Array.isArray(data.items) || typeof data.total !== 'number') throw new Error()
      if (v !== revision.current) return
      const merge = <T extends { id: string }>(prev: Feed<T> | null): Feed<T> => ({ ...data, items: [...new Map([...(prev?.items || []), ...data.items].map(item => [item.id, item])).values()] })
      if (kind === 'notifications') { setNotifications(merge<Notification>); setNotifyPage(page) }
      else { setHistory(merge<UserHistory>); setHistoryPage(page) }
      setErrors(prev => ({ ...prev, [kind]: '' }))
    } catch { if (v === revision.current) setErrors(prev => ({ ...prev, [kind]: '更多记录没有加载成功，已有记录会保留。' })) }
    finally { lock.current = false; setBusy(false) }
  }
  const markRead = async () => {
    if (lock.current) return
    lock.current = true; setBusy(true); const v = revision.current
    try {
      await api.put('/notifications/read-all', null, noReplay)
      if (v === revision.current) { setNotifications(prev => prev ? { ...prev, unread_count: 0, items: prev.items.map(n => ({ ...n, is_read: true })) } : prev); setErrors(prev => ({ ...prev, notifications: '' })); setMessage('通知已全部标为已读') }
    } catch { if (v === revision.current) setErrors(prev => ({ ...prev, notifications: '标记没有成功，请刷新核对后重试。' })) }
    finally { lock.current = false; setBusy(false) }
  }
  const open = (kind: 'nickname' | 'password' | 'logout') => {
    setDialog(kind); setFormError(''); setNickname(profile?.nickname || ''); setOldPwd(''); setNewPwd(''); setConfirmPwd('')
  }
  const close = () => { if (!busy) { setDialog(null); setOldPwd(''); setNewPwd(''); setConfirmPwd('') } }
  const save = async () => {
    if (lock.current) return
    if (dialog === 'nickname' && !nickname.trim()) { setFormError('请输入昵称。'); return }
    if (dialog === 'password') {
      if (!oldPwd || !newPwd || !confirmPwd) { setFormError('请填写旧密码、新密码和确认密码。'); return }
      if (newPwd.length < 8) { setFormError('新密码至少需要 8 个字符。'); return }
      if (newPwd !== confirmPwd) { setFormError('两次新密码不一致，请核对。'); return }
      if (newPwd === oldPwd) { setFormError('新密码需要与旧密码不同。'); return }
    }
    lock.current = true; setBusy(true); setFormError(''); const v = revision.current
    try {
      if (dialog === 'logout') { await logout(); router.replace('/login'); return }
      if (dialog === 'nickname') {
        const res = (await api.put('/user/profile', { nickname: nickname.trim() }, noReplay)).data
        if (res.id !== user?.id || typeof res.nickname !== 'string') throw new Error()
        if (v !== revision.current) return
        setProfile(prev => ({ ...prev, ...res })); setMessage('昵称已保存')
      } else {
        await api.put('/user/password', { old_password: oldPwd, new_password: newPwd }, noReplay)
        if (v !== revision.current) return
        setOldPwd(''); setNewPwd(''); setConfirmPwd(''); setMessage('密码已修改')
      }
      setDialog(null)
    } catch (e) { if (v === revision.current) setFormError(screenError(e, '保存没有确认，输入已保留，请核对后重试。')) }
    finally { lock.current = false; setBusy(false) }
  }
  const label = profile?.nickname || user?.username || '我的账户'
  return <CalmScreen title="我的" subtitle="记录学习，也照顾自己的节奏。" refreshing={loading} onRefresh={busy ? undefined : load}>
    <CalmPanel>
      <View style={calm.row}><Avatar.Text size={48} label={label[0]} style={{ backgroundColor: theme.colors.primaryContainer }} color={theme.colors.onPrimaryContainer} /><View style={{ flex: 1, marginLeft: 6 }}><Text variant="titleLarge">{label}</Text><Text style={{ color: theme.colors.onSurfaceVariant, marginTop: 6 }}>{user?.email}</Text></View></View>
      {!!errors.profile && <ScreenNotice text={errors.profile} onRetry={load} />}
      {loading && !profile && <ActivityIndicator style={{ marginTop: 16 }} />}
      <Divider style={{ marginVertical: 18 }} />
      <Text variant="labelLarge" style={{ color: theme.colors.onSurfaceVariant }}>学习掌握度</Text>
      {mastery ? mastery.total_knowledge_points === 0 ? <Text style={calm.body}>还没有知识点掌握记录。完成练习后，再回来看看。</Text> : <><Text variant="headlineMedium" style={{ marginTop: 8 }}>{Math.round(mastery.overall_mastery * 100)}%</Text><Text style={calm.body}>{mastery.strong_count} 个强项 · {mastery.weak_count} 个待巩固知识点</Text></> : !errors.mastery && <Text style={calm.body}>正在读取学习记录…</Text>}
      {!!errors.mastery && <ScreenNotice text={errors.mastery} onRetry={load} />}
    </CalmPanel>
    <Text variant="titleMedium" style={calm.section}>学习记录</Text>
    <List.Accordion title={notifications ? '通知 · ' + (notifications.unread_count || 0) + ' 未读' : '通知'} expanded={notifyOpen} onPress={() => setNotifyOpen(v => !v)} style={{ backgroundColor: 'transparent' }} left={p => <List.Icon {...p} icon="bell-outline" />}>
      {notifications?.items.map(n => <View key={n.id} style={{ paddingVertical: 12 }}><Text style={{ fontWeight: n.is_read ? '400' : '600' }}>{n.title}</Text><Text style={[calm.body, { color: theme.colors.onSurfaceVariant }]}>{n.content}</Text><Divider /></View>)}
      {notifications?.total === 0 && <Text style={calm.body}>暂无通知。</Text>}
      {!!errors.notifications && <ScreenNotice text={errors.notifications} onRetry={load} />}
      {!!notifications?.unread_count && <Button disabled={busy} onPress={markRead}>全部标为已读</Button>}
      {!!notifications && notifications.items.length < notifications.total && <Button disabled={busy} onPress={() => more('notifications')}>加载更多通知</Button>}
    </List.Accordion><Divider />
    <List.Accordion title="最近活动" expanded={historyOpen} onPress={() => setHistoryOpen(v => !v)} style={{ backgroundColor: 'transparent' }} left={p => <List.Icon {...p} icon="history" />}>
      {history?.items.map(h => <View key={h.id} style={{ paddingVertical: 12 }}><Text>{h.action_label}</Text><Text style={[calm.body, { color: theme.colors.onSurfaceVariant }]}>{h.created_at?.slice(0, 16).replace('T', ' ')}</Text><Divider /></View>)}
      {history?.total === 0 && <Text style={calm.body}>还没有活动记录。</Text>}
      {!!errors.history && <ScreenNotice text={errors.history} onRetry={load} />}
      {!!history && history.items.length < history.total && <Button disabled={busy} onPress={() => more('history')}>加载更多活动</Button>}
    </List.Accordion><Divider />
    <Text variant="titleMedium" style={[calm.section, { marginTop: 28 }]}>偏好与账户</Text>
    <View style={[calm.row, { paddingVertical: 14, paddingHorizontal: 16 }]}>
      <Icon source="weather-night" size={24} color={theme.colors.onSurfaceVariant} /><View style={{ flex: 1, marginLeft: 10 }}><Text variant="bodyLarge">深色外观</Text><Text style={{ color: theme.colors.onSurfaceVariant, marginTop: 4 }}>{isDark ? '已开启' : '已关闭'}</Text></View><Switch accessibilityLabel="深色外观" value={isDark} onValueChange={toggle} />
    </View>
    <List.Item title="修改昵称" onPress={() => open('nickname')} disabled={busy} left={p => <List.Icon {...p} icon="account-edit-outline" />} right={p => <List.Icon {...p} icon="chevron-right" />} />
    <List.Item title="修改密码" onPress={() => open('password')} disabled={busy} left={p => <List.Icon {...p} icon="lock-outline" />} right={p => <List.Icon {...p} icon="chevron-right" />} />
    <Button mode="outlined" onPress={() => open('logout')} disabled={busy} style={{ marginTop: 24 }}>退出登录</Button>
    <Text style={{ textAlign: 'center', marginTop: 20, color: theme.colors.onSurfaceVariant }}>KnowAll · {Constants.expoConfig?.version || '1.0.0'}</Text>
    <Portal><Dialog visible={!!dialog} onDismiss={close} style={{ maxWidth: 460, width: '90%', alignSelf: 'center' }}>
      <Dialog.Title>{dialog === 'nickname' ? '修改昵称' : dialog === 'password' ? '修改密码' : '退出登录？'}</Dialog.Title>
      <Dialog.ScrollArea><ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingVertical: 12 }}>
        {dialog === 'nickname' && <TextInput label="昵称" accessibilityLabel="昵称" value={nickname} onChangeText={setNickname} maxLength={100} mode="outlined" disabled={busy} />}
        {dialog === 'password' && <>{[['旧密码', oldPwd, setOldPwd], ['新密码', newPwd, setNewPwd], ['确认新密码', confirmPwd, setConfirmPwd]].map(([label, value, change]) => <TextInput key={label as string} label={label as string} accessibilityLabel={label as string} value={value as string} onChangeText={change as (value: string) => void} secureTextEntry autoCapitalize="none" mode="outlined" disabled={busy} style={{ marginBottom: 12 }} />)}<Text style={{ color: theme.colors.onSurfaceVariant }}>新密码至少 8 个字符。</Text></>}
        {dialog === 'logout' && <Text style={calm.body}>退出后，需要重新登录才能查看资料。</Text>}
        {!!formError && <ScreenNotice text={formError} />}
      </ScrollView></Dialog.ScrollArea>
      <Dialog.Actions><Button onPress={close} disabled={busy}>取消</Button><Button onPress={save} disabled={busy} loading={busy}>{dialog === 'logout' ? '确认退出' : '保存'}</Button></Dialog.Actions>
    </Dialog></Portal>
    <Snackbar visible={!!message} onDismiss={() => setMessage('')} duration={3000}>{message}</Snackbar>
  </CalmScreen>
}
