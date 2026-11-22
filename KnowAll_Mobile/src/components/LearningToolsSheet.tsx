import { ScrollView, View } from 'react-native'
import { useRouter } from 'expo-router'
import { Button, Icon, IconButton, List, Modal, Portal, Text, useTheme } from 'react-native-paper'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

const tools = [
  ['搜索学习内容', '资料、知识与术语统一查找', 'text-search', '/search'],
  ['每日打卡', '留下今天的学习足迹', 'calendar-check-outline', '/check-in'],
  ['阅读练习', '练习阅读与理解', 'book-open-outline', '/language'],
  ['学习问答', '围绕学习内容提问', 'message-text-outline', '/chat'],
  ['分享中心', '使用分享链接交流资料', 'share-variant-outline', '/share'],
  ['教材编译', '整理你的学习材料', 'notebook-outline', '/books'],
  ['深度研究', '深入一个学习主题', 'magnify-expand', '/research'],
  ['实时协同', '一起整理内容', 'account-group-outline', '/sync-editor'],
  ['实时对话', '开展学习对话', 'microphone-outline', '/live'],
] as const

export default function LearningToolsSheet({ visible, onDismiss }: { visible: boolean; onDismiss: () => void }) {
  const router = useRouter(), theme = useTheme(), insets = useSafeAreaInsets()
  const go = (path: typeof tools[number][3] | '/search' | '/document-sharing') => { onDismiss(); router.push(path) }
  return <Portal><Modal visible={visible} onDismiss={onDismiss} dismissableBackButton overlayAccessibilityLabel="关闭学习工具"
    contentContainerStyle={{ position: 'absolute', bottom: 0, left: 0, right: 0, maxHeight: '88%', borderTopLeftRadius: 24, borderTopRightRadius: 24, padding: 20, paddingBottom: Math.max(insets.bottom, 16), backgroundColor: theme.colors.surface }}>
    <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 12 }}>
      <Text variant="titleLarge" accessibilityRole="header" style={{ flex: 1, fontWeight: '600' }}>学习工具</Text>
      <IconButton icon="close" accessibilityLabel="关闭学习工具" onPress={onDismiss} />
    </View>
    <ScrollView keyboardShouldPersistTaps="handled">
      <View style={{ padding: 18, borderRadius: 16, backgroundColor: theme.colors.primaryContainer, marginBottom: 12 }}>
        <Icon source="book-plus-outline" size={28} color={theme.colors.primary} />
        <Text variant="titleMedium" style={{ marginTop: 10 }}>从共享知识库添加</Text>
        <Text style={{ lineHeight: 22, marginTop: 6, color: theme.colors.onSurfaceVariant }}>检索大家主动共享的资料，把需要的知识加入自己的学习库。</Text>
        <Button mode="contained" onPress={() => { onDismiss(); router.push({ pathname: '/search', params: { source: 'shared' } }) }} style={{ alignSelf: 'flex-start', marginTop: 14 }}>检索共享知识</Button>
        <Button onPress={() => go('/document-sharing')} style={{ alignSelf: 'flex-start', marginTop: 4 }}>管理我的文档共享</Button>
      </View>
      {tools.map(([title, description, icon, path]) => <List.Item key={path} title={title} description={description} accessibilityRole="button" accessibilityLabel={title}
        onPress={() => go(path)} left={props => <List.Icon {...props} icon={icon} />} right={props => <List.Icon {...props} icon="chevron-right" />} />)}
    </ScrollView>
  </Modal></Portal>
}
