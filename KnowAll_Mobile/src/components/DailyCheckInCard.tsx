import { View } from 'react-native'
import { useRouter } from 'expo-router'
import { Button, Icon, Surface, Text, useTheme } from 'react-native-paper'
import { useDailyCheckIn } from '../hooks/useDailyCheckIn'

export default function DailyCheckInCard() {
  const router = useRouter(), theme = useTheme(), { data, loading, error } = useDailyCheckIn()
  let label = '每天一点，留下学习的足迹'
  if (loading) label = '正在读取打卡记录…'
  else if (error) label = '打卡状态待确认，点开后可重试'
  else if (data?.checked_in_today) label = `今日已打卡 · 已连续 ${data.current_streak} 天`
  else if (data) label = `今天还未打卡 · 累计 ${data.total_days} 天`
  return <Surface elevation={0} style={{ borderRadius: 16, padding: 16, marginBottom: 20, backgroundColor: theme.colors.primaryContainer }}>
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
      <Icon source={data?.checked_in_today && !error ? 'calendar-check-outline' : 'calendar-heart'} size={28} color={theme.colors.primary} />
      <View style={{ flex: 1 }}><Text variant="titleMedium">每日学习打卡</Text><Text style={{ marginTop: 5, lineHeight: 20, color: theme.colors.onSurfaceVariant }}>{label}</Text></View>
      <Button mode="contained" compact accessibilityLabel="打开每日打卡" onPress={() => router.push('/check-in')}>{data?.checked_in_today && !error ? '查看' : '去打卡'}</Button>
    </View>
  </Surface>
}
