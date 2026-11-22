import axios, { type AxiosRequestConfig } from 'axios'
import axiosRetry from 'axios-retry'
import { getAuthToken, setAuthToken, authHeaderValue } from './authToken'

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 120000,
})

// Attach the current bearer token to every request. The token is held in
// ./authToken so that modules which merely *store* a token (the auth store) do
// not import this file — importing it pulls axios into the app shell's initial
// critical path. See KnowAll_Studio/docs/optimization-report.md §11.
api.interceptors.request.use((config) => {
  const token = getAuthToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// Keep the existing two retries/1s then 2s delays, using upstream cancellation,
// Retry-After and total timeout handling. Writes never repeat an uncertain result.
axiosRetry(api, {
  retries: 2,
  retryDelay: axiosRetry.linearDelay(1000),
  shouldResetTimeout: false,
  retryCondition: error => !axios.isCancel(error) && !error.config?.signal?.aborted
    && ['get', 'head', 'options'].includes(error.config?.method?.toLowerCase() || '')
    && axiosRetry.isNetworkOrIdempotentRequestError(error),
})

// 401 interceptor: clear token and redirect to login
api.interceptors.response.use(
  r => r,
  error => {
    if (error.response?.status === 401) {
      const url = error.config?.url || ''
      // Don't trigger on login/register requests themselves
      if (!url.includes('/auth/login') && !url.includes('/auth/register')) {
        setAuthToken(null)
        localStorage.removeItem('knowall_user')
        window.dispatchEvent(new Event('auth:logout'))
      }
    }
    return Promise.reject(error)
  },
)

// Documents
export const uploadDocument = (file: File, config?: AxiosRequestConfig) => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/documents/upload', form, config).then(r => r.data)
}
export const listDocuments = (limit?: number, offset?: number, config?: AxiosRequestConfig) =>
  api.get('/documents/', { ...config, params: { limit: limit || 50, offset: offset || 0 } }).then(r => r.data)
export const getDocumentChunks = (docId: string) => api.get(`/documents/${docId}/chunks`).then(r => r.data)
export const getDocumentRaw = (docId: string) => `/api/v1/documents/${docId}/raw`
export const getDocumentSlides = (docId: string) => api.get(`/documents/${docId}/slides`).then(r => r.data)
export const getNativeOutline = (docId: string) => api.get(`/documents/${docId}/native-outline`).then(r => r.data)
export const analyzeDocumentImages = (docId: string, params?: object) =>
  api.post(`/documents/${docId}/analyze-images`, params || {}).then(r => r.data)
export const deleteDocument = (docId: string) => api.delete(`/documents/${docId}`).then(r => r.data)
export const getDocumentDetail = (docId: string) => api.get(`/documents/${docId}`).then(r => r.data)
export const updateDocumentOrganization = (docId: string, params: { subject: string; material_kind: 'knowledge' | 'question_bank' | 'mixed' }, config?: AxiosRequestConfig) =>
  api.patch(`/documents/${encodeURIComponent(docId)}/organization`, params, config).then(r => r.data)
export const indexDocument = (docId: string, config?: AxiosRequestConfig) => api.post(`/search/index?doc_id=${docId}`, undefined, config).then(r => r.data)

export const importDocumentUrl = (params: { url: string }, config?: AxiosRequestConfig) =>
  api.post('/documents/import-url', params, config).then(r => r.data)

// Knowledge Tree
async function configuredModel(params: object, config?: AxiosRequestConfig) {
  if ('model' in params && params.model) return params
  const saved = await api.get('/ai/config', config).then(r => r.data)
  return saved.default_model ? { ...params, model: saved.default_model } : params
}
export const generateTree = async (params: object) => api.post('/knowledge/tree/generate', await configuredModel(params)).then(r => r.data)
export const getTree = (treeId: string) => api.get(`/knowledge/tree/${treeId}`).then(r => r.data)
export const updateTree = (treeId: string, data: object) => api.put(`/knowledge/tree/${treeId}`, data).then(r => r.data)
export const deleteTree = (treeId: string) => api.delete(`/knowledge/tree/${treeId}`).then(r => r.data)
export const listTrees = (limit?: number, offset?: number) =>
  api.get('/knowledge/trees', { params: { limit: limit || 50, offset: offset || 0 } }).then(r => r.data)
export const generateOutline = async (params: object) => api.post('/knowledge/outline/generate', await configuredModel(params)).then(r => r.data)

// Quiz
export const generateQuestions = async (params: object, config?: AxiosRequestConfig) => api.post('/quiz/generate', await configuredModel(params, config), config).then(r => r.data)
export const saveToBank = (params: object, config?: AxiosRequestConfig) => api.post('/quiz/bank/save', params, config).then(r => r.data)
export const deleteQuestion = (questionId: string) => api.delete(`/quiz/bank/${questionId}`).then(r => r.data)
export const deleteQuestionsBatch = (ids: string[], config?: AxiosRequestConfig) => api.post('/quiz/bank/delete-batch', ids, config).then(r => r.data)
export const createExam = (params: object, config?: AxiosRequestConfig) => api.post('/quiz/exam/create', params, config).then(r => r.data)
export const submitExam = (params: object, config?: AxiosRequestConfig) => api.post('/quiz/exam/submit', params, config).then(r => r.data)
export const getErrorQuestions = (config?: AxiosRequestConfig) => api.get('/quiz/errors', config).then(r => r.data)
export const generateVariants = (errorId: string, count: number, config?: AxiosRequestConfig) => api.post(`/quiz/errors/${errorId}/variants`, null, { ...config, params: { count } }).then(r => r.data)
export const listQuestions = (params?: object, config?: AxiosRequestConfig) =>
  api.get('/quiz/questions', { ...config, params: { limit: 200, offset: 0, ...params } }).then(r => r.data)

// Source-versioned learning workbench. All requests use the authenticated client.
export const getMaterialState = (documentId: string, kind: 'note' | 'text_progress' | 'pdf_progress' | 'image_progress') =>
  api.get(`/learning/documents/${encodeURIComponent(documentId)}/state/${kind}`, { timeout: 5000, 'axios-retry': { retries: 0 } }).then(r => r.data)
export const putMaterialState = (documentId: string, kind: 'note' | 'text_progress' | 'pdf_progress' | 'image_progress', body: object) =>
  api.put(`/learning/documents/${encodeURIComponent(documentId)}/state/${kind}`, body, { timeout: 5000 }).then(r => r.data)
export const getLearningPreview = (documentId: string, questionCount = 10, cardCount = 10) =>
  api.get(`/learning/documents/${documentId}/preview`, { params: { question_count: questionCount, card_count: cardCount } }).then(r => r.data)
export const getLearningPack = (documentId: string) =>
  api.get(`/learning/documents/${documentId}/pack`).then(r => r.data)
export const generateLearningPack = (params: object) => api.post('/learning/generate', params, { timeout: 600000 }).then(r => r.data)
export const startLearningSession = (packId: string, mode = 'smart', limit = 20) =>
  api.post(`/learning/packs/${packId}/session`, { mode, limit }).then(r => r.data)
export const submitLearningAnswer = (packId: string, questionId: string, answer: string, timeMs: number) =>
  api.post(`/learning/packs/${packId}/answers`, { question_id: questionId, answer, time_spent_ms: timeMs }).then(r => r.data)
export const reviewLearningCard = (packId: string, cardId: string, rating: number) =>
  api.post(`/learning/packs/${packId}/review`, { card_id: cardId, rating }).then(r => r.data)
export const repairLearningQuestion = (packId: string, questionId: string, params: object) =>
  api.patch(`/learning/packs/${packId}/questions/${questionId}`, params).then(r => r.data)
export const exportLearningPack = (packId: string, format: 'json' | 'markdown' | 'csv' = 'json'): Promise<string> =>
  api.get(`/learning/packs/${packId}/export`, { params: { format }, responseType: 'text' }).then(r => r.data)

// Flashcards
export interface ManualFlashcardInput {
  deck_id: string; card_type: 'qa' | 'cloze' | 'compare'; front: string; back: string; hints: string; tags: string[]
}
export const createFlashcard = (params: ManualFlashcardInput, config?: AxiosRequestConfig) =>
  api.post('/flashcards/cards', params, config).then(r => r.data)
export const generateCards = async (params: object, config?: AxiosRequestConfig) => api.post('/flashcards/generate', await configuredModel(params, config), config).then(r => r.data)
export const reviewCard = (cardId: string, rating: number, config?: AxiosRequestConfig) => api.post('/flashcards/review', { card_id: cardId, rating }, config).then(r => r.data)
export const getDueCards = (limit?: number, deck_id?: string, config?: AxiosRequestConfig) => api.get('/flashcards/due', { ...config, params: { limit, deck_id } }).then(r => r.data)
export const listDecks = (limit?: number, offset?: number, config?: AxiosRequestConfig) =>
  api.get('/flashcards/decks', { ...config, params: { limit: limit || 50, offset: offset || 0 } }).then(r => r.data)
export const exportAnkiDeck = (deckId: string, config?: AxiosRequestConfig) =>
  api.get<Blob>(`/flashcards/export/anki/${encodeURIComponent(deckId)}`, { ...config, responseType: 'blob' })
export const searchCards = (q: string, top_k?: number, tags?: string, config?: AxiosRequestConfig) =>
  api.get('/flashcards/search', { ...config, params: { q, top_k: top_k || 10, tags: tags || '' } }).then(r => r.data)
export const getRelatedCards = (cardId: string, top_k?: number) =>
  api.get(`/flashcards/related/${cardId}`, { params: { top_k: top_k || 5 } }).then(r => r.data)
export const generateDeckSummary = (deckId: string, model?: string, config?: AxiosRequestConfig) =>
  api.post(`/flashcards/deck/${deckId}/summary`, { model: model || 'deepseek-chat' }, config).then(r => r.data)

// Memory Feedback
export const scanFeedback = (params?: object) => api.post('/memory/feedback/scan', params || {}).then(r => r.data)
export const getReviewQueue = (limit?: number) =>
  api.get('/memory/review-queue', { params: { limit: limit || 20 } }).then(r => r.data)
export const completeReviewItem = (queueId: string) =>
  api.post('/memory/review-queue/complete', { queue_id: queueId }).then(r => r.data)
export const getMemoryStats = (config?: AxiosRequestConfig) => api.get('/memory/stats', config).then(r => r.data)
export const recordFlashcardResult = (cardId: string, isCorrect: boolean) =>
  api.post(`/memory/flashcards/${cardId}/record`, null, { params: { is_correct: isCorrect } }).then(r => r.data)
export const detectDecay = (lookbackReviews?: number, decayThreshold?: number) =>
  api.post('/memory/feedback/detect-decay', null, {
    params: { lookback_reviews: lookbackReviews || 5, decay_threshold: decayThreshold || 2 },
  }).then(r => r.data)
export const getRelatedFlashcards = (cardId: string, limit?: number) =>
  api.get(`/memory/flashcards/${cardId}/related`, { params: { limit: limit || 5 } }).then(r => r.data)

// Chat
export const chatWithAssistant = (params: object) => api.post('/chat/assistant', params).then(r => r.data)
export const listConversations = (config?: AxiosRequestConfig) => api.get('/chat/conversations', config).then(r => r.data)
export const getConversation = (convId: string, config?: AxiosRequestConfig) => api.get(`/chat/conversations/${convId}`, config).then(r => r.data)
export const deleteConversation = (convId: string) => api.delete(`/chat/conversations/${convId}`).then(r => r.data)
export const renameConversation = (convId: string, title: string) => api.put(`/chat/conversations/${convId}`, { title }).then(r => r.data)
export const listRoles = (config?: AxiosRequestConfig) => api.get('/chat/roles', config).then(r => r.data)

// Admin
export const addAPIKey = (params: object) => api.post('/admin/keys', params).then(r => r.data)
export const listAPIKeys = (config?: AxiosRequestConfig) => api.get('/admin/keys', config).then(r => r.data)
export const getModelConfiguration = (config?: AxiosRequestConfig) => api.get('/ai/config', config).then(r => ({ ...r.data, model: r.data.default_model }))
export const saveModelConfiguration = (params: object) => api.post('/admin/models', params).then(r => r.data)
export const listAvailableModels = (refresh = false, config?: AxiosRequestConfig) => api.get('/agent/models', { ...config, params: { refresh } }).then(r => r.data)
export const getQuotaStatus = (config?: AxiosRequestConfig) => api.get('/admin/quota/status', config).then(r => r.data)
export const getCacheStats = (config?: AxiosRequestConfig) => api.get('/admin/cache/stats', config).then(r => r.data)
export const getAdminLogs = (limit = 50, config?: AxiosRequestConfig) => api.get('/admin/logs', { ...config, params: { ...config?.params, limit } }).then(r => r.data)
export const clearAdminCache = () => api.delete('/admin/cache/clear').then(r => r.data)

// Pipeline
export const runPipeline = (params: object) => api.post('/pipeline/run', params).then(r => r.data)
export const runPipelineStream = (params: object) =>
  fetch('/api/v1/pipeline/run/stream', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params) })
