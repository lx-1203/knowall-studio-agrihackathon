// Lightweight math/LaTeX text renderer for React Native
// Strips common LaTeX delimiters and renders as readable text
// For full LaTeX rendering, use react-native-webview with KaTeX in production
import React from 'react'
import { Text, type TextProps } from 'react-native'

interface MathTextProps extends TextProps {
  children: string
}

// Common LaTeX patterns to convert to readable Unicode
const LATEX_REPLACEMENTS: [RegExp, string][] = [
  [/\\times/g, '×'],
  [/\\div/g, '÷'],
  [/\\pm/g, '±'],
  [/\\cdot/g, '·'],
  [/\\leq/g, '≤'],
  [/\\geq/g, '≥'],
  [/\\neq/g, '≠'],
  [/\\approx/g, '≈'],
  [/\\infty/g, '∞'],
  [/\\alpha/g, 'α'],
  [/\\beta/g, 'β'],
  [/\\gamma/g, 'γ'],
  [/\\delta/g, 'δ'],
  [/\\theta/g, 'θ'],
  [/\\lambda/g, 'λ'],
  [/\\mu/g, 'μ'],
  [/\\pi/g, 'π'],
  [/\\sigma/g, 'σ'],
  [/\\omega/g, 'ω'],
  [/\\sum/g, 'Σ'],
  [/\\prod/g, 'Π'],
  [/\\int/g, '∫'],
  [/\\sqrt\{([^}]+)\}/g, '√($1)'],
  [/\\frac\{([^}]+)\}\{([^}]+)\}/g, '($1)/($2)'],
  [/\\text\{([^}]+)\}/g, '$1'],
  [/\\mathbf\{([^}]+)\}/g, '$1'],
  [/\^\{([^}]+)\}/g, '^($1)'],
  [/_\{([^}]+)\}/g, '_($1)'],
  [/\$\$?/g, ''],
  [/\\\[|\\\]/g, ''],
  [/\\begin\{[^}]*\}/g, ''],
  [/\\end\{[^}]*\}/g, ''],
]

function convertLaTeX(text: string): string {
  let result = text
  for (const [pattern, replacement] of LATEX_REPLACEMENTS) {
    result = result.replace(pattern, replacement)
  }
  return result
}

export default function MathText({ children, style, ...props }: MathTextProps) {
  const converted = convertLaTeX(children)
  return (
    <Text style={style} {...props}>
      {converted}
    </Text>
  )
}
