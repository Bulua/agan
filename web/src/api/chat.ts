import { apiClient } from '@/api/client'
import type {
  ChatConversationDetail,
  ChatConversationListResponse,
  ChatConversationSummary,
  ChatMessage,
  ChatToolCallRecord,
  CompactChatResponse,
} from '@/types/chat'

export interface SubagentLiveSummary {
  subagent_id: string
  role: string
  agent_name: string
  model_id: string
  task: string
  status: 'running' | 'done' | 'error' | 'stopped' | string
  current_action: string
  step_count: number
  steps: Array<{
    tool_name: string
    arguments: Record<string, any>
    status: string
    success: boolean
    duration_ms: number
    summary: string
  }>
  duration_ms: number
  total_tokens: number
  final_output: string
  error: string
}

export interface StreamSnapshotPayload {
  title: string
  agent_name?: string
  started_at_ms?: number
  content: string
  reasoning_content: string
  tool_calls: ChatToolCallRecord[]
  subagents?: SubagentLiveSummary[]
  pending_steerings?: string[]
  status: string
  error_message?: string
  assistant_message?: ChatMessage | null
}

export interface StreamEventCallbacks {
  onMeta?: (payload: {
    title: string
    agent_name?: string
    started_at_ms?: number
    user_message: ChatMessage | null
  }) => void
  onSnapshot?: (payload: StreamSnapshotPayload) => void
  onReasoningDelta?: (delta: string) => void
  onContentDelta?: (delta: string) => void
  onToolCall?: (tool: ChatToolCallRecord) => void
  onToolResult?: (tool: ChatToolCallRecord) => void
  onSteeringPending?: (content: string) => void
  onSegmentDone?: (payload: { assistant_message: ChatMessage | null }) => void
  onSteeringCommitted?: (payload: { user_messages: ChatMessage[]; started_at_ms?: number }) => void
  onContextCompacted?: (stats: {
    stage: string
    tokens_before: number
    tokens_after: number
    tokens_saved: number
    cleared_tool_outputs: number
    summarized_messages: number
  }) => void
  onSubagentUpdate?: (subagent: SubagentLiveSummary) => void
  onTitleUpdated?: (payload: { conversation_id: string; title: string }) => void
  onDone?: (payload: { title: string; stopped?: boolean; assistant_message: ChatMessage | null }) => void
  onError?: (message: string) => void
}

async function consumeSseResponse(
  resp: Response,
  callbacks: StreamEventCallbacks,
): Promise<void> {
  if (!resp.ok || !resp.body) {
    let detail = `HTTP ${resp.status}`
    try {
      const errJson = await resp.json()
      if (errJson?.detail) detail = String(errJson.detail)
    } catch {
      // ignore
    }
    throw new Error(detail)
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  const dispatchFrame = (frame: string): void => {
    const line = frame.split(/\r?\n/).find((l) => l.startsWith('data:'))
    if (!line) return
    const rawJson = line.slice(5).trim()
    if (!rawJson) return
    try {
      const evt = JSON.parse(rawJson)
      if (evt.type === 'meta') callbacks.onMeta?.(evt)
      else if (evt.type === 'snapshot') callbacks.onSnapshot?.(evt)
      else if (evt.type === 'reasoning_delta') callbacks.onReasoningDelta?.(evt.delta || '')
      else if (evt.type === 'content_delta') callbacks.onContentDelta?.(evt.delta || '')
      else if (evt.type === 'tool_call') callbacks.onToolCall?.(evt.tool)
      else if (evt.type === 'tool_result') callbacks.onToolResult?.(evt.tool)
      else if (evt.type === 'steering_pending') callbacks.onSteeringPending?.(evt.content || '')
      else if (evt.type === 'segment_done') callbacks.onSegmentDone?.(evt)
      else if (evt.type === 'steering_committed') callbacks.onSteeringCommitted?.(evt)
      else if (evt.type === 'context_compacted') callbacks.onContextCompacted?.(evt.stats)
      else if (evt.type === 'subagent_update') callbacks.onSubagentUpdate?.(evt.subagent)
      else if (evt.type === 'title_updated') callbacks.onTitleUpdated?.(evt)
      else if (evt.type === 'done') callbacks.onDone?.(evt)
      else if (evt.type === 'error') callbacks.onError?.(evt.message || 'Streaming error')
    } catch {
      // ignore malformed SSE frame
    }
  }

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const parts = buffer.split(/\r?\n\r?\n/)
    buffer = parts.pop() || ''

    for (const frame of parts) {
      dispatchFrame(frame)
    }
  }
  buffer += decoder.decode()
  if (buffer.trim()) {
    dispatchFrame(buffer)
  }
}

export const chatApi = {
  async list(params?: {
    search?: string
    page?: number
    page_size?: number
  }): Promise<ChatConversationListResponse> {
    const { data } = await apiClient.get<ChatConversationListResponse>('/chat/conversations', {
      params,
    })
    return data
  },

  async create(payload?: {
    title?: string
    agent_id?: string | null
    system_prompt?: string
    temperature?: number | null
  }): Promise<ChatConversationDetail> {
    const { data } = await apiClient.post<ChatConversationDetail>(
      '/chat/conversations',
      payload ?? {},
    )
    return data
  },

  async get(id: string): Promise<ChatConversationDetail> {
    const { data } = await apiClient.get<ChatConversationDetail>(`/chat/conversations/${id}`)
    return data
  },

  async update(
    id: string,
    payload: {
      title?: string
      agent_id?: string | null
      system_prompt?: string
      temperature?: number | null
    },
  ): Promise<ChatConversationSummary> {
    const { data } = await apiClient.patch<ChatConversationSummary>(
      `/chat/conversations/${id}`,
      payload,
    )
    return data
  },

  async remove(id: string): Promise<void> {
    await apiClient.delete(`/chat/conversations/${id}`)
  },

  async stopRun(conversationId: string): Promise<{ stopped: boolean }> {
    const { data } = await apiClient.post<{ stopped: boolean }>(
      `/chat/conversations/${conversationId}/stop`,
    )
    return data
  },

  async steerRun(conversationId: string, content: string): Promise<{ steered: boolean }> {
    const { data } = await apiClient.post<{ steered: boolean }>(
      `/chat/conversations/${conversationId}/steer`,
      { content },
    )
    return data
  },

  async compact(
    conversationId: string,
    payload?: { instructions?: string; keep_recent?: number },
  ): Promise<CompactChatResponse> {
    const { data } = await apiClient.post<CompactChatResponse>(
      `/chat/conversations/${conversationId}/compact`,
      payload ?? {},
    )
    return data
  },

  async streamMessage(
    conversationId: string,
    body: {
      content?: string
      agent_id?: string | null
      system_prompt?: string
      temperature?: number | null
      regenerate?: boolean
    },
    callbacks: StreamEventCallbacks,
    signal?: AbortSignal,
  ): Promise<void> {
    const resp = await fetch(`/api/v1/chat/conversations/${conversationId}/messages/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    })
    await consumeSseResponse(resp, callbacks)
  },

  async attachStream(
    conversationId: string,
    callbacks: StreamEventCallbacks,
    signal?: AbortSignal,
  ): Promise<void> {
    const resp = await fetch(`/api/v1/chat/conversations/${conversationId}/stream`, {
      method: 'GET',
      signal,
    })
    await consumeSseResponse(resp, callbacks)
  },
}