export const cancelPipeline = (documentId: string) => api.post('/pipeline/cancel', null, { params: { document_id: documentId } }).then(r => r.data)

// Game
export const generateGameLevels = (params: object) => api.post('/generate/game-levels', params).then(r => r.data)
export const saveGameProgress = (params: object) => api.post('/generate/game-progress', params).then(r => r.data)
export const getGameProgress = (gameType?: string) =>
  api.get('/generate/game-progress', { params: { game_type: gameType } }).then(r => r.data)

// Game Quiz (new)
export const getGameQuizQuestions = (difficulty: string, count = 5, source = 'local') =>
  api.get('/game/quiz', { params: { difficulty, count, source } }).then(r => r.data)
export const getLocalQuizInfo = () =>
  api.get('/game/quiz/local').then(r => r.data)

// URL Import
export const importURL = (url: string) => api.post('/documents/import-url', { url }).then(r => r.data)

// Knowledge Merge
export const mergeTrees = (treeIds: string[], name: string) =>
  api.post('/knowledge/tree/merge', { tree_ids: treeIds, merged_name: name }).then(r => r.data)

// Knowledge Edges
export const createEdge = (params: object) => api.post('/knowledge/edges', params).then(r => r.data)
export const listEdges = (treeId: string) => api.get(`/knowledge/edges/${treeId}`).then(r => r.data)
export const deleteEdge = (edgeId: string) => api.delete(`/knowledge/edges/${edgeId}`).then(r => r.data)

