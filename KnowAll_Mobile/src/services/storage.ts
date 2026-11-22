// Storage abstraction for React Native
// Uses AsyncStorage instead of localStorage

import AsyncStorage from '@react-native-async-storage/async-storage'

export const storage = {
  getItem: async (key: string): Promise<string | null> => {
    return AsyncStorage.getItem(key)
  },

  setItem: async (key: string, value: string): Promise<void> => {
    return AsyncStorage.setItem(key, value)
  },

  removeItem: async (key: string): Promise<void> => {
    return AsyncStorage.removeItem(key)
  },

  getJSON: async <T>(key: string): Promise<T | null> => {
    const raw = await AsyncStorage.getItem(key)
    if (!raw) return null
    try {
      return JSON.parse(raw) as T
    } catch {
      return null
    }
  },

  setJSON: async (key: string, value: any): Promise<void> => {
    return AsyncStorage.setItem(key, JSON.stringify(value))
  },
}
