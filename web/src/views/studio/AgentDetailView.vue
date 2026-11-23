<script setup lang="ts">
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NCheckbox,
  NForm,
  NFormItem,
  NIcon,
  NInput,
  NInputNumber,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSlider,
  NSpin,
  NSwitch,
  NTabPane,
  NTabs,
  NTooltip,
  useDialog,
  useMessage,
} from 'naive-ui'
import {
  ArrowBackOutlined,
  ArrowUpwardOutlined,
  AutoAwesomeOutlined,
  BuildOutlined,
  ChatBubbleOutlineOutlined,
  CodeOutlined,
  ContentCopyOutlined,
  DeleteOutlineOutlined,
  DescriptionOutlined,
  ExpandLessOutlined,
  ExpandMoreOutlined,
  HubOutlined,
  MenuBookOutlined,
  RefreshOutlined,
  SaveOutlined,
  SearchOutlined,
  SmartToyOutlined,
  StopOutlined,
  TuneOutlined,
} from '@vicons/material'
import AppSurfaceCard from '@/components/common/AppSurfaceCard.vue'
import AppStatusBadge from '@/components/common/AppStatusBadge.vue'
import AppEmptyState from '@/components/common/AppEmptyState.vue'
import AppMarkdown from '@/components/common/AppMarkdown.vue'
import AgentActivityTimeline from '@/components/chat/AgentActivityTimeline.vue'
import ToolTestModal from '@/views/studio/ToolTestModal.vue'
import { agentsApi } from '@/api/agents'
import { chatApi } from '@/api/chat'
import { mcpApi } from '@/api/mcp'
import { modelsApi } from '@/api/models'
import { skillsApi } from '@/api/skills'
import { toolsApi } from '@/api/tools'
import { extractApiError } from '@/api/client'
import type { AgentItem, AgentLoopEngine } from '@/types/agent'
import type { LLMModel } from '@/types/llm'
import type { ToolItem } from '@/types/capability'
import type { MCPServerItem } from '@/types/mcp'
import type { SkillSummaryItem } from '@/types/skill'
import type { ChatMessage } from '@/types/chat'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()

const agentId = computed(() => String(route.params.id || ''))
const loading = ref(true)
const saving = ref(false)
const activeTab = ref('overview')

const agent = ref<AgentItem | null>(null)
const chatModels = ref<LLMModel[]>([])
const allTools = ref<ToolItem[]>([])
const allMcpServers = ref<MCPServerItem[]>([])
const allSkills = ref<SkillSummaryItem[]>([])
const expandedMcpIds = ref<Record<string, boolean>>({})
const mcpSearch = ref('')
const skillSearch = ref('')
const toolSearch = ref('')
const toolTypeFilter = ref<'all' | 'builtin' | 'custom'>('all')
const testModalShow = ref(false)
const testingTool = ref<ToolItem | null>(null)

// Right-pane Live Debug Preview state
const debugConvId = ref<string | null>(null)
const debugMessages = ref<ChatMessage[]>([])
const debugDraft = ref('')
const debugStreaming = ref(false)
const debugScrollEl = ref<HTMLElement | null>(null)
let debugAbort: AbortController | null = null

const DEFAULT_PROMPT_TEMPLATE =
  'You are AGAN, a senior CTF security researcher and AI assistant. Think step by step, use your bound tools cleanly when investigation is needed, and present clear, reproducible technical answers.'

const form = reactive({
  name: '',
  description: '',
  chat_model_id: '',
  loop_engine: 'react' as AgentLoopEngine,
  system_prompt: '',
  temperature: null as number | null,
  sandbox_mode: 'simulated' as 'simulated' | 'docker',
  sandbox_image: 'agan-ctf-sandbox:latest',
  tool_ids: [] as string[],
  mcp_server_ids: [] as string[],
  mcp_tool_filters: {} as Record<string, string[]>,
  skill_names: [] as string[],
  max_steps: 20,
  step_timeout_sec: 120,
  token_budget: 200000,
  cost_budget_usd: 5.0,
  repeat_threshold: 3,
  enable_scratchpad: true,
  enable_long_term_memory: true,
  enable_subagents: true,
  include_global_memory: true,
  memory_mode: 'read_write' as 'read_write' | 'read_only',
  context_compact_ratio: 0.75,
  keep_recent_messages: 10,
  is_active: true,
  is_default: false,
})

const initialSnapshot = ref('')

function serializeForm(): string {
  return JSON.stringify(form)
}

const isDirty = computed(
  () => initialSnapshot.value !== '' && serializeForm() !== initialSnapshot.value,
)

const loopOptions = computed(() =>
  (['direct_chat', 'react', 'plan_execute', 'reflexion'] as AgentLoopEngine[]).map((l) => ({
    label: t(`agents.loops.${l}`),
    value: l,
  })),
)

const modelOptions = computed(() =>
  chatModels.value.map((m) => ({
    label: `${m.name} (${m.model_id})${m.is_default ? ' ★' : ''}`,
    value: m.id,
  })),
)

const selectedModelLabel = computed(() => {
  const m = chatModels.value.find((x) => x.id === form.chat_model_id)
  return m ? `${m.name} (${m.model_id})` : agent.value?.chat_model_name || '—'
})

const boundToolsList = computed(() =>
  allTools.value.filter((tool) => form.tool_ids.includes(tool.id)),
)

const boundMcpList = computed(() =>
  allMcpServers.value.filter((srv) => form.mcp_server_ids.includes(srv.id)),
)

const boundSkillsList = computed(() =>
  allSkills.value.filter((sk) => form.skill_names.includes(sk.name)),
)

const filteredSkills = computed(() => {
  const q = skillSearch.value.trim().toLowerCase()
  if (!q) return allSkills.value
  return allSkills.value.filter(
    (sk) =>
      sk.name.toLowerCase().includes(q) ||
      sk.title.toLowerCase().includes(q) ||
      sk.description.toLowerCase().includes(q),
  )
})

const filteredMcpServers = computed(() => {
  const q = mcpSearch.value.trim().toLowerCase()
  if (!q) return allMcpServers.value
  return allMcpServers.value.filter(
    (srv) =>
      srv.name.toLowerCase().includes(q) ||
      srv.description.toLowerCase().includes(q) ||
      srv.discovered_tools.some((mt) => mt.name.toLowerCase().includes(q)),
  )
})

function filteredToolsForServer(srv: MCPServerItem) {
  const q = mcpSearch.value.trim().toLowerCase()
  if (!q || srv.name.toLowerCase().includes(q)) return srv.discovered_tools
  return srv.discovered_tools.filter((mt) => mt.name.toLowerCase().includes(q))
}

const filteredTools = computed(() => {
  const q = toolSearch.value.trim().toLowerCase()
  return allTools.value.filter((tool) => {
    if (toolTypeFilter.value === 'builtin' && !tool.is_builtin) return false
    if (toolTypeFilter.value === 'custom' && tool.is_builtin) return false
    if (!q) return true
    return (
      tool.name.toLowerCase().includes(q) ||
      tool.display_name.toLowerCase().includes(q) ||
      tool.description.toLowerCase().includes(q)
    )
  })
})

