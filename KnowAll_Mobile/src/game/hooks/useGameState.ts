// Game state management hook for React Native
import { useState, useCallback, useRef, useEffect } from 'react'
import { createInitialBoard, makeMove } from '../engine/gameEngine'
import type { GameBoard, GameState, Direction } from '../types'
import { QUIZ_GATE_TILES, shouldTriggerQuiz } from '../quiz/gateData'

interface UseGameStateReturn {
  board: GameBoard
  grid: (any | null)[][]
  state: GameState
  startGame: () => void
  makeMoveAction: (dir: Direction) => void
  pauseGame: () => void
  resumeGame: () => void
  resetGame: () => void
  tickTimer: () => void
  passedGates: Set<number>
  markGatePassed: (tileValue: number) => void
  triggeringTile: number | null
}

export function useGameState(bestScore: number = 0): UseGameStateReturn {
  const [board, setBoard] = useState<GameBoard>(() => ({
    grid: [],
    score: 0,
    bestScore,
    state: 'ready',
    elapsedSeconds: 0,
  }))
  const [passedGates, setPassedGates] = useState<Set<number>>(new Set())
  const passedGatesRef = useRef(passedGates)

  const grid = board.grid
  const state = board.state

  const startGame = useCallback(() => {
    const newBoard = createInitialBoard(bestScore)
    newBoard.state = 'playing'
    setBoard(newBoard)
  }, [bestScore])

  const makeMoveAction = useCallback((dir: Direction) => {
    setBoard(prev => makeMove(prev, dir))
  }, [])

  const pauseGame = useCallback(() => {
    setBoard(prev => ({ ...prev, state: 'paused' }))
  }, [])

  const resumeGame = useCallback(() => {
    setBoard(prev => ({ ...prev, state: 'playing' }))
  }, [])

  const resetGame = useCallback(() => {
    setBoard({
      grid: [],
      score: 0,
      bestScore: board.bestScore,
      state: 'ready',
      elapsedSeconds: 0,
    })
  }, [board.bestScore])

  const tickTimer = useCallback(() => {
    setBoard(prev => ({
      ...prev,
      elapsedSeconds: prev.elapsedSeconds + 1,
    }))
  }, [])

  const markGatePassed = useCallback((tileValue: number) => {
    setPassedGates(prev => {
      const next = new Set(prev)
      next.add(tileValue)
      passedGatesRef.current = next
      return next
    })
  }, [])

  const triggeringTile = shouldTriggerQuiz(grid, passedGates)

  return {
    board, grid, state,
    startGame, makeMoveAction, pauseGame, resumeGame, resetGame,
    tickTimer, passedGates, markGatePassed, triggeringTile,
  }
}
