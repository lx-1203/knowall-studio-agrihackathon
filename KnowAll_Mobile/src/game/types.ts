// Game types shared across mobile game modules
export type Direction = 'up' | 'down' | 'left' | 'right'

export interface TileData {
  id: string
  value: number
  row: number
  col: number
  mergedFrom?: boolean
  isNew?: boolean
}

export type GameState = 'ready' | 'playing' | 'paused' | 'game_over'

export interface GameBoard {
  grid: (TileData | null)[][]
  score: number
  bestScore: number
  state: GameState
  elapsedSeconds: number
}

export interface QuizQuestion {
  id: string
  question_type: string
  question_text: string
  options: { label: string; text: string }[]
  correct_answer: string
  analysis: string
}
