// Zustand stores for KnowAll Studio Mobile
// Adapted for React Native: AsyncStorage, EventEmitter

import { create } from 'zustand'
import type { AuthUser } from '../types'
import { setAuthToken } from '../api'
import { storage } from '../services/storage'
import { appEvents } from '../services/eventEmitter'

interface Document {
  id: string; filename: string; file_type: string; status: string;
  page_count: number; created_at: string;
}
interface Tree { tree_id: string; name: string; tree_data: any; created_at: string; }
interface Question {
  id: string; question_type: string; difficulty: string; tags: string[];
  question_text: string; options: any[]; answer: string; analysis: string;
}
interface Flashcard {
  id: string; card_type: string; front: string; back: string; hints: string; tags: string[];
}

// ========== Auth Store ==========

interface AuthState {
  token: string | null
  user: AuthUser | null
  isAuthenticated: boolean
  authReady: boolean
  login: (token: string, user: AuthUser) => Promise<void>
  logout: () => Promise<void>
  restore: () => Promise<boolean>
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  user: null,
  isAuthenticated: false,
  authReady: false,

  login: async (token, user) => {
    await setAuthToken(token)
    await storage.setJSON('knowall_user', user)
    set({ token, user, isAuthenticated: true })
  },

  logout: async () => {
    await setAuthToken(null)
    await storage.removeItem('knowall_user')
    // 清空 KAF 密钥内存缓存(登出后不残留)
    const { clearKeyCache } = await import('../services/kaf')
    clearKeyCache()
    set({ token: null, user: null, isAuthenticated: false })
  },

  restore: async () => {
    const token = await storage.getItem('knowall_token')
    if (!token) {
      set({ authReady: true })
      return false
    }
    try {
      const user = await storage.getJSON<AuthUser>('knowall_user')
      if (user && user.id) {
        await setAuthToken(token)
        set({ token, user, isAuthenticated: true, authReady: true })
        return true
      }
    } catch {}
    // Token exists but no user data — clear stale token
    await setAuthToken(null)
    await storage.removeItem('knowall_user')
    set({ authReady: true })
    return false
  },
}))

// Listen for unauthorized event from API interceptor (with dedup guard for HMR)
let _authLogoutListenerRegistered = false
if (!_authLogoutListenerRegistered) {
  _authLogoutListenerRegistered = true
  appEvents.on('auth:logout', () => {
    useAuthStore.getState().logout()
  })
}

// ========== App Store ==========

export const useAppStore = create<{
  documents: Document[];
  trees: Tree[];
  questions: Question[];
  selectedDoc: string | null;
  selectedTree: string | null;
  loading: boolean;
  error: string | null;
  setDocuments: (docs: Document[]) => void;
  setTrees: (trees: Tree[]) => void;
  setQuestions: (qs: Question[]) => void;
  setSelectedDoc: (id: string | null) => void;
  setSelectedTree: (id: string | null) => void;
  setLoading: (v: boolean) => void;
  setError: (e: string | null) => void;
}>(set => ({
  documents: [], trees: [], questions: [],
  selectedDoc: null, selectedTree: null,
  loading: false, error: null,
  setDocuments: (docs) => set({ documents: docs }),
  setTrees: (trees) => set({ trees }),
  setQuestions: (qs) => set({ questions: qs }),
  setSelectedDoc: (id) => set({ selectedDoc: id }),
  setSelectedTree: (id) => set({ selectedTree: id }),
  setLoading: (v) => set({ loading: v }),
  setError: (e) => set({ error: e }),
}))

// ========== Quiz Store ==========

export const useQuizStore = create<{
  currentExam: any | null;
  userAnswers: Record<string, string>;
  results: any | null;
  setCurrentExam: (e: any) => void;
  setAnswer: (questionId: string, answer: string) => void;
  setResults: (r: any) => void;
  reset: () => void;
}>(set => ({
  currentExam: null, userAnswers: {}, results: null,
  setCurrentExam: (e) => set({ currentExam: e, userAnswers: {}, results: null }),
  setAnswer: (qid, ans) => set(s => ({ userAnswers: { ...s.userAnswers, [qid]: ans } })),
  setResults: (r) => set({ results: r }),
  reset: () => set({ currentExam: null, userAnswers: {}, results: null }),
}))

