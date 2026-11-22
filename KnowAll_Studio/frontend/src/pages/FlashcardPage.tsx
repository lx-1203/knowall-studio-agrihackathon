import { useState, useEffect, useCallback, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Card, Button, Space, App, Spin, Tag, Select, Input, Modal, Dropdown, Table, Progress, Result, theme } from 'antd'
import { RobotOutlined, SwapOutlined, RightOutlined, IdcardOutlined, DownloadOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { getDueCards, reviewCard, generateCards, listDecks, exportAnkiDeck, searchCards, getRelatedCards, generateDeckSummary } from '../api'
import { useAppStore, useFlashcardStore, useAuthStore } from '../stores'
import ClozeRenderer from '../components/ClozeRenderer'
import CompareTable from '../components/CompareTable'
import LoadFailure from '../components/LoadFailure'
import LearningContent from '../components/LearningContent'
import FlashcardFront from '../components/FlashcardFront'
import ManualFlashcardModal from '../components/ManualFlashcardModal'
import CreateFlashcardDeckModal from '../components/CreateFlashcardDeckModal'
import EditFlashcardModal from '../components/EditFlashcardModal'
import { formatFlashcardDate } from '../services/flashcardTime'
import './FlashcardPage.css'

export default function FlashcardPage() {
  const accountId = useAuthStore(state => state.user?.id)
  const [searchParams] = useSearchParams()
  const deckId = searchParams.get('deck_id') || ''
  // React's native key resets all private local state and runs old effect
  // cleanup on an account/deck change; same-account token updates keep input.
  return <FlashcardAccountPage key={JSON.stringify([accountId, deckId])} />
}

function FlashcardAccountPage() {
  const accountId = useAuthStore(state => state.user?.id)
  const accountRequests = useRef<AbortController | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    accountRequests.current = controller
    return () => { controller.abort(); if (accountRequests.current === controller) accountRequests.current = null }
  }, [accountId])
  const accountCurrent = () => useAuthStore.getState().isAuthenticated && useAuthStore.getState().user?.id === accountId
    && Boolean(accountRequests.current && !accountRequests.current.signal.aborted)
  const requestConfig = () => ({ signal: accountRequests.current?.signal })
  const [searchParams] = useSearchParams()
  const deckId = searchParams.get('deck_id') || undefined
  const { selectedDoc } = useAppStore()
  const { dueCards, currentIndex, isFlipped, reviewLimit, setDueCards, completeReview, flip, next, prev, setReviewLimit } = useFlashcardStore()
  const { message } = App.useApp()
  const { token } = theme.useToken()
  const [loading, setLoading] = useState(true)
  const [reviewedCount, setReviewedCount] = useState(0)
  const [dueError, setDueError] = useState<unknown>(null)
  const [decksLoading, setDecksLoading] = useState(false)
  const [decksError, setDecksError] = useState<unknown>(null)
  const [generating, setGenerating] = useState(false)
  const exportingRef = useRef(false)
  const exportRequest = useRef<AbortController | null>(null)
  const [exporting, setExporting] = useState<string | null>(null)
  const [exportRetry, setExportRetry] = useState(0)
  const [exportFailure, setExportFailure] = useState<{ deckId: string; error: unknown } | null>(null)
  const reviewingRef = useRef(false)
  const [reviewing, setReviewing] = useState(false)
  const [decks, setDecks] = useState<any[]>([])
  const [genConfig, setGenConfig] = useState({ card_type: 'qa', count: 20, deck_name: '默认牌组' })
  const [genModalVisible, setGenModalVisible] = useState(false)
  const [manualDeck, setManualDeck] = useState<{ id: string; name: string } | null>(null)
  const [createDeckVisible, setCreateDeckVisible] = useState(false)
  const [editCardId, setEditCardId] = useState<string | null>(null)
  const [genInput, setGenInput] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<any[]>([])
  const [searching, setSearching] = useState(false)

  const loadDueCards = async (limit = reviewLimit) => {
    if (reviewingRef.current || !accountCurrent()) return
    setLoading(true)
    setDueError(null)
    try {
      const result = await getDueCards(limit, deckId, requestConfig())
      if (!accountCurrent()) return
      setDueCards(result.cards || [])
      setReviewedCount(0)
    } catch (e: any) { if (!accountCurrent()) return;
      setDueError(e)
    } finally {
      if (accountCurrent()) setLoading(false)
    }
  }

  const loadDecks = async () => {
    if (!accountCurrent()) return
    setDecksLoading(true); setDecksError(null)
    try {
      const result = await listDecks(undefined, undefined, requestConfig())
      if (accountCurrent()) setDecks(result)
    } catch (error) { if (accountCurrent()) setDecksError(error) }
    finally { if (accountCurrent()) setDecksLoading(false) }
  }

  useEffect(() => { setDueCards([]); loadDueCards(); loadDecks() }, [deckId, accountId])

  // ── Keyboard shortcuts ──────────────────────────────────────

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (genModalVisible || manualDeck || createDeckVisible || editCardId || reviewingRef.current || loading || dueError || e.defaultPrevented
        || e.ctrlKey || e.metaKey || e.altKey || e.repeat) return
      if (e.target instanceof HTMLElement && e.target.closest('input,textarea,select,button,a,[contenteditable="true"],[role="textbox"]')) return
      if (window.getSelection()?.isCollapsed === false) return

      const card = dueCards[currentIndex]
      if (!card) return

      switch (e.key) {
        case ' ':
          if (card.card_type === 'cloze') return
          e.preventDefault()
          flip()
          break
        case 'ArrowRight':
          e.preventDefault()
          if (currentIndex < dueCards.length - 1) next()
          break
        case 'ArrowLeft':
          e.preventDefault()
          prev()
          break
        case '1':
          if (isFlipped || card.card_type === 'cloze') { e.preventDefault(); handleReview(1) }
          break
        case '2':
          if (isFlipped || card.card_type === 'cloze') { e.preventDefault(); handleReview(2) }
          break
        case '3':
          if (isFlipped || card.card_type === 'cloze') { e.preventDefault(); handleReview(3) }
          break
        case '4':
          if (isFlipped || card.card_type === 'cloze') { e.preventDefault(); handleReview(4) }
          break
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [dueCards, currentIndex, isFlipped, genModalVisible, manualDeck, createDeckVisible, editCardId, loading, dueError])

  const handleExportAnki = async (deckId: string) => {
    if (exportingRef.current || !accountCurrent()) return
    const controller = new AbortController()
    exportRequest.current = controller
    const signal = AbortSignal.any([controller.signal, accountRequests.current!.signal])
    exportingRef.current = true; setExporting(deckId); setExportFailure(null); setExportRetry(0)
    try {
      const response = await exportAnkiDeck(deckId, { signal, 'axios-retry': {
        onRetry: retryCount => { if (accountCurrent() && !signal.aborted) setExportRetry(retryCount) },
      } })
      if (!accountCurrent() || signal.aborted) return
      const url = URL.createObjectURL(response.data)
      const a = document.createElement('a')
      const name = decks.find(d => d.id === deckId)?.name || `deck_${deckId.slice(0, 8)}`
      try {
        a.href = url; a.download = `${name.replace(/[\\/:*?"<>|\x00-\x1f]/g, '_')}.apkg`; a.click()
      } finally { URL.revokeObjectURL(url) }
      message.success('Anki 导出成功')
    } catch (error) { if (accountCurrent() && !signal.aborted) setExportFailure({ deckId, error }) }
    finally {
      if (exportRequest.current === controller) exportRequest.current = null
      exportingRef.current = false
      if (accountCurrent()) { setExporting(null); setExportRetry(0) }
    }
  }

  const handleReview = async (rating: number) => {
    const card = dueCards[currentIndex]
    if (!card || !accountCurrent() || reviewingRef.current || editCardId) return
    reviewingRef.current = true; setReviewing(true)
    try {
      const result = await reviewCard(card.id, rating, requestConfig())
      if (!accountCurrent()) return
      const ratingLabel = { 1: '完全忘记', 2: '困难想起', 3: '正常回忆', 4: '轻松想起' }[rating]
      const nextDate = formatFlashcardDate(result.next_review_at)
      message.success(`${ratingLabel} — 下次复习: ${nextDate === '—' ? '待定' : nextDate}`)
      completeReview(card.id)
      setReviewedCount(count => count + 1)
    } catch (e: any) { if (!accountCurrent()) return;
      message.error('提交失败')
    } finally { reviewingRef.current = false; if (accountCurrent()) setReviewing(false) }
  }

  const handleGenerate = async () => {
    if (!genInput.trim()) { message.warning('请输入知识点文本'); return }
    setGenerating(true)
    try {
      const result = await generateCards({
        knowledge_text: genInput,
        card_type: genConfig.card_type,
        count: genConfig.count,
        deck_name: genConfig.deck_name,
      }, requestConfig())
      if (!accountCurrent()) return
      message.success(`已生成 ${result.generated_count} 张卡片`)
      setGenModalVisible(false)
      await Promise.all([loadDueCards(), loadDecks()])
    } catch (e: any) { if (!accountCurrent()) return;
      message.error('生成失败')
    } finally {
      if (accountCurrent()) setGenerating(false)
    }
  }

  const handleSearch = async () => {
    if (!searchQuery.trim()) return
    setSearching(true)
    try {
      const result = await searchCards(searchQuery, 10, undefined, requestConfig())
      if (!accountCurrent()) return
      setSearchResults(result.results || [])
    } catch { if (!accountCurrent()) return; message.error('搜索失败') }
    finally { setSearching(false) }
  }

  const card = dueCards[currentIndex]
  const ratingLabels = [
    { value: 1, label: '完全忘记', color: '#ff4d4f', key: '1' },
    { value: 2, label: '困难想起', color: '#faad14', key: '2' },
    { value: 3, label: '正常回忆', color: '#52c41a', key: '3' },
    { value: 4, label: '轻松想起', color: '#1890ff', key: '4' },
  ]

  // ── Render card content based on type ────────────────────────

  const renderCardContent = () => {
    if (!card) return null

    // Cloze: interactive fill-in-the-blanks
    if (card.card_type === 'cloze') {
      return <ClozeRenderer key={JSON.stringify([card.id, card.front, card.back, card.hints])} front={card.front} back={card.back} hints={card.hints} />
    }

    // Compare: table rendering
    if (card.card_type === 'compare' && isFlipped) {
      return <CompareTable back={card.back} />
    }

    // Default QA and fallback
    return (
      <div>
        <div style={{ color: '#999', fontSize: 12, marginBottom: 8 }}>
          {isFlipped ? '背面 (答案)' : '正面 (问题)'}
        </div>
        {isFlipped ? <div className="flashcard-answer"><LearningContent text={card.back} /></div>
          : <div style={{ fontWeight: 600, lineHeight: 1.8 }}><FlashcardFront text={card.front} /></div>}
        {card.hints && !isFlipped && (
          <div style={{ marginTop: 16, color: '#aaa', fontSize: 13 }}>
            提示: <FlashcardFront text={card.hints} />
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="flashcard-page">
      <Card className="flashcard-review-panel" title="记忆闪卡" extra={
        <Space wrap>
          <Input.Search
            placeholder="搜索卡片..."
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            onSearch={handleSearch}
            loading={searching}
            style={{ width: 200 }}
            enterButton={<SearchOutlined />}
          />
          <Select
            value={reviewLimit}
            disabled={reviewing || loading}
            onChange={(v) => {
              if (reviewingRef.current) return
              setReviewLimit(v)
              loadDueCards(v)
            }}
            style={{ width: 100 }}
            options={[10, 20, 30, 50, 100].map(n => ({ value: n, label: `每次${n}张` }))}
          />
          <Button icon={<RobotOutlined />} type="primary" disabled={reviewing} onClick={() => setGenModalVisible(true)}>
            AI 生成卡片
          </Button>
          <Button disabled={reviewing} onClick={() => setCreateDeckVisible(true)}>创建牌组</Button>
          <Button disabled={reviewing || loading || decksLoading || Boolean(decksError) || decks.length === 0}
            onClick={() => setManualDeck(decks.find(d => d.id === deckId) || decks[0])}>手动添加</Button>
          <Dropdown disabled={Boolean(exporting) || decksLoading || Boolean(decksError) || decks.length === 0} menu={{ items: decks.map(d => ({ key: d.id, label: `导出 ${d.name} (${d.card_count}张)` })), onClick: ({ key }) => handleExportAnki(key) }}>
            <Button icon={<DownloadOutlined />} loading={Boolean(exporting)} disabled={Boolean(exporting) || decksLoading || Boolean(decksError) || decks.length === 0}>导出Anki</Button>
          </Dropdown>
          <Button icon={<ReloadOutlined />} disabled={reviewing || loading} onClick={() => loadDueCards()}>刷新</Button>
        </Space>
      }>
        {exporting && <Space style={{ marginBottom: 12 }}>
          <span role="status" aria-live="polite">{exportRetry
            ? `云端暂忙，正在重试（${exportRetry}/2）`
            : '正在准备 Anki 文件…'}</span>
          <Button size="small" onClick={() => exportRequest.current?.abort()}>取消导出</Button>
        </Space>}
        {exportFailure && <LoadFailure inline title="Anki 导出失败" error={exportFailure.error}
          retryLabel="重新导出" pending={Boolean(exporting)} onRetry={() => handleExportAnki(exportFailure.deckId)} />}
        {Boolean(dueError) && <LoadFailure title="待复习卡片加载失败" error={dueError}
          retryLabel="重新加载卡片" pending={loading} onRetry={() => loadDueCards()} />}
        {/* Search Results */}
        {searchResults.length > 0 && (
          <div style={{ marginBottom: 16, padding: 12, background: '#f6f8fa', borderRadius: 8 }}>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>搜索结果 ({searchResults.length})</div>
            {searchResults.map(r => (
              <div key={r.id} style={{ padding: '8px 0', borderBottom: '1px solid #eee', cursor: 'pointer' }}
                onClick={() => setSearchResults([])}>
                <Tag>{r.card_type}</Tag>
                <strong>{r.front?.slice(0, 80)}</strong>
                <div style={{ color: '#666', fontSize: 13 }}>{r.back?.slice(0, 120)}</div>
                <Button size="small" disabled={reviewing || loading} onClick={event => {
                  event.stopPropagation(); if (!reviewingRef.current) setEditCardId(r.id)
                }}>编辑卡片</Button>
              </div>
            ))}
            <Button size="small" style={{ marginTop: 8 }} onClick={() => setSearchResults([])}>清除结果</Button>
          </div>
        )}

        {loading && <div style={{ textAlign: 'center', padding: 40 }}><Spin size="large" /></div>}

        {!loading && !dueError && dueCards.length === 0 && reviewedCount > 0 && (
          <Result status="success" title="本轮复习完成" subTitle={`已复习 ${reviewedCount} 张卡片`}
            extra={<Button type="primary" onClick={() => loadDueCards()}>检查待复习卡片</Button>} />
        )}

        {!loading && !dueError && dueCards.length === 0 && reviewedCount === 0 && (
          <div style={{ textAlign: 'center', padding: 60, color: '#999' }}>
            <IdcardOutlined style={{ fontSize: 64, marginBottom: 16 }} />
            <p>暂无待复习卡片</p>
            <p style={{ fontSize: 12 }}>可稍后检查到期卡片，或创建新的记忆卡片</p>
            <Button onClick={() => loadDueCards()}>检查待复习卡片</Button>
          </div>
        )}

        {!loading && !dueError && card && (
          <div style={{ maxWidth: 650, margin: '0 auto' }}>
            <div style={{ textAlign: 'right', marginBottom: 8 }}>
              <Button size="small" disabled={reviewing || !['qa', 'cloze', 'compare'].includes(card.card_type)}
                onClick={() => { if (!reviewingRef.current) setEditCardId(card.id) }}>编辑当前卡片</Button>
            </div>
            {/* Progress bar */}
            <div style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ fontSize: 13, color: '#999' }}>
                  第 {currentIndex + 1} / {dueCards.length} 张
                  <Tag style={{ marginLeft: 8 }}>{card.card_type}</Tag>
                </span>
                <span data-testid="flashcard-session-progress" style={{ fontSize: 13, color: token.colorTextSecondary }}>
                  本轮已复习 {reviewedCount} 张，待复习 {dueCards.length} 张
                </span>
              </div>
              <Progress
                percent={Math.round((reviewedCount / (reviewedCount + dueCards.length)) * 100)}
                showInfo={false}
                strokeColor="#4f46e5"
                trailColor="#e8eaed"
                size="small"
              />
            </div>

            {/* Card face */}
            <div
              className="flashcard-flip"
              style={{ minHeight: 200, cursor: card.card_type === 'cloze' && !isFlipped ? 'default' : 'pointer' }}
              onClick={event => {
                if (reviewingRef.current || window.getSelection()?.isCollapsed === false) return
                if (event.target instanceof Element && event.target.closest('a,button,input,textarea,select,pre,code,[contenteditable="true"]')) return
                if (card.card_type === 'qa' || card.card_type === 'compare') flip()
              }}
            >
              <div
                className="flashcard-face"
                style={{ background: token.colorBgContainer, color: token.colorText, borderColor: token.colorPrimary }}
              >
                {renderCardContent()}
              </div>
            </div>

            {/* Cloze doesn't need flip button */}
            {!isFlipped && card.card_type !== 'cloze' && (
              <div style={{ textAlign: 'center', marginTop: 12 }}>
                <Tag color="blue">点击卡片翻转 | 空格键翻转</Tag>
              </div>
            )}

            {/* Rating buttons (for QA/Compare when flipped, for Cloze always) */}
            {(isFlipped || card.card_type === 'cloze') && (
              <div className="flashcard-ratings">
                {ratingLabels.map(r => (
                  <Button key={r.value} block disabled={reviewing} onClick={() => handleReview(r.value)}
                    style={{ borderColor: r.color, color: r.color }}>
                    {r.label} ({r.key})
                  </Button>
                ))}
              </div>
            )}

            {/* Navigation */}
            <div className="flashcard-navigation">
              <Button
                icon={<RightOutlined style={{ transform: 'rotate(180deg)' }} />}
                onClick={prev}
                disabled={reviewing || currentIndex <= 0}
                size="small"
              >
                上一张
              </Button>
              <span style={{ fontSize: 12, color: '#999', lineHeight: '32px' }}>
                快捷键: 空格翻转 | 1-4 评分 | ← → 导航
              </span>
              <Button
                icon={<RightOutlined />}
                onClick={next}
                disabled={reviewing || currentIndex >= dueCards.length - 1}
                size="small"
              >
                下一张 (跳过)
              </Button>
            </div>
          </div>
        )}
      </Card>

      <Card title="牌组列表" style={{ marginTop: 16 }}>
        {decksError ? <LoadFailure title="牌组列表加载失败" error={decksError} retryLabel="重新加载牌组"
          pending={decksLoading} onRetry={loadDecks} /> : <Table
          loading={decksLoading}
          dataSource={decks}
          locale={{ emptyText: <div><p>还没有牌组，可以先手动创建。</p><Button type="primary" disabled={reviewing || decksLoading} onClick={() => setCreateDeckVisible(true)}>创建我的第一个牌组</Button></div> }}
          rowKey="id"
          scroll={{ x: 600 }}
          columns={[
            { title: '名称', dataIndex: 'name', ellipsis: true },
            { title: '卡片数', dataIndex: 'card_count', width: 100 },
            { title: '创建时间', dataIndex: 'created_at', width: 140, render: formatFlashcardDate },
            { title: '操作', width: 280, render: (_: any, record: any) => (
              <Space>
                <Button size="small" icon={<RobotOutlined />}
                  onClick={async () => {
                    try {
                      const summary = await generateDeckSummary(record.id, undefined, requestConfig())
                      if (!accountCurrent()) return
                      Modal.info({
                        title: `牌组摘要: ${summary.deck_name}`,
                        content: (
                          <div>
                            <p>{summary.summary}</p>
                            <p>核心主题: {(summary.core_topics || []).join(', ')}</p>
                            <p>难度: {summary.difficulty_level} | 预计学习时间: {summary.estimated_study_time_minutes} 分钟</p>
                            <p style={{ color: '#4f46e5' }}>{summary.learning_tips}</p>
                          </div>
                        ),
                        width: 500,
                      })
                    } catch { if (!accountCurrent()) return; message.error('生成摘要失败') }
                  }}
                >摘要</Button>
                <Button size="small" icon={<DownloadOutlined />} disabled={Boolean(exporting)} loading={exporting === record.id} onClick={() => handleExportAnki(record.id)}>导出Anki</Button>
                <Button size="small" disabled={reviewing || loading} onClick={() => setManualDeck(record)}>添加卡片</Button>
              </Space>
            )},
          ]}
          pagination={{ pageSize: 10, showSizeChanger: true, showTotal: (total) => `共 ${total} 个牌组` }}
          size="middle"
        />}
      </Card>

      {editCardId && <EditFlashcardModal key={editCardId} cardId={editCardId} onClose={() => setEditCardId(null)} onSaved={saved => {
        if (!accountCurrent()) return
        useFlashcardStore.setState(state => ({
          dueCards: state.dueCards.map(item => item.id === saved.id ? { ...item, ...saved, tags: saved.tags || [] } : item),
          isFlipped: state.dueCards[state.currentIndex]?.id === saved.id ? false : state.isFlipped,
        }))
        setSearchResults(results => results.map(item => item.id === saved.id ? { ...item, ...saved } : item))
        setEditCardId(null); message.success('卡片已更新')
      }} />}
      {createDeckVisible && <CreateFlashcardDeckModal onClose={() => setCreateDeckVisible(false)} onCreated={deck => {
        if (!accountCurrent()) return
        setCreateDeckVisible(false); message.success('牌组已创建'); setManualDeck(deck)
        void loadDecks()
      }} />}
      {manualDeck && <ManualFlashcardModal deck={manualDeck} onClose={() => setManualDeck(null)} onSaved={() => {
        if (!accountCurrent()) return
        setManualDeck(null); message.success('已添加到牌组')
        void Promise.all([loadDueCards(), loadDecks()])
      }} />}

      <Modal title="AI 生成闪卡" open={genModalVisible} onCancel={() => setGenModalVisible(false)}
        onOk={handleGenerate} confirmLoading={generating} width={600}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <label>知识点文本:</label>
          <Input.TextArea rows={5} value={genInput} onChange={e => setGenInput(e.target.value)}
            placeholder="粘贴知识点内容，AI 将据此生成问答/填空/对比卡片..." />
          <Space>
            <Select value={genConfig.card_type} onChange={v => setGenConfig(g => ({ ...g, card_type: v }))}
              options={[
                { value: 'qa', label: '问答卡' },
                { value: 'cloze', label: '填空卡' },
                { value: 'compare', label: '对比辨析卡' },
              ]} />
            <Select value={genConfig.count} onChange={v => setGenConfig(g => ({ ...g, count: v }))}
              options={[10, 20, 30, 50].map(n => ({ value: n, label: `${n}张` }))} />
          </Space>
        </Space>
      </Modal>
    </div>
  )
}
