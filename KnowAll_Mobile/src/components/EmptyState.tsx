// Reusable empty state placeholder for mobile
import React from 'react'
import { View, StyleSheet } from 'react-native'
import { Text, Button, Icon } from 'react-native-paper'

interface EmptyStateProps {
  icon?: string
  title: string
  description?: string
  actionLabel?: string
  onAction?: () => void
}

export function EmptyState({ icon = 'email-outline', title, description, actionLabel, onAction }: EmptyStateProps) {
  return (
    <View style={styles.container}>
      <Icon source={icon} size={56} color="#9CA3AF" />
      <View style={{ height: 16 }} />
      <Text variant="titleMedium" style={styles.title}>{title}</Text>
      {description && (
        <Text variant="bodyMedium" style={styles.description}>{description}</Text>
      )}
      {actionLabel && onAction && (
        <Button mode="contained" onPress={onAction} style={styles.button}>
          {actionLabel}
        </Button>
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: 32 },
  icon: { fontSize: 56, marginBottom: 16 },
  title: { fontWeight: '600', color: '#1F2937', marginBottom: 8, textAlign: 'center' },
  description: { color: '#6B7280', textAlign: 'center', lineHeight: 20, marginBottom: 20 },
  button: { backgroundColor: '#7C3AED', borderRadius: 8 },
})

export default EmptyState
