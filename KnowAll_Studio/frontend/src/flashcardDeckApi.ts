import type { AxiosRequestConfig } from 'axios'
import api from './api'

export type ManualDeckInput = { name: string; description: string }
export type ManualDeck = { id: string; name: string; card_count: number; created_at: string }

export const createFlashcardDeck = (values: ManualDeckInput, config?: AxiosRequestConfig) =>
  api.post<ManualDeck>('/flashcards/decks', values, config).then(response => response.data)
