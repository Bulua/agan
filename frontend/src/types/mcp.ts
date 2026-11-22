import type { PaginatedResponse } from '@/types/common'

export type MCPTransport = 'stdio' | 'streamable_http' | 'sse'

export interface MCPDiscoveredTool {
  name: string
  description: string
  inputSchema: Record<string, any>
}

export interface MCPServerItem {
  id: string
  name: string
  description: string
  transport: MCPTransport
  command: string
  args: string[]
  env_masked: Record<string, string>
  has_env: boolean
  url: string
  headers_masked: Record<string, string>
  has_headers: boolean
  timeout_sec: number
  is_enabled: boolean
  last_status: 'unknown' | 'connected' | 'error' | string
  last_error: string
  last_latency_ms: number | null
  last_synced_at: string | null
  discovered_tools: MCPDiscoveredTool[]
  tool_count: number
  created_at: string
  updated_at: string
}

export interface MCPServerCreatePayload {
  name: string
  description?: string
  transport: MCPTransport
  command?: string
  args?: string[]
  env?: Record<string, string>
  url?: string
  headers?: Record<string, string>
  timeout_sec?: number
  is_enabled?: boolean
}

export type MCPServerUpdatePayload = Partial<MCPServerCreatePayload>

export interface MCPServerListResponse extends PaginatedResponse<MCPServerItem> {
  transport_counts: {
    all: number
    stdio: number
    streamable_http: number
    sse: number
    enabled: number
  }
}

export interface MCPToolCallTestResponse {
  success: boolean
  output: string
  error: string | null
  metadata: Record<string, any>
}

export interface MCPImportResponse {
  created: number
  updated: number
  skipped: number
  items: MCPServerItem[]
}
