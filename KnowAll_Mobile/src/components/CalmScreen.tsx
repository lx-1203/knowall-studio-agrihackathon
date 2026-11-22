import type { ReactNode } from 'react'
import { KeyboardAvoidingView, Platform, RefreshControl, ScrollView, StyleSheet, View } from 'react-native'
import { Button, IconButton, Text, useTheme } from 'react-native-paper'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

export function CalmScreen({ title, subtitle, children, onBack, backLabel = '返回资料', action, refreshing = false, onRefresh }: {
  title: string; subtitle?: string; children: ReactNode; onBack?: () => void; backLabel?: string; action?: ReactNode;
  refreshing?: boolean; onRefresh?: () => void;
}) {
  const theme = useTheme(), insets = useSafeAreaInsets()
  return <KeyboardAvoidingView style={{ flex: 1, backgroundColor: theme.colors.background }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
    <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingTop: insets.top + 20, paddingHorizontal: 20, paddingBottom: 32 }}
      refreshControl={onRefresh ? <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={theme.colors.primary} colors={[theme.colors.primary]} /> : undefined}>
      <View style={calm.row}>
        {onBack && <IconButton icon="arrow-left" accessibilityLabel={backLabel} onPress={onBack} style={{ marginLeft: -10 }} />}
        <Text variant="headlineSmall" accessibilityRole="header" style={{ flex: 1, fontWeight: '600' }}>{title}</Text>
        {action}
      </View>
      {subtitle && <Text style={{ color: theme.colors.onSurfaceVariant, lineHeight: 22, marginTop: 6, marginBottom: 24 }}>{subtitle}</Text>}
      {children}
    </ScrollView>
  </KeyboardAvoidingView>
}
export function CalmPanel({ children }: { children: ReactNode }) {
  const theme = useTheme()
  return <View style={{ backgroundColor: theme.colors.surface, borderColor: theme.colors.outlineVariant, borderWidth: 1, borderRadius: 16, padding: 18, marginBottom: 18 }}>{children}</View>
}
export function ScreenNotice({ text, onRetry }: { text: string; onRetry?: () => void }) {
  const theme = useTheme()
  return <View accessibilityLiveRegion="polite" style={{ paddingVertical: 12 }}>
    <Text style={{ color: theme.colors.onSurfaceVariant, lineHeight: 22 }}>{text}</Text>
    {onRetry && <Button onPress={onRetry} style={{ alignSelf: 'flex-start', marginLeft: -8 }}>重试</Button>}
  </View>
}
export const calm = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  section: { marginTop: 10, marginBottom: 14, fontWeight: '600' },
  body: { lineHeight: 24, marginVertical: 8 },
})
