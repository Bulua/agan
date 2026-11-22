<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ChatToolCallRecord } from '@/types/chat'

const props = withDefaults(
  defineProps<{
    reasoning?: string
    toolCalls?: ChatToolCallRecord[]
    streaming?: boolean
    latencyMs?: number
    startedAtMs?: number
    hasContent?: boolean
    compact?: boolean
  }>(),
  {
    reasoning: '',
    toolCalls: () => [],
    streaming: false,
    latencyMs: 0,
    startedAtMs: undefined,
    hasContent: false,
    compact: false,
  },
)

const { t, locale } = useI18n()

const rootExpanded = ref(false)
const groupExpanded = ref(true)
const reasoningExpanded = ref(false)
const expandedStepIds = ref<Record<string, boolean>>({})
const showAllSteps = ref(false)

// Live ticking timer while streaming
const nowMs = ref(Date.now())
let timerHandle: number | undefined

function startTimer(): void {
  stopTimer()
  nowMs.value = Date.now()
  timerHandle = window.setInterval(() => {
    nowMs.value = Date.now()
  }, 150)
}

function stopTimer(): void {
  if (timerHandle !== undefined) {
    window.clearInterval(timerHandle)
    timerHandle = undefined
  }
}

watch(
  () => props.streaming,
  (isLive, wasLive) => {
    if (isLive) {
      // Keep tool calls & reasoning collapsed by default while agent works
      if (!wasLive) {
        rootExpanded.value = false
        reasoningExpanded.value = false
      }
      startTimer()
    } else {
      stopTimer()
      // Auto-collapse entire activity timeline when turn completes
      rootExpanded.value = false
      reasoningExpanded.value = false
      expandedStepIds.value = {}
      showAllSteps.value = false
    }
  },
  { immediate: true },
)

onBeforeUnmount(stopTimer)

const hasReasoning = computed(() => Boolean(props.reasoning && props.reasoning.trim().length > 0))
const hasTools = computed(() => (props.toolCalls?.length || 0) > 0)
const shouldRender = computed(
  () => hasReasoning.value || hasTools.value || Boolean(props.streaming && !props.hasContent),
)

