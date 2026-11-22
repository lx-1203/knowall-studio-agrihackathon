import { lazy, Suspense } from 'react'
import ErrorBoundary from './ErrorBoundary'
import './previews/markdown-preview.css'

const MarkdownContent = lazy(() => import('./MarkdownContent'))

/** Keep questions and answer controls readable while the native renderer loads. */
export default function LearningContent({ text }: { text: string }) {
  const original = <div className="learning-content-fallback">{text}</div>
  return <ErrorBoundary key={text} fallback={original}>
    <Suspense fallback={original}><MarkdownContent text={text} learning /></Suspense>
  </ErrorBoundary>
}
