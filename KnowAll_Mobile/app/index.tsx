// Root index: redirect based on auth state

import { Redirect } from 'expo-router'
import { useAuthStore } from '../src/stores'
import { ActivityIndicator, View } from 'react-native'

export default function Index() {
  const { isAuthenticated, authReady } = useAuthStore()

  if (!authReady) {
    return (
      <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center' }}>
        <ActivityIndicator size="large" color="#7C3AED" />
      </View>
    )
  }

  if (isAuthenticated) {
    return <Redirect href="/(tabs)" />
  }

  return <Redirect href="/login" />
}
