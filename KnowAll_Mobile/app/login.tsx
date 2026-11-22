// Login page

import { useState } from 'react'
import { isAxiosError } from 'axios'
import { View, StyleSheet, Linking, KeyboardAvoidingView, Platform, ScrollView } from 'react-native'
import { TextInput, Button, Text, Surface, Divider, Icon, HelperText, useTheme } from 'react-native-paper'
import { SafeAreaView } from 'react-native-safe-area-context'
import { useRouter } from 'expo-router'
import { useAuthStore } from '../src/stores'
import { API_BASE_URL, login, register } from '../src/api'

const API_ORIGIN = API_BASE_URL.replace(/\/api\/v1\/?$/, '')

export default function LoginPage() {
  const theme = useTheme()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)
  const [isRegister, setIsRegister] = useState(false)
  const [email, setEmail] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const router = useRouter()
  const { login: doLogin } = useAuthStore()

  const isValidEmail = (e: string) => /^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$/.test(e)

  const handleSubmit = async () => {
    if (loading) return
    setError('')
    if (!username.trim() || !password) {
      setError('请输入用户名和密码')
      return
    }
    if (isRegister) {
      if (!email.trim() || !isValidEmail(email.trim())) {
        setError('请输入有效的邮箱地址')
        return
      }
      if (password.length < 8) {
        setError('密码至少需要8个字符')
        return
      }
      if (password !== confirmPassword) {
        setError('两次输入的密码不一致')
        return
      }
    }
    setLoading(true)
    try {
      if (isRegister) {
        const res = await register({ username: username.trim(), email: email.trim(), password, confirm_password: confirmPassword })
        await doLogin(res.access_token, res.user)
        router.replace('/(tabs)')
      } else {
        const res = await login({ username: username.trim(), password })
        await doLogin(res.access_token, res.user)
        router.replace('/(tabs)')
      }
    } catch (e: unknown) {
      let message = '操作失败，请稍后重试'
      if (isAxiosError<{ detail?: string | { msg?: string }[] }>(e)) {
        const detail = e.response?.data?.detail
        if (typeof detail === 'string') {
          message = detail
        } else if (Array.isArray(detail)) {
          message = detail.map(item => typeof item?.msg === 'string' ? item.msg : '输入有误').join('；')
        } else if (!e.response) {
          message = '连接失败，请检查网络或本地服务后重试'
        }
      }
      setError(message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }}>
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : 'height'}>
    <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.container}>
      <Surface elevation={0} style={[styles.card, { backgroundColor: theme.colors.background }]}>
        <View style={[styles.brandMark, { backgroundColor: theme.colors.primaryContainer }]}><Icon source="book-open-page-variant-outline" size={28} color={theme.colors.primary} /></View>
        <Text variant="headlineLarge" style={styles.title}>
          {isRegister ? '开始你的学习旅程' : '欢迎回来'}
        </Text>
        <Text variant="bodyLarge" style={styles.subtitle}>
          {isRegister ? '把教材、笔记和练习，放在一起。' : '登录 KnowAll，接着上次的内容继续学。'}
        </Text>

        <TextInput
          label={isRegister ? '用户名' : '用户名或邮箱'}
          value={username}
          onChangeText={setUsername}
          mode="outlined"
          style={styles.input}
          autoCapitalize="none"
          accessibilityLabel={isRegister ? '用户名' : '用户名或邮箱'}
        />
        {isRegister && (
          <>
            <TextInput
              label="邮箱"
              value={email}
              onChangeText={setEmail}
              mode="outlined"
              style={styles.input}
              autoCapitalize="none"
              keyboardType="email-address"
            />
          </>
        )}
        <TextInput
          label="密码"
          value={password}
          onChangeText={setPassword}
          mode="outlined"
          style={styles.input}
          accessibilityLabel="密码"
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry={!showPassword}
          right={<TextInput.Icon
            icon={showPassword ? 'eye-off-outline' : 'eye-outline'}
            accessibilityLabel={showPassword ? '隐藏密码' : '显示密码'}
            forceTextInputFocus={false}
            onPress={() => setShowPassword(value => !value)}
          />}
        />
        {isRegister && (
          <TextInput
            label="确认密码"
            value={confirmPassword}
            onChangeText={setConfirmPassword}
            mode="outlined"
            style={styles.input}
            accessibilityLabel="确认密码"
            autoCapitalize="none"
            autoCorrect={false}
            secureTextEntry={!showConfirmPassword}
            right={<TextInput.Icon
              icon={showConfirmPassword ? 'eye-off-outline' : 'eye-outline'}
              accessibilityLabel={showConfirmPassword ? '隐藏确认密码' : '显示确认密码'}
              forceTextInputFocus={false}
              onPress={() => setShowConfirmPassword(value => !value)}
            />}
          />
        )}

        {!!error && (
          <HelperText type="error" accessibilityRole="alert" accessibilityLiveRegion="polite">
            {error}
          </HelperText>
        )}

        <Button
          mode="contained"
          onPress={handleSubmit}
          loading={loading}
          disabled={loading}
          style={styles.button}
          contentStyle={{ minHeight: 48 }}
        >
          {isRegister ? '注册' : '登录'}
        </Button>

        <Button
          mode="text"
          disabled={loading}
          onPress={() => { setError(''); setShowPassword(false); setShowConfirmPassword(false); setIsRegister(!isRegister) }}
        >
          {isRegister ? '已有账号？去登录' : '没有账号？去注册'}
        </Button>

        {/* Social Login */}
        <Divider style={{ marginVertical: 16 }} />
        <Text variant="bodySmall" style={{ textAlign: 'center', color: theme.colors.onSurfaceVariant, marginBottom: 12 }}>
          第三方登录
        </Text>
        <View style={{ flexDirection: 'row', justifyContent: 'center', gap: 16 }}>
          <Button
            mode="outlined"
            icon="qqchat"
            textColor="#12B7F5"
            style={{ borderColor: '#12B7F5', borderRadius: 24 }}
            onPress={() => Linking.openURL(`${API_ORIGIN}/api/v1/oauth/qq/login`)}
          >
            QQ
          </Button>
          <Button
            mode="outlined"
            icon="wechat"
            textColor="#07C160"
            style={{ borderColor: '#07C160', borderRadius: 24 }}
            onPress={() => Linking.openURL(`${API_ORIGIN}/api/v1/oauth/wechat/login`)}
          >
            微信
          </Button>
        </View>
      </Surface>
    </ScrollView></KeyboardAvoidingView></SafeAreaView>
  )
}

const styles = StyleSheet.create({
  container: {
    flexGrow: 1,
    justifyContent: 'center',
    padding: 24,
  },
  card: {
    paddingVertical: 32,
    width: '100%',
    maxWidth: 400,
    alignSelf: 'center',
  },
  title: {
    fontWeight: '700',
    letterSpacing: -0.8,
  },
  subtitle: {
    marginTop: 12,
    marginBottom: 32,
    lineHeight: 24,
  },
  input: {
    marginBottom: 12,
  },
  button: {
    marginTop: 8,
    marginBottom: 4,
    paddingVertical: 6,
    borderRadius: 12,
  },
  brandMark: { width: 56, height: 56, borderRadius: 16, alignItems: 'center', justifyContent: 'center', marginBottom: 28 },
})
