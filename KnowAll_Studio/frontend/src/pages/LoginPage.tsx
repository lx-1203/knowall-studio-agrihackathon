import { useState, useEffect } from 'react'
import { Tabs, Form, Input, Button, App, Alert, Divider, Space } from 'antd'
import { UserOutlined, LockOutlined, MailOutlined, QqOutlined, WechatOutlined, GithubOutlined, BulbOutlined, ArrowRightOutlined, ReadOutlined, FileTextOutlined, CheckCircleOutlined } from '@ant-design/icons'
import { login, register } from '../api'
import { useAuthStore } from '../stores'
import { useTheme } from '../components/ThemeProvider'

const SOCIAL_LOGINS = [
  { provider: 'qq', icon: <QqOutlined />, label: 'QQ登录' },
  { provider: 'wechat', icon: <WechatOutlined />, label: '微信登录' },
  { provider: 'github', icon: <GithubOutlined />, label: 'GitHub登录' },
]

function authError(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof detail === 'string' ? detail : fallback
}

export default function LoginPage() {
  const { message } = App.useApp()
  const authLogin = useAuthStore(s => s.login)
  const { isDark, toggle } = useTheme()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState('login')
  const [providers, setProviders] = useState<string[]>([])

  useEffect(() => {
    let cancelled = false
    fetch('/api/v1/oauth/providers').then(r => r.json()).then(data => {
      if (!cancelled && Array.isArray(data.providers)) setProviders(data.providers.map((p: { provider: string }) => p.provider))
    }).catch(() => {})
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    const hash = window.location.hash
    if (hash.includes('oauth_token=')) {
      const params = new URLSearchParams(hash.slice(1))
      const token = params.get('oauth_token')
      if (token) {
        authLogin(token, { id: params.get('user_id') || '', username: params.get('username') || '', email: '', created_at: new Date().toISOString() })
        message.success(`${params.get('provider') || '第三方'}登录成功`)
        window.location.hash = ''
      }
    }
  }, [authLogin, message])

  const handleLogin = async (values: { username: string; password: string }) => {
    setError('')
    setLoading(true)
    try {
      const data = await login(values)
      authLogin(data.access_token, data.user)
      message.success('登录成功')
    } catch (err) {
      setError(authError(err, '登录失败，请检查网络连接后重试。'))
    } finally { setLoading(false) }
  }

  const handleRegister = async (values: { username: string; email: string; password: string; confirm_password: string }) => {
    setError('')
    setLoading(true)
    try {
      const data = await register(values)
      authLogin(data.access_token, data.user)
      message.success('注册成功，已自动登录')
    } catch (err) {
      setError(authError(err, '注册失败，请检查网络连接后重试。'))
    } finally { setLoading(false) }
  }

  return (
    <main className="product-login page-fade">
      <aside className="product-login-story" aria-label="智识工坊介绍">
        <div className="product-brand">
          <img src="/logo.svg" alt="智识工坊 KnowAll 标志" width={44} height={44} />
          <div><strong>KnowAll Studio</strong><span>智识工坊 · 大学生学习系统</span></div>
        </div>
        <div className="product-story-body">
          <span className="product-story-kicker">你的学习，从这里连起来</span>
          <h1 aria-label="把知识变成自己的。">把知识<br />变成自己的。</h1>
          <p>从一份课程资料开始，整理知识、练习巩固，再把学习进度接着往前推。</p>
          <ol className="product-workflow">
            <li><FileTextOutlined /><div><strong>整理资料</strong><span>课程文档与知识脉络，放在同一个空间</span></div></li>
            <li><ReadOutlined /><div><strong>理解知识</strong><span>阅读、导图与 AI 助手，辅助梳理重点</span></div></li>
            <li><CheckCircleOutlined /><div><strong>练习巩固</strong><span>题库、闪卡与学习计划，按自己的节奏来</span></div></li>
          </ol>
        </div>
        <p className="product-story-footer">Windows · macOS · Linux / 同一账号，同一学习空间</p>
      </aside>
      <section className="product-login-panel" aria-labelledby="product-login-title">
        <Button className="product-login-theme" type="text" icon={<BulbOutlined />} onClick={toggle}
          aria-label={isDark ? '切换到浅色模式' : '切换到深色模式'} title={isDark ? '浅色模式' : '深色模式'} />
        <div className="product-login-form">
          <div className="product-mobile-brand"><img src="/logo.svg" alt="" width={40} height={40} /><strong>智识工坊</strong></div>
          <h2 id="product-login-title">{activeTab === 'login' ? '欢迎回到智识工坊' : '创建你的学习账号'}</h2>
          <p className="product-form-description">{activeTab === 'login' ? '登录账号，继续你的学习。' : '注册后，即可开始建立自己的学习空间。'}</p>
          <Tabs destroyInactiveTabPane activeKey={activeTab} onChange={key => { setActiveTab(key); setError('') }} items={[
            { key: 'login', label: '登录', disabled: loading, children: (
              <Form name="product-login" layout="vertical" requiredMark={false} onFinish={handleLogin} size="large" disabled={loading}>
                <Form.Item name="username" label="账号" rules={[{ required: true, whitespace: true, message: '请输入用户名或邮箱' }]}>
                  <Input prefix={<UserOutlined />} placeholder="用户名或邮箱" autoComplete="username" />
                </Form.Item>
                <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
                  <Input.Password prefix={<LockOutlined />} placeholder="输入你的密码" autoComplete="current-password" />
                </Form.Item>
                {error && <Alert type="error" showIcon message={error} className="product-auth-error" />}
                <Button aria-label="登录" type="primary" htmlType="submit" loading={loading} block icon={!loading && <ArrowRightOutlined />} iconPosition="end">登录</Button>
              </Form>
            ) },
            { key: 'register', label: '注册', disabled: loading, children: (
              <Form name="product-register" layout="vertical" requiredMark={false} onFinish={handleRegister} size="large" disabled={loading}>
                <Form.Item name="username" label="用户名" rules={[
                  { required: true, message: '请输入用户名' },
                  { pattern: /^[a-zA-Z0-9_\u4e00-\u9fff]{2,50}$/, message: '2–50 个字符，可使用中文、英文、数字和下划线' },
                ]}><Input prefix={<UserOutlined />} placeholder="给自己起一个名字" maxLength={50} autoComplete="username" /></Form.Item>
                <Form.Item name="email" label="邮箱" rules={[{ required: true, message: '请输入邮箱' }, { type: 'email', message: '邮箱格式不正确' }]}>
                  <Input prefix={<MailOutlined />} placeholder="you@example.com" autoComplete="email" />
                </Form.Item>
                <Form.Item name="password" label="密码" extra="至少 8 个字符，最多 72 字节（中文约 24 字）。" rules={[
                  { required: true, message: '请输入密码' },
                  { min: 8, message: '密码至少 8 个字符' },
                  { validator: (_, value) => !value || new TextEncoder().encode(value).length <= 72 ? Promise.resolve() : Promise.reject(new Error('密码最多 72 字节（中文约 24 字）')) },
                ]}><Input.Password prefix={<LockOutlined />} placeholder="设置登录密码" autoComplete="new-password" /></Form.Item>
                <Form.Item name="confirm_password" label="确认密码" dependencies={['password']} rules={[
                  { required: true, message: '请确认密码' },
                  ({ getFieldValue }) => ({ validator: (_, value) => !value || getFieldValue('password') === value ? Promise.resolve() : Promise.reject(new Error('两次输入的密码不一致')) }),
                ]}><Input.Password prefix={<LockOutlined />} placeholder="再次输入密码" autoComplete="new-password" /></Form.Item>
                {error && <Alert type="error" showIcon message={error} className="product-auth-error" />}
                <Button aria-label="注册" type="primary" htmlType="submit" loading={loading} block>注册</Button>
              </Form>
            ) },
          ]} />
          {providers.some(p => SOCIAL_LOGINS.some(s => s.provider === p)) && <>
            <Divider plain>其他登录方式</Divider>
            <Space wrap className="product-social-login">{SOCIAL_LOGINS.filter(s => providers.includes(s.provider)).map(s => (
              <Button key={s.provider} icon={s.icon} disabled={loading} onClick={() => { window.location.href = `/api/v1/oauth/${s.provider}/login?redirect_to=/` }}>{s.label}</Button>
            ))}</Space>
          </>}
          <p className="product-form-footer">你的课程资料与学习工具，都在这里。</p>
        </div>
      </section>
    </main>
  )
}