// Search / RAG
export const searchDocuments = (query: string, topK?: number, config?: AxiosRequestConfig, mode?: 'keyword' | 'semantic' | 'ai') =>
  api.post('/search/', { query, top_k: topK || 5, ...(mode ? { mode } : {}) }, config).then(r => r.data)
export const ragQuery = (query: string, topK?: number, config?: AxiosRequestConfig) =>
  api.post('/search/rag', { query, top_k: topK || 3 }, config).then(r => r.data)

// Stats
export const getOverview = () => api.get('/stats/overview').then(r => r.data)
export const getDailyStats = (days?: number) => api.get('/stats/daily', { params: { days } }).then(r => r.data)
export const getTopicStats = () => api.get('/stats/topics').then(r => r.data)

// Backup
export const exportBackup = () => api.get('/backup/export').then(r => r.data)
export const exportFiles = () => api.get('/backup/files', { responseType: 'blob' }).then(r => r.data)
export const importBackup = (file: File) => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/backup/import', form).then(r => r.data)
}

// Study Plan
export const listPlans = (limit?: number, offset?: number) =>
  api.get('/study/plans', { params: { limit: limit || 50, offset: offset || 0 } }).then(r => r.data)
export const getPlan = (planId: string) => api.get(`/study/plans/${planId}`).then(r => r.data)
export const createPlan = (params: object) => api.post('/study/plans', params).then(r => r.data)
export const deletePlan = (planId: string) => api.delete(`/study/plans/${planId}`).then(r => r.data)
export const createGoal = (params: object) => api.post('/study/goals', params).then(r => r.data)
export const toggleGoal = (goalId: string) => api.put(`/study/goals/${goalId}/toggle`).then(r => r.data)
export const getDueReminders = () => api.get('/study/reminders/due').then(r => r.data)

