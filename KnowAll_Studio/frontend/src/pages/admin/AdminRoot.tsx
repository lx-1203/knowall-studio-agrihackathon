import { useEffect, useState } from 'react'
import { Alert, Button, Card, Form, Input, Spin } from 'antd'
import { BulbOutlined, LockOutlined, LogoutOutlined, SafetyCertificateOutlined, UserOutlined } from '@ant-design/icons'
import { useAuthStore } from '../../stores'
import { useTheme } from '../../components/ThemeProvider'
import { login } from '../../api'
import ErrorBoundary from '../../components/ErrorBoundary'
import AdminPage from '../AdminPage'
import { errorText } from './adminApi'
import './admin.css'

/** Dedicated admin bootstrap: no local learner session, sync, glossary or study providers. */
export default function AdminRoot() {
  const { isDark, toggle } = useTheme()
  const authenticated = useAuthStore(state => state.isAuthenticated)
  const user = useAuthStore(state => state.user)
  const restore = useAuthStore(state => state.restore)
  const logout = useAuthStore(state => state.logout)
  const authenticate = useAuthStore(state => state.login)
  const [ready, setReady] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    const previousTitle = document.title
    document.title = 'KnowAll Admin · 管理控制台'
    restore(); setReady(true)
    return () => { document.title = previousTitle }
  }, [restore])
  const submit = async (values: { username: string; password: string }) => {
    setPending(true); setError('')
    try {
      const session = await login(values)
      authenticate(session.access_token, session.user)
    } catch (reason) { setError(errorText(reason)) }
    finally { setPending(false) }
  }
  return <div className="admin-app">
    <header className="admin-app-header"><div className="admin-brand"><img src="/logo.svg" width={35} height={35} alt="KnowAll 管理端标志" /><div><strong>KnowAll Admin</strong><span>平台管理中心</span></div></div><div className="admin-account"><span>{authenticated ? user?.username || '管理员账户' : '管理员登录'}</span><Button type="text" icon={<BulbOutlined />} aria-label={isDark ? '切换到浅色模式' : '切换到深色模式'} onClick={toggle} />{authenticated && <Button type="text" icon={<LogoutOutlined />} aria-label="退出管理端" onClick={logout}>退出</Button>}</div></header>
    <main className="admin-app-content">
      {!ready ? <div className="admin-auth-loading" role="status"><Spin size="large" />正在验证管理会话…</div> : authenticated ? <ErrorBoundary><AdminPage /></ErrorBoundary> : <div className="admin-auth"><Card><div className="admin-auth-symbol"><SafetyCertificateOutlined /></div><h1>登录管理控制台</h1><p>使用管理员账户管理平台运行、用户与内容。</p>{error && <Alert type="error" showIcon message={error} />}<Form layout="vertical" onFinish={submit} requiredMark={false} disabled={pending}><Form.Item name="username" label="管理员账号" rules={[{ required: true, whitespace: true, message: '请输入管理员账号' }]}><Input prefix={<UserOutlined />} autoComplete="username" placeholder="用户名或邮箱" /></Form.Item><Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}><Input.Password prefix={<LockOutlined />} autoComplete="current-password" /></Form.Item><Button type="primary" htmlType="submit" block loading={pending}>登录管理端</Button></Form><p className="admin-hint">仅已授权的管理员可进入。账号权限由服务器核验。</p></Card></div>}
    </main>
  </div>
}
