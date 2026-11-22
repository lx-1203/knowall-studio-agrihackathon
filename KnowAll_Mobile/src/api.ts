// API client for KnowAll Studio Mobile
// Adapted for React Native: AsyncStorage, configurable base URL, EventEmitter

import axios, { type AxiosRequestConfig } from 'axios'
import { Platform } from 'react-native'
import { storage } from './services/storage'
import { appEvents } from './services/eventEmitter'
import { bytesDataUri } from './materialOriginal'
import { generationRequest, previewRequest, type GenerationSettings } from './materialGeneration'
import { learningPackPath, learningSessionRequest, learningAnswerRequest, learningCardReviewRequest, normalizeLearningPack, type MaterialPack } from './materialLearning'

// Android emulator uses 10.0.2.2 to reach host localhost; iOS simulator uses localhost
const DEFAULT_HOST = Platform.OS === 'android' ? '10.0.2.2' : 'localhost'
const API_BASE_URL = process.env.EXPO_PUBLIC_API_URL || `http://${DEFAULT_HOST}:8001/api/v1`
// WebSocket for realtime features (live sessions). Rest backend serves WS on :8001.
const WS_BASE_URL = process.env.EXPO_PUBLIC_WS_URL || `ws://${DEFAULT_HOST}:8001`

export { API_BASE_URL, WS_BASE_URL, DEFAULT_HOST }

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 120000,
})

// 无前缀路由（/books /research 等挂在 root，不带 /api/v1）
const rootApi = axios.create({
  baseURL: API_BASE_URL.replace(/\/api\/v1$/, ''),
  timeout: 120000,
})
rootApi.interceptors.response.use(undefined, async (error) => {
  const config = error.config
  if (!config || config._retryCount >= 2) return Promise.reject(error)
  const shouldRetry = !error.response || (error.response.status >= 500 && error.response.status < 600)
  if (!shouldRetry) return Promise.reject(error)
  config._retryCount = (config._retryCount || 0) + 1
  const delay = config._retryCount * 1000
  await new Promise(resolve => setTimeout(resolve, delay))
  return rootApi(config)
})

// Retry interceptor for transient network errors
api.interceptors.response.use(undefined, async (error) => {
  const config = error.config
  if (config?._retryDisabled || axios.isCancel(error) || config?.signal?.aborted) return Promise.reject(error)
  if (!config || config._retryCount >= 2) return Promise.reject(error)

  // Only retry on network errors or 5xx server errors
  const shouldRetry = !error.response || (error.response.status >= 500 && error.response.status < 600)
  if (!shouldRetry) return Promise.reject(error)

  config._retryCount = (config._retryCount || 0) + 1
  const delay = config._retryCount * 1000  // 1s, then 2s backoff
  await new Promise(resolve => setTimeout(resolve, delay))
  return api(config)
})

// 401 interceptor: clear token and redirect to login
api.interceptors.response.use(
  r => r,
  error => {
    // Provide friendly message for timeout errors
    if (error.code === 'ECONNABORTED' && error.message?.includes('timeout')) {
      error.friendlyMessage = '请求超时，请检查网络连接后重试'
    }
    if (error.response?.status === 401) {
      const url = error.config?.url || ''
      if (!url.includes('/auth/login') && !url.includes('/auth/register')) {
        setAuthToken(null)
        storage.removeItem('knowall_user')
        appEvents.emit('auth:logout')
      }
    }
    return Promise.reject(error)
  },
)

// 401 interceptor for rootApi (no-prefix routes share the same token)
rootApi.interceptors.response.use(
  r => r,
  error => {
    if (error.response?.status === 401) {
      setAuthToken(null)
      storage.removeItem('knowall_user')
      appEvents.emit('auth:logout')
    }
    return Promise.reject(error)
  },
)

