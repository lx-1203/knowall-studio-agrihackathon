import { useState, useRef, useCallback, useEffect } from 'react'
import api from '../api'

interface UseVoiceOptions {
  language?: string
  modelSize?: string
  ttsVoice?: string
  onTranscription?: (text: string) => void
  onError?: (error: string) => void
}

/**
 * 语音录制和播放 Hook
 *
 * 提供：
 * - startRecording() / stopRecording() — 录音控制
 * - transcribe(blob?) — 调用 STT API 转文字
 * - speak(text) — 调用 TTS API 播放语音
 * - isRecording / isSpeaking / isTranscribing 状态
 */
export function useVoice(options: UseVoiceOptions = {}) {
  const {
    language = 'zh',
    modelSize = 'base',
    ttsVoice = 'xiaoxiao',
    onTranscription,
    onError,
  } = options

  const [isRecording, setIsRecording] = useState(false)
  const [isSpeaking, setIsSpeaking] = useState(false)
  const [isTranscribing, setIsTranscribing] = useState(false)
  const [audioLevel, setAudioLevel] = useState(0)

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const audioCtxRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const animFrameRef = useRef<number>(0)
  const currentAudioRef = useRef<HTMLAudioElement | null>(null)

  // ===== 录音 =====
  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mediaRecorder = new MediaRecorder(stream, {
        mimeType: MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
          ? 'audio/webm;codecs=opus'
          : 'audio/webm',
      })

      chunksRef.current = []
      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }

      // 音频可视化
      const audioCtx = new AudioContext()
      const analyser = audioCtx.createAnalyser()
      analyser.fftSize = 256
      const source = audioCtx.createMediaStreamSource(stream)
      source.connect(analyser)
      audioCtxRef.current = audioCtx
      analyserRef.current = analyser

      const updateLevel = () => {
        if (!analyserRef.current) return
        const data = new Uint8Array(analyserRef.current.frequencyBinCount)
        analyserRef.current.getByteFrequencyData(data)
        const avg = data.reduce((a, b) => a + b, 0) / data.length
        setAudioLevel(avg / 255)
        animFrameRef.current = requestAnimationFrame(updateLevel)
      }
      updateLevel()

      mediaRecorder.start(100) // 每100ms收集一次数据
      mediaRecorderRef.current = mediaRecorder
      setIsRecording(true)
    } catch (err: any) {
      onError?.('无法访问麦克风: ' + (err.message || '请检查权限'))
    }
  }, [onError])

  const stopRecording = useCallback((): Promise<Blob | null> => {
    return new Promise((resolve) => {
      const recorder = mediaRecorderRef.current
      if (!recorder || recorder.state === 'inactive') {
        resolve(null)
        return
      }

      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
        chunksRef.current = []
        resolve(blob)
      }

      recorder.stop()
      recorder.stream.getTracks().forEach(t => t.stop())
      setIsRecording(false)
      setAudioLevel(0)
      cancelAnimationFrame(animFrameRef.current)

      if (audioCtxRef.current) {
        audioCtxRef.current.close().catch(() => {})
        audioCtxRef.current = null
      }
      analyserRef.current = null
    })
  }, [])

  // ===== 语音识别 (STT) =====
  const transcribe = useCallback(async (audioBlob?: Blob): Promise<string> => {
    const blob = audioBlob || (await stopRecording())
    if (!blob || blob.size === 0) return ''

    setIsTranscribing(true)
    try {
      const formData = new FormData()
      formData.append('audio', blob, 'recording.webm')
      formData.append('language', language)
      formData.append('model_size', modelSize)

      const response = await api.post('/voice/stt', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 120000, // 语音识别可能较慢
      })

      const text = response.data?.text || ''
      onTranscription?.(text)
      return text
    } catch (err: any) {
      const msg = err.response?.data?.detail || '语音识别失败'
      onError?.(msg)
      return ''
    } finally {
      setIsTranscribing(false)
    }
  }, [language, modelSize, stopRecording, onTranscription, onError])

  // ===== 录音并识别（一键操作） =====
  const recordAndTranscribe = useCallback(async (): Promise<string> => {
    const blob = await stopRecording()
    if (!blob) return ''
    return transcribe(blob)
  }, [stopRecording, transcribe])

  // ===== 语音合成 (TTS) =====
  const speak = useCallback(async (text: string, voice?: string) => {
    if (!text.trim()) return

    // 停止当前播放
    stopSpeaking()

    setIsSpeaking(true)
    try {
      const response = await api.post('/voice/tts', {
        text,
        voice: voice || ttsVoice,
      }, {
        responseType: 'blob',
        timeout: 30000,
      })

      const audioBlob = response.data as Blob
      const audioUrl = URL.createObjectURL(audioBlob)
      const audio = new Audio(audioUrl)
      currentAudioRef.current = audio

      audio.onended = () => {
        setIsSpeaking(false)
        URL.revokeObjectURL(audioUrl)
        currentAudioRef.current = null
      }
      audio.onerror = () => {
        setIsSpeaking(false)
        URL.revokeObjectURL(audioUrl)
        currentAudioRef.current = null
      }

      await audio.play()
    } catch (err: any) {
      setIsSpeaking(false)
      onError?.('语音合成失败: ' + (err.response?.data?.detail || err.message))
    }
  }, [ttsVoice, onError])

  const stopSpeaking = useCallback(() => {
    if (currentAudioRef.current) {
      currentAudioRef.current.pause()
      currentAudioRef.current = null
      setIsSpeaking(false)
    }
  }, [])

  // Release media resources when the component unmounts / the hook is torn
  // down. Prevents the microphone, AudioContext, rAF loop, and any in-flight
  // TTS playback from leaking after navigating away from a recording UI.
  useEffect(() => {
    return () => {
      const recorder = mediaRecorderRef.current
      if (recorder && recorder.state !== 'inactive') {
        try { recorder.stream.getTracks().forEach(t => t.stop()) } catch {}
        try { recorder.stop() } catch {}
      }
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current)
      if (audioCtxRef.current) { audioCtxRef.current.close().catch(() => {}) }
      audioCtxRef.current = null
      analyserRef.current = null
      mediaRecorderRef.current = null
      if (currentAudioRef.current) {
        currentAudioRef.current.pause()
        currentAudioRef.current = null
      }
    }
  }, [])

  return {
    isRecording,
    isSpeaking,
    isTranscribing,
    audioLevel,
    startRecording,
    stopRecording,
    transcribe,
    recordAndTranscribe,
    speak,
    stopSpeaking,
  }
}
