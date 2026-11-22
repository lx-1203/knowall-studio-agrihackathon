import api from './api'
import type { KnowledgeItem } from './knowledgeCollections'

export type NetworkScope = 'public' | 'personal'
export interface ExtractionJob {
  id: string; document_id: string; status: 'pending' | 'running' | 'completed' | 'failed'
  point_count: number; error: string | null; summary_id?: string | null
}
export interface LearningState { saved: boolean; mastery: 'unseen' | 'learning' | 'mastered'; note: string }
export interface NetworkConcept {
  id: string; name: string; subject: string; topic: string; definition: string; aliases: string[]
  review_status: 'compiled' | 'needs_review' | 'reviewed'; in_learning: boolean; source_count: number
}
export interface AuthoritySource { title: string; url: string; authority: string; excerpt?: string }
export interface SourceSearch { status: 'searching' | 'found'; sources: AuthoritySource[] }
export interface ConceptDetail extends NetworkConcept {
  content: KnowledgeItem
  sources: unknown[] // Compatibility only; uploaded materials are never displayed as authority.
  source_search?: SourceSearch
  can_contribute?: boolean
  relations: { source_id: string; target_id: string; relation_type: string; target_name: string }[]
  learning: LearningState | null
}
const root = '/knowledge-network'
export interface KnowledgeDocument { id: string; filename: string; status: string }
export const listNetworkDocuments = (signal?: AbortSignal): Promise<{ items: KnowledgeDocument[] }> =>
  api.get(`${root}/documents`, { signal }).then(r => r.data)
export const listNetworkConcepts = (params: { scope: NetworkScope; q?: string; subject?: string; document_id?: string; limit: number; offset: number }, signal?: AbortSignal): Promise<{ items: NetworkConcept[]; total: number }> =>
  api.get(`${root}/discover`, { params, signal }).then(r => r.data)
export const getNetworkConcept = (id: string, signal?: AbortSignal, scope?: NetworkScope): Promise<ConceptDetail> =>
  api.get(`${root}/concepts/${encodeURIComponent(id)}`, { signal, params: { scope } }).then(r => r.data)
export const saveNetworkLearning = (id: string, state: LearningState, signal?: AbortSignal): Promise<LearningState> =>
  api.put(`${root}/concepts/${encodeURIComponent(id)}/learning`, state, { signal }).then(r => r.data)
export const publishNetworkConcept = (id: string, signal?: AbortSignal): Promise<ConceptDetail> =>
  api.post(`${root}/concepts/${encodeURIComponent(id)}/publish`, { reviewed: true }, { signal }).then(r => r.data)
export const searchNetworkSources = (id: string, signal?: AbortSignal): Promise<SourceSearch> =>
  api.post(`${root}/concepts/${encodeURIComponent(id)}/sources/search`, {}, { signal }).then(r => r.data)
export const getNetworkExtraction = (id: string, signal?: AbortSignal): Promise<ExtractionJob> =>
  api.get(`${root}/extractions/${encodeURIComponent(id)}`, { signal }).then(r => r.data)
export const startNetworkExtraction = (document_id: string, signal?: AbortSignal): Promise<ExtractionJob> =>
  api.post(`${root}/extractions`, { document_id }, { signal }).then(r => r.data)
export function networkError(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail
  return typeof detail === 'string' ? detail : fallback
}