// ===== Documents =====
export const uploadDocument = (fileUri: string, fileName: string, fileType: string, onProgress?: (pct: number) => void) => {
  const form = new FormData()
  form.append('file', { uri: fileUri, name: fileName, type: fileType } as any)
  return api.post('/documents/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (e) => {
      if (onProgress && e.total) {
        onProgress(Math.round((e.loaded / e.total) * 100) / 100)
      }
    },
  }).then(r => r.data)
}
export const listDocuments = (limit?: number, offset?: number) =>
  api.get('/documents/', { params: { limit: limit || 50, offset: offset || 0 } }).then(r => r.data)
export const getDocumentChunks = (docId: string) => api.get(`/documents/${docId}/chunks`).then(r => r.data)
export const getDocumentRaw = (docId: string) => `${API_BASE_URL}/documents/${docId}/raw`
export const getDocumentSlides = (docId: string) => api.get(`/documents/${docId}/slides`).then(r => r.data)
export const getNativeOutline = (docId: string) => api.get(`/documents/${docId}/native-outline`).then(r => r.data)
export const analyzeDocumentImages = (docId: string, params?: object) =>
  api.post(`/documents/${docId}/analyze-images`, params || {}).then(r => r.data)
export const deleteDocument = (docId: string) => api.delete(`/documents/${docId}`).then(r => r.data)
export const getDocumentDetail = (docId: string) => api.get(`/documents/${docId}`).then(r => r.data)
type OriginalPreviewInfo = { page_count: number; file_type: string; source_version?: string }
const originalVersions = new Map<string,OriginalPreviewInfo>()
const originalPageSources = new Map<string,OriginalPreviewInfo & {page:number}>()
const originalScope = (docId: string) => `${String(api.defaults.headers.common.Authorization || '')}:${docId}`
export const getOriginalPreview = (docId: string, signal?: AbortSignal, remember = true) => {
  const scope = originalScope(docId)
  return api.get<OriginalPreviewInfo>(`/documents/${encodeURIComponent(docId)}/preview`, { signal }).then(r => {
    if (remember && scope === originalScope(docId)) {
      if (originalVersions.size > 32) originalVersions.clear()
      if (r.data.source_version) originalVersions.set(scope,r.data); else originalVersions.delete(scope)
    }
    return r.data
  })
}
export const getReadOriginalSource = (docId:string,page:number) => originalPageSources.get(originalScope(docId)+':'+page)
export const getOriginalPage = (docId: string, page: number, signal?: AbortSignal) => {
  const scope=originalScope(docId),source=originalVersions.get(scope)
  return api.get<ArrayBuffer>(`/documents/${encodeURIComponent(docId)}/preview/${page}/image`, { signal, responseType: 'arraybuffer', params: {source_version:source?.source_version} })
    .then(r => {
      if(source?.source_version && !signal?.aborted && scope===originalScope(docId)) {
        if(originalPageSources.size>64)originalPageSources.clear()
        originalPageSources.set(scope+':'+page,{...source,page})
      }
      return bytesDataUri(r.data)
    })
}
export const updateDocumentOrganization = (docId: string, params: { subject: string; material_kind: 'knowledge' | 'question_bank' | 'mixed' }) =>
  api.patch(`/documents/${encodeURIComponent(docId)}/organization`, params).then(r => r.data)
export const indexDocument = (docId: string) => api.post(`/search/index?doc_id=${docId}`).then(r => r.data)

// Shared, authenticated source-based learning APIs; no global-bank fallback.
// A lost response must not automatically replay mutations that increment learning history.
const learningMutationConfig: AxiosRequestConfig & { _retryDisabled: boolean } = { _retryDisabled: true }
export const getMaterialState = (documentId: string, kind: 'note' | 'text_progress' | 'pdf_progress' | 'image_progress') =>
  api.get(`/learning/documents/${encodeURIComponent(documentId)}/state/${kind}`, { ...learningMutationConfig, timeout: 5000 }).then(r => r.data)
export const putMaterialState = (documentId: string, kind: 'note' | 'text_progress' | 'pdf_progress' | 'image_progress', body: object) =>
  api.put(`/learning/documents/${encodeURIComponent(documentId)}/state/${kind}`, body, { ...learningMutationConfig, timeout: 5000 }).then(r => r.data)