// Share
export const createShareLink = (params: object) => api.post('/share/create', params).then(r => r.data)
export const listShareLinks = (limit?: number, offset?: number) =>
  api.get('/share/my-links', { params: { limit: limit || 50, offset: offset || 0 } }).then(r => r.data)
export const deleteShareLink = (shareId: string) => api.delete(`/share/${shareId}`).then(r => r.data)

// Auth
export const register = (params: { username: string; email: string; password: string; confirm_password?: string }) =>
  api.post('/auth/register', params).then(r => r.data)
export const login = (params: { username: string; password: string }) =>
  api.post('/auth/login', params).then(r => r.data)
export const getProfile = () => api.get('/auth/me').then(r => r.data)
export const checkUsername = (username: string) =>
  api.post('/auth/check-username', { username }).then(r => r.data)
export const getOAuthProviders = () => api.get('/oauth/providers').then(r => r.data)

// OAuth callback handler - process token from URL fragment after third-party login redirect
export function handleOAuthCallback(): boolean {
  const hash = window.location.hash
  if (!hash.includes('oauth_token=')) return false
  const params = new URLSearchParams(hash.slice(1))
  const token = params.get('oauth_token')
  if (token) {
    setAuthToken(token)
    const user = {
      id: params.get('user_id') || '',
      username: params.get('username') || '',
      email: '',
    }
    localStorage.setItem('knowall_user', JSON.stringify(user))
    window.location.hash = ''
    return true
  }
  return false
}

