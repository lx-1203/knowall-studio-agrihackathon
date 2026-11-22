import { create } from 'zustand'
import type { AuthUser } from '../types'
import { setAuthToken } from '../authToken'

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
  login: (token: string, user: AuthUser) => void
  logout: () => void
  restore: () => boolean
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  user: null,
  isAuthenticated: false,

  login: (token, user) => {
    setAuthToken(token)
    localStorage.setItem('knowall_user', JSON.stringify(user))
    set({ token, user, isAuthenticated: true })
  },

  logout: () => {
    setAuthToken(null)
    localStorage.removeItem('knowall_user')
    set({ token: null, user: null, isAuthenticated: false })
  },

  restore: () => {
    const token = localStorage.getItem('knowall_token')
    if (!token) return false
    try {
      setAuthToken(token)
      const raw = localStorage.getItem('knowall_user')
      if (!raw) {
        // Token exists but user data missing - token still valid, fetch from /me
        set({ token, isAuthenticated: true })
        return true
      }
      const user = JSON.parse(raw)
      if (user && user.id) {
        set({ token, user, isAuthenticated: true })
        return true
      }
    } catch {}
    // Corrupted data — clear and start fresh
    setAuthToken(null)
    localStorage.removeItem('knowall_user')
    localStorage.removeItem('knowall_token')
    return false
  },
}))

// Listen for unauthorized event from API interceptor
if (typeof window !== 'undefined') {
  window.addEventListener('auth:logout', () => {
    useAuthStore.getState().logout()
  })
}

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

export const useFlashcardStore = create<{
  dueCards: Flashcard[];
  currentIndex: number;
  isFlipped: boolean;
  reviewLimit: number;
  setDueCards: (cards: Flashcard[]) => void;
  completeReview: (cardId: string) => void;
  flip: () => void;
  next: () => void;
  prev: () => void;
  setCurrentIndex: (i: number) => void;
  setReviewLimit: (n: number) => void;
}>(set => {
  const stored = Number(localStorage.getItem('knowall_review_limit'))
  return {
    dueCards: [], currentIndex: 0, isFlipped: false,
    reviewLimit: isNaN(stored) || stored <= 0 ? 20 : stored,
    setDueCards: (cards) => set({ dueCards: cards, currentIndex: 0, isFlipped: false }),
    completeReview: (cardId) => set(s => {
      const reviewedIndex = s.dueCards.findIndex(card => card.id === cardId)
      if (reviewedIndex < 0) return s
      const dueCards = s.dueCards.filter(card => card.id !== cardId)
      return {
        dueCards,
        currentIndex: Math.min(s.currentIndex - (reviewedIndex < s.currentIndex ? 1 : 0), Math.max(dueCards.length - 1, 0)),
        isFlipped: false,
      }
    }),
  flip: () => set(s => ({ isFlipped: !s.isFlipped })),
  next: () => set(s => ({
    currentIndex: s.dueCards.length > 0 ? Math.min(s.currentIndex + 1, s.dueCards.length - 1) : 0,
    isFlipped: false,
  })),
  prev: () => set(s => ({
    currentIndex: Math.max(s.currentIndex - 1, 0),
    isFlipped: false,
  })),
  setCurrentIndex: (i) => set({ currentIndex: i, isFlipped: false }),
  setReviewLimit: (n) => {
    localStorage.setItem('knowall_review_limit', String(n))
    set({ reviewLimit: n })
  },
  }
})

// ========== Recitation (背诵) Store ==========

import type { RecitationPassage, HidePlan, HideSegment, DictationResult } from '../types'

interface RecitationState {
  passages: RecitationPassage[]
  currentPassage: RecitationPassage | null
  mode: 'hide' | 'dictation'
  hideLevel: number
  hidePlan: HidePlan | null
  revealedIndices: Set<number>
  allRevealed: boolean
  dictationInput: string
  dictationResult: DictationResult | null
  hideRevealedSegments: Set<number>
  setPassages: (p: RecitationPassage[]) => void
  setCurrentPassage: (p: RecitationPassage | null) => void
  setMode: (m: 'hide' | 'dictation') => void
  setHideLevel: (l: number) => void
  setHidePlan: (plan: HidePlan | null) => void
  toggleReveal: (idx: number) => void
  revealAll: () => void
  setDictationInput: (input: string) => void
  setDictationResult: (result: DictationResult | null) => void
  toggleHideSegment: (idx: number) => void
  revealAllHideSegments: () => void
  reset: () => void
}

export const useRecitationStore = create<RecitationState>(set => ({
  passages: [],
  currentPassage: null,
  mode: 'hide',
  hideLevel: 2,
  hidePlan: null,
  revealedIndices: new Set(),
  allRevealed: false,
  dictationInput: '',
  dictationResult: null,
  hideRevealedSegments: new Set(),
  setPassages: (p) => set({ passages: p }),
  setCurrentPassage: (p) => set({
    currentPassage: p,
    dictationInput: '',
    dictationResult: null,
    hidePlan: null,
    revealedIndices: new Set(),
    allRevealed: false,
    hideRevealedSegments: new Set(),
  }),
  setMode: (m) => set({ mode: m, dictationResult: null, dictationInput: '', hidePlan: null, hideRevealedSegments: new Set() }),
  setHideLevel: (l) => set({ hideLevel: l, hidePlan: null, hideRevealedSegments: new Set() }),
  setHidePlan: (plan) => set({ hidePlan: plan, hideRevealedSegments: new Set() }),
  toggleReveal: (idx) => set(s => {
    const next = new Set(s.revealedIndices)
    if (next.has(idx)) next.delete(idx); else next.add(idx)
    return { revealedIndices: next }
  }),
  revealAll: () => set({ allRevealed: true }),
  setDictationInput: (input) => set({ dictationInput: input }),
  setDictationResult: (result) => set({ dictationResult: result }),
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
  reset: () => set({
    currentPassage: null, hidePlan: null, dictationInput: '', dictationResult: null,
    revealedIndices: new Set(), allRevealed: false, hideRevealedSegments: new Set(),
  }),
}))

// Session boundaries reset the existing app store synchronously, including 401.
// A same-account token/profile refresh keeps state; persisted reading/notes stay.
useAuthStore.subscribe((current, previous) => {
  if (current.user?.id !== previous.user?.id || current.isAuthenticated !== previous.isAuthenticated) {
    useAppStore.setState(useAppStore.getInitialState())
    useQuizStore.setState(useQuizStore.getInitialState())
    useFlashcardStore.setState({ ...useFlashcardStore.getInitialState(), reviewLimit: useFlashcardStore.getState().reviewLimit })
    const { mode, hideLevel } = useRecitationStore.getState()
    useRecitationStore.setState({ ...useRecitationStore.getInitialState(), mode, hideLevel })
  }
})
