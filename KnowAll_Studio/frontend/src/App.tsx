import { Suspense, lazy, useState, useEffect } from 'react'
import { Routes, Route, Link, Navigate, useLocation } from 'react-router-dom'
import { Layout, Menu, Button, Spin, Drawer, Grid } from 'antd'
import { BulbOutlined, MenuOutlined, LogoutOutlined, BookOutlined, ReadOutlined, CalendarOutlined } from '@ant-design/icons'
import { useTheme } from './components/ThemeProvider'
import ErrorBoundary from './components/ErrorBoundary'
import { useAppStore, useAuthStore } from './stores'
import { materialPath } from './materials'
import { openLocalSession } from './localSession'
import { SyncProvider } from './hooks/useSync'
import { GlossaryProvider } from './components/GlossaryContext'
import { DashboardIcon, QuizIcon, FlashcardIcon, StudyPlanIcon, MindMapIcon, UploadIcon, SearchIcon, AIIcon, GameIcon, UserIcon, ShareIcon, SettingsIcon, RecitationIcon, LiveSessionIcon } from './components/MenuIcons'
import { ExperimentOutlined, BookOutlined as BookFilledOutlined } from '@ant-design/icons'
import './product.css'

// Lazy-load all pages for code splitting.
// LoginPage belongs here too: importing it eagerly pulled its antd
// Tabs/Form/Input/App (rc-tabs, rc-field-form, async-validator, rc-notification)
// into the app shell — i.e. into the first load of *every* visitor, including
// already-authenticated ones who never render it.
// See KnowAll_Studio/docs/optimization-report.md §5.
const LoginPage = lazy(() => import('./pages/LoginPage'))
const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const AdminRoot = lazy(() => import('./pages/admin/AdminRoot'))
const MaterialsPage = lazy(() => import('./pages/MaterialsPage'))
const MaterialStudyPage = lazy(() => import('./pages/MaterialStudyPage'))
const BuiltinMaterialPage = lazy(() => import('./pages/BuiltinMaterialPage'))
const UploadPage = lazy(() => import('./pages/UploadPage'))
const KnowledgeNetworkPage = lazy(() => import('./pages/KnowledgeNetworkPage'))
const KnowledgePage = lazy(() => import('./pages/KnowledgePage'))
const QuizPage = lazy(() => import('./pages/QuizPage'))
const FlashcardPage = lazy(() => import('./pages/FlashcardPage'))
const GamePage = lazy(() => import('./pages/GamePage'))
const ChatPage = lazy(() => import('./pages/ChatPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const StudyPage = lazy(() => import('./pages/StudyPage'))
const CheckInPage = lazy(() => import('./pages/CheckInPage'))
const LearningPage = lazy(() => import('./pages/LearningPage'))
const SharePage = lazy(() => import('./pages/SharePage'))
const SearchPage = lazy(() => import('./pages/SearchPage'))
const PersonalCenterPage = lazy(() => import('./pages/PersonalCenterPage'))
const ReadingPage = lazy(() => import('./pages/ReadingPage'))
const SummaryPage = lazy(() => import('./pages/SummaryPage'))
const MindMapPage = lazy(() => import('./pages/MindMapPage'))
const InteractiveQuizPage = lazy(() => import('./pages/InteractiveQuizPage'))
const CoverageReportPage = lazy(() => import('./pages/CoverageReportPage'))
const LanguageLearningPage = lazy(() => import('./pages/LanguageLearningPage'))
const ResearchPage = lazy(() => import('./pages/ResearchPage'))
const BookPage = lazy(() => import('./pages/BookPage'))
const RecitationPage = lazy(() => import('./pages/RecitationPage'))
const LiveSessionPage = lazy(() => import('./pages/LiveSessionPage'))
const GlossarySearchPage = lazy(() => import('./pages/GlossarySearchPage'))
const GlossaryDetailPage = lazy(() => import('./pages/GlossaryDetailPage'))

const { Header, Sider, Content } = Layout
const { useBreakpoint } = Grid

function KnowledgeLibraryRedirect() {
  const { search } = useLocation()
  return <Navigate to={`/knowledge-network${search}`} replace />
}

function PageLoader() {
  return (
    <div className="product-loader" role="status" aria-live="polite">
      <Spin size="large" />
      <span>正在打开学习工具…</span>
    </div>
  )
}

export default function App() {
  const location = useLocation()
  if (location.pathname === '/admin' || location.pathname.startsWith('/admin/')) {
    return <ErrorBoundary><Suspense fallback={<PageLoader />}><AdminRoot /></Suspense></ErrorBoundary>
  }
  return <LearningApp />
}

function LearningApp() {
  const location = useLocation()
  const { isDark, toggle } = useTheme()
  const screens = useBreakpoint()
  const isMobile = !screens.lg
  const [drawerOpen, setDrawerOpen] = useState(false)
  const selectedKey = location.pathname === '/' ? '/materials' : '/' + location.pathname.split('/')[1]
  const selectedDoc = useAppStore(state => state.selectedDoc)
  const isMaterialPage = location.pathname === '/' || location.pathname.startsWith('/materials')

  // Auth
  const isAuthenticated = useAuthStore(s => s.isAuthenticated)
  const user = useAuthStore(s => s.user)
  const logout = useAuthStore(s => s.logout)
  const restore = useAuthStore(s => s.restore)
  const [authReady, setAuthReady] = useState(false)
  const [localMode, setLocalMode] = useState(false)
  const [authError, setAuthError] = useState('')
  const [authAttempt, setAuthAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    setAuthReady(false)
    setAuthError('')
    openLocalSession().then(session => {
      if (cancelled) return
      if (session) {
        useAuthStore.getState().login(session.access_token, session.user)
        setLocalMode(true)
      } else {
        restore()
        setLocalMode(false)
      }
      setAuthReady(true)
    }).catch(error => {
      if (!cancelled) { setAuthError(error.message); setAuthReady(true) }
    })
    return () => { cancelled = true }
  }, [authAttempt, restore])

  useEffect(() => {
    if (localMode && !isAuthenticated) setAuthAttempt(attempt => attempt + 1)
  }, [isAuthenticated, localMode])

  // Close the mobile drawer whenever the route changes
  useEffect(() => { setDrawerOpen(false) }, [location.pathname])

  if (!authReady) {
    return <div className="product-login product-bootstrap" role="status"><Spin size="large" /><p>正在连接你的学习空间…</p></div>
  }

  if (authError) {
    return <div role="alert" className="product-login product-bootstrap">
      <img src="/logo.svg" alt="智识工坊 KnowAll 标志" width={64} height={64} />
      <p>{authError}</p>
      <Button type="primary" onClick={() => setAuthAttempt(attempt => attempt + 1)}>重新连接</Button>
    </div>
  }

  if (!isAuthenticated) {
    // LoginPage is lazy now, so it needs its own boundary: this branch sits
    // outside the Suspense that wraps the authenticated route tree.
    return (
      <Suspense fallback={<PageLoader />}>
        <LoginPage />
      </Suspense>
    )
  }

  const menuItems = [
    { key: '/materials', icon: <BookOutlined />, label: <Link to="/materials">我的资料</Link> },
    { key: '/knowledge-network', icon: <MindMapIcon />, label: <Link to="/knowledge-network">知识库</Link> },
    { key: '/flashcards', icon: <FlashcardIcon />, label: <Link to="/flashcards">统一复习</Link> },
    { key: '/study', icon: <StudyPlanIcon />, label: <Link to="/study">学习计划</Link> },
    { key: '/check-in', icon: <CalendarOutlined />, label: <Link to="/check-in">每日打卡</Link> },
    { key: '/quiz', icon: <QuizIcon />, label: <Link to="/quiz">全部题库</Link> },
    { key: '/dashboard', icon: <DashboardIcon />, label: <Link to="/dashboard">学习进度</Link> },
    { key: '/search', icon: <SearchIcon />, label: <Link to="/search">全局搜索</Link> },
    { key: 'learn', icon: <MindMapIcon />, label: '更多工具', children: [
      { key: '/learning', icon: <BookOutlined />, label: <Link to="/learning">内容整理与质检</Link> },
      { key: '/knowledge', icon: <MindMapIcon />, label: <Link to="/knowledge">思维导图</Link> },
      { key: '/language', icon: <BookOutlined />, label: <Link to="/language">词汇学习</Link> },
      { key: '/reading', icon: <ReadOutlined />, label: <Link to="/reading">阅读语言</Link> },
      { key: '/recitation', icon: <RecitationIcon />, label: <Link to="/recitation">背诵练习</Link> },
      { key: '/live', icon: <LiveSessionIcon />, label: <Link to="/live">实时对话</Link> },
      { key: '/upload', icon: <UploadIcon />, label: <Link to="/upload">资料导入</Link> },
      { key: '/chat', icon: <AIIcon />, label: <Link to="/chat">AI助手</Link> },
      { key: '/research', icon: <ExperimentOutlined />, label: <Link to="/research">深度研究</Link> },
      { key: '/books', icon: <BookFilledOutlined />, label: <Link to="/books">教材生成</Link> },
      { key: '/glossary', icon: <DashboardIcon />, label: <Link to="/glossary">名词解释库</Link> },
      { key: '/game', icon: <GameIcon />, label: <Link to="/game">互动游戏</Link> },
    ]},
    { key: 'account', icon: <UserIcon />, label: '个人空间', children: [
      { key: '/personal', icon: <UserIcon />, label: <Link to="/personal">个人中心</Link> },
      { key: '/share', icon: <ShareIcon />, label: <Link to="/share">协作分享</Link> },
      { key: '/settings', icon: <SettingsIcon />, label: <Link to="/settings">系统设置</Link> },
    ]},
  ]

  const menu = (
    <nav className="product-navigation" aria-label="学习导航">
    <p className="product-navigation-caption">我的学习空间</p>
    <Menu
      mode="inline"
      selectedKeys={[selectedKey]}
      items={menuItems}
      defaultOpenKeys={menuItems.filter(item => item.children?.some(child => child.key === selectedKey)).map(item => item.key)}
      forceSubMenuRender
    />
    </nav>
  )

  return (
    <GlossaryProvider>
    <SyncProvider>
    <Layout className="product-shell">
      <Header className="product-header">
        <div className="product-header-brand">
          {isMobile && (
            <Button type="text" icon={<MenuOutlined />} onClick={() => setDrawerOpen(true)}
              aria-label="打开菜单" />
          )}
          <img src="/logo.svg" alt="智识工坊 KnowAll 标志" />
          <div style={{ lineHeight: 1.2 }}>
            <h1>KnowAll Studio</h1>
            <span className="product-subbrand">智识工坊 · 把知识变成自己的</span>
          </div>
        </div>
        <div className="product-header-actions">
          <span className="product-account">{localMode ? '本地空间' : user?.username || '我的账号'}</span>
          <Button type="text" icon={<BulbOutlined />} onClick={toggle}
            aria-label={isDark ? '切换到浅色模式' : '切换到深色模式'}
            title={isDark ? '浅色模式' : '深色模式'} />
          {!localMode && <Button type="text" icon={<LogoutOutlined />} onClick={logout}
            title="登出" aria-label="登出" />}
        </div>
      </Header>
      <Layout>
        {isMobile ? (
          <Drawer
            placement="left"
            open={drawerOpen}
            onClose={() => setDrawerOpen(false)}
            title="学习导航"
            width={264}
            styles={{ body: { padding: 0 } }}
            className={isDark ? 'app-drawer-dark' : undefined}
          >
            {menu}
          </Drawer>
        ) : (
          <Sider width={236} className="product-sidebar">
            {menu}
          </Sider>
        )}
        <Content className="product-content">
          <div className="product-content-inner">
          {!isMaterialPage && <div className="product-context">
            <p><strong>{localMode ? '本地学习空间' : `${user?.username || '同学'}的学习空间`}</strong><br />从资料出发，阅读、理解与巩固。</p>
            <Link to={selectedDoc ? materialPath(selectedDoc) : '/materials'}><Button icon={<BookOutlined />}>{selectedDoc ? '返回当前资料' : '打开我的资料'}</Button></Link>
          </div>}
          <ErrorBoundary>
            <Suspense fallback={<PageLoader />}>
              <div className="page-fade" key={location.pathname}>
                <Routes>
                  <Route path="/" element={<MaterialsPage />} />
                  <Route path="/materials" element={<MaterialsPage />} />
                  <Route path="/materials/builtin-molbio" element={<BuiltinMaterialPage />} />
                  <Route path="/materials/:documentId" element={<MaterialStudyPage />} />
                  <Route path="/dashboard" element={<DashboardPage />} />
                  <Route path="/upload" element={<UploadPage />} />
                  <Route path="/knowledge-network" element={<KnowledgeNetworkPage />} />
                  <Route path="/knowledge" element={<KnowledgePage />} />
                  <Route path="/knowledge-collections" element={<KnowledgeLibraryRedirect />} />
                  <Route path="/search" element={<SearchPage />} />
                  <Route path="/quiz" element={<QuizPage />} />
                  <Route path="/molbio-quiz" element={
                    <iframe src="/molbio-quiz.html" title="分子生物学刷题"
                      style={{ width: '100%', height: 'calc(100vh - 130px)', border: 'none', borderRadius: 12, background: '#fff' }} />
                  } />
                  <Route path="/flashcards" element={<FlashcardPage />} />
                  <Route path="/game" element={<GamePage />} />
                  <Route path="/chat" element={<ChatPage />} />
                  <Route path="/study" element={<StudyPage />} />
                  <Route path="/check-in" element={<CheckInPage />} />
                  <Route path="/learning" element={<LearningPage />} />
                  <Route path="/share" element={<SharePage />} />
                  <Route path="/settings" element={<SettingsPage />} />
                  <Route path="/personal" element={<PersonalCenterPage />} />
                  <Route path="/reading" element={<ReadingPage />} />
                  <Route path="/summary/:id" element={<SummaryPage />} />
                  <Route path="/mindmap/:summaryId" element={<MindMapPage />} />
                  <Route path="/quiz/interactive/:summaryId" element={<InteractiveQuizPage />} />
                  <Route path="/coverage/:summaryId" element={<CoverageReportPage />} />
                  <Route path="/language" element={<LanguageLearningPage />} />
                  <Route path="/language/:docId" element={<LanguageLearningPage />} />
                  <Route path="/research" element={<ResearchPage />} />
                  <Route path="/books" element={<BookPage />} />
                  <Route path="/glossary" element={<GlossarySearchPage />} />
                  <Route path="/glossary/:term" element={<GlossaryDetailPage />} />
                  <Route path="/recitation" element={<RecitationPage />} />
                  <Route path="/live" element={<LiveSessionPage />} />
                  <Route path="/live/:sessionId" element={<LiveSessionPage />} />
                  <Route path="*" element={<Navigate to="/materials" replace />} />
                </Routes>
              </div>
            </Suspense>
          </ErrorBoundary>
          </div>
        </Content>
      </Layout>
    </Layout>
    </SyncProvider>
    </GlossaryProvider>
  )
}
