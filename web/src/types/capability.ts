import type { PaginatedResponse } from '@/types/common'

export type ToolType = 'builtin' | 'custom_http' | 'custom_python'

export interface ToolItem {
  id: string
  name: string
  display_name: string
  description: string
  tool_type: ToolType
  category: string
  parameters_schema: Record<string, any>
  implementation_config: Record<string, any>
  is_enabled: boolean
  requires_sandbox: boolean
  is_builtin: boolean
  created_at: string
  updated_at: string
}

export interface ToolCreatePayload {
  name: string
  display_name: string
  description: string
  tool_type: ToolType
  category: string
  parameters_schema: Record<string, any>
  implementation_config: Record<string, any>
  is_enabled: boolean
  requires_sandbox: boolean
}

export type ToolUpdatePayload = Partial<Omit<ToolCreatePayload, 'name' | 'tool_type'>>

export interface ToolListResponse extends PaginatedResponse<ToolItem> {
  type_counts: {
    all: number
    builtin: number
    custom_http: number
    custom_python: number
    enabled: number
  }
}

export interface ToolTestResponse {
  success: boolean
  output: string
  error: string | null
  metadata: Record<string, any>
}
