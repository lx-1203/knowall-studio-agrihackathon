import { useMemo, useState } from 'react'
import { View } from 'react-native'
import { Text, useTheme } from 'react-native-paper'
import { materialTextHtml, renderMaterialText, richTextNeeded } from '../materialRichText'
import MaterialRichTextFrame from './MaterialRichTextFrame'

export default function MaterialRichText({ text, fontSize, raw }: { text: string; fontSize: number; raw: boolean }) {
  const theme = useTheme()
  const [failed, setFailed] = useState(false)
  const rich = useMemo(() => raw || !richTextNeeded(text) ? null : renderMaterialText(text), [text, raw])
  const html = useMemo(() => rich?.html ? materialTextHtml(rich.html, { color: theme.colors.onSurface, background: theme.colors.surface,
    muted: theme.colors.onSurfaceVariant, border: theme.colors.outlineVariant, fontSize }) : '', [rich, theme, fontSize])
  const fallback = failed ? '排版显示未完成，已显示完整原始文字。' : rich?.fallback
  return <View style={{ gap: 12 }}>
    {!!fallback && <Text accessibilityLiveRegion="polite" style={{ color: theme.colors.onSurfaceVariant }}>{fallback}</Text>}
    {rich && rich.errors > 0 && !raw && !fallback && <Text style={{ color: theme.colors.onSurfaceVariant }}>有 {rich.errors} 处公式未能排版，已保留原式；可切换原始文字查看。</Text>}
    {html && !raw && !fallback ? <MaterialRichTextFrame key={`${fontSize}:${theme.dark}`} html={html} onFailure={() => setFailed(true)} />
      : <Text selectable style={{ fontSize, lineHeight: fontSize * 1.9, color: theme.colors.onSurface }}>{text}</Text>}
  </View>
}
