import { useEffect, useRef, useState } from 'react'

export default function MaterialRichTextFrame({ html, onFailure }: { html: string; onFailure: () => void }) {
  const frame = useRef<HTMLIFrameElement>(null)
  const observer = useRef<ResizeObserver | null>(null)
  const active = useRef(true)
  const [height, setHeight] = useState(120)
  useEffect(() => { active.current = true; return () => { active.current = false; observer.current?.disconnect() } }, [])
  const measure = () => {
    if (!active.current) return
    try {
      const doc = frame.current?.contentDocument
      if (!doc?.body) { onFailure(); return }
      const next = Math.ceil(doc.body.getBoundingClientRect().height) + 16
      if (!Number.isFinite(next) || next <= 0 || next > 60000) { onFailure(); return }
      setHeight(next)
    } catch { onFailure() }
  }
  return <iframe ref={frame} title="资料排版正文" sandbox="allow-same-origin" srcDoc={html}
    style={{ border: 0, width: '100%', height, display: 'block' }} onError={onFailure} onLoad={() => {
      measure()
      const doc = frame.current?.contentDocument
      if (doc?.body) {
        observer.current?.disconnect(); observer.current = new ResizeObserver(measure); observer.current.observe(doc.body)
        void doc.fonts.ready.then(measure).catch(onFailure)
      }
    }} />
}
