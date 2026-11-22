import { useState } from 'react'
import { Alert, Button, Card, Result, Skeleton, Tabs, Tag } from 'antd'
import { DashboardOutlined, TeamOutlined, FileTextOutlined, QuestionCircleOutlined, KeyOutlined, UnorderedListOutlined, FundProjectionScreenOutlined, SafetyCertificateOutlined, CloudServerOutlined } from '@ant-design/icons'
import { useAuthStore } from '../stores'
import AdminOverview from './admin/AdminOverview'
import AdminBusiness from './admin/AdminBusiness'
import { AdminLogs, AdminModels, AdminSystem } from './admin/AdminOperations'
import { errorText, useAdminRead } from './admin/adminApi'
import './admin/admin.css'

export default function AdminPage() {
  const access = useAdminRead<{ is_admin: boolean }>('/access')
  const logout = useAuthStore(state => state.logout)
  const [tab, setTab] = useState('overview')
  if (access.pending) return <Card aria-label="检查管理员权限"><Skeleton active paragraph={{ rows: 8 }} /></Card>
  if (access.error) return <Alert type="error" showIcon message="无法验证管理员权限" description={errorText(access.error)} action={<Button onClick={access.reload}>重试</Button>} />
  if (!access.data?.is_admin) return <Result status="403" title="需要管理员权限" subTitle="管理控制台需要由服务维护者为指定账户开通管理员权限。请使用管理员账户登录。" extra={<Button type="primary" onClick={logout}>切换账户</Button>} />
  const tabs = [
    { key: 'overview', label: <><DashboardOutlined />运行概览</>, children: <AdminOverview /> },
    { key: 'users', label: <><TeamOutlined />用户管理</>, children: <AdminBusiness kind="users" /> },
    { key: 'documents', label: <><FileTextOutlined />资料管理</>, children: <AdminBusiness kind="documents" /> },
    { key: 'questions', label: <><QuestionCircleOutlined />题库管理</>, children: <AdminBusiness kind="questions" /> },
    { key: 'system', label: <><CloudServerOutlined />系统监控</>, children: <AdminSystem /> },
    { key: 'models', label: <><KeyOutlined />模型与密钥</>, children: <AdminModels /> },
    { key: 'logs', label: <><UnorderedListOutlined />调用日志</>, children: <AdminLogs /> },
    { key: 'wall', label: <><FundProjectionScreenOutlined />数据大屏</>, children: <AdminOverview wall /> },
  ]
  return <div className="admin-console">
    <header className="admin-heading"><div><span className="admin-eyebrow">KNOWALL STUDIO / ADMINISTRATION</span><h1>管理控制台</h1><p>从平台运行到业务内容，在这里掌握全局。</p></div><Tag icon={<SafetyCertificateOutlined />} color="cyan">管理员工作空间</Tag></header>
    <Tabs activeKey={tab} onChange={setTab} items={tabs} destroyInactiveTabPane className="admin-tabs" />
  </div>
}
