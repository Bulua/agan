import type { PaginatedResponse } from '@/types/common'

export type SessionStatus = 'pending' | 'running' | 'paused' | 'solved' | 'failed' | 'stopped'
export type SandboxMode = 'docker' | 'simulated'
export type LoopEngineType = 'react' | 'plan_execute' | 'reflexion'

export interface GuardrailConfigDTO {
  max_steps: number
  step_timeout_sec: number
  token_budget: number
  cost_budget_usd: number
  repeat_threshold: number
}

export interface ScratchpadDTO {
  known_clues: string[]
  endpoints_ports: string[]
  vulnerabilities: string[]
  ruled_out: string[]
  notes_md: string
}

export interface WriteupDTO {
  id: string
  session_id: string
  title: string
  content_md: string
  model_used: string
  created_at: string
  updated_at: string
}

export interface SessionSummary {
  id: string
  title: string
  category: string
  chat_model_id: string | null
  model_name_snapshot: string
  loop_engine: string
  sandbox_mode: SandboxMode
  sandbox_image: string
  status: SessionStatus
  current_step: number
  max_steps: number
  total_prompt_tokens: number
  total_completion_tokens: number
  total_tokens: number
  total_cost_usd: number
  detected_flag: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
  is_live: boolean
}

export interface SessionDetail extends SessionSummary {
  description: string
  target_info: Record<string, any>
  attachments: string[]
  flag_regex: string
  sandbox_container_id: string | null
  system_prompt_override: string
  guardrail_config: Record<string, any>
  summary: string
  error_message: string
  scratchpad: ScratchpadDTO
  writeup: WriteupDTO | null
}

export interface SessionListResponse extends PaginatedResponse<SessionSummary> {
  status_counts: Record<string, number>
}

export interface SessionCreatePayload {
  title: string
  category: string
  description: string
  target_info: Record<string, any>
  flag_regex: string
  chat_model_id: string
  loop_engine: LoopEngineType
  sandbox_mode: SandboxMode
  sandbox_image: string
  system_prompt_override?: string
  guardrail_config: GuardrailConfigDTO
}

export interface TraceEventItem {
  seq_id: number
  session_id: string
  step_index: number
  agent_name: string
  event_type: string
  timestamp: string
  usage: {
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
    cost_usd: number
    latency_ms: number
  } | null
  payload: Record<string, any>
}

export interface ManualExecResponse {
  command: string
  output: string
  exit_code: number
  timed_out: boolean
  truncated: boolean
  cwd: string | null
  duration_ms: number
}
