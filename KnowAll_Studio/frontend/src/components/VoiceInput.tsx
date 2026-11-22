import { useState, useRef, useCallback, useEffect } from 'react'
import { Button, Tooltip, Tag, Space } from 'antd'
import { AudioOutlined, AudioMutedOutlined, LoadingOutlined, SoundOutlined, PauseOutlined } from '@ant-design/icons'
import { useVoice } from '../hooks/useVoice'

interface VoiceInputProps {
  onResult: (text: string, isFinal: boolean) => void
  onCommand?: (command: string) => void  // voice commands like "下一空"
  /** 要朗读的文本（传入后显示朗读按钮） */
  readAloud?: string
  /** 使用后端 Whisper 模式（更高精度，需录音） vs 浏览器实时模式 */
  mode?: 'browser' | 'server'
  disabled?: boolean
  lang?: string
  ttsVoice?: string
  /** 仅显示图标（无文字标签） */
  compact?: boolean
}

type VoiceState = 'idle' | 'listening' | 'recognizing' | 'error' | 'unsupported'

/**
 * 语音输入组件
 *
 * 两种模式：
 * - browser（默认）：使用浏览器 Web Speech API，实时识别，延迟低
 * - server：使用后端 faster-whisper，录音后一次性识别，精度更高
 *
 * 额外功能：
 * - readAloud 传入文本后显示朗读按钮（使用 edge-tts）
 */
export default function VoiceInput({
  onResult, onCommand, readAloud, mode = 'browser', disabled, lang = 'zh-CN', ttsVoice = 'xiaoxiao', compact,
}: VoiceInputProps) {
  const [state, setState] = useState<VoiceState>('idle')
  const [interimText, setInterimText] = useState('')
  const recognitionRef = useRef<any>(null)
  const finalTranscriptRef = useRef('')

  // 后端语音 Hook（server 模式使用）
  const serverVoice = useVoice({
    language: lang.split('-')[0] || 'zh',
    modelSize: 'base',
    ttsVoice,
    onTranscription: (text) => {
      if (text) onResult(text, true)
    },
    onError: (err) => console.warn('Voice error:', err),
  })

  // ===== 浏览器模式 =====
  const isSupported = mode === 'server' || !!(window as any).SpeechRecognition || !!(window as any).webkitSpeechRecognition

  useEffect(() => {
    if (mode === 'browser' && !isSupported) {
      setState('unsupported')
      return
    }
    return () => {
      if (recognitionRef.current) recognitionRef.current.abort()
    }
  }, [isSupported, mode])

  const startListening = useCallback(() => {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
    if (!SpeechRecognition) return

    const recognition = new SpeechRecognition()
    recognition.lang = lang
    recognition.interimResults = true
    recognition.continuous = true
    recognition.maxAlternatives = 1

    recognition.onstart = () => { setState('listening'); setInterimText(''); finalTranscriptRef.current = '' }
    recognition.onspeechstart = () => { setState('recognizing') }

    recognition.onresult = (event: any) => {
      let interim = '', final = ''
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i]
        if (result.isFinal) final += result[0].transcript
        else interim += result[0].transcript
      }
      if (interim) { setInterimText(interim); onResult(interim, false) }
      if (final) {
        finalTranscriptRef.current += final
        const cmd = final.toLowerCase().trim()
        if (['下一空', '下一个', 'next', '下一题'].includes(cmd) && onCommand) {
          onCommand(cmd); setInterimText(''); return
        }
        onResult(finalTranscriptRef.current, true)
        setInterimText('')
      }
    }

    recognition.onerror = (event: any) => {
      if (event.error === 'not-allowed') setState('error')
      else if (event.error === 'no-speech' || event.error === 'network') setState('idle')
    }
    recognition.onend = () => { setState('idle'); setInterimText('') }
    recognitionRef.current = recognition
    try { recognition.start() } catch {}
  }, [lang, onResult, onCommand])

  const stopListening = useCallback(() => {
    if (recognitionRef.current) recognitionRef.current.stop()
    setState('idle'); setInterimText('')
  }, [])

  // ===== 统一点击处理 =====
  const handleClick = async () => {
    if (mode === 'server') {
      if (serverVoice.isRecording) {
        await serverVoice.recordAndTranscribe()
      } else {
        await serverVoice.startRecording()
      }
    } else {
      const isActive = state === 'listening' || state === 'recognizing'
      if (isActive) stopListening()
      else startListening()
    }
  }

  // ===== 状态计算 =====
  const isBrowserActive = mode === 'browser' && (state === 'listening' || state === 'recognizing')
  const isServerActive = mode === 'server' && serverVoice.isRecording
  const isActive = isBrowserActive || isServerActive
  const isLoading = mode === 'server' && serverVoice.isTranscribing

  if (!isSupported) {
    return (
      <Tooltip title="您的浏览器不支持语音识别，请使用 Chrome 或 Edge">
        <Button size="small" icon={<AudioMutedOutlined />} disabled>语音不可用</Button>
      </Tooltip>
    )
  }

  return (
    <Space size={4} align="center">
      {/* 录音按钮 */}
      <Tooltip title={
        isLoading ? '识别中...'
          : isActive ? '点击停止'
            : mode === 'server' ? '语音输入 (高精度)' : '点击开始语音输入'
      }>
        <Button
          size="small"
          type={isActive ? 'primary' : 'default'}
          danger={isActive}
          icon={isLoading ? <LoadingOutlined spin /> : isActive ? <AudioMutedOutlined /> : <AudioOutlined />}
          onClick={handleClick}
          disabled={disabled || state === 'error' || state === 'unsupported'}
          loading={isLoading}
        />
      </Tooltip>

      {/* 录音状态标签 */}
      {isActive && mode === 'browser' && (
        <Tag color="red" style={{ fontSize: 11, margin: 0 }}>
          {state === 'listening' ? '监听中' : '识别中'}
        </Tag>
      )}
      {isServerActive && (
        <Tag color="red" style={{ fontSize: 11, margin: 0, animation: 'pulse 1.5s infinite' }}>
          录音中
        </Tag>
      )}

      {/* 中间文本 */}
      {interimText && (
        <span style={{ color: '#999', fontSize: 12, fontStyle: 'italic', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {interimText}
        </span>
      )}

      {/* 朗读按钮 (TTS) */}
      {readAloud && (
        <Tooltip title={serverVoice.isSpeaking ? '停止朗读' : '朗读内容'}>
          <Button
            size="small"
            type={serverVoice.isSpeaking ? 'primary' : 'default'}
            icon={serverVoice.isSpeaking ? <PauseOutlined /> : <SoundOutlined />}
            onClick={() => serverVoice.isSpeaking ? serverVoice.stopSpeaking() : serverVoice.speak(readAloud)}
          />
        </Tooltip>
      )}

      <style>{`@keyframes pulse { 0%,100%{transform:scale(1)} 50%{transform:scale(1.05)} }`}</style>
    </Space>
  )
}
