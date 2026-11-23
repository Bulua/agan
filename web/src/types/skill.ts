export interface SkillFileNode {
  name: string
  path: string
  kind: 'dir' | 'file'
  size: number
  children: SkillFileNode[]
}

export interface SkillSummaryItem {
  name: string
  title: string
  description: string
  has_skill_md: boolean
  file_count: number
  total_size: number
  updated_at: string
  tree: SkillFileNode[]
}

export interface SkillListResponse {
  root_dir: string
  items: SkillSummaryItem[]
  total: number
}

export interface SkillFileContent {
  skill_name: string
  path: string
  content: string
  size: number
  is_binary: boolean
  truncated: boolean
}