function formatDuration(ms: number): string {
  const safeMs = Math.max(0, ms)
  if (safeMs < 1000) return `${(safeMs / 1000).toFixed(1)}s`
  const totalSec = safeMs / 1000
  if (totalSec < 60) {
    return props.streaming ? `${totalSec.toFixed(1)}s` : `${Math.round(totalSec)}s`
  }
  const mins = Math.floor(totalSec / 60)
  const secs = Math.round(totalSec % 60)
  return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`
}

const headerText = computed(() => {
  if (props.streaming) {
    const base = props.startedAtMs ?? nowMs.value
    const elapsed = Math.max(0, nowMs.value - base)
    return t('chat.activity.working', { time: formatDuration(elapsed) })
  }
  const totalMs =
    props.latencyMs && props.latencyMs > 0
      ? props.latencyMs
      : (props.toolCalls || []).reduce((acc, tc) => acc + (tc.duration_ms || 0), 0)
  return t('chat.activity.workedFor', { time: formatDuration(totalMs || 500) })
})

const groupSummaryText = computed(() => {
  const calls = props.toolCalls || []
  if (calls.length === 0) {
    return hasReasoning.value ? t('chat.activity.groupThoughtOnly') : t('chat.thinkingActive')
  }
  const parts: string[] = []
  const seen = new Set<string>()
  const addPart = (key: string, label: string) => {
    if (!seen.has(key)) {
      seen.add(key)
      parts.push(label)
    }
  }
  for (const tc of calls) {
    if (tc.tool_name === 'read_file' || tc.tool_name === 'list_files')
      addPart('read', t('chat.activity.groupRead'))
    else if (tc.tool_name === 'grep_search' || tc.tool_name === 'web_search')
      addPart('search', t('chat.activity.groupSearch'))
    else if (tc.tool_name === 'bash_exec' || tc.tool_name === 'python_exec')
      addPart('run', t('chat.activity.groupRun'))
    else if (tc.tool_name === 'write_file' || tc.tool_name === 'edit_file')
      addPart('write', t('chat.activity.groupWrite'))
    else if (tc.tool_name === 'http_request' || tc.tool_name === 'web_fetch')
      addPart('http', t('chat.activity.groupHttp'))
    else if (tc.tool_name === 'scratchpad_update' || tc.tool_name === 'submit_flag')
      addPart('note', t('chat.activity.groupNote'))
    else if (
      tc.tool_name === 'load_skill' ||
      tc.tool_name === 'create_skill' ||
      tc.tool_name === 'edit_skill_file'
    )
      addPart('skill', t('chat.activity.groupSkill'))
    else if (
      tc.tool_name === 'memory_search' ||
      tc.tool_name === 'memory_read' ||
      tc.tool_name === 'memory_write' ||
      tc.tool_name === 'memory_edit'
    )
      addPart('memory', t('chat.activity.groupMemory'))
    else if (
      tc.tool_name === 'spawn_subagent' ||
      tc.tool_name === 'send_agent_message' ||
      tc.tool_name === 'wait_subagents'
    )
      addPart('subagent', t('chat.activity.groupSubagent'))
    else if (tc.tool_name === '__user_steering__')
      addPart('steer', t('chat.activity.groupSteer'))
    else if (tc.tool_name === '__context_compact__')
      addPart('compact', t('chat.activity.groupCompact'))
    else addPart('tool', t('chat.activity.groupTool'))
  }
  const sep = locale.value.startsWith('zh') ? '、' : ', '
  return parts.join(sep)
})

interface RenderedStep {
  tc: ChatToolCallRecord
  verb: string
  iconKind:
    | 'py'
    | 'ts'
    | 'js'
    | 'vue'
    | 'json'
    | 'doc'
    | 'term'
    | 'http'
    | 'flag'
    | 'note'
    | 'mcp'
    | 'skill'
    | 'memory'
    | 'subagent'
    | 'steer'
    | 'compact'
    | 'search'
    | 'folder'
    | 'tool'
  label: string
  fullTitle: string
  lineRange: string
  addedLines: string
  removedLines: string
  errorTag: string
}

function basename(rawPath: string): string {
  const clean = rawPath.replace(/\\/g, '/').replace(/\/+$/, '')
  const idx = clean.lastIndexOf('/')
  return idx >= 0 ? clean.slice(idx + 1) : clean
}

function inferFileIcon(filename: string): RenderedStep['iconKind'] {
  const lower = filename.toLowerCase()
  if (lower.endsWith('.py')) return 'py'
  if (lower.endsWith('.ts') || lower.endsWith('.tsx')) return 'ts'
  if (lower.endsWith('.js') || lower.endsWith('.jsx') || lower.endsWith('.mjs')) return 'js'
  if (lower.endsWith('.vue')) return 'vue'
  if (
    lower.endsWith('.json') ||
    lower.endsWith('.yaml') ||
    lower.endsWith('.yml') ||
    lower.endsWith('.toml')
  )
    return 'json'
  if (lower.endsWith('.sh') || lower.endsWith('.bash')) return 'term'
  return 'doc'
}

const renderedSteps = computed<RenderedStep[]>(() => {
  return (props.toolCalls || []).map((tc) => {
    const args = tc.arguments || {}
    const meta = tc.metadata || {}
    let verb = t('chat.activity.verbTool')
    let iconKind: RenderedStep['iconKind'] = 'tool'
    let label = tc.tool_name
    let fullTitle = tc.tool_name
    let lineRange = ''
    let addedLines = ''
    let removedLines = ''
    let errorTag = ''

    if (tc.tool_name === 'read_file') {
      verb = t('chat.activity.verbRead')
      const rawPath = String(args.path || meta.path || '')
      label = rawPath ? basename(rawPath) : 'file'
      fullTitle = String(meta.host_path || rawPath || label)
      iconKind = inferFileIcon(label)
      const s = meta.start_line ?? args.start_line
      const e = meta.end_line ?? args.end_line ?? meta.total_lines
      if (s && e) lineRange = `#L${s}-${e}`
      else if (meta.total_lines) lineRange = `#L1-${meta.total_lines}`
    } else if (tc.tool_name === 'write_file') {
      const mode = String(args.mode || meta.mode || 'overwrite').toLowerCase()
      verb = mode === 'append' ? t('chat.activity.verbAppend') : t('chat.activity.verbWrite')
      const rawPath = String(args.path || meta.path || '')
      label = rawPath ? basename(rawPath) : 'file'
      fullTitle = String(meta.host_path || rawPath || label)
      iconKind = inferFileIcon(label)
      const lines =
        meta.lines_written ??
        (typeof args.content === 'string' && args.content.length > 0
          ? args.content.split(/\r?\n/).length
          : 0)
      if (lines > 0) addedLines = `+${lines}`
    } else if (tc.tool_name === 'edit_file') {
      verb = t('chat.activity.verbEdit')
      const rawPath = String(args.path || meta.path || '')
      label = rawPath ? basename(rawPath) : 'file'
      fullTitle = String(meta.host_path || rawPath || label)
      iconKind = inferFileIcon(label)
      const added = Number(meta.lines_added || 0)
      const removed = Number(meta.lines_removed || 0)
      if (added > 0) addedLines = `+${added}`
      if (removed > 0) removedLines = `-${removed}`
    } else if (tc.tool_name === 'list_files') {
      verb = t('chat.activity.verbList')
      iconKind = 'folder'
      const rawPath = String(args.path || meta.path || '.')
      const pat = String(args.pattern || '*')
      label = pat && pat !== '*' ? `${rawPath} (${pat})` : rawPath
      fullTitle = label
    } else if (tc.tool_name === 'grep_search') {
      verb = t('chat.activity.verbGrep')
      iconKind = 'search'
      const pat = String(args.pattern || '')
      const scope = String(args.path || '.')
      label = scope && scope !== '.' ? `"${pat}" in ${scope}` : `"${pat}"`
      fullTitle = label
    } else if (tc.tool_name === 'web_search') {
      verb = t('chat.activity.verbSearch')
      iconKind = 'search'
      label = String(args.query || '')
      fullTitle = label
    } else if (tc.tool_name === 'web_fetch') {
      verb = t('chat.activity.verbFetch')
      iconKind = 'http'
      label = String(args.url || meta.url || '')
      fullTitle = label
    } else if (tc.tool_name === 'bash_exec') {
      verb = t('chat.activity.verbRun')
      iconKind = 'term'
      const cmd = String(args.command || '').trim()
      label = cmd || 'bash'
      fullTitle = cmd
      if (tc.status === 'done' && !tc.success && meta.exit_code !== undefined) {
        errorTag = `exit ${meta.exit_code}`
      }
    } else if (tc.tool_name === 'python_exec') {
      verb = t('chat.activity.verbPython')
      iconKind = 'py'
      const fn = String(args.filename || meta.script_path || 'script.py')
      label = basename(fn)
      fullTitle = fn
    } else if (tc.tool_name === 'http_request') {
      verb = t('chat.activity.verbHttp')
      iconKind = 'http'
      const method = String(args.method || 'GET').toUpperCase()
      const url = String(args.url || '')
      label = `${method} ${url}`.trim()
      fullTitle = label
    } else if (tc.tool_name === 'scratchpad_update') {
      verb = t('chat.activity.verbNote')
      iconKind = 'note'
      label = 'Working Scratchpad'
      fullTitle = label
    } else if (tc.tool_name === 'submit_flag') {
      verb = t('chat.activity.verbFlag')
      iconKind = 'flag'
      label = String(args.flag || 'flag')
      fullTitle = label
    } else if (tc.tool_name === 'load_skill') {
      verb = t('chat.activity.verbSkill')
      iconKind = 'skill'
      const sName = String(meta.skill_name || args.name || 'skill')
      const fPath = String(args.file_path || '')
      label = fPath ? `${sName} / ${fPath}` : sName
      fullTitle = label
    } else if (tc.tool_name === 'create_skill') {
      verb = t('chat.activity.verbSkillCreate')
      iconKind = 'skill'
      label = String(meta.skill_name || args.name || 'skill')
      fullTitle = label
    } else if (tc.tool_name === 'edit_skill_file') {
      verb = t('chat.activity.verbSkillEdit')
      iconKind = 'skill'
      const act = String(args.action || meta.action || '')
      const sName = String(meta.skill_name || args.skill_name || '')
      const fPath = String(meta.file_path || args.file_path || '')
      label = [act, sName && fPath ? `${sName}/${fPath}` : sName].filter(Boolean).join(' ')
      fullTitle = label
      const added = Number(meta.lines_added || 0)
      const removed = Number(meta.lines_removed || 0)
      if (added > 0) addedLines = `+${added}`
      if (removed > 0) removedLines = `-${removed}`
    } else if (tc.tool_name === 'memory_search') {
      verb = t('chat.activity.verbMemorySearch')
      iconKind = 'memory'
      const scope = String(args.scope || meta.scope || 'all')
      const q = String(args.query || meta.query || '*')
      label = `${scope} · ${q}`
      fullTitle = label
    } else if (tc.tool_name === 'memory_read') {
      verb = t('chat.activity.verbMemoryRead')
      iconKind = 'memory'
      const scope = String(meta.scope || args.scope || 'agent')
      const p = String(meta.path || args.path || '')
      label = `${scope} / ${p}`
      fullTitle = label
    } else if (tc.tool_name === 'memory_write') {
      verb = t('chat.activity.verbMemoryWrite')
      iconKind = 'memory'
      const scope = String(meta.scope || args.scope || 'agent')
      const p = String(meta.path || args.path || '')
      label = `${scope} / ${p}`
      fullTitle = label
      const lines = Number(meta.lines_written || 0)
      if (lines > 0) addedLines = `+${lines}`
    } else if (tc.tool_name === 'memory_edit') {
      const act = String(args.action || meta.action || 'edit')
      verb =
        act === 'delete'
          ? t('chat.activity.verbMemoryDelete')
          : t('chat.activity.verbMemoryEdit')
      iconKind = 'memory'
      const scope = String(meta.scope || args.scope || 'agent')
      const p = String(meta.path || args.path || '')
      label = `${scope} / ${p}`
      fullTitle = label
      const added = Number(meta.lines_added || 0)
      const removed = Number(meta.lines_removed || 0)
      if (added > 0) addedLines = `+${added}`
      if (removed > 0) removedLines = `-${removed}`
    } else if (tc.tool_name === 'spawn_subagent') {
      verb = t('chat.activity.verbSubagentSpawn')
      iconKind = 'subagent'
      const role = String(meta.role || args.role || 'worker')
      const liveAct = String(meta.current_action || '')
      const stepCount = Number(meta.step_count || 0)
      const suffix =
        tc.status === 'running' && liveAct
          ? ` · ${liveAct}`
          : stepCount > 0
            ? ` (${stepCount} steps)`
            : ''
      label = `${role}${suffix}`
      fullTitle = `${role}: ${String(args.task || '')}`
    } else if (tc.tool_name === 'send_agent_message') {
      verb = t('chat.activity.verbSubagentMsg')
      iconKind = 'subagent'
      const sid = String(meta.subagent_id || args.subagent_id || '')
      const role = String(meta.role || '')
      label = role ? `${role} (${sid})` : sid || 'subagent'
      fullTitle = `${label}: ${String(args.message || '')}`
    } else if (tc.tool_name === 'wait_subagents') {
      verb = t('chat.activity.verbSubagentWait')
      iconKind = 'subagent'
      const total = Number(meta.count || 0)
      const done = Number(meta.done_count || 0)
      label = total > 0 ? `${done}/${total}` : 'all'
      fullTitle = label
    } else if (tc.tool_name === '__user_steering__') {
      verb = t('chat.activity.verbSteer')
      iconKind = 'steer'
      const text = String(args.content || '').trim()
      label = text
      fullTitle = text
    } else if (tc.tool_name === '__context_compact__') {
      verb = t('chat.activity.verbCompact')
      iconKind = 'compact'
      const stage = String(meta.stage || args.stage || 'auto')
      const saved = Number(meta.tokens_saved || 0)
      label = saved > 0 ? `${stage} · -${saved.toLocaleString()} tokens` : stage
      fullTitle = `${stage} compaction`
    } else if (tc.tool_name.startsWith('mcp__') || meta.mcp_server) {
      verb = 'MCP'
      iconKind = 'mcp'
      const srv = String(meta.mcp_server || tc.tool_name.split('__')[1] || 'mcp')
      const mtool = String(meta.mcp_tool || tc.tool_name.split('__').slice(2).join('__') || tc.tool_name)
      label = `${srv} / ${mtool}`
      fullTitle = `${label} ${JSON.stringify(args)}`
    } else {
      const firstVal = Object.values(args).find(
        (v) => typeof v === 'string' && v.trim().length > 0,
      )
      label = firstVal ? `${tc.tool_name} (${String(firstVal).slice(0, 48)})` : tc.tool_name
      fullTitle = JSON.stringify(args)
    }

    if (tc.status === 'done' && !tc.success && !errorTag) {
      errorTag = 'failed'
    }

    return { tc, verb, iconKind, label, fullTitle, lineRange, addedLines, removedLines, errorTag }
  })
})

