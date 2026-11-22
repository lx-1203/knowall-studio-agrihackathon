import ReactDOM from 'react-dom/client'
import { ConfigProvider, App as AntdApp } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { ThemeProvider } from './components/ThemeProvider'
import AdminRoot from './pages/admin/AdminRoot'
import './admin-base.css'

// Independent HTML/module entry. The learner App, router, sync and glossary are absent.
ReactDOM.createRoot(document.getElementById('root')!).render(
  <ConfigProvider locale={zhCN}><ThemeProvider><AntdApp><AdminRoot /></AntdApp></ThemeProvider></ConfigProvider>,
)