export const getMaterialGenerationPreview = (documentId: string, settings: GenerationSettings, signal?: AbortSignal) => {
  const request = previewRequest(documentId, settings)
  return api.get(request.path, { params: request.params, signal }).then(r => r.data)
}
export const generateMaterialLearningPack = (documentId: string, settings: GenerationSettings) => {
  const request = generationRequest(documentId, settings)
  return api.post(request.path, request.body, { ...learningMutationConfig, timeout: 600000 }).then(r => r.data)
}
export const getMaterialGenerationResult = (documentId: string) => api.get(learningPackPath(documentId)).then(r => r.data)
export const getMaterialLearningPack = (documentId: string) =>
  api.get(learningPackPath(documentId)).then(r => normalizeLearningPack(r.data))
export const startMaterialPractice = (pack: MaterialPack, mode: 'practice' | 'smart' = 'practice') => {
  const request = learningSessionRequest(pack, mode)
  return api.post(request.path, request.body).then(r => r.data)
}
export const reviewMaterialCard = (pack: MaterialPack, cardId: string, rating: number) => {
  const request = learningCardReviewRequest(pack, cardId, rating)
  return api.post(request.path, request.body, learningMutationConfig).then(r => r.data)
}
export const submitMaterialAnswer = (packId: string, questionId: string, answer: string, elapsed: number) => {
  const request = learningAnswerRequest(packId, questionId, answer, elapsed)
  return api.post(request.path, request.body, learningMutationConfig).then(r => r.data)
}

// ===== Knowledge Tree =====
export const generateTree = (params: object) => api.post('/knowledge/tree/generate', params).then(r => r.data)
export const getTree = (treeId: string) => api.get(`/knowledge/tree/${treeId}`).then(r => r.data)
export const updateTree = (treeId: string, data: object) => api.put(`/knowledge/tree/${treeId}`, data).then(r => r.data)
export const listTrees = (limit?: number, offset?: number) =>
  api.get('/knowledge/trees', { params: { limit: limit || 50, offset: offset || 0 } }).then(r => r.data)
export const generateOutline = (params: object) => api.post('/knowledge/outline/generate', params).then(r => r.data)

// ===== Quiz =====
export const generateQuestions = (params: object) => api.post('/quiz/generate', params).then(r => r.data)
export const saveToBank = (params: object) => api.post('/quiz/bank/save', params).then(r => r.data)
export const deleteQuestion = (questionId: string) => api.delete(`/quiz/bank/${questionId}`).then(r => r.data)
export const deleteQuestionsBatch = (ids: string[]) => api.post('/quiz/bank/delete-batch', ids).then(r => r.data)
export const createExam = (params: object) => api.post('/quiz/exam/create', params).then(r => r.data)
export const submitExam = (params: object) => api.post('/quiz/exam/submit', params).then(r => r.data)
export const getErrorQuestions = () => api.get('/quiz/errors').then(r => r.data)
export const generateVariants = (errorId: string, count: number) =>
  api.post(`/quiz/errors/${errorId}/variants`, null, { params: { count } }).then(r => r.data)
export const listQuestions = (params?: object) =>
  api.get('/quiz/questions', { params: { limit: 200, offset: 0, ...params } }).then(r => r.data)

// ===== Flashcards =====
export const generateCards = (params: object) => api.post('/flashcards/generate', params).then(r => r.data)
export const reviewCard = (cardId: string, rating: number) =>
  api.post('/flashcards/review', { card_id: cardId, rating }).then(r => r.data)
export const getDueCards = (limit?: number) => api.get('/flashcards/due', { params: { limit } }).then(r => r.data)
export const listDecks = (limit?: number, offset?: number) =>
  api.get('/flashcards/decks', { params: { limit: limit || 50, offset: offset || 0 } }).then(r => r.data)
export const searchCards = (q: string, top_k?: number, tags?: string) =>
  api.get('/flashcards/search', { params: { q, top_k: top_k || 10, tags: tags || '' } }).then(r => r.data)
