export type CheckInRecord = { date: string; note: string; checked_at: string }
export type CheckInSummary = {
  today: string; timezone: string; checked_in_today: boolean; current_streak: number;
  longest_streak: number; total_days: number; month: string; month_days: number; records: CheckInRecord[];
}

const DAY = 86400000, BEIJING_OFFSET = 8 * 3600000
export function beijingDate(now = new Date()) { return new Date(now.getTime() + BEIJING_OFFSET).toISOString().slice(0, 10) }
export function beijingClock(instant: string) { return new Date(new Date(instant).getTime() + BEIJING_OFFSET).toISOString().slice(11, 16) }
export function millisecondsToBeijingMidnight(now = new Date()) { return DAY - ((now.getTime() + BEIJING_OFFSET) % DAY) }
function parseMonth(month: string) {
  if (!/^(19|20|21)\d{2}-(0[1-9]|1[0-2])$/.test(month)) throw new Error('无效月份')
  return month.split('-').map(Number)
}
export function shiftMonth(month: string, offset: number) {
  const [year, number] = parseMonth(month)
  return new Date(Date.UTC(year, number - 1 + offset, 1)).toISOString().slice(0, 7)
}
export function monthCells(month: string): (string | null)[] {
  const [year, number] = parseMonth(month)
  const leading = (new Date(Date.UTC(year, number - 1, 1)).getUTCDay() + 6) % 7
  const days = new Date(Date.UTC(year, number, 0)).getUTCDate()
  const cells: (string | null)[] = Array(leading).fill(null)
  for (let day = 1; day <= days; day++) cells.push(`${month}-${String(day).padStart(2, '0')}`)
  while (cells.length % 7) cells.push(null)
  return cells
}