// User Profile & Settings
export const getUserProfile = () => api.get('/user/profile').then(r => r.data)
export const updateUserProfile = (params: { nickname?: string; phone?: string; avatar_url?: string; email?: string }) =>
  api.put('/user/profile', params).then(r => r.data)
export const changePassword = (params: { old_password: string; new_password: string }) =>
  api.put('/user/password', params).then(r => r.data)
export const getUserBinds = () => api.get('/user/binds').then(r => r.data)
export const bindAccount = (params: { provider: string; provider_name?: string; provider_uid?: string }) =>
  api.post('/user/bind', params).then(r => r.data)
export const unbindAccount = (provider: string) => api.delete(`/user/bind/${provider}`).then(r => r.data)

// SMS verification
export const sendSmsCode = (phone: string) => api.post('/sms/send-code', { phone }).then(r => r.data)
export const verifySmsCode = (phone: string, code: string) => api.post('/sms/verify-code', { phone, code }).then(r => r.data)
export const bindPhone = (phone: string, code: string) => api.post('/sms/bind-phone', { phone, code }).then(r => r.data)
export const unbindPhone = () => api.post('/sms/unbind-phone').then(r => r.data)
export const getPhoneStatus = () => api.get('/sms/phone-status').then(r => r.data)
export const getUserHistory = (page?: number, pageSize?: number, actionType?: string) =>
  api.get('/user/history', { params: { page: page || 1, page_size: pageSize || 20, action_type: actionType || '' } }).then(r => r.data)
export const createHistory = (params: { action_type?: string; action_label?: string; resource_type?: string; resource_id?: string; detail?: string }) =>
  api.post('/user/history', null, { params }).then(r => r.data)

// Notifications
export const getNotifications = (page?: number, pageSize?: number, isRead?: string, category?: string) =>
  api.get('/notifications', { params: { page: page || 1, page_size: pageSize || 20, is_read: isRead || '', category: category || '' } }).then(r => r.data)
export const markNotificationRead = (id: string) => api.put(`/notifications/${id}/read`).then(r => r.data)
export const markAllNotificationsRead = () => api.put('/notifications/read-all').then(r => r.data)
export const deleteNotification = (id: string) => api.delete(`/notifications/${id}`).then(r => r.data)
export const batchDeleteNotifications = (ids: string[]) =>
  api.delete('/notifications', { params: { ids: ids.join(',') } }).then(r => r.data)

// Reading Language (阅读语言) - Progressive Chinese-English Mixed Reading
export const getReadingArticles = (difficulty?: number) =>
  api.get('/reading/articles', { params: difficulty ? { difficulty } : {} }).then(r => r.data)
export const getReadingArticle = (id: string, config?: AxiosRequestConfig) =>
  api.get(`/reading/articles/${id}`, config).then(r => r.data)
export const convertReadingText = (text: string, level: number, knownWords?: {cn:string;en:string}[], seenWords?: {cn:string;en:string}[], skipCache = false, config?: AxiosRequestConfig) =>
  api.post('/reading/convert', { text, level, known_words: knownWords || [], seen_words: seenWords || [], skip_cache: skipCache }, config).then(r => r.data)