export const getRelatedCards = (cardId: string, top_k?: number) =>
  api.get(`/flashcards/related/${cardId}`, { params: { top_k: top_k || 5 } }).then(r => r.data)
export const generateDeckSummary = (deckId: string, model?: string) =>
  api.post(`/flashcards/deck/${deckId}/summary`, { model: model || 'deepseek-chat' }).then(r => r.data)

// ===== Memory Feedback =====
export const scanFeedback = (params?: object) => api.post('/memory/feedback/scan', params || {}).then(r => r.data)
export const getReviewQueue = (limit?: number) =>
  api.get('/memory/review-queue', { params: { limit: limit || 20 } }).then(r => r.data)
export const completeReviewItem = (queueId: string) =>
  api.post('/memory/review-queue/complete', { queue_id: queueId }).then(r => r.data)
export const getMemoryStats = () => api.get('/memory/stats').then(r => r.data)
export const recordFlashcardResult = (cardId: string, isCorrect: boolean) =>
  api.post(`/memory/flashcards/${cardId}/record`, { is_correct: isCorrect }).then(r => r.data)
export const detectDecay = (lookbackReviews?: number, decayThreshold?: number) =>
  api.post('/memory/feedback/detect-decay', null, {
    params: { lookback_reviews: lookbackReviews || 5, decay_threshold: decayThreshold || 2 },
  }).then(r => r.data)
export const getRelatedFlashcards = (cardId: string, limit?: number) =>
  api.get(`/memory/flashcards/${cardId}/related`, { params: { limit: limit || 5 } }).then(r => r.data)

// ===== Chat =====
export const chatWithAssistant = (params: object) => api.post('/chat/assistant', params).then(r => r.data)
export const listConversations = () => api.get('/chat/conversations').then(r => r.data)
export const getConversation = (convId: string) => api.get(`/chat/conversations/${convId}`).then(r => r.data)
export const listRoles = () => api.get('/chat/roles').then(r => r.data)

// ===== Admin =====
export const addAPIKey = (params: object) => api.post('/admin/keys', params).then(r => r.data)
export const getQuotaStatus = () => api.get('/admin/quota/status').then(r => r.data)
export const getCacheStats = () => api.get('/admin/cache/stats').then(r => r.data)

