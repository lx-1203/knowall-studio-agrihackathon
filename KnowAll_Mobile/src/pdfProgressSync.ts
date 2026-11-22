import type { PdfStatePayload, StatePayload } from './materialStateSync'
const pageOK = (page: unknown): page is number => Number.isSafeInteger(page) && Number(page) >= 1
const zoomOK = (zoom: unknown): zoom is number => typeof zoom === 'number' && Number.isFinite(zoom) && zoom >= .5 && zoom <= 3
export function pdfLocalSource(raw:string):string|null {
  const value=JSON.parse(raw),version=value?.source_version
  if(version===undefined)return null
  if(typeof version!=='string'||!/^[a-f0-9]{64}$/.test(version))throw Error('Invalid local PDF source')
  return version
}
export function pdfStateCodec(platform: 'mobile' | 'desktop') {
  const decode = (raw: string): PdfStatePayload => {
    const value = JSON.parse(raw)
    const page = typeof value === 'number' && platform === 'desktop' ? value : value?.page
    const zoom = typeof value === 'number' && platform === 'desktop' ? 1 : value?.sync_zoom ?? value?.zoom
    if (!pageOK(page) || !zoomOK(zoom)) throw Error('Invalid PDF position')
    return { page_number: page, zoom, zoom_basis: 'fit_width' }
  }
  const encode = (payload: StatePayload, _previous: string | null): string => {
    if (!('page_number' in payload) || !pageOK(payload.page_number) || !zoomOK(payload.zoom) || payload.zoom_basis !== 'fit_width') throw Error('Invalid PDF state')
    const zoom = platform === 'mobile' ? [1,1.5,2].reduce((best, value) => Math.abs(value-payload.zoom)<Math.abs(best-payload.zoom)?value:best) : payload.zoom
    return JSON.stringify({ page:payload.page_number, zoom, ...(platform === 'mobile' ? {sync_zoom:payload.zoom} : {}) })
  }
  return {decode,encode}
}
// Navigation at the projected scale must not rewrite another device's precise scale.
export function preservePdfZoom(raw: string, previous: string | null): string {
  const next = JSON.parse(raw)
  let old = null
  try { old = previous === null ? null : JSON.parse(previous) } catch { return raw }
  // An offline edit cannot rebind an old source until the bridge has actual new render proof.
  if (old && old.source_version !== undefined && next.source_version === undefined) next.source_version = old.source_version
  if (old && zoomOK(old.sync_zoom) && old.zoom === next.zoom) return JSON.stringify({...next,sync_zoom:old.sync_zoom})
  return JSON.stringify(next)
}