export const convertReadingStream = (params: { text: string; level?: number; known_words: {cn:string;en:string}[]; seen_words: {cn:string;en:string}[]; skip_cache?: boolean }, signal?: AbortSignal) =>
  fetch('/api/v1/reading/convert/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': authHeaderValue() },
    body: JSON.stringify(params),
    signal,
  })
export const uploadReadingArticle = (title: string, content: string) =>
  api.post('/reading/articles/upload', { title, content }).then(r => r.data)
export const uploadReadingArticleFile = (file: File) => {
  const form = new FormData()
  form.append('file', file)
  return api.post('/reading/articles/upload-file', form).then(r => r.data)
}
export const deleteReadingArticle = (id: string) =>
  api.delete(`/reading/articles/${id}`).then(r => r.data)

// ===== Knowledge Summary (NEW) =====
export const generateSummary = async (params: object) => api.post('/knowledge/summary/generate', await configuredModel(params)).then(r => r.data)
export const getSummary = (summaryId: string, config?: AxiosRequestConfig) => api.get(`/knowledge/summary/${summaryId}`, config).then(r => r.data)
export const getSummaryNodes = (summaryId: string, level?: number) =>
  api.get(`/knowledge/summary/${summaryId}/nodes`, { params: { level } }).then(r => r.data)
export const getSummaryMindmap = (summaryId: string, config?: AxiosRequestConfig) =>
  api.get(`/knowledge/summary/${summaryId}/mindmap`, config).then(r => r.data)
export const deleteSummary = (summaryId: string) =>
  api.delete(`/knowledge/summary/${summaryId}`).then(r => r.data)

// ===== Agent Orchestration (NEW) =====
export const orchestrateAgents = (params: object, config?: AxiosRequestConfig) => api.post('/agents/orchestrate', params, config).then(r => r.data)
export const listAgents = () => api.get('/agents/list').then(r => r.data)

// ===== Interactive Quiz (NEW) =====
// This legacy GET creates a session on the server, so it is a write for retry purposes.
export const startInteractiveQuiz = (params: object, config?: AxiosRequestConfig) =>
  api.get('/quiz/interactive/start', { ...config, params, 'axios-retry': { ...config?.['axios-retry'], retries: 0 } }).then(r => r.data)
export const submitInteractiveAnswer = (params: object, config?: AxiosRequestConfig) =>
  api.post('/quiz/interactive/submit', params, config).then(r => r.data)
export const getInteractiveSessionStats = (sessionId: string, config?: AxiosRequestConfig) =>
  api.get(`/quiz/interactive/session/${encodeURIComponent(sessionId)}/stats`, {
    ...config, 'axios-retry': { ...config?.['axios-retry'], retries: 0 },
  }).then(r => r.data)

// ===== Language Vocabulary (NEW) =====
export const generateVocabulary = (params: object) => api.post('/language/vocabulary/generate', params).then(r => r.data)
export const getVocabulary = (params?: object) => api.get('/language/vocabulary', { params }).then(r => r.data)
export const markVocabularyMastered = (vocabId: string, mastered: boolean) =>
  api.patch(`/language/vocabulary/${vocabId}`, { mastered }).then(r => r.data)

// ===== Coverage Report (NEW) =====
export const getCoverageReport = (summaryId: string, config?: AxiosRequestConfig) =>
  api.get(`/knowledge/coverage/${summaryId}`, config).then(r => r.data)
export const refreshCoverage = (params: object) =>
  api.post('/knowledge/coverage/refresh', params).then(r => r.data)

// ===== Answer Review & Mastery (NEW) =====
export const getMasteryAnalysis = () => api.get('/review/mastery').then(r => r.data)
export const getKpMasteryDetail = (kpId: string) => api.get(`/review/mastery/${kpId}`).then(r => r.data)
export const getAnswerHistory = (params?: { kp_id?: string; is_correct?: boolean; page?: number; page_size?: number }) =>
  api.get('/review/history', { params }).then(r => r.data)
export const getReviewStats = () => api.get('/review/stats').then(r => r.data)
export const generateReviewRecommendations = (model?: string) =>
  api.post('/review/recommend', { model: model || 'deepseek-chat' }).then(r => r.data)
export const getReviewKnowledgePoints = () => api.get('/review/knowledge-points').then(r => r.data)

