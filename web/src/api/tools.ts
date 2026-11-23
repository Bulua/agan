import { apiClient } from '@/api/client'
import type {
  ToolCreatePayload,
  ToolItem,
  ToolListResponse,
  ToolTestResponse,
  ToolType,
  ToolUpdatePayload,
} from '@/types/capability'

export const toolsApi = {
  async list(params?: {
    tool_type?: ToolType
    is_enabled?: boolean
    search?: string
    page?: number
    page_size?: number
  }): Promise<ToolListResponse> {
    const { data } = await apiClient.get<ToolListResponse>('/tools', { params })
    return data
  },

  async create(payload: ToolCreatePayload): Promise<ToolItem> {
    const { data } = await apiClient.post<ToolItem>('/tools', payload)
    return data
  },

  async update(id: string, payload: ToolUpdatePayload): Promise<ToolItem> {
    const { data } = await apiClient.put<ToolItem>(`/tools/${id}`, payload)
    return data
  },

  async toggle(id: string): Promise<ToolItem> {
    const { data } = await apiClient.patch<ToolItem>(`/tools/${id}/toggle`)
    return data
  },

  async remove(id: string): Promise<void> {
    await apiClient.delete(`/tools/${id}`)
  },

  async test(id: string, args: Record<string, any>): Promise<ToolTestResponse> {
    const { data } = await apiClient.post<ToolTestResponse>(`/tools/${id}/test`, {
      arguments: args,
    })
    return data
  },
}
