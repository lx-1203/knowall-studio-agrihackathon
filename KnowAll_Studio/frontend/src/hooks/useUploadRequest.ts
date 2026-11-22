import { useEffect, useRef, useState } from 'react'
import type { AxiosProgressEvent } from 'axios'

// Only bridges native Axios progress/signal to UI. No queue, retry or transport.
export default function useUploadRequest() {
  const controller = useRef<AbortController | null>(null)
  const [active, setActive] = useState(false)
  const [percent, setPercent] = useState<number | null>(null)
  const [phase, setPhase] = useState<'idle' | 'uploading' | 'processing' | 'cancelled'>('idle')
  useEffect(() => () => { controller.current?.abort(); controller.current = null }, [])
  const cancel = () => {
    controller.current?.abort(); controller.current = null
    setActive(false); setPhase('cancelled')
  }
  const begin = () => {
    if (controller.current) return null
    const next = new AbortController(); controller.current = next
    const current = () => controller.current === next && !next.signal.aborted
    setActive(true); setPercent(null); setPhase('uploading')
    return {
      signal: next.signal, current,
      config: { signal: next.signal, onUploadProgress: (event: AxiosProgressEvent) => {
        if (!current()) return
        // Axios already bounds loaded/total and normalizes its native event.
        setPercent(event.progress === undefined ? null : Math.round(event.progress * 100))
        if (event.total && event.loaded >= event.total) setPhase('processing')
      } },
      finish: () => { if (current()) { controller.current = null; setActive(false); setPhase('idle') } },
    }
  }
  return { active, percent, phase, begin, cancel }
}
