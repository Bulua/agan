import type { PaginatedResponse } from '@/types/common'

export type AgentRole = 'general' | 'router' | 'web' | 'pwn' | 'crypto' | 'reverse' | 'misc'
export type AgentLoopEngine = 'direct_chat' | 'react' | 'plan_execute' | 'reflexion'

export interface AgentToolBrief {
  id: string
  name: string
  display_name: string
  tool_type: string
  is_enabled: boolean
}

export interface AgentMcpBrief {
  id: string
  name: string
  transport: string
  is_enabled: boolean
  last_status: string
  tool_count: number
}

export interface AgentSkillBrief {
  name: string
  title: string
  description: string
  file_count: number
  exists: boolean
}

export interface AgentGuardrails {
  max_steps: number
  step_timeout_sec: number
  token_budget: number
  cost_budget_usd: number
  repeat_threshold: number
}

export interface AgentItem {
  id: string
  name: string
  role: AgentRole
  description: string
  chat_model_id: string | null
  chat_model_name: string
  loop_engine: AgentLoopEngine | string
  system_prompt: string
  temperature: number | null
  sandbox_mode: 'simulated' | 'docker' | string
  sandbox_image: string
  tool_ids: string[]
  tools: AgentToolBrief[]
  mcp_server_ids: string[]
  mcp_servers: AgentMcpBrief[]
  mcp_tool_filters?: Record<string, string[] | null>
  skill_names?: string[]
  skills?: AgentSkillBrief[]
  guardrail_config: Record<string, any>
  memory_config: Record<string, any>
  is_active: boolean
  is_default: boolean
  created_at: string
  updated_at: string
}

export interface AgentCreatePayload {
  name: string
  role: AgentRole
  description: string
  chat_model_id: string
  loop_engine: AgentLoopEngine
  system_prompt: string
  temperature: number | null
  sandbox_mode: 'simulated' | 'docker'
  sandbox_image: string
  tool_ids: string[]
  mcp_server_ids?: string[]
  mcp_tool_filters?: Record<string, string[] | null>
  skill_names?: string[]
  guardrail_config: AgentGuardrails
  is_active: boolean
  is_default: boolean
}

export type AgentUpdatePayload = Partial<AgentCreatePayload>

export interface AgentListResponse extends PaginatedResponse<AgentItem> {
  role_counts: Record<string, number>
}
