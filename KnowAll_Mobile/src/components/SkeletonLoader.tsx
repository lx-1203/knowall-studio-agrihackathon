// Skeleton loading placeholders for React Native
import React, { useEffect, useRef } from 'react'
import { View, StyleSheet, Animated } from 'react-native'
import { Surface } from 'react-native-paper'

function SkeletonBlock({ width, height, style }: { width?: number | string; height?: number; style?: any }) {
  const opacity = useRef(new Animated.Value(0.3)).current

  useEffect(() => {
    const animation = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, { toValue: 0.7, duration: 800, useNativeDriver: true }),
        Animated.timing(opacity, { toValue: 0.3, duration: 800, useNativeDriver: true }),
      ])
    )
    animation.start()
    return () => animation.stop()
  }, [])

  return (
    <Animated.View
      style={[
        { width: width || '100%', height: height || 16, backgroundColor: '#E5E7EB', borderRadius: 8, opacity },
        style,
      ]}
    />
  )
}

export function PageSkeleton() {
  return (
    <View style={styles.container}>
      {/* Header */}
      <SkeletonBlock height={24} width="60%" style={{ marginBottom: 16 }} />
      <SkeletonBlock height={14} width="80%" style={{ marginBottom: 24 }} />

      {/* Stat cards */}
      <View style={styles.grid}>
        {[1, 2, 3, 4].map(i => (
          <Surface key={i} style={styles.card}>
            <SkeletonBlock height={28} width={28} style={{ marginBottom: 8 }} />
            <SkeletonBlock height={20} width="60%" style={{ marginBottom: 4 }} />
            <SkeletonBlock height={12} width="80%" />
          </Surface>
        ))}
      </View>

      {/* Section */}
      <Surface style={styles.section}>
        <SkeletonBlock height={18} width="40%" style={{ marginBottom: 12 }} />
        <SkeletonBlock height={8} width="100%" style={{ marginBottom: 8 }} />
        <SkeletonBlock height={8} width="100%" />
      </Surface>

      {/* List */}
      <Surface style={styles.section}>
        <SkeletonBlock height={18} width="30%" style={{ marginBottom: 16 }} />
        {[1, 2, 3].map(i => (
          <View key={i} style={{ flexDirection: 'row', marginBottom: 12 }}>
            <SkeletonBlock height={40} width={40} style={{ borderRadius: 20, marginRight: 12 }} />
            <View style={{ flex: 1 }}>
              <SkeletonBlock height={14} width="70%" style={{ marginBottom: 6 }} />
              <SkeletonBlock height={12} width="50%" />
            </View>
          </View>
        ))}
      </Surface>
    </View>
  )
}

export function ListSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <View style={styles.container}>
      {Array.from({ length: rows }).map((_, i) => (
        <View key={i} style={{ flexDirection: 'row', marginBottom: 16, padding: 12 }}>
          <SkeletonBlock height={48} width={48} style={{ borderRadius: 8, marginRight: 12 }} />
          <View style={{ flex: 1 }}>
            <SkeletonBlock height={14} width="60%" style={{ marginBottom: 8 }} />
            <SkeletonBlock height={12} width="90%" style={{ marginBottom: 4 }} />
            <SkeletonBlock height={12} width="40%" />
          </View>
        </View>
      ))}
    </View>
  )
}

export function CardSkeleton() {
  return (
    <Surface style={styles.section}>
      <SkeletonBlock height={20} width="50%" style={{ marginBottom: 12 }} />
      <SkeletonBlock height={14} width="100%" style={{ marginBottom: 8 }} />
      <SkeletonBlock height={14} width="100%" style={{ marginBottom: 8 }} />
      <SkeletonBlock height={14} width="70%" style={{ marginBottom: 16 }} />
      <SkeletonBlock height={36} width="100%" style={{ borderRadius: 18 }} />
    </Surface>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 16, backgroundColor: '#F5F3FF' },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 16 },
  card: { width: '47%', padding: 16, borderRadius: 12, backgroundColor: '#fff', elevation: 1 },
  section: { padding: 16, borderRadius: 12, marginBottom: 12, backgroundColor: '#fff', elevation: 1 },
})

export default SkeletonBlock
