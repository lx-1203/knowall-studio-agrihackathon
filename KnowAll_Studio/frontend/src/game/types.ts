// Game Agent Module - Type Definitions

/** 游戏状态枚举 */
export type GameState = 'ready' | 'playing' | 'answering' | 'paused' | 'game_over'

/** 2048 棋盘：4x4 二维数组，0 表示空格 */
export type Grid = number[][]

/** 单个瓦片 */
export interface Tile {
  id: number
  value: number
  row: number
  col: number
  /** 是否刚合并生成 */
  merged: boolean
  /** 是否新生成 */
  isNew: boolean
}

/** 移动方向 */
export type Direction = 'up' | 'down' | 'left' | 'right'

/** 移动结果 */
export interface MoveResult {
  moved: boolean
  scoreGained: number
  mergedTiles: { row: number; col: number; value: number }[]
}

/** 题目难度 */
export type QuestionDifficulty = 'easy' | 'medium' | 'hard'

/** 题目类型（游戏内支持的类型） */
export type GameQuestionType = 'single_choice' | 'multi_choice' | 'true_false'

// GameOption / GameQuestion / QuizGateState were removed: an export audit found
// zero references to any of them. They were the type side of the quiz-gate
// feature that was deleted as unreachable code. GameQuestionType stays because
// QuizBankConfig (used by GameConfig) still references it.

/** 题库配置 */
export interface QuizBankConfig {
  source: 'local' | 'remote'
  remoteUrl: string
  timeLimit: number               // 秒
  questionTypes: GameQuestionType[]
  /** 里程碑 → 难度映射 */
  difficultyMap: Record<number, QuestionDifficulty>
  /** 触发答题的里程碑瓦片值 */
  milestoneTiles: number[]
}

/** 游戏配置 */
export interface GameConfig {
  gridSize: number                // 4
  initialTiles: number            // 2
  winTile: number                 // 2048
  maxLives: number                // 3
  quiz: QuizBankConfig
  scoring: {
    quizCorrect: Record<QuestionDifficulty, number>
    winBonus: number
  }
}

/** 答题结果 */
export interface QuizResult {
  questionId: string
  userAnswer: string
  correctAnswer: string
  isCorrect: boolean
  timeSpent: number               // 毫秒
}

/** 游戏统计 */
export interface GameStats {
  score: number
  maxTile: number
  moves: number
  quizTotal: number
  quizCorrect: number
  lives: number
  startTime: number
  elapsedTime: number
}
