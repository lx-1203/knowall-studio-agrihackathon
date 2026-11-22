import { Tabs, Redirect } from 'expo-router'
import { useState } from 'react'
import { Pressable, View } from 'react-native'
import LearningToolsSheet from '../../src/components/LearningToolsSheet'
import { useAuthStore } from '../../src/stores'
import { Icon, useTheme } from 'react-native-paper'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
export default function TabLayout() {
  const { isAuthenticated, authReady } = useAuthStore()
  const theme = useTheme()
  const insets = useSafeAreaInsets()
  const [toolsOpen, setToolsOpen] = useState(false)
  if (!authReady) return null
  if (!isAuthenticated) return <Redirect href="/login" />
  return <><Tabs screenOptions={{
    headerStyle: { backgroundColor: theme.colors.surface }, headerTintColor: theme.colors.onSurface,
    tabBarActiveTintColor: theme.colors.primary, tabBarInactiveTintColor: theme.colors.onSurfaceVariant,
    tabBarStyle: { backgroundColor: theme.colors.surface, paddingTop: 8, paddingBottom: Math.max(insets.bottom, 8), height: 64 + Math.max(insets.bottom, 8) },
    tabBarLabelStyle: { fontSize: 12 },
  }}>
    <Tabs.Screen name="index" options={{ title: '我的资料', headerShown: false, tabBarLabel: '资料', tabBarIcon: ({ color }) => <Icon source="book-open-outline" size={24} color={typeof color === 'string' ? color : theme.colors.primary} /> }} />
    <Tabs.Screen name="review" options={{ title: '复习', headerShown: false, tabBarIcon: ({ color }) => <Icon source="cards-outline" size={24} color={typeof color === 'string' ? color : theme.colors.primary} /> }} />
    <Tabs.Screen name="tools" options={{ title: '学习工具', headerShown: false, tabBarButton: () =>
      <Pressable accessibilityRole="button" accessibilityLabel="更多学习工具" accessibilityHint="展开学习工具与共享知识库" onPress={() => setToolsOpen(true)} style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
        <View style={{ width: 54, height: 54, borderRadius: 27, marginTop: -20, backgroundColor: theme.colors.primary, alignItems: 'center', justifyContent: 'center', borderWidth: 4, borderColor: theme.colors.surface }}><Icon source="plus" size={28} color={theme.colors.onPrimary} /></View>
      </Pressable> }} />
    <Tabs.Screen name="study" options={{ title: '学习计划', headerShown: false, tabBarLabel: '计划', tabBarIcon: ({ color }) => <Icon source="calendar-outline" size={24} color={typeof color === 'string' ? color : theme.colors.primary} /> }} />
    <Tabs.Screen name="profile" options={{ title: '我的', headerShown: false, tabBarIcon: ({ color }) => <Icon source="account-outline" size={24} color={typeof color === 'string' ? color : theme.colors.primary} /> }} />
    <Tabs.Screen name="upload" options={{ title: '上传资料', headerShown: false, href: null }} />
    <Tabs.Screen name="knowledge" options={{ title: '知识检索', headerShown: false, href: null }} />
    <Tabs.Screen name="quiz" options={{ title: '全部题库', headerShown: false, href: null }} />
    <Tabs.Screen name="flashcards" options={{ title: '知识卡', headerShown: false, href: null }} />
    <Tabs.Screen name="recitation" options={{ title: '背诵练习', headerShown: false, href: null }} />
    <Tabs.Screen name="live" options={{ title: '实时对话', href: null }} />
    <Tabs.Screen name="chat" options={{ title: '学习问答', headerShown: false, href: null }} />
  </Tabs><LearningToolsSheet visible={toolsOpen} onDismiss={() => setToolsOpen(false)} /></>
}