const collapsedPreviewText = computed(() => {
  if (props.streaming) {
    const running = [...renderedSteps.value].reverse().find((s) => s.tc.status === 'running')
    if (running) {
      return `${running.verb} ${running.label}`
    }
    if (renderedSteps.value.length > 0) {
      return groupSummaryText.value
    }
    return hasReasoning.value ? t('chat.thinkingActive') : ''
  }
  if (renderedSteps.value.length > 0) {
    return groupSummaryText.value
  }
  return hasReasoning.value ? t('chat.activity.groupThoughtOnly') : ''
})

const maxVisibleSteps = computed(() => (props.compact ? 5 : 9))
const hasOverflow = computed(
  () => !showAllSteps.value && renderedSteps.value.length > maxVisibleSteps.value,
)
const visibleSteps = computed(() =>
  hasOverflow.value ? renderedSteps.value.slice(0, maxVisibleSteps.value) : renderedSteps.value,
)

function toggleStep(id: string): void {
  expandedStepIds.value[id] = !expandedStepIds.value[id]
}
</script>

<template>
  <div v-if="shouldRender" class="activity-root mb-3 select-none">
    <!-- Top Header: Worked for Xm · Explored files, ran commands ▸ -->
    <button
      type="button"
      class="activity-trigger g-transition inline-flex max-w-full items-center gap-1.5 border-0 bg-transparent px-0.5 py-0.5 text-[13px] cursor-pointer"
      @click="rootExpanded = !rootExpanded"
    >
      <span
        v-if="streaming"
        class="inline-block h-1.5 w-1.5 shrink-0 animate-pulse"
        style="background: var(--g-primary); border-radius: 50%"
      />
      <span class="shrink-0">{{ headerText }}</span>
      <template v-if="!rootExpanded && collapsedPreviewText">
        <span class="shrink-0" style="color: var(--g-text-tertiary)">·</span>
        <span class="min-w-0 truncate" style="color: var(--g-text-tertiary)">
          {{ collapsedPreviewText }}
        </span>
        <span
          v-if="renderedSteps.length > 0"
          class="shrink-0 font-mono text-[11px]"
          style="color: var(--g-text-tertiary)"
        >
          ({{ renderedSteps.length }})
        </span>
      </template>
      <svg
        class="chevron-svg g-transition shrink-0"
        :class="{ rotated: !rootExpanded }"
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <polyline points="6 9 12 15 18 9" />
      </svg>
    </button>

    <!-- Collapsible Activity Body -->
    <Transition name="g-fade">
      <div v-if="rootExpanded" class="mt-1.5 pl-3">
        <!-- Second-Level Group Trigger: Explored files, ran commands, edited files ⌄ -->
        <button
          v-if="hasTools"
          type="button"
          class="activity-subtrigger g-transition inline-flex items-center gap-1 border-0 bg-transparent px-0.5 py-0.5 text-[13px] cursor-pointer"
          @click="groupExpanded = !groupExpanded"
        >
          <span>{{ groupSummaryText }}</span>
          <svg
            class="chevron-svg g-transition"
            :class="{ rotated: !groupExpanded }"
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>

        <!-- Reasoning / Thinking Stream -->
        <div v-if="hasReasoning" :class="hasTools ? 'mt-1.5 pl-3' : 'mt-1'">
          <button
            type="button"
            class="step-row g-transition flex w-full items-center gap-2 border-0 bg-transparent px-1.5 py-1 text-left text-[12.5px] cursor-pointer"
            @click="reasoningExpanded = !reasoningExpanded"
          >
            <span class="step-verb shrink-0">{{
              streaming && !hasContent ? t('chat.thinkingActive') : t('chat.thinkingProcess')
            }}</span>
            <span class="text-[11px] font-mono" style="color: var(--g-text-tertiary)">
              {{ reasoning.length.toLocaleString() }} chars
            </span>
            <svg
              class="chevron-svg g-transition ml-0.5"
              :class="{ rotated: !reasoningExpanded }"
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </button>

          <div
            v-if="reasoningExpanded"
            class="mt-1 mb-1.5 ml-1.5 max-h-48 overflow-y-auto border-l-2 py-0.5 pl-3 font-mono text-[12px] leading-relaxed whitespace-pre-wrap select-text"
            style="border-color: var(--g-border); color: var(--g-text-secondary)"
          >
            {{ reasoning }}
          </div>
        </div>

        <!-- Compact Single-Line Action Rows -->
        <div v-if="hasTools && groupExpanded" class="relative mt-1 pl-3">
          <div class="flex flex-col gap-0.5" :class="{ 'steps-fade-mask': hasOverflow }">
            <div v-for="step in visibleSteps" :key="step.tc.id" class="flex flex-col">
              <button
                type="button"
                class="step-row g-transition flex w-full items-center gap-1.5 border-0 bg-transparent px-1.5 py-1 text-left text-[13px] cursor-pointer"
                :title="step.fullTitle"
                @click="toggleStep(step.tc.id)"
              >
                <!-- Action Verb -->
                <span class="step-verb shrink-0">{{ step.verb }}</span>

                <!-- File-Type / Tool Micro Icon -->
                <span class="micro-icon inline-flex h-4 w-4 shrink-0 items-center justify-center">
                  <!-- Python -->
                  <svg v-if="step.iconKind === 'py'" width="14" height="14" viewBox="0 0 24 24" fill="none">
                    <path
                      d="M11.9 2C7.8 2 8.1 3.8 8.1 3.8v1.9h4v.6H6.5S4 6 4 9.9c0 3.9 2.2 3.8 2.2 3.8h1.3v-1.8s-.1-2.2 2.2-2.2h3.8s2.1 0 2.1-2V4.1S16 2 11.9 2zm-2 1.2a.75.75 0 1 1 0 1.5.75.75 0 0 1 0-1.5z"
                      fill="#3776AB"
                    />
                    <path
                      d="M12.1 22c4.1 0 3.8-1.8 3.8-1.8v-1.9h-4v-.6h5.6s2.5.3 2.5-3.6c0-3.9-2.2-3.8-2.2-3.8h-1.3v1.8s.1 2.2-2.2 2.2h-3.8s-2.1 0-2.1 2v3.6S8 22 12.1 22zm2-1.2a.75.75 0 1 1 0-1.5.75.75 0 0 1 0 1.5z"
                      fill="#2AA198"
                    />
                  </svg>
                  <!-- TypeScript -->
                  <span
                    v-else-if="step.iconKind === 'ts'"
                    class="font-mono text-[10px] font-bold leading-none tracking-tighter"
                    style="color: #3178c6"
                  >
                    TS
                  </span>
                  <!-- JavaScript -->
                  <span
                    v-else-if="step.iconKind === 'js'"
                    class="font-mono text-[10px] font-bold leading-none tracking-tighter"
                    style="color: #d97706"
                  >
                    JS
                  </span>
                  <!-- Vue -->
                  <svg v-else-if="step.iconKind === 'vue'" width="13" height="13" viewBox="0 0 24 24">
                    <path d="M2 4h4l6 10.4L18 4h4L12 21.3 2 4z" fill="#41B883" />
                    <path d="M6.8 4h3.4L12 7.2 13.8 4h3.4L12 13 6.8 4z" fill="#35495E" />
                  </svg>
                  <!-- JSON / Config -->
                  <span
                    v-else-if="step.iconKind === 'json'"
                    class="font-mono text-[10px] font-bold leading-none"
                    style="color: #d97706"
                  >
                    {}
                  </span>
                  <!-- Terminal / Bash -->
                  <svg
                    v-else-if="step.iconKind === 'term'"
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#188038"
                    stroke-width="2.2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <polyline points="4 17 10 11 4 5" />
                    <line x1="12" y1="19" x2="20" y2="19" />
                  </svg>
                  <!-- HTTP -->
                  <svg
                    v-else-if="step.iconKind === 'http'"
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#1A73E8"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <circle cx="12" cy="12" r="9" />
                    <line x1="3" y1="12" x2="21" y2="12" />
                    <path d="M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />
                  </svg>
                  <!-- Flag -->
                  <svg
                    v-else-if="step.iconKind === 'flag'"
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#188038"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" />
                    <line x1="4" y1="22" x2="4" y2="15" />
                  </svg>
                  <!-- Note / Scratchpad -->
                  <svg
                    v-else-if="step.iconKind === 'note'"
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#7C3AED"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <polyline points="14 2 14 8 20 8" />
                    <line x1="8" y1="13" x2="16" y2="13" />
                    <line x1="8" y1="17" x2="13" y2="17" />
                  </svg>
                  <!-- Loaded Skill -->
                  <svg
                    v-else-if="step.iconKind === 'skill'"
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#D97706"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M12 3l1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2z" />
                  </svg>
                  <!-- Memory Bookmark -->
                  <svg
                    v-else-if="step.iconKind === 'memory'"
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#0284C7"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                    <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                  </svg>
                  <!-- Subagent / Multi-Agent Team -->
                  <svg
                    v-else-if="step.iconKind === 'subagent'"
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#6366F1"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                    <circle cx="9" cy="7" r="4" />
                    <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
                    <path d="M16 3.13a4 4 0 0 1 0 7.75" />
                  </svg>
                  <!-- Live User Steering -->
                  <svg
                    v-else-if="step.iconKind === 'steer'"
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="var(--g-primary)"
                    stroke-width="2.2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <polyline points="15 10 20 15 15 20" />
                    <path d="M4 4v7a4 4 0 0 0 4 4h12" />
                  </svg>
                  <!-- Context Compaction -->
                  <svg
                    v-else-if="step.iconKind === 'compact'"
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#0F9D58"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <polyline points="4 14 10 14 10 20" />
                    <polyline points="20 10 14 10 14 4" />
                    <line x1="14" y1="10" x2="21" y2="3" />
                    <line x1="3" y1="21" x2="10" y2="14" />
                  </svg>
                  <!-- MCP Hub -->
                  <svg
                    v-else-if="step.iconKind === 'mcp'"
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#7C3AED"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <circle cx="12" cy="12" r="3" />
                    <circle cx="5" cy="6" r="2" />
                    <circle cx="19" cy="6" r="2" />
                    <circle cx="5" cy="18" r="2" />
                    <circle cx="19" cy="18" r="2" />
                    <line x1="9.8" y1="9.8" x2="6.5" y2="7.2" />
                    <line x1="14.2" y1="9.8" x2="17.5" y2="7.2" />
                    <line x1="9.8" y1="14.2" x2="6.5" y2="16.8" />
                    <line x1="14.2" y1="14.2" x2="17.5" y2="16.8" />
                  </svg>
                  <!-- Search (grep / web_search) -->
                  <svg
                    v-else-if="step.iconKind === 'search'"
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="var(--g-primary)"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <circle cx="11" cy="11" r="7" />
                    <line x1="21" y1="21" x2="16.65" y2="16.65" />
                  </svg>
                  <!-- Folder (list_files) -->
                  <svg
                    v-else-if="step.iconKind === 'folder'"
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#D97706"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path
                      d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"
                    />
                  </svg>
                  <!-- Document -->
                  <svg
                    v-else-if="step.iconKind === 'doc'"
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="var(--g-text-secondary)"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <polyline points="14 2 14 8 20 8" />
                  </svg>
                  <!-- Generic Tool -->
                  <svg
                    v-else
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="var(--g-text-secondary)"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <path
                      d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"
                    />
                  </svg>
                </span>

                <!-- Target Label -->
                <span
                  class="min-w-0 truncate"
                  :class="step.iconKind === 'term' ? 'font-mono text-[12.5px]' : 'font-normal'"
                  style="color: var(--g-text-primary)"
                >
                  {{ step.label }}
                </span>

                <!-- Line Range (#L1-235) -->
                <span
                  v-if="step.lineRange"
                  class="shrink-0 font-mono text-[12px]"
                  style="color: var(--g-text-tertiary)"
                >
                  {{ step.lineRange }}
                </span>

                <!-- Added / Removed Lines (+60 -12) -->
                <span
                  v-if="step.addedLines"
                  class="shrink-0 font-mono text-[12px] font-medium"
                  style="color: var(--g-success)"
                >
                  {{ step.addedLines }}
                </span>
                <span
                  v-if="step.removedLines"
                  class="shrink-0 font-mono text-[12px] font-medium"
                  style="color: var(--g-danger)"
                >
                  {{ step.removedLines }}
                </span>

                <!-- Running Indicator -->
                <span
                  v-if="step.tc.status === 'running'"
                  class="ml-1 inline-flex shrink-0 items-center gap-1 text-[11px]"
                  style="color: var(--g-primary-text)"
                >
                  <span
                    class="inline-block h-1.5 w-1.5 animate-pulse"
                    style="background: var(--g-primary); border-radius: 50%"
                  />
                  {{ t('chat.toolRunning') }}
                </span>

                <!-- Error Tag -->
                <span
                  v-else-if="step.errorTag"
                  class="ml-1 shrink-0 font-mono text-[11px]"
                  style="color: var(--g-error)"
                >
                  {{ step.errorTag }}
                </span>

                <!-- Subtle Duration on Hover -->
                <span
                  v-else-if="step.tc.duration_ms !== undefined"
                  class="step-duration ml-auto shrink-0 font-mono text-[11px]"
                  style="color: var(--g-text-tertiary)"
                >
                  {{ step.tc.duration_ms }}ms
                </span>
              </button>

              <!-- Expandable Inline Inspector -->
              <div
                v-if="expandedStepIds[step.tc.id]"
                class="my-1 ml-5 flex flex-col gap-1.5 border p-2.5 font-mono text-[11px] select-text"
                style="
                  background: var(--g-bg-canvas);
                  border-color: var(--g-border-subtle);
                  border-radius: var(--g-radius-xs);
                "
              >
                <div class="text-[10px] font-semibold uppercase tracking-wide" style="color: var(--g-text-tertiary)">
                  {{ t('chat.activity.args') }} · {{ step.tc.tool_name }}
                </div>
                <pre
                  class="m-0 max-h-36 overflow-auto whitespace-pre-wrap break-all"
                  style="color: var(--g-primary-text)"
                >{{
                  step.tc.arguments?.command
                    ? `$ ${step.tc.arguments.command}`
                    : JSON.stringify(step.tc.arguments, null, 2)
                }}</pre>

                <!-- Subagent Internal Step Trace -->
                <template
                  v-if="
                    Array.isArray(step.tc.metadata?.subagent_steps) &&
                    step.tc.metadata.subagent_steps.length > 0
                  "
                >
                  <div
                    class="mt-1 border-t pt-1.5 text-[10px] font-semibold uppercase tracking-wide"
                    style="border-color: var(--g-border-subtle); color: var(--g-text-tertiary)"
                  >
                    {{ t('chat.activity.subagentStepsTitle') }} ({{
                      step.tc.metadata.subagent_steps.length
                    }})
                  </div>
                  <div class="flex flex-col gap-0.5">
                    <div
                      v-for="(sub, sIdx) in step.tc.metadata.subagent_steps"
                      :key="sIdx"
                      class="flex items-center justify-between gap-2 text-[11px]"
                      style="color: var(--g-text-secondary)"
                    >
                      <span class="truncate">
                        <span
                          class="mr-1.5 inline-block h-1.5 w-1.5"
                          :style="{
                            background:
                              sub.status === 'running'
                                ? 'var(--g-primary)'
                                : sub.success
                                  ? 'var(--g-success)'
                                  : 'var(--g-error)',
                            borderRadius: '50%',
                          }"
                        />
                        <span style="color: var(--g-text-primary)">{{ sub.tool_name }}</span>
                      </span>
                      <span class="shrink-0" style="color: var(--g-text-tertiary)">
                        {{ sub.duration_ms }}ms
                      </span>
                    </div>
                  </div>
                </template>

                <template v-if="step.tc.output">
                  <div
                    class="mt-1 border-t pt-1.5 text-[10px] font-semibold uppercase tracking-wide"
                    style="border-color: var(--g-border-subtle); color: var(--g-text-tertiary)"
                  >
                    {{ t('chat.activity.output') }}
                  </div>
                  <pre
                    class="m-0 max-h-52 overflow-auto whitespace-pre-wrap break-words"
                    style="color: var(--g-text-secondary)"
                  >{{ step.tc.output }}</pre>
                </template>
              </div>
            </div>
          </div>

          <!-- Expand All / Collapse Extra Toggle -->
          <button
            v-if="renderedSteps.length > maxVisibleSteps"
            type="button"
            class="mt-1 border-0 bg-transparent px-1.5 py-0.5 text-[11.5px] cursor-pointer"
            style="color: var(--g-text-tertiary)"
            @click="showAllSteps = !showAllSteps"
          >
            {{
              showAllSteps
                ? t('chat.activity.collapseExtra')
                : t('chat.activity.showAll', { count: renderedSteps.length })
            }}
          </button>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.activity-trigger,
.activity-subtrigger {
  color: var(--g-text-secondary);
  border-radius: var(--g-radius-xs);
}

.activity-trigger:hover,
.activity-subtrigger:hover {
  color: var(--g-text-primary);
}

.chevron-svg {
  opacity: 0.72;
}

.chevron-svg.rotated {
  transform: rotate(-90deg);
}

.step-row {
  border-radius: var(--g-radius-xs);
  line-height: 1.55;
}

.step-row:hover {
  background: var(--g-bg-hover);
}

.step-verb {
  color: var(--g-text-secondary);
}

.step-duration {
  opacity: 0;
  transition: opacity 0.15s ease;
}

.step-row:hover .step-duration {
  opacity: 1;
}

.steps-fade-mask {
  mask-image: linear-gradient(to bottom, #000 72%, transparent 100%);
  -webkit-mask-image: linear-gradient(to bottom, #000 72%, transparent 100%);
}
</style>