// ===== Commercial (NEW) =====
export const getUserTier = () => api.get('/commercial/tier').then(r => r.data)
export const activateLicense = (licenseKey: string) =>
  api.post('/commercial/tier/activate-license', null, { params: { license_key: licenseKey } }).then(r => r.data)
export const createOrder = (tier: string, durationMonths: number = 1) =>
  api.post('/commercial/orders/create', { tier, duration_months: durationMonths }).then(r => r.data)
export const payOrder = (orderId: string) =>
  api.post(`/commercial/orders/${orderId}/pay`).then(r => r.data)
export const exportMyData = () =>
  api.get('/commercial/export/my-data', { responseType: 'blob' }).then(r => r.data)
export const deleteMyData = () =>
  api.delete('/commercial/delete-my-data').then(r => r.data)

// ===== Gamification (NEW) =====
export const getGamificationDashboard = () => api.get('/gamification/dashboard').then(r => r.data)
export const recordActivity = (activityType: string, count?: number, durationMinutes?: number) =>
  api.post('/gamification/record-activity', { activity_type: activityType, count: count || 1, duration_minutes: durationMinutes || 0 }).then(r => r.data)
export const startFocusSession = (durationMinutes: number, sessionType?: string) =>
  api.post('/gamification/focus/start', { duration_minutes: durationMinutes, session_type: sessionType || 'study' }).then(r => r.data)
export const getFocusHistory = (limit?: number) =>
  api.get('/gamification/focus/history', { params: { limit: limit || 30 } }).then(r => r.data)

// ===== Enhanced Study Plan (NEW) =====
export const generateEnhancedPlan = (params: object) =>
  api.post('/study/generate-plan-enhanced', params).then(r => r.data)
export const getEbbinghausNodes = (planId: string) =>
  api.get(`/study/plans/${planId}/ebbinghaus`).then(r => r.data)

// ==================== Deep Research ====================

export const startResearch = (data: { topic: string; max_subtopics?: number }) =>
  api.post('/research/start', data).then(r => r.data)

export const getResearchStatus = (taskId: string) =>
  api.get(`/research/${taskId}/status`).then(r => r.data)

export const listResearchTasks = () =>
  api.get('/research/tasks').then(r => r.data)

// ==================== Book Generation ====================

export const createBook = (data: { title: string; description?: string; source_doc_ids?: string[] }) =>
  api.post('/books/create', data).then(r => r.data)

export const listBooks = () =>
  api.get('/books').then(r => r.data)

export const getBook = (bookId: string) =>
  api.get(`/books/${bookId}`).then(r => r.data)

export const deleteBook = (bookId: string) =>
  api.delete(`/books/${bookId}`).then(r => r.data)

export const exportBook = (bookId: string) =>
  api.get(`/books/${bookId}/export`, { responseType: 'blob' }).then(r => r.data)

// ==================== Recitation (背诵) ====================

export const createPassage = (data: { title: string; content: string; source?: string; category?: string; difficulty?: number; tags?: string[] }, config?: AxiosRequestConfig) =>
  api.post('/recitation/passages', data, config).then(r => r.data)

export const listPassages = (params?: { category?: string; difficulty?: number; limit?: number; offset?: number }, config?: AxiosRequestConfig) =>
  api.get('/recitation/passages', { ...config, params }).then(r => r.data)

export const getPassage = (passageId: string) =>
  api.get(`/recitation/passages/${passageId}`).then(r => r.data)

export const deletePassage = (passageId: string, config?: AxiosRequestConfig) =>
  api.delete(`/recitation/passages/${passageId}`, config).then(r => r.data)

export const getHidePlan = (content: string, level: number, config?: AxiosRequestConfig) =>
  api.post('/recitation/hide-plan', { content, level }, config).then(r => r.data)

export const submitDictation = (data: { passage_id: string; user_input: string; time_spent_ms?: number }, config?: AxiosRequestConfig) =>
  api.post('/recitation/dictation/submit', data, config).then(r => r.data)

export const reviewRecitation = (recordId: string, rating: number, config?: AxiosRequestConfig) =>
  api.post('/recitation/review', { record_id: recordId, rating }, config).then(r => r.data)

