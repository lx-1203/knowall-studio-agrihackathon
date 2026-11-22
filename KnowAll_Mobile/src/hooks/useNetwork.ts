// Network connectivity hook for React Native
// Uses @react-native-community/netinfo if installed, otherwise assumes online
import { useState, useEffect } from 'react'

interface NetworkState {
  isConnected: boolean
  isInternetReachable: boolean | null
  type: string
}

export function useNetworkStatus(): NetworkState {
  const [state, setState] = useState<NetworkState>({
    isConnected: true,
    isInternetReachable: true,
    type: 'unknown',
  })

  useEffect(() => {
    let unsubscribe: (() => void) | undefined
    // Dynamic import: gracefully degrades if netinfo is not installed
    try {
      const NetInfo = require('@react-native-community/netinfo')
      if (NetInfo?.addEventListener) {
        const sub = NetInfo.addEventListener((netState: any) => {
          setState({
            isConnected: netState.isConnected ?? true,
            isInternetReachable: netState.isInternetReachable,
            type: netState.type ?? 'unknown',
          })
        })
        unsubscribe = sub
      }
    } catch {
      // NetInfo not installed — assume connected
    }
    return () => { if (unsubscribe) unsubscribe() }
  }, [])

  return state
}

