import type { PaginatedResponse } from '@/types/common'

export interface ChatToolCallRecord {
  id: string
  tool_name: string
  arguments: Record<string, any>
  status: 'running' | 'done' | string
  success: boolean
  output: string
  duration_ms?: number
  metadata?: Record<string, any>
}

export interface ChatMessage {
  id: string
  conversation_id: string
  role: 'user' | 'assistant' | 'system' | string
  content: string
  reasoning_content: string
  tool_calls_json: ChatToolCallRecord[]
  model_id_used: string
  prompt_tokens: number
  completion_tokens: number
  cost_usd: number
  latency_ms: number
  created_at: string
  streaming?: boolean
  started_at_ms?: number
}

export interface ChatConversationSummary {
  id: string
  title: string
  agent_id: string | null
  agent_name_snapshot: string
  model_id: string | null
  model_name_snapshot: string
  system_prompt: string
  temperature: number | null
  message_count: number
  total_tokens: number
  total_cost_usd: number
  is_running?: boolean
  created_at: string
  updated_at: string
}

export interface ChatConversationDetail extends ChatConversationSummary {
  messages: ChatMessage[]
  context_tokens_estimate?: number
  context_window?: number
  has_compacted_summary?: boolean
}

export interface CompactChatResponse {
  compacted: boolean
  stage: string
  tokens_before: number
  tokens_after: number
  tokens_saved: number
  summarized_messages: number
  summary: string
  context_tokens_estimate: number
  context_window: number
  compact_message: ChatMessage | null
}

export type ChatConversationListResponse = PaginatedResponse<ChatConversationSummary>
