import type { AxiosRequestConfig } from 'axios'
import api from './api'

export type FlashcardContent = {
  id: string; card_type: string; front: string; back: string; hints: string
  tags: string[] | null; deck_id: string | null
}
export type CardContentValues = Pick<FlashcardContent, 'front' | 'back' | 'hints'>
export type EditFlashcardInput = CardContentValues & {
  original: CardContentValues & { card_type: string }
}
export const getFlashcardContent = (id: string, config?: AxiosRequestConfig) =>
  api.get<FlashcardContent>(`/flashcards/cards/${encodeURIComponent(id)}`, config).then(response => response.data)
export const editFlashcardContent = (id: string, values: EditFlashcardInput, config?: AxiosRequestConfig) =>
  api.patch<FlashcardContent>(`/flashcards/cards/${encodeURIComponent(id)}`, values, config).then(response => response.data)
