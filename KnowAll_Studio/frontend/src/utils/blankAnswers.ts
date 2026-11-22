/** Keep literal delimiters inside their blank while supporting saved legacy text. */
function parseBlankAnswers(value: string): string[] | undefined {
  try {
    const parts: unknown = JSON.parse(value)
    if (Array.isArray(parts) && parts.every(v => typeof v === 'string')) return parts
  } catch { /* Saved answers can use the legacy semicolon format. */ }
}

export function readBlankAnswers(value: string, count: number): string[] {
  return parseBlankAnswers(value)?.slice(0, count) ?? (count === 1 ? [value] : value.split(';'))
}

export function writeBlankAnswers(parts: string[]): string {
  if (!parts.some(v => v.trim())) return ''
  return parts.some(v => /[;；,，\n]/.test(v)) || /^\s*\[/.test(parts[0])
    ? JSON.stringify(parts) : parts.join(';')
}

export function displayBlankAnswer(value: string): string {
  return parseBlankAnswers(value)?.join('；') ?? value
}
