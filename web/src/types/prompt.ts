export interface PromptTemplateInput {
  name: string
  content: string
}

export interface PromptTemplate extends PromptTemplateInput {
  id: string
  created_at: string
  updated_at: string
  character_count: number
}