// ===== Pipeline =====
export const runPipeline = (params: object) => api.post('/pipeline/run', params).then(r => r.data)
export const runPipelineStream = (params: object) =>
  fetch(`${API_BASE_URL}/pipeline/run/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  })

// ===== Game =====
export const generateGameLevels = (params: object) => api.post('/generate/game-levels', params).then(r => r.data)
export const saveGameProgress = (params: object) => api.post('/generate/game-progress', params).then(r => r.data)
export const getGameProgress = (gameType?: string) =>
  api.get('/generate/game-progress', { params: { game_type: gameType } }).then(r => r.data)
export const getGameQuizQuestions = (difficulty: string, count = 5, source = 'local') =>
  api.get('/game/quiz', { params: { difficulty, count, source } }).then(r => r.data)
export const getLocalQuizInfo = () => api.get('/game/quiz/local').then(r => r.data)

// ===== URL Import =====
export const importURL = (url: string) => api.post('/documents/import-url', { url }).then(r => r.data)

// ===== Knowledge Merge =====
export const mergeTrees = (treeIds: string[], name: string) =>
  api.post('/knowledge/tree/merge', { tree_ids: treeIds, merged_name: name }).then(r => r.data)

// ===== Knowledge Edges =====
export const createEdge = (params: object) => api.post('/knowledge/edges', params).then(r => r.data)
export const listEdges = (treeId: string) => api.get(`/knowledge/edges/${treeId}`).then(r => r.data)
export const deleteEdge = (edgeId: string) => api.delete(`/knowledge/edges/${edgeId}`).then(r => r.data)

// ===== Search / RAG =====
export const searchDocuments = (query: string, topK?: number) =>
  api.post('/search/', { query, top_k: topK || 5 }).then(r => r.data)
export const ragQuery = (query: string, topK?: number) =>
  api.post('/search/rag', { query, top_k: topK || 3 }).then(r => r.data)

// ===== Stats =====
export const getOverview = () => api.get('/stats/overview').then(r => r.data)
export const getDailyStats = (days?: number) => api.get('/stats/daily', { params: { days } }).then(r => r.data)
export const getTopicStats = () => api.get('/stats/topics').then(r => r.data)

// ===== Backup =====
export const exportBackup = () => api.get('/backup/export').then(r => r.data)
export const exportFiles = () => api.get('/backup/files', { responseType: 'blob' }).then(r => r.data)
export const importBackup = (fileUri: string, fileName: string) => {
  const form = new FormData()
  form.append('file', { uri: fileUri, name: fileName, type: 'application/octet-stream' } as any)
  return api.post('/backup/import', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

// ===== Study Plan =====
export const listPlans = (limit?: number, offset?: number) =>
  api.get('/study/plans', { params: { limit: limit || 50, offset: offset || 0 } }).then(r => r.data)
export const getPlan = (planId: string) => api.get(`/study/plans/${planId}`).then(r => r.data)
export const createPlan = (params: object) => api.post('/study/plans', params).then(r => r.data)
export const deletePlan = (planId: string) => api.delete(`/study/plans/${planId}`).then(r => r.data)
export const createGoal = (params: object) => api.post('/study/goals', params).then(r => r.data)
export const toggleGoal = (goalId: string) => api.put(`/study/goals/${goalId}/toggle`).then(r => r.data)
export const getDueReminders = () => api.get('/study/reminders/due').then(r => r.data)

// ===== Share =====
export const createShareLink = (params: object) => api.post('/share/create', params).then(r => r.data)
export const listShareLinks = (limit?: number, offset?: number) =>
  api.get('/share/my-links', { params: { limit: limit || 50, offset: offset || 0 } }).then(r => r.data)
export const deleteShareLink = (shareId: string) => api.delete(`/share/${shareId}`).then(r => r.data)

// ===== Auth =====
export const register = (params: { username: string; email: string; password: string; confirm_password?: string }) =>
  api.post('/auth/register', params).then(r => r.data)
export const login = (params: { username: string; password: string }) =>
  api.post('/auth/login', params).then(r => r.data)
export const getProfile = () => api.get('/auth/me').then(r => r.data)

// ===== User Profile & Settings =====
export const getUserProfile = () => api.get('/user/profile').then(r => r.data)
export const updateUserProfile = (params: { nickname?: string; phone?: string; avatar_url?: string; email?: string }) =>
  api.put('/user/profile', params).then(r => r.data)
export const changePassword = (params: { old_password: string; new_password: string }) =>
  api.put('/user/password', params).then(r => r.data)
export const getUserBinds = () => api.get('/user/binds').then(r => r.data)
export const bindAccount = (params: { provider: string; provider_name?: string; provider_uid?: string }) =>
  api.post('/user/bind', params).then(r => r.data)
export const unbindAccount = (provider: string) => api.delete(`/user/bind/${provider}`).then(r => r.data)
export const getUserHistory = (page?: number, pageSize?: number, actionType?: string) =>
  api.get('/user/history', { params: { page: page || 1, page_size: pageSize || 20, action_type: actionType || '' } }).then(r => r.data)
export const createHistory = (params: { action_type?: string; action_label?: string; resource_type?: string; resource_id?: string; detail?: string }) =>
  api.post('/user/history', params).then(r => r.data)

// ===== Notifications =====
export const getNotifications = (page?: number, pageSize?: number, isRead?: string, category?: string) =>
  api.get('/notifications', { params: { page: page || 1, page_size: pageSize || 20, is_read: isRead || '', category: category || '' } }).then(r => r.data)
export const markNotificationRead = (id: string) => api.put(`/notifications/${id}/read`).then(r => r.data)
export const markAllNotificationsRead = () => api.put('/notifications/read-all').then(r => r.data)
export const deleteNotification = (id: string) => api.delete(`/notifications/${id}`).then(r => r.data)
export const batchDeleteNotifications = (ids: string[]) =>
  api.delete('/notifications', { params: { ids: ids.join(',') } }).then(r => r.data)

// ===== Reading Language =====
export const getReadingArticles = (difficulty?: number) =>
  api.get('/reading/articles', { params: difficulty ? { difficulty } : {} }).then(r => r.data)
export const getReadingArticle = (id: string) =>
  api.get(`/reading/articles/${id}`).then(r => r.data)
export const convertReadingText = (text: string, level: number, knownWords?: {cn:string;en:string}[], seenWords?: {cn:string;en:string}[]) =>
  api.post('/reading/convert', { text, level, known_words: knownWords || [], seen_words: seenWords || [] }).then(r => r.data)
export const convertReadingStream = async (params: { text: string; known_words: {cn:string;en:string}[]; seen_words: {cn:string;en:string}[]; skip_cache?: boolean }) => {
  const token = await storage.getItem('knowall_token')
  return fetch(`${API_BASE_URL}/reading/convert/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(params),
  })
}
export const uploadReadingArticle = (title: string, content: string) =>
  api.post('/reading/articles/upload', { title, content }).then(r => r.data)
