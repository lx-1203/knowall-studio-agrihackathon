// Core 2048 game engine for React Native
import type { TileData, GameBoard, Direction } from '../types'

const SIZE = 4

let _tileIdCounter = 0
function nextTileId(): string {
  return `t_${Date.now()}_${++_tileIdCounter}`
}

function emptyGrid(): (TileData | null)[][] {
  return Array.from({ length: SIZE }, () => Array(SIZE).fill(null))
}

function addRandomTile(grid: (TileData | null)[][]): (TileData | null)[][] {
  const empty: [number, number][] = []
  for (let r = 0; r < SIZE; r++) {
    for (let c = 0; c < SIZE; c++) {
      if (!grid[r][c]) empty.push([r, c])
    }
  }
  if (empty.length === 0) return grid

  const [row, col] = empty[Math.floor(Math.random() * empty.length)]
  const newGrid = grid.map(r => [...r])
  newGrid[row][col] = {
    id: nextTileId(),
    value: Math.random() < 0.9 ? 2 : 4,
    row, col,
    isNew: true,
  }
  return newGrid
}

function slideRow(row: (TileData | null)[]): { row: (TileData | null)[]; score: number; moved: boolean } {
  const filtered = row.filter(t => t !== null) as TileData[]
  const merged: (TileData | null)[] = []
  let score = 0
  let i = 0

  while (i < filtered.length) {
    if (i + 1 < filtered.length && filtered[i].value === filtered[i + 1].value) {
      const newValue = filtered[i].value * 2
      merged.push({
        id: nextTileId(),
        value: newValue,
        row: 0, col: 0,
        mergedFrom: true,
      })
      score += newValue
      i += 2
    } else {
      merged.push({ ...filtered[i], mergedFrom: false })
      i++
    }
  }

  while (merged.length < SIZE) merged.push(null)

  const moved = row.some((t, idx) => t?.id !== merged[idx]?.id || t?.value !== merged[idx]?.value)
  return { row: merged, score, moved }
}

function rotateGrid(grid: (TileData | null)[][], times: number): (TileData | null)[][] {
  let result = grid.map(r => [...r])
  for (let t = 0; t < times; t++) {
    const rotated: (TileData | null)[][] = emptyGrid()
    for (let r = 0; r < SIZE; r++) {
      for (let c = 0; c < SIZE; c++) {
        rotated[c][SIZE - 1 - r] = result[r][c]
          ? { ...result[r][c]!, row: c, col: SIZE - 1 - r }
          : null
      }
    }
    result = rotated
  }
  return result
}

export function createInitialBoard(bestScore: number = 0): GameBoard {
  let grid = emptyGrid()
  grid = addRandomTile(grid)
  grid = addRandomTile(grid)
  return {
    grid,
    score: 0,
    bestScore,
    state: 'playing',
    elapsedSeconds: 0,
  }
}

export function makeMove(board: GameBoard, direction: Direction): GameBoard {
  if (board.state !== 'playing') return board

  // Map direction to rotation count
  const rotationMap: Record<Direction, number> = { left: 0, up: 1, right: 2, down: 3 }
  const rotations = rotationMap[direction]

  // Rotate so we always slide left
  let working = rotateGrid(board.grid, rotations)
  let totalScore = 0
  let moved = false

  for (let r = 0; r < SIZE; r++) {
    const { row, score, moved: rowMoved } = slideRow(working[r])
    working[r] = row
    totalScore += score
    if (rowMoved) moved = true
  }

  if (!moved) return board

  // Rotate back
  working = rotateGrid(working, (4 - rotations) % 4)

  // Add new tile
  working = addRandomTile(working)

  const newScore = board.score + totalScore

  // Check for game over
  const state = isGameOver(working) ? 'game_over' : 'playing'

  return {
    grid: working,
    score: newScore,
    bestScore: Math.max(board.bestScore, newScore),
    state,
    elapsedSeconds: board.elapsedSeconds,
  }
}

function isGameOver(grid: (TileData | null)[][]): boolean {
  // Check for empty cells
  for (let r = 0; r < SIZE; r++) {
    for (let c = 0; c < SIZE; c++) {
      if (!grid[r][c]) return false
    }
  }
  // Check for mergeable neighbors
  for (let r = 0; r < SIZE; r++) {
    for (let c = 0; c < SIZE; c++) {
      const val = grid[r][c]!.value
      if (c + 1 < SIZE && grid[r][c + 1]?.value === val) return false
      if (r + 1 < SIZE && grid[r + 1][c]?.value === val) return false
    }
  }
  return true
}

export function hasWon(grid: (TileData | null)[][]): boolean {
  return grid.some(row => row.some(t => t && t.value >= 2048))
}
