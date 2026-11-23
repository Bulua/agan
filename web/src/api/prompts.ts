import { apiClient } from '@/api/client'
import type { PromptTemplate, PromptTemplateInput } from '@/types/prompt'

export const promptsApi = {
  async list(): Promise<PromptTemplate[]> {
    const { data } = await apiClient.get<PromptTemplate[]>('/prompts')
    return data
  },

  async create(payload: PromptTemplateInput): Promise<PromptTemplate> {
    const { data } = await apiClient.post<PromptTemplate>('/prompts', payload)
    return data
  },

  async update(id: string, payload: PromptTemplateInput): Promise<PromptTemplate> {
    const { data } = await apiClient.put<PromptTemplate>(`/prompts/${id}`, payload)
    return data
  },

  async remove(id: string): Promise<void> {
    await apiClient.delete(`/prompts/${id}`)
  },
}
