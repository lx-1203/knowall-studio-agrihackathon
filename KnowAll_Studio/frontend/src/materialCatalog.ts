import { listDocuments } from './api'
import type { Material } from './materials'

export async function loadMaterialCatalog(cancelled: () => boolean = () => false): Promise<Material[]> {
  const all: Material[] = []
  for (let offset = 0; !cancelled(); offset += 200) {
    const result = await listDocuments(200, offset)
    const batch: Material[] = Array.isArray(result) ? result : result?.items || result?.documents || []
    all.push(...batch)
    if (batch.length < 200) break
  }
  return all
}
