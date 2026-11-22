export function materialLabel(filename: string) {
  return filename.trim().replace(/\.(?:pdf|md|txt|docx?|pptx?|xlsx?|png|jpe?g|webp)$/i, '') || '未命名资料'
}

export function chapterLabel(text: string, index: number) {
  let fence = ''
  for (const line of text.split('\n').slice(0, 80)) {
    const marker = line.trim().match(/^(`{3,}|~{3,})/)
    if (marker) {
      if (!fence) fence = marker[1][0]
      else if (fence === marker[1][0]) fence = ''
      continue
    }
    if (fence) continue
    const heading = line.trim().match(/^#{1,6}\s+(.+?)(?:\s+#+)?$/)?.[1]
      || line.trim().match(/^(第[一二三四五六七八九十百零\d]+[章节篇].{0,60})$/)?.[1]
    if (heading) return heading.replace(/[*`]/g, '').trim()
  }
  return `第 ${index + 1} 段`
}
