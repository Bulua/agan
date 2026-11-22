import { apiClient } from '@/api/client'
import type {
  ManualExecResponse,
  ScratchpadDTO,
  SessionCreatePayload,
  SessionDetail,
  SessionListResponse,
  SessionStatus,
  TraceEventItem,
  WriteupDTO,
} from '@/types/session'

export const sessionsApi = {
  async list(params?: {
    status?: SessionStatus
    search?: string
    page?: number
    page_size?: number
  }): Promise<SessionListResponse> {
    const { data } = await apiClient.get<SessionListResponse>('/sessions', { params })
    return data
  },

  async create(payload: SessionCreatePayload): Promise<SessionDetail> {
    const { data } = await apiClient.post<SessionDetail>('/sessions', payload)
    return data
  },

  async get(id: string): Promise<SessionDetail> {
    const { data } = await apiClient.get<SessionDetail>(`/sessions/${id}`)
    return data
  },

  async getEvents(id: string, afterSeq = 0): Promise<TraceEventItem[]> {
    const { data } = await apiClient.get<TraceEventItem[]>(`/sessions/${id}/events`, {
      params: { after_seq: afterSeq },
    })
    return data
  },

  async uploadAttachment(id: string, file: File): Promise<SessionDetail> {
    const formData = new FormData()
    formData.append('file', file)
    const { data } = await apiClient.post<SessionDetail>(`/sessions/${id}/attachments`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return data
  },

  async start(id: string): Promise<SessionDetail> {
    const { data } = await apiClient.post<SessionDetail>(`/sessions/${id}/start`)
    return data
  },

  async pause(id: string): Promise<SessionDetail> {
    const { data } = await apiClient.post<SessionDetail>(`/sessions/${id}/pause`)
    return data
  },

  async resume(id: string): Promise<SessionDetail> {
    const { data } = await apiClient.post<SessionDetail>(`/sessions/${id}/resume`)
    return data
  },

  async steer(id: string, message: string, immediateResume = true): Promise<void> {
    await apiClient.post(`/sessions/${id}/steer`, {
      message,
      immediate_resume: immediateResume,
    })
  },

  async stop(id: string, keepContainer = false): Promise<SessionDetail> {
    const { data } = await apiClient.post<SessionDetail>(`/sessions/${id}/stop`, {
      keep_container: keepContainer,
    })
    return data
  },

  async updateScratchpad(id: string, payload: ScratchpadDTO): Promise<ScratchpadDTO> {
    const { data } = await apiClient.put<ScratchpadDTO>(`/sessions/${id}/scratchpad`, payload)
    return data
  },

  async execSandbox(id: string, command: string, timeout = 30): Promise<ManualExecResponse> {
    const { data } = await apiClient.post<ManualExecResponse>(
      `/sessions/${id}/sandbox/exec`,
      { command, timeout },
      { timeout: (timeout + 10) * 1000 },
    )
    return data
  },

  async generateWriteup(id: string, language = 'zh-CN'): Promise<WriteupDTO> {
    const { data } = await apiClient.post<WriteupDTO>(
      `/sessions/${id}/writeup/generate`,
      { language },
      { timeout: 120000 },
    )
    return data
  },

  exportWriteupUrl(id: string): string {
    return `/api/v1/sessions/${id}/writeup/export`
  },

  async remove(id: string): Promise<void> {
    await apiClient.delete(`/sessions/${id}`)
  },
}

export class SessionTraceSocket {
  private ws: WebSocket | null = null
  private pingTimer: number | null = null
  private closedManually = false
  private reconnectTimer: number | null = null
  private lastSeq = 0

  constructor(
    private sessionId: string,
    private handlers: {
      onEvent: (ev: TraceEventItem) => void
      onStateChange?: (connected: boolean) => void
    },
    initialSeq = 0,
  ) {
    this.lastSeq = initialSeq
  }

  connect(): void {
    this.closedManually = false
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${proto}//${window.location.host}/ws/sessions/${this.sessionId}?after_seq=${this.lastSeq}`
    this.ws = new WebSocket(url)

    this.ws.onopen = () => {
      this.handlers.onStateChange?.(true)
      this.pingTimer = window.setInterval(() => {
        if (this.ws?.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ type: 'ping' }))
        }
      }, 20000)
    }

    this.ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data)
        if (msg?.type === 'pong') return
        if (typeof msg?.seq_id === 'number') {
          this.lastSeq = Math.max(this.lastSeq, msg.seq_id)
          this.handlers.onEvent(msg as TraceEventItem)
        }
      } catch {
        // ignore malformed frames
      }
    }

    this.ws.onclose = () => {
      this.clearTimers()
      this.handlers.onStateChange?.(false)
      if (!this.closedManually) {
        this.reconnectTimer = window.setTimeout(() => this.connect(), 2500)
      }
    }

    this.ws.onerror = () => {
      this.ws?.close()
    }
  }

  private clearTimers(): void {
    if (this.pingTimer !== null) {
      window.clearInterval(this.pingTimer)
      this.pingTimer = null
    }
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
  }

  close(): void {
    this.closedManually = true
    this.clearTimers()
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
  }
}