export const uploadReadingArticleFile = (fileUri: string, fileName: string) => {
  const form = new FormData()
  form.append('file', { uri: fileUri, name: fileName, type: 'text/plain' } as any)
  return api.post('/reading/articles/upload-file', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}
export const deleteReadingArticle = (id: string) =>
  api.delete(`/reading/articles/${id}`).then(r => r.data)

// ===== Knowledge Summary =====
export const generateSummary = (params: object) => api.post('/knowledge/summary/generate', params).then(r => r.data)
export const getSummary = (summaryId: string) => api.get(`/knowledge/summary/${summaryId}`).then(r => r.data)
export const getSummaryNodes = (summaryId: string, level?: number) =>
  api.get(`/knowledge/summary/${summaryId}/nodes`, { params: { level } }).then(r => r.data)
export const getSummaryMindmap = (summaryId: string) =>
  api.get(`/knowledge/summary/${summaryId}/mindmap`).then(r => r.data)
export const listSummaries = (limit?: number, offset?: number) =>
  api.get('/knowledge/summaries', { params: { limit: limit || 20, offset: offset || 0 } }).then(r => r.data)

// ===== Glossary (名词解释库) =====
export const glossaryListTerms = (params?: { subject?: string; chapter?: string; q?: string; page?: number; page_size?: number }) =>
  api.get('/glossary/terms', { params: { ...params, page: params?.page || 1, page_size: params?.page_size || 50 } }).then(r => r.data)
export const glossaryGetTerm = (term: string, subject?: string) =>
  api.get(`/glossary/terms/${encodeURIComponent(term)}`, { params: subject ? { subject } : {} }).then(r => r.data)
export const glossaryGetVariants = (term: string) =>
  api.get(`/glossary/terms/${encodeURIComponent(term)}/variants`).then(r => r.data)
export const glossarySuggest = (q: string, limit = 10) =>
  api.get('/glossary/terms/suggest', { params: { q, limit } }).then(r => r.data)
export const glossarySync = (params?: { subject?: string; summary_id?: string }) =>
  api.post('/glossary/sync', params || {}).then(r => r.data)
export const glossaryStats = () => api.get('/glossary/stats').then(r => r.data)

// ===== Agent Orchestration =====
export const orchestrateAgents = (params: object) => api.post('/agents/orchestrate', params).then(r => r.data)
export const listAgents = () => api.get('/agents/list').then(r => r.data)

// ===== Interactive Quiz =====
export const startInteractiveQuiz = (params: object) =>
  api.get('/quiz/interactive/start', { params }).then(r => r.data)
export const submitInteractiveAnswer = (params: object) =>
  api.post('/quiz/interactive/submit', params).then(r => r.data)

// ===== Language Vocabulary =====
export const generateVocabulary = (params: object) => api.post('/language/vocabulary/generate', params).then(r => r.data)
export const getVocabulary = (params?: object) => api.get('/language/vocabulary', { params }).then(r => r.data)
export const markVocabularyMastered = (vocabId: string, mastered: boolean) =>
  api.patch(`/language/vocabulary/${vocabId}`, { mastered }).then(r => r.data)

// ===== Coverage Report =====
export const getCoverageReport = (summaryId: string) =>
  api.get(`/knowledge/coverage/${summaryId}`).then(r => r.data)
export const refreshCoverage = (params: object) =>
  api.post('/knowledge/coverage/refresh', params).then(r => r.data)

// ===== Answer Review & Mastery =====
export const getMasteryAnalysis = () => api.get('/review/mastery').then(r => r.data)
export const getKpMasteryDetail = (kpId: string) => api.get(`/review/mastery/${kpId}`).then(r => r.data)
export const getAnswerHistory = (params?: { kp_id?: string; is_correct?: boolean; page?: number; page_size?: number }) =>
  api.get('/review/history', { params }).then(r => r.data)
export const getReviewStats = () => api.get('/review/stats').then(r => r.data)
export const generateReviewRecommendations = (model?: string) =>
  api.post('/review/recommend', { model: model || 'deepseek-chat' }).then(r => r.data)
export const getReviewKnowledgePoints = () => api.get('/review/knowledge-points').then(r => r.data)

// ===== Enhanced Study Plan =====
export const generateEnhancedPlan = (params: object) =>
  api.post('/study/generate-plan-enhanced', params).then(r => r.data)
export const getEbbinghausNodes = (planId: string) =>
  api.get(`/study/plans/${planId}/ebbinghaus`).then(r => r.data)

// ===== Token Management =====
export const setAuthToken = async (token: string | null) => {
  if (token) {
    api.defaults.headers.common['Authorization'] = `Bearer ${token}`
    await storage.setItem('knowall_token', token)
  } else {
    delete api.defaults.headers.common['Authorization']
    await storage.removeItem('knowall_token')
  }
}

// Restore token on app load (async)
export const restoreToken = async (): Promise<string | null> => {
  const token = await storage.getItem('knowall_token')
  if (token) {
    api.defaults.headers.common['Authorization'] = `Bearer ${token}`
  }
  return token
}

// ===== KAF 加密资料 (NEW) =====
export const listKafFiles = (params?: { limit?: number; topic?: string }) =>
  api.get('/kaf/files', { params: { limit: params?.limit || 50, topic: params?.topic || '' } }).then(r => r.data)
export const getKafFile = (fileId: string) => api.get(`/kaf/files/${fileId}`).then(r => r.data)
export const grantKafKey = (fileId: string) => api.post(`/kaf/files/${fileId}/grant`).then(r => r.data)
export const checkKafAccess = (fileId: string) => api.post(`/kaf/files/${fileId}/check`).then(r => r.data)
export const publishKaf = (params: { markdown: string; topic?: string; title?: string }) =>
  api.post('/kaf/admin/publish', params).then(r => r.data)

// ===== Gamification (NEW) =====
export const getGamificationDashboard = () => api.get('/gamification/dashboard').then(r => r.data)
export const recordActivity = (activityType: string, count?: number, durationMinutes?: number) =>
  api.post('/gamification/record-activity', { activity_type: activityType, count: count || 1, duration_minutes: durationMinutes || 0 }).then(r => r.data)
export const startFocusSession = (durationMinutes: number, sessionType?: string) =>
  api.post('/gamification/focus/start', { duration_minutes: durationMinutes, session_type: sessionType || 'study' }).then(r => r.data)
export const getFocusHistory = (limit?: number) =>
  api.get('/gamification/focus/history', { params: { limit: limit || 30 } }).then(r => r.data)

// ===== Data Export (NEW) =====
export const exportMyData = () => api.get('/commercial/export/my-data', { responseType: 'blob' }).then(r => r.data)
export const deleteMyData = () => api.delete('/commercial/delete-my-data').then(r => r.data)
export const getUserTier = () => api.get('/commercial/tier').then(r => r.data)

// ===== Recitation (背诵) =====
export const createPassage = (data: { title: string; content: string; source?: string; category?: string; difficulty?: number; tags?: string[] }) =>
  api.post('/recitation/passages', data).then(r => r.data)
export const listPassages = (params?: { category?: string; difficulty?: number; limit?: number; offset?: number }) =>
  api.get('/recitation/passages', { params }).then(r => r.data)
export const getPassage = (passageId: string) =>
  api.get(`/recitation/passages/${passageId}`).then(r => r.data)
export const deletePassage = (passageId: string) =>
  api.delete(`/recitation/passages/${passageId}`).then(r => r.data)
export const getHidePlan = (content: string, level: number) =>
  api.post('/recitation/hide-plan', { content, level }).then(r => r.data)
export const submitDictation = (data: { passage_id: string; user_input: string; time_spent_ms?: number }) =>
  api.post('/recitation/dictation/submit', data).then(r => r.data)
export const reviewRecitation = (recordId: string, rating: number) =>
  api.post('/recitation/review', { record_id: recordId, rating }).then(r => r.data)
export const getDueRecitations = (limit?: number) =>
  api.get('/recitation/due', { params: { limit } }).then(r => r.data)
export const getRecitationStats = () =>
  api.get('/recitation/stats').then(r => r.data)

// ===== Live Session (实时对话考试) =====
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

// ===== SMS (NEW) =====
export const sendSmsCode = (phone: string) => api.post('/sms/send-code', { phone }).then(r => r.data)
export const verifySmsCode = (phone: string, code: string) => api.post('/sms/verify-code', { phone, code }).then(r => r.data)
export const bindPhone = (phone: string, code: string) => api.post('/sms/bind-phone', { phone, code }).then(r => r.data)
export const unbindPhone = () => api.post('/sms/unbind-phone').then(r => r.data)
export const getPhoneStatus = () => api.get('/sms/phone-status').then(r => r.data)

// ===== Books (教材编译, 无前缀 root) =====
export const createBook = (params: { title: string; description?: string; source_doc_ids?: string[]; source_kb?: string }) =>
  rootApi.post('/books/create', params).then(r => r.data)
export const listBooks = () => rootApi.get('/books').then(r => r.data)
export const getBook = (bookId: string) => rootApi.get(`/books/${bookId}`).then(r => r.data)
export const deleteBook = (bookId: string) => rootApi.delete(`/books/${bookId}`).then(r => r.data)
export const exportBookMarkdown = (bookId: string) =>
  rootApi.get(`/books/${bookId}/export`, { responseType: 'blob' }).then(r => r.data)

// ===== Deep Research (研究任务, 无前缀 root) =====
export const startResearch = (params: { topic: string; max_subtopics?: number }) =>
  rootApi.post('/research/start', params).then(r => r.data)
export const getResearchStatus = (taskId: string) =>
  rootApi.get(`/research/${taskId}/status`).then(r => r.data)
export const listResearchTasks = () => rootApi.get('/research/tasks').then(r => r.data)

// ===== Share View (查看分享内容) =====
export const viewSharedResource = (shareId: string, accessCode: string) =>
  api.get(`/share/view/${shareId}`, { params: { access_code: accessCode } }).then(r => r.data)

// ===== Sync File Versions (文件版本/事件) =====
export const getSyncCatchup = (params?: { space_id?: string; from_seq?: number }) =>
  rootApi.get('/api/events/catchup', { params: { space_id: params?.space_id || 'default', from_seq: params?.from_seq || 0 } }).then(r => r.data)
export const listSyncRooms = () => rootApi.get('/ws/rooms').then(r => r.data)
export const getSyncRoomMembers = (roomId: string) =>
  rootApi.get(`/ws/rooms/${roomId}/members`).then(r => r.data)

export default api
