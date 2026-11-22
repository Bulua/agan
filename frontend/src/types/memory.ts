export type MemoryScopeType = 'global' | 'agent' | 'shared'

export interface MemoryFileItem {
  path: string
  title: string
  memory_type: string
  summary: string
  tags: string[]
  author: string
  size: number
  is_index: boolean
  updated_at: string
}

export interface MemoryScopeGroup {
  scope: MemoryScopeType
  scope_id: string
  label: string
  subtitle: string
  file_count: number
  total_size: number
  files: MemoryFileItem[]
}

export interface MemoryScopesOverview {
  root_dir: string
  global_scope: MemoryScopeGroup
  agent_scopes: MemoryScopeGroup[]
  shared_scopes: MemoryScopeGroup[]
}

export interface MemoryFileContent {
  scope: MemoryScopeType
  scope_id: string
  path: string
  content: string
  title: string
  memory_type: string
  tags: string[]
  author: string
  summary: string
  size: number
  is_index: boolean
  updated_at: string
}

export interface MemorySearchHit {
  scope: MemoryScopeType
  scope_id: string
  scope_label: string
  path: string
  title: string
  memory_type: string
  tags: string[]
  author: string
  snippet: string
  score: number
}

export interface MemorySearchResponse {
  query: string
  total: number
  items: MemorySearchHit[]
}