async function hydrateForm(data: AgentItem): Promise<void> {
  form.name = data.name
  form.description = data.description
  form.chat_model_id = data.chat_model_id || ''
  form.loop_engine = (data.loop_engine as AgentLoopEngine) || 'react'
  form.system_prompt = data.system_prompt || ''
  form.temperature = data.temperature
  form.sandbox_mode = data.sandbox_mode === 'docker' ? 'docker' : 'simulated'
  form.sandbox_image = data.sandbox_image || 'agan-ctf-sandbox:latest'
  form.tool_ids = [...(data.tool_ids || [])]
  form.mcp_server_ids = [...(data.mcp_server_ids || [])]
  const cleanFilters: Record<string, string[]> = {}
  for (const [sid, list] of Object.entries(data.mcp_tool_filters || {})) {
    if (Array.isArray(list)) {
      cleanFilters[sid] = [...list]
      expandedMcpIds.value[sid] = true
    }
  }
  form.mcp_tool_filters = cleanFilters
  form.skill_names = [...(data.skill_names || [])]

  const g = data.guardrail_config || {}
  form.max_steps = Number(g.max_steps ?? 20)
  form.step_timeout_sec = Number(g.step_timeout_sec ?? 120)
  form.token_budget = Number(g.token_budget ?? 200000)
  form.cost_budget_usd = Number(g.cost_budget_usd ?? 5.0)
  form.repeat_threshold = Number(g.repeat_threshold ?? 3)

  const mem = data.memory_config || {}
  form.enable_scratchpad = mem.enable_scratchpad !== false
  form.enable_long_term_memory = mem.enable_long_term_memory !== false
  form.enable_subagents = mem.enable_subagents !== false
  form.include_global_memory = mem.include_global_memory !== false
  form.memory_mode = mem.memory_mode === 'read_only' ? 'read_only' : 'read_write'
  form.context_compact_ratio = Number(mem.context_compact_ratio ?? 0.75)
  form.keep_recent_messages = Number(mem.keep_recent_messages ?? 10)

  form.is_active = data.is_active
  form.is_default = data.is_default
  await nextTick()
  initialSnapshot.value = serializeForm()
}

async function loadAll(): Promise<void> {
  loading.value = true
  try {
    const aData = await agentsApi.get(agentId.value)
    agent.value = aData
    await hydrateForm(aData)

    const [mRes, tRes, mcpRes, skRes] = await Promise.all([
      modelsApi.list({ model_type: 'chat', is_active: true, page: 1, page_size: 100 }),
      toolsApi.list({ page: 1, page_size: 100 }),
      mcpApi.list({ page: 1, page_size: 100 }),
      skillsApi.list(),
    ])
    chatModels.value = mRes.items
    allTools.value = tRes.items
    allMcpServers.value = mcpRes.items
    allSkills.value = skRes.items
    await nextTick()
    initialSnapshot.value = serializeForm()
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
    if (!agent.value) {
      router.push('/studio/agents')
    }
  } finally {
    loading.value = false
  }
}

async function handleSave(silent = false): Promise<boolean> {
  if (!agent.value) return false
  if (!form.name.trim()) {
    message.warning(t('models.form.requiredField'))
    return false
  }
  saving.value = true
  try {
    const updated = await agentsApi.update(agent.value.id, {
      name: form.name.trim(),
      description: form.description.trim(),
      chat_model_id: form.chat_model_id || undefined,
      loop_engine: form.loop_engine,
      system_prompt: form.system_prompt,
      temperature: form.temperature,
      sandbox_mode: form.sandbox_mode,
      sandbox_image: form.sandbox_image.trim() || 'agan-ctf-sandbox:latest',
      tool_ids: form.tool_ids,
      mcp_server_ids: form.mcp_server_ids,
      mcp_tool_filters: form.mcp_tool_filters,
      skill_names: form.skill_names,
      guardrail_config: {
        max_steps: form.max_steps,
        step_timeout_sec: form.step_timeout_sec,
        token_budget: form.token_budget,
        cost_budget_usd: form.cost_budget_usd,
        repeat_threshold: form.repeat_threshold,
      },
      memory_config: {
        enable_scratchpad: form.enable_scratchpad,
        enable_long_term_memory: form.enable_long_term_memory,
        enable_subagents: form.enable_subagents,
        include_global_memory: form.include_global_memory,
        memory_mode: form.memory_mode,
        context_compact_ratio: form.context_compact_ratio,
        keep_recent_messages: form.keep_recent_messages,
      },
      is_active: form.is_active,
      is_default: form.is_default,
    } as any)
    agent.value = updated
    await hydrateForm(updated)
    if (!silent) message.success(t('common.saveSuccess'))
    return true
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
    return false
  } finally {
    saving.value = false
  }
}

function toggleToolSelection(toolId: string, checked: boolean): void {
  if (checked) {
    if (!form.tool_ids.includes(toolId)) form.tool_ids.push(toolId)
  } else {
    form.tool_ids = form.tool_ids.filter((id) => id !== toolId)
  }
}

function isMcpBound(serverId: string): boolean {
  return form.mcp_server_ids.includes(serverId)
}

function getSelectedMcpToolNames(srv: MCPServerItem): string[] {
  if (!isMcpBound(srv.id)) return []
  const explicit = form.mcp_tool_filters[srv.id]
  if (Array.isArray(explicit)) return explicit
  return (srv.discovered_tools || []).map((x) => x.name)
}

function isMcpToolSelected(srv: MCPServerItem, toolName: string): boolean {
  if (!isMcpBound(srv.id)) return false
  const explicit = form.mcp_tool_filters[srv.id]
  if (!Array.isArray(explicit)) return true
  return explicit.includes(toolName)
}

function isMcpIndeterminate(srv: MCPServerItem): boolean {
  if (!isMcpBound(srv.id)) return false
  const explicit = form.mcp_tool_filters[srv.id]
  if (!Array.isArray(explicit)) return false
  const total = srv.discovered_tools.length
  return total > 0 && explicit.length > 0 && explicit.length < total
}

function mcpSelectionBadge(srv: MCPServerItem): string {
  const total = srv.tool_count
  if (!isMcpBound(srv.id)) return t('mcp.toolsCount', { count: total })
  const explicit = form.mcp_tool_filters[srv.id]
  if (!Array.isArray(explicit)) return t('mcp.allToolsBadge', { total })
  return t('mcp.selectedRatio', { selected: explicit.length, total })
}

function mcpSnapshotLabel(srv: MCPServerItem): string {
  const explicit = form.mcp_tool_filters[srv.id]
  if (Array.isArray(explicit)) {
    return `${srv.name} (${explicit.length}/${srv.tool_count})`
  }
  return `${srv.name} (${srv.tool_count})`
}

function toggleMcpSelection(srv: MCPServerItem, checked: boolean): void {
  if (checked) {
    if (!form.mcp_server_ids.includes(srv.id)) form.mcp_server_ids.push(srv.id)
    delete form.mcp_tool_filters[srv.id]
    if (srv.discovered_tools.length > 0) {
      expandedMcpIds.value[srv.id] = true
    }
  } else {
    form.mcp_server_ids = form.mcp_server_ids.filter((id) => id !== srv.id)
    delete form.mcp_tool_filters[srv.id]
  }
}

