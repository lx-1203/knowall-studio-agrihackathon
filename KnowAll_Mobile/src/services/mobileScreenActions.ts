import type { AxiosRequestConfig } from 'axios'
import { Platform } from 'react-native'
import type { DocumentPickerAsset } from 'expo-document-picker'
import api from '../api'

// A missing response must not silently replay an upload, review or goal toggle.
export const noReplay: AxiosRequestConfig & { _retryDisabled: boolean } = { _retryDisabled: true }
// Secondary activity tracking must never replay the already saved learning action.
export async function recordToolActivity(activity_type: string, count = 1) {
  try { await api.post('/gamification/record-activity', { activity_type, count, duration_minutes: 0 }, noReplay) }
  catch { return false }
  return true
}
export async function uploadPickedFile(file: DocumentPickerAsset, onProgress: (progress: number) => void) {
  const form = new FormData()
  if (Platform.OS === 'web') {
    const blob = file.file || await fetch(file.uri).then(r => { if (!r.ok) throw new Error('无法读取文件，请重新选择'); return r.blob() })
    if (!blob) throw new Error('无法读取文件，请重新选择')
    form.append('file', blob, file.name)
  } else {
    form.append('file', { uri: file.uri, name: file.name, type: file.mimeType || 'application/octet-stream' } as any)
  }
  const result = await api.post('/documents/upload', form, {
    ...noReplay, headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: event => onProgress(event.total ? Math.min(1, event.loaded / event.total) : 0),
  })
  return result.data
}
export { screenError } from '../userFacingError'
