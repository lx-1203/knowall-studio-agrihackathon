// Error Boundary for React Native - catches render errors gracefully
import React, { Component, type ReactNode } from 'react'
import { View, StyleSheet, TouchableOpacity } from 'react-native'
import { Text, Button, Surface } from 'react-native-paper'

interface Props {
  children: ReactNode
  fallback?: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary]', error.message, info.componentStack?.slice(0, 500))
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null })
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback
      return (
        <View style={styles.container}>
          <Surface style={styles.card}>
            <Text variant="headlineSmall" style={styles.icon}>⚠️</Text>
            <Text variant="titleMedium" style={styles.title}>页面出错了</Text>
            <Text variant="bodyMedium" style={styles.message}>
              {this.state.error?.message || '未知错误'}
            </Text>
            <Button mode="contained" onPress={this.handleReset} style={styles.button}>
              重试
            </Button>
          </Surface>
        </View>
      )
    }
    return this.props.children
  }
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: 24, backgroundColor: '#F5F3FF' },
  card: { padding: 32, borderRadius: 16, alignItems: 'center', maxWidth: 320, backgroundColor: '#fff', elevation: 4 },
  icon: { fontSize: 48, marginBottom: 12 },
  title: { fontWeight: 'bold', marginBottom: 8, color: '#1F2937' },
  message: { color: '#6B7280', textAlign: 'center', marginBottom: 20, lineHeight: 20 },
  button: { backgroundColor: '#7C3AED', borderRadius: 8 },
})