function toggleSingleMcpTool(srv: MCPServerItem, toolName: string, checked: boolean): void {
  const allNames = srv.discovered_tools.map((x) => x.name)
  if (!isMcpBound(srv.id)) {
    if (!checked) return
    form.mcp_server_ids.push(srv.id)
    if (allNames.length === 1 && allNames[0] === toolName) {
      delete form.mcp_tool_filters[srv.id]
    } else {
      form.mcp_tool_filters[srv.id] = [toolName]
    }
    return
  }

  const current = new Set(getSelectedMcpToolNames(srv))
  if (checked) current.add(toolName)
  else current.delete(toolName)

  const ordered = allNames.filter((n) => current.has(n))
  if (ordered.length === allNames.length) {
    delete form.mcp_tool_filters[srv.id]
  } else {
    form.mcp_tool_filters[srv.id] = ordered
  }
}

function selectAllMcpTools(srv: MCPServerItem): void {
  if (!form.mcp_server_ids.includes(srv.id)) form.mcp_server_ids.push(srv.id)
  delete form.mcp_tool_filters[srv.id]
}

function clearMcpTools(srv: MCPServerItem): void {
  if (!form.mcp_server_ids.includes(srv.id)) form.mcp_server_ids.push(srv.id)
  form.mcp_tool_filters[srv.id] = []
}

function toggleSkillSelection(skillName: string, checked: boolean): void {
  if (checked) {
    if (!form.skill_names.includes(skillName)) form.skill_names.push(skillName)
  } else {
    form.skill_names = form.skill_names.filter((n) => n !== skillName)
  }
}

function selectAllEnabledTools(): void {
  form.tool_ids = allTools.value.filter((x) => x.is_enabled).map((x) => x.id)
}

function openToolTest(tool: ToolItem): void {
  testingTool.value = tool
  testModalShow.value = true
}

function fillDefaultTemplate(): void {
  form.system_prompt = DEFAULT_PROMPT_TEMPLATE
}

async function handleClone(): Promise<void> {
  if (!agent.value) return
  try {
    const cloned = await agentsApi.clone(agent.value.id)
    message.success(t('common.saveSuccess'))
    router.push(`/studio/agents/${cloned.id}`)
  } catch (err) {
    message.error(extractApiError(err))
  }
}

function handleDelete(): void {
  if (!agent.value) return
  const target = agent.value
  dialog.warning({
    title: t('common.delete'),
    content: t('agents.deleteConfirm', { name: target.name }),
    positiveText: t('common.confirmDelete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await agentsApi.remove(target.id)
        message.success(t('common.deleteSuccess'))
        router.push('/studio/agents')
      } catch (err) {
        message.error(extractApiError(err))
      }
    },
  })
}

// --- Right Pane Live Debug Chat ---
async function scrollDebugBottom(): Promise<void> {
  await nextTick()
  if (debugScrollEl.value) {
    debugScrollEl.value.scrollTop = debugScrollEl.value.scrollHeight
  }
}

async function stopDebugStream(): Promise<void> {
  if (debugConvId.value && debugStreaming.value) {
    try {
      await chatApi.stopRun(debugConvId.value)
    } catch {
      // ignore
    }
  }
  if (debugAbort) {
    debugAbort.abort()
    debugAbort = null
  }
  debugStreaming.value = false
}

function resetDebugChat(): void {
  stopDebugStream()
  debugConvId.value = null
  debugMessages.value = []
}

async function sendDebugMessage(): Promise<void> {
  if (!agent.value || debugStreaming.value) return
  const text = debugDraft.value.trim()
  if (!text) return

  // Auto-save unsaved edits so debug preview tests the latest config immediately
  if (isDirty.value) {
    const saved = await handleSave(true)
    if (!saved) return
  }

  let convId = debugConvId.value
  if (!convId) {
    try {
      const created = await chatApi.create({
        title: `[Debug] ${agent.value.name}`,
        agent_id: agent.value.id,
      })
      convId = created.id
      debugConvId.value = created.id
    } catch (err) {
      message.error(extractApiError(err))
      return
    }
  }

  debugDraft.value = ''
  debugMessages.value.push({
    id: `dbg-u-${Date.now()}`,
    conversation_id: convId,
    role: 'user',
    content: text,
    reasoning_content: '',
    tool_calls_json: [],
    model_id_used: '',
    prompt_tokens: 0,
    completion_tokens: 0,
    cost_usd: 0,
    latency_ms: 0,
    created_at: new Date().toISOString(),
  })

  const asstId = `dbg-a-${Date.now()}`
  debugMessages.value.push({
    id: asstId,
    conversation_id: convId,
    role: 'assistant',
    content: '',
    reasoning_content: '',
    tool_calls_json: [],
    model_id_used: '',
    prompt_tokens: 0,
    completion_tokens: 0,
    cost_usd: 0,
    latency_ms: 0,
    created_at: new Date().toISOString(),
    streaming: true,
    started_at_ms: Date.now(),
  })
  await scrollDebugBottom()

  debugStreaming.value = true
  debugAbort = new AbortController()

  try {
    await chatApi.streamMessage(
      convId,
      { content: text, agent_id: agent.value.id },
      {
        onReasoningDelta: (delta) => {
          const target = debugMessages.value.find((m) => m.id === asstId)
          if (target) target.reasoning_content += delta
          scrollDebugBottom()
        },
        onContentDelta: (delta) => {
          const target = debugMessages.value.find((m) => m.id === asstId)
          if (target) target.content += delta
          scrollDebugBottom()
        },
        onToolCall: (tool) => {
          const target = debugMessages.value.find((m) => m.id === asstId)
          if (target) target.tool_calls_json.push({ ...tool })
          scrollDebugBottom()
        },
        onToolResult: (tool) => {
          const target = debugMessages.value.find((m) => m.id === asstId)
          if (target) {
            const idx = target.tool_calls_json.findIndex((x) => x.id === tool.id)
            if (idx !== -1) target.tool_calls_json[idx] = { ...tool }
          }
          scrollDebugBottom()
        },
        onDone: (done) => {
          const idx = debugMessages.value.findIndex((m) => m.id === asstId)
          if (idx !== -1 && done.assistant_message) {
            debugMessages.value[idx] = { ...done.assistant_message, streaming: false }
          }
        },
        onError: (errMsg) => {
          const target = debugMessages.value.find((m) => m.id === asstId)
          if (target && !target.content) target.content = `**Error:** ${errMsg}`
          message.error(errMsg)
        },
      },
      debugAbort.signal,
    )
  } catch (err: any) {
    if (err?.name !== 'AbortError') {
      const target = debugMessages.value.find((m) => m.id === asstId)
      if (target && !target.content) target.content = `**Error:** ${extractApiError(err)}`
    }
  } finally {
    const target = debugMessages.value.find((m) => m.id === asstId)
    if (target) target.streaming = false
    debugStreaming.value = false
    debugAbort = null
  }
}

function handleDebugKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    sendDebugMessage()
  }
}

function handleKeydown(e: KeyboardEvent): void {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    e.preventDefault()
    handleSave()
  }
}

onMounted(() => {
  loadAll()
  window.addEventListener('keydown', handleKeydown)
})

