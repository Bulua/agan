import { apiClient } from '@/api/client'
import type {
  SkillFileContent,
  SkillListResponse,
  SkillSummaryItem,
} from '@/types/skill'

export const skillsApi = {
  async list(search?: string): Promise<SkillListResponse> {
    const { data } = await apiClient.get<SkillListResponse>('/skills', {
      params: search ? { search } : undefined,
    })
    return data
  },

  async get(skillName: string): Promise<SkillSummaryItem> {
    const { data } = await apiClient.get<SkillSummaryItem>(
      `/skills/${encodeURIComponent(skillName)}`,
    )
    return data
  },

  async create(payload: {
    name: string
    description?: string
    content_md?: string
  }): Promise<SkillSummaryItem> {
    const { data } = await apiClient.post<SkillSummaryItem>('/skills', payload)
    return data
  },

  async uploadZip(
    file: File,
    options?: { skillName?: string; overwrite?: boolean },
  ): Promise<SkillSummaryItem> {
    const formData = new FormData()
    formData.append('file', file)
    if (options?.skillName) formData.append('skill_name', options.skillName)
    if (options?.overwrite) formData.append('overwrite', 'true')
    const { data } = await apiClient.post<SkillSummaryItem>('/skills/upload-zip', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return data
  },

  async remove(skillName: string): Promise<void> {
    await apiClient.delete(`/skills/${encodeURIComponent(skillName)}`)
  },

  async readFile(skillName: string, path: string): Promise<SkillFileContent> {
    const { data } = await apiClient.get<SkillFileContent>(
      `/skills/${encodeURIComponent(skillName)}/file`,
      { params: { path } },
    )
    return data
  },

  async writeFile(skillName: string, path: string, content: string): Promise<SkillFileContent> {
    const { data } = await apiClient.put<SkillFileContent>(
      `/skills/${encodeURIComponent(skillName)}/file`,
      { path, content },
    )
    return data
  },

  async deleteFile(skillName: string, path: string): Promise<void> {
    await apiClient.delete(`/skills/${encodeURIComponent(skillName)}/file`, {
      params: { path },
    })
  },
}
