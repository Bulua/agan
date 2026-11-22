import { apiClient } from '@/api/client'
import type {
  AgentCreatePayload,
  AgentItem,
  AgentListResponse,
  AgentRole,
  AgentUpdatePayload,
} from '@/types/agent'

export const agentsApi = {
  async list(params?: {
    role?: AgentRole
    is_active?: boolean
    search?: string
    page?: number
    page_size?: number
  }): Promise<AgentListResponse> {
    const { data } = await apiClient.get<AgentListResponse>('/agents', { params })
    return data
  },

  async get(id: string): Promise<AgentItem> {
    const { data } = await apiClient.get<AgentItem>(`/agents/${id}`)
    return data
  },

  async create(payload: AgentCreatePayload): Promise<AgentItem> {
    const { data } = await apiClient.post<AgentItem>('/agents', payload)
    return data
  },

  async update(id: string, payload: AgentUpdatePayload): Promise<AgentItem> {
    const { data } = await apiClient.put<AgentItem>(`/agents/${id}`, payload)
    return data
  },

  async toggle(id: string): Promise<AgentItem> {
    const { data } = await apiClient.patch<AgentItem>(`/agents/${id}/toggle`)
    return data
  },

  async setDefault(id: string): Promise<AgentItem> {
    const { data } = await apiClient.patch<AgentItem>(`/agents/${id}/default`)
    return data
  },

  async clone(id: string): Promise<AgentItem> {
    const { data } = await apiClient.post<AgentItem>(`/agents/${id}/clone`)
    return data
  },

  async remove(id: string): Promise<void> {
    await apiClient.delete(`/agents/${id}`)
  },
}
