// Quiz gate integration - tile values that trigger questions
export const QUIZ_GATE_TILES = [256, 512, 1024, 2048]

export function shouldTriggerQuiz(grid: (any | null)[][], passedGates: Set<number>): number | null {
  for (const row of grid) {
    for (const tile of row) {
      if (tile && QUIZ_GATE_TILES.includes(tile.value) && !passedGates.has(tile.value)) {
        return tile.value
      }
    }
  }
  return null
}

// Score milestones for achievement badges
export const SCORE_MILESTONES = [
  { score: 500, badge: '初出茅庐' },
  { score: 2000, badge: '渐入佳境' },
  { score: 5000, badge: '学识渊博' },
  { score: 10000, badge: '融会贯通' },
  { score: 20000, badge: '登峰造极' },
]