// ========== Flashcard Store ==========

export const useFlashcardStore = create<{
  dueCards: Flashcard[];
  currentIndex: number;
  isFlipped: boolean;
  reviewLimit: number;
  setDueCards: (cards: Flashcard[]) => void;
  flip: () => void;
  next: () => void;
  prev: () => void;
  setCurrentIndex: (i: number) => void;
  setReviewLimit: (n: number) => void;
}>(set => ({
  dueCards: [], currentIndex: 0, isFlipped: false,
  reviewLimit: 20,
  setDueCards: (cards) => set({ dueCards: cards, currentIndex: 0, isFlipped: false }),
  flip: () => set(s => ({ isFlipped: !s.isFlipped })),
  next: () => set(s => ({
    currentIndex: Math.min(s.currentIndex + 1, s.dueCards.length - 1),
    isFlipped: false,
  })),
  prev: () => set(s => ({
    currentIndex: Math.max(s.currentIndex - 1, 0),
    isFlipped: false,
  })),
  setCurrentIndex: (i) => set({ currentIndex: i, isFlipped: false }),
  setReviewLimit: async (n) => {
    await storage.setItem('knowall_review_limit', String(n))
    set({ reviewLimit: n })
  },
}))

// Restore review limit on init (with error guard)
storage.getItem('knowall_review_limit').then(val => {
  if (val) {
    const num = Number(val)
    if (!isNaN(num) && num > 0) {
      useFlashcardStore.setState({ reviewLimit: num })
    }
  }
}).catch(() => { /* storage not available, use default */ })

// ========== Recitation (背诵) Store ==========

import type { RecitationPassage, HidePlan, DictationResult } from '../types'

export const useRecitationStore = create<{
  passages: RecitationPassage[]
  currentPassage: RecitationPassage | null
  mode: 'hide' | 'dictation'
  hideLevel: number
  hidePlan: HidePlan | null
  hideRevealedSegments: Set<number>
  dictationInput: string
  dictationResult: DictationResult | null
  setPassages: (p: RecitationPassage[]) => void
  setCurrentPassage: (p: RecitationPassage | null) => void
  setMode: (m: 'hide' | 'dictation') => void
  setHideLevel: (l: number) => void
  setHidePlan: (plan: HidePlan | null) => void
  toggleHideSegment: (idx: number) => void
  revealAllHideSegments: () => void
  setDictationInput: (input: string) => void
  setDictationResult: (result: DictationResult | null) => void
  reset: () => void
}>(set => ({
  passages: [],
  currentPassage: null,
  mode: 'hide',
  hideLevel: 2,
  hidePlan: null,
  hideRevealedSegments: new Set(),
  dictationInput: '',
  dictationResult: null,
  setPassages: (p) => set({ passages: p }),
  setCurrentPassage: (p) => set({
    currentPassage: p, dictationInput: '', dictationResult: null,
    hidePlan: null, hideRevealedSegments: new Set(),
  }),
  setMode: (m) => set({ mode: m, dictationResult: null, dictationInput: '', hidePlan: null, hideRevealedSegments: new Set() }),
  setHideLevel: (l) => set({ hideLevel: l, hidePlan: null, hideRevealedSegments: new Set() }),
  setHidePlan: (plan) => set({ hidePlan: plan, hideRevealedSegments: new Set() }),
  toggleHideSegment: (idx) => set(s => {
    const next = new Set(s.hideRevealedSegments)
    if (next.has(idx)) next.delete(idx); else next.add(idx)
    return { hideRevealedSegments: next }
  }),
  revealAllHideSegments: () => set(s => {
    if (!s.hidePlan) return {}
    const all = new Set<number>()
    s.hidePlan.segments.forEach((seg, i) => { if (seg.hidden) all.add(i) })
    return { hideRevealedSegments: all }
  }),
  setDictationInput: (input) => set({ dictationInput: input }),
  setDictationResult: (result) => set({ dictationResult: result }),
  reset: () => set({
    currentPassage: null, hidePlan: null, dictationInput: '', dictationResult: null,
    hideRevealedSegments: new Set(),
  }),
}))