export const getDueRecitations = (limit?: number, config?: AxiosRequestConfig) =>
  api.get('/recitation/due', { ...config, params: { limit } }).then(r => r.data)

export const getRecitationStats = (config?: AxiosRequestConfig) =>
  api.get('/recitation/stats', config).then(r => r.data)

// ==================== Live Session (实时对话考试) ====================

export const createLiveSession = (data: { title: string; knowledge_tree_id?: string }) =>
  api.post('/live/sessions', data).then(r => r.data)

export const getLiveSessionByCode = (code: string) =>
  api.get(`/live/sessions/${code}`).then(r => r.data)

export const joinLiveSession = (sessionId: string, data: { user_name?: string }) =>
  api.post(`/live/sessions/${sessionId}/join`, data).then(r => r.data)

export const pushLiveQuestions = (sessionId: string, data: { question_ids: string[]; mode?: string; target_participant_id?: string }) =>
  api.post(`/live/sessions/${sessionId}/push`, data).then(r => r.data)

export const spotlightLiveQuestion = (sessionId: string, data: { question_id?: string; target_participant_id?: string }) =>
  api.post(`/live/sessions/${sessionId}/spotlight`, data).then(r => r.data)

export const submitLiveAnswer = (sessionId: string, data: { live_question_id: string; user_answer: string; time_spent_ms?: number }) =>
  api.post(`/live/sessions/${sessionId}/answer`, data).then(r => r.data)

export const getLiveLeaderboard = (sessionId: string) =>
  api.get(`/live/sessions/${sessionId}/leaderboard`).then(r => r.data)

export const endLiveSession = (sessionId: string) =>
  api.post(`/live/sessions/${sessionId}/end`).then(r => r.data)

export const getLiveSessionQuestions = (sessionId: string) =>
  api.get(`/live/sessions/${sessionId}/questions`).then(r => r.data)

// ==================== Voice (语音) ====================

export const speechToText = (formData: FormData) =>
  api.post('/voice/stt', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  }).then(r => r.data)

export const textToSpeech = (data: { text: string; voice?: string; rate?: string; volume?: string }) =>
  api.post('/voice/tts', data, { responseType: 'blob', timeout: 30000 }).then(r => r.data)

export const listVoices = () =>
  api.get('/voice/voices').then(r => r.data)

// ==================== MCP Tools ====================

export const listMCPServers = (config?: AxiosRequestConfig) =>
  api.get('/mcp/servers', config).then(r => r.data)

export const addMCPServer = (data: { name: string; transport?: string; command?: string; args?: string[]; url?: string }) =>
  api.post('/mcp/servers', data).then(r => r.data)

export const removeMCPServer = (name: string) =>
  api.delete(`/mcp/servers/${name}`).then(r => r.data)

export const listBuiltinTools = (config?: AxiosRequestConfig) =>
  api.get('/mcp/tools/builtin', config).then(r => r.data)

export const listServerTools = (name: string) =>
  api.get(`/mcp/servers/${name}/tools`).then(r => r.data)

// ===== Glossary 名词解释库 =====
export const getGlossaryTerms = (params?: { subject?: string; chapter?: string; q?: string; page?: number; page_size?: number }) =>
  api.get('/glossary/terms', { params: { page: 1, page_size: 200, ...params } }).then(r => r.data)
export const getGlossaryTerm = (term: string, subject?: string) => api.get(`/glossary/terms/${encodeURIComponent(term)}`, { params: { subject } }).then(r => r.data)
export const getGlossaryTermVariants = (term: string) => api.get(`/glossary/terms/${encodeURIComponent(term)}/variants`).then(r => r.data)
export const suggestGlossaryTerms = (q: string) => api.get('/glossary/terms/suggest', { params: { q } }).then(r => r.data)
export const syncGlossary = (subject?: string) => api.post('/glossary/sync', { subject: subject || '综合' }).then(r => r.data)
export const getGlossaryStats = () => api.get('/glossary/stats').then(r => r.data)

// Attach token to all requests after login.
// Defined in ./authToken (dependency-free) and re-exported here for callers that
// already import from this module; the interceptor above reads the same value.
export { setAuthToken }

// Restore token on app load
const savedToken = localStorage.getItem('knowall_token')
if (savedToken) {
  setAuthToken(savedToken)
}

export default api
