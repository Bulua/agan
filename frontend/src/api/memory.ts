import { apiClient } from '@/api/client'
import type {
  MemoryFileContent,
  MemoryScopesOverview,
  MemoryScopeType,
  MemorySearchResponse,
} from '@/types/memory'

export const memoryApi = {
  async listScopes(): Promise<MemoryScopesOverview> {
    const { data } = await apiClient.get<MemoryScopesOverview>('/memories/scopes')
    return data
  },

  async readFile(
    scope: MemoryScopeType,
    scopeId: string,
    path: string,
  ): Promise<MemoryFileContent> {
    const { data } = await apiClient.get<MemoryFileContent>('/memories/file', {
      params: { scope, scope_id: scopeId, path },
    })
    return data
  },

  async saveFile(payload: {
    scope: MemoryScopeType
    scope_id?: string
    path: string
    content: string
    title?: string
    memory_type?: string
    tags?: string[]
    raw_mode?: boolean
  }): Promise<MemoryFileContent> {
    const { data } = await apiClient.put<MemoryFileContent>('/memories/file', payload)
    return data
  },

  async deleteFile(scope: MemoryScopeType, scopeId: string, path: string): Promise<void> {
    await apiClient.delete('/memories/file', {
      params: { scope, scope_id: scopeId, path },
    })
  },

  async search(params: {
    q: string
    scope?: 'all' | MemoryScopeType
    scope_id?: string
    limit?: number
  }): Promise<MemorySearchResponse> {
    const { data } = await apiClient.get<MemorySearchResponse>('/memories/search', { params })
    return data
  },
}