onActivated(async () => {
  window.addEventListener('keydown', handleKeydown)
  try {
    const [mRes, tRes, mcpRes, skRes] = await Promise.all([
      modelsApi.list({ model_type: 'chat', is_active: true, page: 1, page_size: 100 }),
      toolsApi.list({ page: 1, page_size: 100 }),
      mcpApi.list({ page: 1, page_size: 100 }),
      skillsApi.list(),
    ])
    chatModels.value = mRes.items
    allTools.value = tRes.items
    allMcpServers.value = mcpRes.items
    allSkills.value = skRes.items
  } catch {
    // ignore silent refresh errors
  }
})

onDeactivated(() => {
  window.removeEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown)
  stopDebugStream()
})
</script>

<template>
  <NSpin :show="loading">
    <div v-if="agent" class="flex flex-col gap-3.5">
      <!-- Top Action & Navigation Header -->
      <div
        class="g-transition flex flex-wrap items-center justify-between gap-3 border px-4 py-2.5"
        style="
          background: var(--g-bg-surface);
          border-color: var(--g-border);
          border-radius: var(--g-radius-sm);
        "
      >
        <div class="flex min-w-0 items-center gap-2.5">
          <NButton quaternary size="small" @click="router.push('/studio/agents')">
            <template #icon><NIcon :component="ArrowBackOutlined" /></template>
            {{ t('agents.detail.backToList') }}
          </NButton>
          <div class="h-4 w-px" style="background: var(--g-border)" />
          <NIcon :size="18" :component="SmartToyOutlined" style="color: var(--g-primary-text)" />
          <span class="truncate text-sm font-medium" style="color: var(--g-text-primary)">
            {{ form.name || agent.name }}
          </span>
          <AppStatusBadge
            v-if="isDirty"
            variant="warning"
            :label="t('agents.detail.unsaved')"
            pulse
          />
        </div>

        <div class="flex flex-wrap items-center gap-2">
          <NButton
            secondary
            size="small"
            @click="router.push({ path: '/chat', query: { agent: agent.id } })"
          >
            <template #icon><NIcon :component="ChatBubbleOutlineOutlined" /></template>
            {{ t('agents.detail.openInChat') }}
          </NButton>
          <NButton secondary size="small" @click="handleClone">
            <template #icon><NIcon :component="ContentCopyOutlined" /></template>
            {{ t('agents.clone') }}
          </NButton>
          <NButton quaternary size="small" type="error" @click="handleDelete">
            <template #icon><NIcon :component="DeleteOutlineOutlined" /></template>
          </NButton>
          <NButton type="primary" size="small" :loading="saving" @click="handleSave(false)">
            <template #icon><NIcon :component="SaveOutlined" /></template>
            {{ t('agents.detail.saveChanges') }}
          </NButton>
        </div>
      </div>

      <!-- Balanced 2-Column Studio Grid (Left: Config Tabs 8 cols | Right: Live Snapshot + Debug Preview 4 cols) -->
      <div class="grid grid-cols-1 items-start gap-3.5 lg:grid-cols-12">
        <!-- LEFT 8 COLS: Top Tabs + Editable Panels -->
        <div class="lg:col-span-8">
          <AppSurfaceCard no-padding>
            <div class="border-b px-4 pt-1" style="border-color: var(--g-border-subtle)">
              <NTabs v-model:value="activeTab" type="line" size="medium" animated>
                <NTabPane name="overview">
                  <template #tab>
                    <span class="inline-flex items-center gap-1.5">
                      <NIcon :size="15" :component="TuneOutlined" />
                      {{ t('agents.detail.tabs.overview') }}
                    </span>
                  </template>
                </NTabPane>

                <NTabPane name="prompts">
                  <template #tab>
                    <span class="inline-flex items-center gap-1.5">
                      <NIcon :size="15" :component="DescriptionOutlined" />
                      {{ t('agents.detail.tabs.prompts') }}
                    </span>
                  </template>
                </NTabPane>

                <NTabPane name="tools">
                  <template #tab>
                    <span class="inline-flex items-center gap-1.5">
                      <NIcon :size="15" :component="BuildOutlined" />
                      {{ t('agents.detail.tabs.tools') }} ({{ form.tool_ids.length }})
                    </span>
                  </template>
                </NTabPane>

                <NTabPane name="mcp">
                  <template #tab>
                    <span class="inline-flex items-center gap-1.5">
                      <NIcon :size="15" :component="HubOutlined" />
                      {{ t('agents.detail.tabs.mcp') }} ({{ form.mcp_server_ids.length }})
                    </span>
                  </template>
                </NTabPane>

                <NTabPane name="skills">
                  <template #tab>
                    <span class="inline-flex items-center gap-1.5">
                      <NIcon :size="15" :component="AutoAwesomeOutlined" />
                      {{ t('agents.detail.tabs.skills') }} ({{ form.skill_names.length }})
                    </span>
                  </template>
                </NTabPane>

                <NTabPane name="knowledge_memory">
                  <template #tab>
                    <span class="inline-flex items-center gap-1.5">
                      <NIcon :size="15" :component="MenuBookOutlined" />
                      {{ t('agents.detail.tabs.knowledgeMemory') }}
                    </span>
                  </template>
                </NTabPane>
              </NTabs>
            </div>

            <div class="p-5">
              <Transition name="g-fade" mode="out-in">
                <!-- PANEL 1: Overview, Model, Loop & Guardrails -->
                <div v-if="activeTab === 'overview'" key="overview">
                  <NForm label-placement="top" size="medium">
                    <NFormItem :label="t('agents.form.name')">
                      <NInput v-model:value="form.name" :placeholder="t('agents.form.namePh')" />
                    </NFormItem>

                    <NFormItem :label="t('agents.form.description')">
                      <NInput
                        v-model:value="form.description"
                        type="textarea"
                        :rows="2"
                        :placeholder="t('agents.form.descriptionPh')"
                      />
                    </NFormItem>

                    <div class="grid grid-cols-1 gap-x-5 sm:grid-cols-2">
                      <NFormItem :label="t('agents.form.chatModel')">
                        <NSelect
                          v-model:value="form.chat_model_id"
                          :options="modelOptions"
                          filterable
                        />
                      </NFormItem>
                      <NFormItem :label="t('agents.form.loopEngine')">
                        <NSelect v-model:value="form.loop_engine" :options="loopOptions" />
                      </NFormItem>
                    </div>

                    <div class="grid grid-cols-1 gap-x-5 sm:grid-cols-3">
                      <NFormItem :label="t('agents.form.sandboxMode')">
                        <NRadioGroup v-model:value="form.sandbox_mode" size="small">
                          <NRadioButton value="simulated">Simulated</NRadioButton>
                          <NRadioButton value="docker">Docker</NRadioButton>
                        </NRadioGroup>
                      </NFormItem>
                      <NFormItem :label="t('workbench.create.sandboxImage')">
                        <NInput v-model:value="form.sandbox_image" class="font-mono" />
                      </NFormItem>
                      <NFormItem :label="t('chat.temperature')">
                        <NInputNumber
                          v-model:value="form.temperature"
                          :min="0"
                          :max="2"
                          :step="0.1"
                          clearable
                          :placeholder="t('agents.detail.temperaturePlaceholder')"
                          class="w-full"
                        />
                      </NFormItem>
                    </div>

                    <div
                      class="mt-2 mb-4 border p-4"
                      style="
                        background: var(--g-bg-canvas);
                        border-color: var(--g-border-subtle);
                        border-radius: var(--g-radius-sm);
                      "
                    >
                      <div class="mb-3 text-xs font-semibold" style="color: var(--g-text-primary)">
                        {{ t('agents.detail.guardrailsTitle') }}
                      </div>
                      <div class="grid grid-cols-2 gap-x-3 sm:grid-cols-5">
                        <NFormItem :label="t('agents.form.maxSteps')" class="mb-0">
                          <NInputNumber v-model:value="form.max_steps" :min="1" :max="200" class="w-full" />
                        </NFormItem>
                        <NFormItem :label="t('agents.detail.stepTimeout')" class="mb-0">
                          <NInputNumber v-model:value="form.step_timeout_sec" :min="10" :max="600" class="w-full" />
                        </NFormItem>
                        <NFormItem :label="t('agents.detail.tokenBudget')" class="mb-0">
                          <NInputNumber v-model:value="form.token_budget" :min="0" :step="50000" class="w-full" />
                        </NFormItem>
                        <NFormItem :label="t('agents.detail.costBudget')" class="mb-0">
                          <NInputNumber v-model:value="form.cost_budget_usd" :min="0" :step="1" :precision="2" class="w-full" />
                        </NFormItem>
                        <NFormItem :label="t('agents.detail.repeatThreshold')" class="mb-0">
                          <NInputNumber v-model:value="form.repeat_threshold" :min="2" :max="10" class="w-full" />
                        </NFormItem>
                      </div>
                    </div>

                    <div class="flex flex-wrap items-center gap-6">
                      <div class="flex items-center gap-2">
                        <NSwitch v-model:value="form.is_active" />
                        <span class="text-xs" style="color: var(--g-text-primary)">
                          {{ t('agents.form.isActive') }}
                        </span>
                      </div>
                      <div class="flex items-center gap-2">
                        <NSwitch v-model:value="form.is_default" />
                        <span class="text-xs" style="color: var(--g-text-primary)">
                          {{ t('agents.form.isDefault') }}
                        </span>
                      </div>
                    </div>
                  </NForm>
                </div>

                <!-- PANEL 2: System Prompt Editor -->
                <div v-else-if="activeTab === 'prompts'" key="prompts" class="flex flex-col gap-3">
                  <div class="flex flex-wrap items-center justify-between gap-2">
                    <span class="font-mono text-xs" style="color: var(--g-text-secondary)">
                      {{ t('agents.detail.promptChars') }}: {{ form.system_prompt.length }}
                    </span>
                    <NButton size="small" secondary type="primary" @click="fillDefaultTemplate">
                      <template #icon><NIcon :component="AutoAwesomeOutlined" /></template>
                      {{ t('agents.form.fillRolePrompt') }}
                    </NButton>
                  </div>

                  <NInput
                    v-model:value="form.system_prompt"
                    type="textarea"
                    :rows="18"
                    class="font-mono text-xs"
                    :placeholder="t('agents.form.systemPromptPh')"
                  />

                  <p class="m-0 font-mono text-[11px]" style="color: var(--g-text-tertiary)">
                    {{ t('agents.detail.promptVarsHint') }}
                  </p>
                </div>

                <!-- PANEL 3: Tools Binding Matrix -->
                <div v-else-if="activeTab === 'tools'" key="tools" class="flex flex-col gap-3">
                  <div class="flex flex-wrap items-center justify-between gap-2">
                    <div class="flex flex-wrap items-center gap-2">
                      <div class="w-60">
                        <NInput
                          v-model:value="toolSearch"
                          size="small"
                          clearable
                          :placeholder="t('agents.detail.toolsSearchPh')"
                        >
                          <template #prefix><NIcon :component="SearchOutlined" /></template>
                        </NInput>
                      </div>
                      <NRadioGroup v-model:value="toolTypeFilter" size="small">
                        <NRadioButton value="all">{{ t('common.all') }}</NRadioButton>
                        <NRadioButton value="builtin">{{ t('tools.tabBuiltin') }}</NRadioButton>
                        <NRadioButton value="custom">{{ t('tools.kpiCustom') }}</NRadioButton>
                      </NRadioGroup>
                    </div>

                    <div class="flex items-center gap-2">
                      <NButton size="tiny" secondary type="primary" @click="selectAllEnabledTools">
                        {{ t('agents.form.selectAllTools') }}
                      </NButton>
                      <NButton size="tiny" secondary @click="form.tool_ids = []">
                        {{ t('agents.form.clearTools') }}
                      </NButton>
                    </div>
                  </div>

                  <p class="m-0 text-xs" style="color: var(--g-text-secondary)">
                    {{ t('agents.detail.toolsEnabledHint') }}
                  </p>

                  <div class="grid grid-cols-1 gap-2.5 md:grid-cols-2">
                    <div
                      v-for="tool in filteredTools"
                      :key="tool.id"
                      class="g-transition flex flex-col justify-between border p-3"
                      :style="{
                        background: form.tool_ids.includes(tool.id)
                          ? 'var(--g-bg-active)'
                          : 'var(--g-bg-canvas)',
                        borderColor: form.tool_ids.includes(tool.id)
                          ? 'var(--g-primary)'
                          : 'var(--g-border)',
                        borderRadius: 'var(--g-radius-sm)',
                        opacity: tool.is_enabled ? 1 : 0.55,
                      }"
                    >
                      <div>
                        <div class="flex items-start justify-between gap-2">
                          <NCheckbox
                            :checked="form.tool_ids.includes(tool.id)"
                            :disabled="!tool.is_enabled"
                            @update:checked="(val) => toggleToolSelection(tool.id, val)"
                          >
                            <span class="text-xs font-medium" style="color: var(--g-text-primary)">
                              {{ tool.display_name }}
                            </span>
                          </NCheckbox>
                          <AppStatusBadge
                            :variant="tool.is_builtin ? 'info' : 'success'"
                            :label="tool.is_builtin ? 'BUILTIN' : 'CUSTOM'"
                            :dot="false"
                          />
                        </div>
                        <div class="mt-1 pl-6 font-mono text-[11px]" style="color: var(--g-primary-text)">
                          {{ tool.name }}
                        </div>
                        <p
                          class="mt-1.5 mb-0 pl-6 line-clamp-2 text-[11px] leading-relaxed"
                          style="color: var(--g-text-secondary)"
                        >
                          {{ tool.description }}
                        </p>
                      </div>

                      <div class="mt-2.5 flex justify-end">
                        <NButton size="tiny" quaternary type="primary" @click="openToolTest(tool)">
                          <template #icon><NIcon :component="CodeOutlined" /></template>
                          {{ t('tools.schemaAndTest') }}
                        </NButton>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- PANEL 4: MCP Servers Binding -->
                <div v-else-if="activeTab === 'mcp'" key="mcp" class="flex flex-col gap-3">
                  <div class="flex flex-wrap items-center justify-between gap-2">
                    <div class="w-full sm:w-64">
                      <NInput
                        v-model:value="mcpSearch"
                        size="small"
                        clearable
                        :placeholder="t('mcp.filterToolPh')"
                      >
                        <template #prefix><NIcon :component="SearchOutlined" /></template>
                      </NInput>
                    </div>
                    <NButton size="small" secondary @click="router.push('/studio/mcp')">
                      <template #icon><NIcon :component="HubOutlined" /></template>
                      {{ t('mcp.goMcpManage') }}
                    </NButton>
                  </div>

                  <AppEmptyState
                    v-if="allMcpServers.length === 0"
                    :title="t('mcp.emptyTitle')"
                    :description="t('mcp.noMcpAvailable')"
                  >
                    <template #icon><NIcon :size="22" :component="HubOutlined" /></template>
                    <template #action>
                      <NButton type="primary" size="small" @click="router.push('/studio/mcp')">
                        {{ t('mcp.goMcpManage') }}
                      </NButton>
                    </template>
                  </AppEmptyState>

                  <div v-else class="flex flex-col gap-2.5">
                    <div
                      v-for="srv in filteredMcpServers"
                      :key="srv.id"
                      class="g-transition flex flex-col border p-3"
                      :style="{
                        background: isMcpBound(srv.id)
                          ? 'var(--g-bg-active)'
                          : 'var(--g-bg-canvas)',
                        borderColor: isMcpBound(srv.id)
                          ? 'var(--g-primary)'
                          : 'var(--g-border)',
                        borderRadius: 'var(--g-radius-sm)',
                        opacity: srv.is_enabled ? 1 : 0.55,
                      }"
                    >
                      <!-- Single Clean Server Header Row -->
                      <div class="flex flex-wrap items-center justify-between gap-2">
                        <div class="flex min-w-0 items-center gap-2">
                          <NCheckbox
                            :checked="isMcpBound(srv.id)"
                            :indeterminate="isMcpIndeterminate(srv)"
                            :disabled="!srv.is_enabled"
                            @update:checked="(val) => toggleMcpSelection(srv, val)"
                          >
                            <span class="font-mono text-xs font-semibold" style="color: var(--g-text-primary)">
                              {{ srv.name }}
                            </span>
                          </NCheckbox>
                          <AppStatusBadge
                            variant="info"
                            :label="srv.transport === 'streamable_http' ? 'HTTP' : srv.transport.toUpperCase()"
                            :dot="false"
                          />
                          <AppStatusBadge
                            :variant="
                              isMcpBound(srv.id)
                                ? isMcpIndeterminate(srv)
                                  ? 'warning'
                                  : 'success'
                                : 'neutral'
                            "
                            :label="mcpSelectionBadge(srv)"
                            :dot="false"
                          />
                        </div>

                        <!-- Right: Select All / Clear + Compact Chevron Icon -->
                        <div v-if="srv.discovered_tools.length > 0" class="flex items-center gap-1">
                          <NButton
                            size="tiny"
                            quaternary
                            type="primary"
                            :disabled="!srv.is_enabled"
                            @click="selectAllMcpTools(srv)"
                          >
                            {{ t('mcp.selectAllTools') }}
                          </NButton>
                          <NButton
                            size="tiny"
                            quaternary
                            :disabled="!srv.is_enabled"
                            @click="clearMcpTools(srv)"
                          >
                            {{ t('mcp.clearSelectedTools') }}
                          </NButton>
                          <NTooltip>
                            <template #trigger>
                              <NButton
                                size="tiny"
                                quaternary
                                @click="expandedMcpIds[srv.id] = !expandedMcpIds[srv.id]"
                              >
                                <template #icon>
                                  <NIcon
                                    :size="16"
                                    :component="
                                      expandedMcpIds[srv.id] || Boolean(mcpSearch.trim())
                                        ? ExpandLessOutlined
                                        : ExpandMoreOutlined
                                    "
                                  />
                                </template>
                              </NButton>
                            </template>
                            {{
                              expandedMcpIds[srv.id] ? t('mcp.collapseTools') : t('mcp.expandTools')
                            }}
                          </NTooltip>
                        </div>
                      </div>

                      <!-- Hint if server has no synced tools yet -->
                      <div
                        v-if="srv.discovered_tools.length === 0"
                        class="mt-1.5 pl-6 text-[11px]"
                        style="color: var(--g-text-tertiary)"
                      >
                        {{ t('mcp.syncFirstHint') }}
                      </div>

                      <!-- Compact 3-Column Single-Line Tool Checkboxes (description on hover tooltip) -->
                      <div
                        v-else-if="expandedMcpIds[srv.id] || Boolean(mcpSearch.trim())"
                        class="mt-2.5 max-h-[360px] overflow-y-auto border p-2.5"
                        style="
                          background: var(--g-bg-surface);
                          border-color: var(--g-border-subtle);
                          border-radius: var(--g-radius-xs);
                        "
                      >
                        <div class="grid grid-cols-1 gap-1.5 sm:grid-cols-2 xl:grid-cols-3">
                          <NTooltip
                            v-for="mt in filteredToolsForServer(srv)"
                            :key="mt.name"
                            :disabled="!mt.description"
                            placement="top"
                            :style="{ maxWidth: '320px' }"
                          >
                            <template #trigger>
                              <label
                                class="g-transition flex cursor-pointer items-center gap-2 border px-2 py-1.5"
                                :style="{
                                  background: isMcpToolSelected(srv, mt.name)
                                    ? 'var(--g-primary-soft)'
                                    : 'var(--g-bg-canvas)',
                                  borderColor: isMcpToolSelected(srv, mt.name)
                                    ? 'var(--g-primary)'
                                    : 'var(--g-border-subtle)',
                                  borderRadius: 'var(--g-radius-xs)',
                                }"
                              >
                                <NCheckbox
                                  size="small"
                                  :checked="isMcpToolSelected(srv, mt.name)"
                                  :disabled="!srv.is_enabled"
                                  @update:checked="(val) => toggleSingleMcpTool(srv, mt.name, val)"
                                />
                                <span
                                  class="min-w-0 flex-1 truncate font-mono text-[11.5px]"
                                  :style="{
                                    color: isMcpToolSelected(srv, mt.name)
                                      ? 'var(--g-primary-text)'
                                      : 'var(--g-text-primary)',
                                  }"
                                >
                                  {{ mt.name }}
                                </span>
                              </label>
                            </template>
                            <span class="text-xs leading-relaxed">{{ mt.description }}</span>
                          </NTooltip>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- PANEL 5: Skills Binding -->
                <div v-else-if="activeTab === 'skills'" key="skills" class="flex flex-col gap-3">
                  <div class="flex flex-wrap items-center justify-between gap-2">
                    <div class="w-full sm:w-64">
                      <NInput
                        v-model:value="skillSearch"
                        size="small"
                        clearable
                        :placeholder="t('skills.filterPh')"
                      >
                        <template #prefix><NIcon :component="SearchOutlined" /></template>
                      </NInput>
                    </div>
                    <NButton size="small" secondary @click="router.push('/studio/skills')">
                      <template #icon><NIcon :component="AutoAwesomeOutlined" /></template>
                      {{ t('skills.goSkillsManage') }}
                    </NButton>
                  </div>

                  <AppEmptyState
                    v-if="allSkills.length === 0"
                    :title="t('skills.emptyTitle')"
                    :description="t('skills.noSkillsAvailable')"
                  >
                    <template #icon><NIcon :size="22" :component="AutoAwesomeOutlined" /></template>
                    <template #action>
                      <NButton type="primary" size="small" @click="router.push('/studio/skills')">
                        {{ t('skills.goSkillsManage') }}
                      </NButton>
                    </template>
                  </AppEmptyState>

                  <div v-else class="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <NTooltip
                      v-for="sk in filteredSkills"
                      :key="sk.name"
                      :disabled="!sk.description"
                      placement="top"
                      :style="{ maxWidth: '340px' }"
                    >
                      <template #trigger>
                        <label
                          class="g-transition flex cursor-pointer items-center justify-between gap-2 border px-3 py-2.5"
                          :style="{
                            background: form.skill_names.includes(sk.name)
                              ? 'var(--g-bg-active)'
                              : 'var(--g-bg-canvas)',
                            borderColor: form.skill_names.includes(sk.name)
                              ? 'var(--g-primary)'
                              : 'var(--g-border)',
                            borderRadius: 'var(--g-radius-sm)',
                          }"
                        >
                          <div class="flex min-w-0 items-center gap-2">
                            <NCheckbox
                              size="small"
                              :checked="form.skill_names.includes(sk.name)"
                              @update:checked="(val) => toggleSkillSelection(sk.name, val)"
                            />
                            <div class="min-w-0">
                              <div
                                class="truncate font-mono text-xs font-medium"
                                style="color: var(--g-text-primary)"
                              >
                                {{ sk.name }}
                              </div>
                              <div
                                v-if="sk.title && sk.title !== sk.name"
                                class="truncate text-[11px]"
                                style="color: var(--g-text-tertiary)"
                              >
                                {{ sk.title }}
                              </div>
                            </div>
                          </div>

                          <AppStatusBadge
                            variant="neutral"
                            :label="t('skills.filesCount', { count: sk.file_count })"
                            :dot="false"
                          />
                        </label>
                      </template>
                      <span class="text-xs leading-relaxed">{{ sk.description }}</span>
                    </NTooltip>
                  </div>
                </div>

                <!-- PANEL 6: Knowledge Base & Memory Strategy -->
                <div
                  v-else-if="activeTab === 'knowledge_memory'"
                  key="knowledge_memory"
                  class="flex flex-col gap-5"
                >
                  <div
                    class="border p-4"
                    style="
                      background: var(--g-bg-canvas);
                      border-color: var(--g-border);
                      border-radius: var(--g-radius-sm);
                    "
                  >
                    <div class="mb-3 flex items-center justify-between gap-2">
                      <div class="text-xs font-semibold" style="color: var(--g-text-primary)">
                        {{ t('agents.detail.memoryTitle') }}
                      </div>
                      <NButton size="tiny" quaternary @click="router.push('/studio/memory')">
                        {{ t('memory.openExplorer') }}
                      </NButton>
                    </div>
                    <div class="flex flex-col gap-3.5">
                      <div class="flex items-center justify-between gap-4">
                        <span class="text-xs" style="color: var(--g-text-primary)">
                          {{ t('agents.detail.enableScratchpad') }}
                        </span>
                        <NSwitch v-model:value="form.enable_scratchpad" />
                      </div>
                      <div class="flex items-center justify-between gap-4">
                        <span class="text-xs" style="color: var(--g-text-primary)">
                          {{ t('agents.detail.enableLongTerm') }}
                        </span>
                        <NSwitch v-model:value="form.enable_long_term_memory" />
                      </div>
                      <div class="flex items-center justify-between gap-4">
                        <span class="text-xs" style="color: var(--g-text-primary)">
                          {{ t('memory.enableSubagents') }}
                        </span>
                        <NSwitch v-model:value="form.enable_subagents" />
                      </div>
                      <template v-if="form.enable_long_term_memory">
                        <div class="flex items-center justify-between gap-4">
                          <span class="text-xs" style="color: var(--g-text-primary)">
                            {{ t('memory.includeGlobal') }}
                          </span>
                          <NSwitch v-model:value="form.include_global_memory" />
                        </div>
                        <div class="flex items-center justify-between gap-4">
                          <span class="text-xs" style="color: var(--g-text-primary)">
                            {{ t('memory.accessMode') }}
                          </span>
                          <NRadioGroup v-model:value="form.memory_mode" size="small">
                            <NRadioButton value="read_write">{{ t('memory.modeReadWrite') }}</NRadioButton>
                            <NRadioButton value="read_only">{{ t('memory.modeReadOnly') }}</NRadioButton>
                          </NRadioGroup>
                        </div>
                      </template>
                      <div class="grid grid-cols-1 gap-4 pt-1 sm:grid-cols-2">
                        <div>
                          <div class="mb-1 text-xs" style="color: var(--g-text-secondary)">
                            {{ t('agents.detail.compactRatio') }}:
                            <span class="font-mono font-medium">
                              {{ Math.round(form.context_compact_ratio * 100) }}%
                            </span>
                          </div>
                          <NSlider
                            v-model:value="form.context_compact_ratio"
                            :min="0.4"
                            :max="0.95"
                            :step="0.05"
                          />
                        </div>
                        <div>
                          <div class="mb-1 text-xs" style="color: var(--g-text-secondary)">
                            {{ t('agents.detail.keepRecent') }}
                          </div>
                          <NInputNumber
                            v-model:value="form.keep_recent_messages"
                            :min="4"
                            :max="50"
                            class="w-full"
                          />
                        </div>
                      </div>
                    </div>
                  </div>

                  <div
                    class="border"
                    style="border-color: var(--g-border-subtle); border-radius: var(--g-radius-sm)"
                  >
                    <AppEmptyState
                      :title="t('agents.detail.kbEmptyTitle')"
                      :description="t('agents.detail.kbEmptyDesc')"
                    >
                      <template #icon><NIcon :size="22" :component="MenuBookOutlined" /></template>
                    </AppEmptyState>
                  </div>
                </div>
              </Transition>
            </div>
          </AppSurfaceCard>
        </div>

        <!-- RIGHT 4 COLS: Live Configuration Snapshot + Interactive Debug Chat Preview -->
        <aside class="flex flex-col gap-3.5 lg:col-span-4">
          <!-- Live Snapshot Card -->
          <AppSurfaceCard :title="t('agents.detail.snapshotTitle')">
            <div class="flex flex-col gap-2.5 text-xs">
              <div class="flex items-center justify-between gap-2">
                <span style="color: var(--g-text-secondary)">Chat LLM</span>
                <span class="truncate font-mono text-[11px] font-medium" style="color: var(--g-text-primary)">
                  {{ selectedModelLabel }}
                </span>
              </div>
              <div class="flex items-center justify-between gap-2">
                <span style="color: var(--g-text-secondary)">{{ t('agents.form.loopEngine') }}</span>
                <span class="font-mono text-[11px]" style="color: var(--g-primary-text)">
                  {{ form.loop_engine }} · {{ form.sandbox_mode }}
                </span>
              </div>
              <div class="flex items-center justify-between gap-2">
                <span style="color: var(--g-text-secondary)">{{ t('agents.detail.tabs.prompts') }}</span>
                <span class="font-mono text-[11px]" style="color: var(--g-text-primary)">
                  {{ form.system_prompt.length }} chars
                </span>
              </div>
              <div class="border-t pt-2.5" style="border-color: var(--g-border-subtle)">
                <div class="mb-1.5 flex items-center justify-between">
                  <span style="color: var(--g-text-secondary)">
                    {{ t('agents.colTools') }} ({{ boundToolsList.length }})
                  </span>
                  <button
                    type="button"
                    class="border-0 bg-transparent p-0 text-[11px] cursor-pointer"
                    style="color: var(--g-primary-text)"
                    @click="activeTab = 'tools'"
                  >
                    {{ t('common.edit') }}
                  </button>
                </div>
                <div v-if="boundToolsList.length > 0" class="flex flex-wrap gap-1">
                  <span
                    v-for="tool in boundToolsList"
                    :key="tool.id"
                    class="px-1.5 py-0.5 font-mono text-[10px]"
                    style="
                      background: var(--g-bg-subtle);
                      color: var(--g-text-secondary);
                      border-radius: var(--g-radius-xs);
                    "
                  >
                    {{ tool.name }}
                  </span>
                </div>
                <div v-else class="text-[11px]" style="color: var(--g-text-tertiary)">—</div>
              </div>

              <div class="border-t pt-2.5" style="border-color: var(--g-border-subtle)">
                <div class="mb-1.5 flex items-center justify-between">
                  <span style="color: var(--g-text-secondary)">
                    MCP ({{ boundMcpList.length }})
                  </span>
                  <button
                    type="button"
                    class="border-0 bg-transparent p-0 text-[11px] cursor-pointer"
                    style="color: var(--g-primary-text)"
                    @click="activeTab = 'mcp'"
                  >
                    {{ t('common.edit') }}
                  </button>
                </div>
                <div v-if="boundMcpList.length > 0" class="flex flex-wrap gap-1">
                  <span
                    v-for="srv in boundMcpList"
                    :key="srv.id"
                    class="px-1.5 py-0.5 font-mono text-[10px]"
                    style="
                      background: var(--g-primary-soft);
                      color: var(--g-primary-text);
                      border-radius: var(--g-radius-xs);
                    "
                  >
                    {{ mcpSnapshotLabel(srv) }}
                  </span>
                </div>
                <div v-else class="text-[11px]" style="color: var(--g-text-tertiary)">—</div>
              </div>

              <div class="border-t pt-2.5" style="border-color: var(--g-border-subtle)">
                <div class="mb-1.5 flex items-center justify-between">
                  <span style="color: var(--g-text-secondary)">
                    {{ t('agents.detail.tabs.skills') }} ({{ boundSkillsList.length }})
                  </span>
                  <button
                    type="button"
                    class="border-0 bg-transparent p-0 text-[11px] cursor-pointer"
                    style="color: var(--g-primary-text)"
                    @click="activeTab = 'skills'"
                  >
                    {{ t('common.edit') }}
                  </button>
                </div>
                <div v-if="boundSkillsList.length > 0" class="flex flex-wrap gap-1">
                  <span
                    v-for="sk in boundSkillsList"
                    :key="sk.name"
                    class="px-1.5 py-0.5 font-mono text-[10px]"
                    style="
                      background: var(--g-bg-subtle);
                      color: var(--g-text-secondary);
                      border-radius: var(--g-radius-xs);
                    "
                  >
                    {{ sk.name }}
                  </span>
                </div>
                <div v-else class="text-[11px]" style="color: var(--g-text-tertiary)">—</div>
              </div>

              <NAlert
                v-if="
                  form.loop_engine === 'direct_chat' &&
                  (form.tool_ids.length > 0 || form.mcp_server_ids.length > 0)
                "
                type="info"
                :show-icon="false"
                class="mt-1"
              >
                <span class="text-[11px] leading-relaxed">
                  {{ t('agents.detail.directChatToolsHint') }}
                </span>
              </NAlert>
            </div>
          </AppSurfaceCard>

          <!-- Interactive Live Debug Chat Preview -->
          <AppSurfaceCard :title="t('agents.detail.debugTitle')" no-padding>
            <template #extra>
              <NButton size="tiny" quaternary @click="resetDebugChat">
                <template #icon><NIcon :size="14" :component="RefreshOutlined" /></template>
                {{ t('agents.detail.debugReset') }}
              </NButton>
            </template>

            <div class="flex h-[420px] flex-col">
              <div
                ref="debugScrollEl"
                class="min-h-0 flex-1 overflow-y-auto p-3"
                style="background: var(--g-bg-canvas)"
              >
                <div
                  v-if="debugMessages.length === 0"
                  class="flex h-full items-center justify-center px-4 text-center text-xs leading-relaxed"
                  style="color: var(--g-text-tertiary)"
                >
                  {{ t('agents.detail.debugEmpty') }}
                </div>

                <div v-else class="flex flex-col gap-3">
                  <div
                    v-for="m in debugMessages"
                    :key="m.id"
                    class="flex flex-col"
                    :class="m.role === 'user' ? 'items-end' : 'items-start'"
                  >
                    <div
                      v-if="m.role === 'user'"
                      class="max-w-[90%] border px-3 py-1.5 text-xs whitespace-pre-wrap"
                      style="
                        background: var(--g-primary-soft);
                        border-color: var(--g-border-subtle);
                        color: var(--g-text-primary);
                        border-radius: var(--g-radius-sm);
                      "
                    >
                      {{ m.content }}
                    </div>

                    <div
                      v-else
                      class="w-full border p-2.5"
                      style="
                        background: var(--g-bg-surface);
                        border-color: var(--g-border-subtle);
                        border-radius: var(--g-radius-sm);
                      "
                    >
                      <AgentActivityTimeline
                        :reasoning="m.reasoning_content"
                        :tool-calls="m.tool_calls_json"
                        :streaming="m.streaming"
                        :latency-ms="m.latency_ms"
                        :started-at-ms="m.started_at_ms"
                        :has-content="Boolean(m.content)"
                        compact
                      />
                      <AppMarkdown v-if="m.content" :content="m.content" />
                      <span
                        v-else-if="m.streaming"
                        class="font-mono text-[11px]"
                        style="color: var(--g-text-tertiary)"
                      >
                        ...
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              <!-- Compact Debug Composer -->
              <div
                class="flex items-end gap-2 border-t p-2.5"
                style="background: var(--g-bg-surface); border-color: var(--g-border-subtle)"
              >
                <NInput
                  v-model:value="debugDraft"
                  type="textarea"
                  size="small"
                  :autosize="{ minRows: 1, maxRows: 3 }"
                  :disabled="debugStreaming"
                  :placeholder="t('agents.detail.debugInputPh')"
                  @keydown="handleDebugKeydown"
                />
                <NButton
                  v-if="debugStreaming"
                  size="small"
                  type="error"
                  secondary
                  @click="stopDebugStream"
                >
                  <template #icon><NIcon :component="StopOutlined" /></template>
                </NButton>
                <NButton
                  v-else
                  size="small"
                  type="primary"
                  :disabled="!debugDraft.trim()"
                  @click="sendDebugMessage"
                >
                  <template #icon><NIcon :component="ArrowUpwardOutlined" /></template>
                </NButton>
              </div>
            </div>
          </AppSurfaceCard>
        </aside>
      </div>
    </div>

    <ToolTestModal v-model:show="testModalShow" :tool="testingTool" />
  </NSpin>
</template>
