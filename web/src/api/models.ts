import { apiClient } from '@/api/client'
import type {
  LLMModel,
  LLMModelCreatePayload,
  LLMModelListResponse,
  LLMModelTestPayload,
  LLMModelTestResponse,
  LLMModelUpdatePayload,
  LLMUsageSummary,
  ModelType,
} from '@/types/llm'

export const modelsApi = {
  async list(params?: {
    model_type?: ModelType
    is_active?: boolean
    search?: string
    page?: number
    page_size?: number
  }): Promise<LLMModelListResponse> {
    const { data } = await apiClient.get<LLMModelListResponse>('/models', { params })
    return data
  },

  async create(payload: LLMModelCreatePayload): Promise<LLMModel> {
    const { data } = await apiClient.post<LLMModel>('/models', payload)
    return data
  },

  async update(id: string, payload: LLMModelUpdatePayload): Promise<LLMModel> {
    const { data } = await apiClient.put<LLMModel>(`/models/${id}`, payload)
    return data
  },

  async toggle(id: string): Promise<LLMModel> {
    const { data } = await apiClient.patch<LLMModel>(`/models/${id}/toggle`)
    return data
  },

  async setDefault(id: string): Promise<LLMModel> {
    const { data } = await apiClient.patch<LLMModel>(`/models/${id}/default`)
    return data
  },

  async remove(id: string): Promise<void> {
    await apiClient.delete(`/models/${id}`)
  },

  async testSaved(id: string, payload?: LLMModelTestPayload): Promise<LLMModelTestResponse> {
    const { data } = await apiClient.post<LLMModelTestResponse>(`/models/${id}/test`, payload ?? {}, {
      timeout: 45000,
    })
    return data
  },

  async testUnsaved(
    payload: LLMModelCreatePayload & { test_input?: LLMModelTestPayload },
  ): Promise<LLMModelTestResponse> {
    const { data } = await apiClient.post<LLMModelTestResponse>('/models/test-unsaved', payload, {
      timeout: 45000,
    })
    return data
  },

  async usageStats(): Promise<LLMUsageSummary> {
    const { data } = await apiClient.get<LLMUsageSummary>('/models/usage-stats')
    return data
  },
}
