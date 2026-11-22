import type { PaginatedResponse } from '@/types/common'

export type ModelType = 'chat' | 'embedding' | 'rerank'

export interface LLMModel {
  id: string
  name: string
  model_type: ModelType
  provider: string
  base_url: string
  model_id: string
  description: string
  context_window: number
  max_output_tokens: number
  temperature: number
  input_price_per_1m: number
  output_price_per_1m: number
  extra_params: Record<string, unknown>
  is_active: boolean
  is_default: boolean
  api_key_masked: string
  has_api_key: boolean
  last_test_status: 'ok' | 'error' | 'untested' | string
  last_test_latency_ms: number | null
  last_test_message: string
  last_tested_at: string | null
  created_at: string
  updated_at: string
}

export interface LLMModelCreatePayload {
  name: string
  model_type: ModelType
  provider: string
  base_url: string
  api_key: string
  model_id: string
  description?: string
  context_window: number
  max_output_tokens: number
  temperature: number
  input_price_per_1m: number
  output_price_per_1m: number
  extra_params: Record<string, unknown>
  is_active: boolean
  is_default: boolean
}

export type LLMModelUpdatePayload = Partial<LLMModelCreatePayload>

export interface LLMModelTestPayload {
  prompt?: string
  query?: string
  documents?: string[]
}

export interface LLMModelTestResponse {
  ok: boolean
  model_type: ModelType
  latency_ms: number
  detail: string
  preview: Record<string, any>
  usage: Record<string, any>
}

export interface LLMModelListResponse extends PaginatedResponse<LLMModel> {
  category_counts: {
    all: number
    chat: number
    embedding: number
    rerank: number
  }
}

export interface LLMUsageSummary {
  total_calls: number
  total_prompt_tokens: number
  total_completion_tokens: number
  total_tokens: number
  total_cost_usd: number
  by_model: Array<{
    model_id: string
    model_type: string
    calls: number
    total_tokens: number
    cost_usd: number
  }>
}
