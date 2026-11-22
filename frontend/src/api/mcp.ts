import { apiClient } from '@/api/client'
import type {
  MCPImportResponse,
  MCPServerCreatePayload,
  MCPServerItem,
  MCPServerListResponse,
  MCPServerUpdatePayload,
  MCPToolCallTestResponse,
  MCPTransport,
} from '@/types/mcp'

export const mcpApi = {
  async list(params?: {
    transport?: MCPTransport
    is_enabled?: boolean
    search?: string
    page?: number
    page_size?: number
  }): Promise<MCPServerListResponse> {
    const { data } = await apiClient.get<MCPServerListResponse>('/mcp', { params })
    return data
  },

  async get(id: string): Promise<MCPServerItem> {
    const { data } = await apiClient.get<MCPServerItem>(`/mcp/${id}`)
    return data
  },

  async create(payload: MCPServerCreatePayload): Promise<MCPServerItem> {
    const { data } = await apiClient.post<MCPServerItem>('/mcp', payload)
    return data
  },

  async update(id: string, payload: MCPServerUpdatePayload): Promise<MCPServerItem> {
    const { data } = await apiClient.put<MCPServerItem>(`/mcp/${id}`, payload)
    return data
  },

  async toggle(id: string): Promise<MCPServerItem> {
    const { data } = await apiClient.patch<MCPServerItem>(`/mcp/${id}/toggle`)
    return data
  },

  async remove(id: string): Promise<void> {
    await apiClient.delete(`/mcp/${id}`)
  },

  async refresh(id: string): Promise<MCPServerItem> {
    const { data } = await apiClient.post<MCPServerItem>(`/mcp/${id}/refresh`)
    return data
  },

  async testTool(
    serverId: string,
    toolName: string,
    args: Record<string, any>,
  ): Promise<MCPToolCallTestResponse> {
    const { data } = await apiClient.post<MCPToolCallTestResponse>(
      `/mcp/${serverId}/tools/${encodeURIComponent(toolName)}/test`,
      { arguments: args },
    )
    return data
  },

  async importJson(
    mcpServers: Record<string, Record<string, any>>,
    overwriteExisting = false,
  ): Promise<MCPImportResponse> {
    const { data } = await apiClient.post<MCPImportResponse>('/mcp/import', {
      mcpServers,
      overwrite_existing: overwriteExisting,
    })
    return data
  },
}
