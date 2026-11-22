import dayjs from 'dayjs'
import utc from 'dayjs/plugin/utc'

dayjs.extend(utc)

// Both legacy offset-less UTC and new explicit offsets identify an instant.
// Day.js owns parsing, local timezone/DST conversion and calendar formatting.
function formatLocalTime(value: unknown, pattern: string): string {
  if (typeof value !== 'string' || !value.trim()) return '—'
  const instant = dayjs.utc(value)
  return instant.isValid() ? instant.local().format(pattern) : '—'
}

export function formatFlashcardDate(value: unknown): string {
  return formatLocalTime(value, 'YYYY-MM-DD')
}

export function formatLocalDateTime(value: unknown): string {
  return formatLocalTime(value, 'YYYY-MM-DD HH:mm:ss')
}
