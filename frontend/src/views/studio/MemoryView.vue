<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NButton,
  NIcon,
  NInput,
  NModal,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSpin,
  NTooltip,
  useDialog,
  useMessage,
} from 'naive-ui'
import {
  AddOutlined,
  ChevronRightOutlined,
  DeleteOutlineOutlined,
  DescriptionOutlined,
  ExpandMoreOutlined,
  ForumOutlined,
  MenuBookOutlined,
  NoteAddOutlined,
  RefreshOutlined,
  SaveOutlined,
  SearchOutlined,
  SmartToyOutlined,
} from '@vicons/material'
import AppEmptyState from '@/components/common/AppEmptyState.vue'
import AppMarkdown from '@/components/common/AppMarkdown.vue'
import { memoryApi } from '@/api/memory'
import { extractApiError } from '@/api/client'
import type {
  MemoryFileContent,
  MemoryScopeGroup,
  MemoryScopesOverview,
  MemoryScopeType,
  MemorySearchHit,
} from '@/types/memory'

const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()

const loadingList = ref(false)
const loadingFile = ref(false)
const savingFile = ref(false)
const overview = ref<MemoryScopesOverview | null>(null)

const activeScopeTab = ref<MemoryScopeType>('global')
const searchQuery = ref('')
const searching = ref(false)
const searchHits = ref<MemorySearchHit[]>([])
let searchTimer: number | undefined

// Accordion expand state for agent/shared groups
const expandedGroups = ref<Record<string, boolean>>({})

// Selected file state
const selectedScope = ref<MemoryScopeType>('global')
const selectedScopeId = ref('')
const selectedScopeLabel = ref('')
const selectedPath = ref('')
const activeFile = ref<MemoryFileContent | null>(null)
const draftContent = ref('')
const viewMode = ref<'preview' | 'edit'>('preview')

// New memory modal
const showCreateModal = ref(false)
const createScope = ref<MemoryScopeType>('global')
const createScopeId = ref('')
const createPath = ref('')
const createTitle = ref('')
const createType = ref('note')
const createTags = ref('')
const createBody = ref('')
const creating = ref(false)

const isDirty = computed(
  () => Boolean(activeFile.value && draftContent.value !== activeFile.value.content),
)

const isSearchMode = computed(() => searchQuery.value.trim().length > 0)

const currentGroups = computed<MemoryScopeGroup[]>(() => {
  if (!overview.value) return []
  if (activeScopeTab.value === 'global') return [overview.value.global_scope]
  if (activeScopeTab.value === 'agent') return overview.value.agent_scopes
  return overview.value.shared_scopes
})

const scopeTabs = computed<Array<{ value: MemoryScopeType; label: string; count: number }>>(() => {
  const ov = overview.value
  const globalCount = ov?.global_scope?.file_count ?? 0
  const agentCount = (ov?.agent_scopes || []).reduce((acc, g) => acc + g.file_count, 0)
  const sharedCount = (ov?.shared_scopes || []).reduce((acc, g) => acc + g.file_count, 0)
  return [
    { value: 'global', label: t('memory.scopeGlobal'), count: globalCount },
    { value: 'agent', label: t('memory.scopeAgent'), count: agentCount },
    { value: 'shared', label: t('memory.scopeShared'), count: sharedCount },
  ]
})

function openQuickCreate(): void {
  if (activeScopeTab.value === 'agent' && overview.value?.agent_scopes.length) {
    openCreateModal('agent', overview.value.agent_scopes[0].scope_id)
    return
  }
  if (activeScopeTab.value === 'shared' && overview.value?.shared_scopes.length) {
    openCreateModal('shared', overview.value.shared_scopes[0].scope_id)
    return
  }
  openCreateModal('global', '')
}

const memoryTypeOptions = computed(() => [
  { label: t('memory.types.note'), value: 'note' },
  { label: t('memory.types.preference'), value: 'preference' },
  { label: t('memory.types.lesson'), value: 'lesson' },
  { label: t('memory.types.finding'), value: 'finding' },
  { label: t('memory.types.handoff'), value: 'handoff' },
  { label: t('memory.types.ruledOut'), value: 'ruled_out' },
])

function groupKey(scope: MemoryScopeType, scopeId: string): string {
  return `${scope}:${scopeId}`
}

function isGroupExpanded(grp: MemoryScopeGroup): boolean {
  if (grp.scope === 'global') return true
  const k = groupKey(grp.scope, grp.scope_id)
  return expandedGroups.value[k] !== false
}

function toggleGroup(grp: MemoryScopeGroup): void {
  const k = groupKey(grp.scope, grp.scope_id)
  expandedGroups.value[k] = !isGroupExpanded(grp)
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

async function loadScopes(preserveSelection = true): Promise<void> {
  loadingList.value = true
  try {
    const res = await memoryApi.listScopes()
    overview.value = res

    // Expand groups that already have files
    for (const grp of [...res.agent_scopes, ...res.shared_scopes]) {
      const k = groupKey(grp.scope, grp.scope_id)
      if (expandedGroups.value[k] === undefined) {
        expandedGroups.value[k] = grp.files.length > 0
      }
    }

    if (preserveSelection && selectedPath.value) {
      await openFile(selectedScope.value, selectedScopeId.value, selectedPath.value, selectedScopeLabel.value, true)
      return
    }

    // Auto-select first available file in current tab
    const firstGroup = currentGroups.value.find((g) => g.files.length > 0)
    if (firstGroup && firstGroup.files.length > 0) {
      const preferred =
        firstGroup.files.find((f) => !f.is_index) || firstGroup.files[0]
      await openFile(firstGroup.scope, firstGroup.scope_id, preferred.path, firstGroup.label, false)
    } else if (!preserveSelection) {
      activeFile.value = null
      selectedPath.value = ''
    }
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    loadingList.value = false
  }
}

async function openFile(
  scope: MemoryScopeType,
  scopeId: string,
  path: string,
  scopeLabel?: string,
  silentNotFound = false,
): Promise<void> {
  if (
    isDirty.value &&
    (scope !== selectedScope.value || scopeId !== selectedScopeId.value || path !== selectedPath.value)
  ) {
    const confirmed = await new Promise<boolean>((resolve) => {
      dialog.warning({
        title: t('memory.unsavedTitle'),
        content: t('memory.unsavedConfirm'),
        positiveText: t('memory.discardChanges'),
        negativeText: t('common.cancel'),
        onPositiveClick: () => resolve(true),
        onNegativeClick: () => resolve(false),
        onClose: () => resolve(false),
      })
    })
    if (!confirmed) return
  }

  loadingFile.value = true
  try {
    const file = await memoryApi.readFile(scope, scopeId, path)
    selectedScope.value = scope
    selectedScopeId.value = scopeId
    selectedScopeLabel.value = scopeLabel || (scope === 'global' ? 'global' : scopeId.slice(0, 8))
    selectedPath.value = file.path
    activeFile.value = file
    draftContent.value = file.content
  } catch (err) {
    if (!silentNotFound) {
      message.error(extractApiError(err, t('common.operationFailed')))
    } else {
      activeFile.value = null
      selectedPath.value = ''
    }
  } finally {
    loadingFile.value = false
  }
}

async function saveActiveFile(): Promise<void> {
  if (!activeFile.value || !isDirty.value) return
  savingFile.value = true
  try {
    const saved = await memoryApi.saveFile({
      scope: selectedScope.value,
      scope_id: selectedScopeId.value,
      path: selectedPath.value,
      content: draftContent.value,
      raw_mode: true,
    })
    activeFile.value = saved
    draftContent.value = saved.content
    message.success(t('common.saveSuccess'))
    await loadScopes(true)
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    savingFile.value = false
  }
}

function openCreateModal(scope: MemoryScopeType, scopeId = ''): void {
  createScope.value = scope
  createScopeId.value = scopeId
  createPath.value = ''
  createTitle.value = ''
  createType.value = scope === 'shared' ? 'finding' : scope === 'global' ? 'preference' : 'lesson'
  createTags.value = ''
  createBody.value = ''
  showCreateModal.value = true
}

async function confirmCreate(): Promise<void> {
  const rawPath = createPath.value.trim()
  if (!rawPath) {
    message.warning(t('memory.pathRequired'))
    return
  }
  creating.value = true
  try {
    const tags = createTags.value
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    const created = await memoryApi.saveFile({
      scope: createScope.value,
      scope_id: createScopeId.value,
      path: rawPath,
      title: createTitle.value.trim(),
      memory_type: createType.value,
      tags,
      content: createBody.value.trim() || `# ${createTitle.value.trim() || rawPath}\n`,
      raw_mode: false,
    })
    showCreateModal.value = false
    activeScopeTab.value = createScope.value
    expandedGroups.value[groupKey(createScope.value, createScopeId.value)] = true
    message.success(t('common.saveSuccess'))
    await loadScopes(false)
    await openFile(created.scope, created.scope_id, created.path)
    viewMode.value = 'edit'
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    creating.value = false
  }
}

function confirmDeleteFile(scope: MemoryScopeType, scopeId: string, path: string): void {
  dialog.warning({
    title: t('common.delete'),
    content: t('memory.deleteConfirm', { path }),
    positiveText: t('common.confirmDelete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await memoryApi.deleteFile(scope, scopeId, path)
        message.success(t('common.deleteSuccess'))
        if (
          selectedScope.value === scope &&
          selectedScopeId.value === scopeId &&
          selectedPath.value === path
        ) {
          activeFile.value = null
          selectedPath.value = ''
        }
        await loadScopes(false)
      } catch (err) {
        message.error(extractApiError(err, t('common.operationFailed')))
      }
    },
  })
}

async function runSearch(): Promise<void> {
  const q = searchQuery.value.trim()
  if (!q) {
    searchHits.value = []
    return
  }
  searching.value = true
  try {
    const res = await memoryApi.search({ q, scope: 'all', limit: 30 })
    searchHits.value = res.items
  } catch (err) {
    message.error(extractApiError(err))
  } finally {
    searching.value = false
  }
}

watch(searchQuery, () => {
  window.clearTimeout(searchTimer)
  if (!searchQuery.value.trim()) {
    searchHits.value = []
    return
  }
  searchTimer = window.setTimeout(() => {
    void runSearch()
  }, 240)
})

function handleEditorKeydown(e: KeyboardEvent): void {
  if (e.key === 'Tab') {
    e.preventDefault()
    const target = e.target as HTMLTextAreaElement
    const start = target.selectionStart
    const end = target.selectionEnd
    draftContent.value =
      draftContent.value.slice(0, start) + '  ' + draftContent.value.slice(end)
    window.requestAnimationFrame(() => {
      target.selectionStart = target.selectionEnd = start + 2
    })
  }
}

function handleGlobalKeydown(e: KeyboardEvent): void {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    if (activeFile.value && isDirty.value) {
      e.preventDefault()
      void saveActiveFile()
    }
  }
}

function bindGlobalKeys(): void {
  window.addEventListener('keydown', handleGlobalKeydown)
}

function unbindGlobalKeys(): void {
  window.removeEventListener('keydown', handleGlobalKeydown)
}

onMounted(() => {
  void loadScopes(false)
  bindGlobalKeys()
})

onActivated(() => {
  void loadScopes(true)
  bindGlobalKeys()
})

onDeactivated(() => {
  unbindGlobalKeys()
})

onUnmounted(() => {
  window.clearTimeout(searchTimer)
  unbindGlobalKeys()
})
</script>

<template>
  <div
    class="memory-explorer flex overflow-hidden border"
    style="
      height: calc(100vh - 96px);
      min-height: 540px;
      background: var(--g-bg-surface);
      border-color: var(--g-border);
      border-radius: var(--g-radius-sm);
    "
  >
    <!-- LEFT PANE: Scope Tabs + Search + Markdown File Tree -->
    <aside
      class="flex w-[300px] shrink-0 flex-col border-r"
      style="background: var(--g-bg-canvas); border-color: var(--g-border)"
    >
      <!-- Top Full-Width Scope Tabs -->
      <div
        class="grid grid-cols-3 border-b"
        style="background: var(--g-bg-surface); border-color: var(--g-border-subtle)"
      >
        <button
          v-for="tab in scopeTabs"
          :key="tab.value"
          type="button"
          class="scope-tab g-transition relative flex items-center justify-center gap-1 border-0 bg-transparent px-2 py-2.5 text-xs cursor-pointer"
          :class="{ 'is-active': activeScopeTab === tab.value }"
          @click="activeScopeTab = tab.value"
        >
          <span>{{ tab.label }}</span>
          <span
            v-if="tab.count > 0"
            class="font-mono text-[10px]"
            style="color: var(--g-text-tertiary)"
          >
            {{ tab.count }}
          </span>
        </button>
      </div>

      <!-- Compact Search + Actions Toolbar -->
      <div
        class="flex items-center gap-1.5 border-b px-2.5 py-2"
        style="border-color: var(--g-border-subtle)"
      >
        <NInput
          v-model:value="searchQuery"
          size="small"
          clearable
          :placeholder="t('memory.searchPh')"
          class="flex-1"
        >
          <template #prefix><NIcon :size="14" :component="SearchOutlined" /></template>
        </NInput>

        <NTooltip>
          <template #trigger>
            <NButton size="small" quaternary @click="openQuickCreate">
              <template #icon><NIcon :size="16" :component="NoteAddOutlined" /></template>
            </NButton>
          </template>
          {{ t('memory.newNote') }}
        </NTooltip>

        <NTooltip>
          <template #trigger>
            <NButton size="small" quaternary :loading="loadingList" @click="loadScopes(true)">
              <template #icon><NIcon :size="16" :component="RefreshOutlined" /></template>
            </NButton>
          </template>
          {{ t('common.refresh') }}
        </NTooltip>
      </div>

      <!-- Tree / Search Results -->
      <div class="min-h-0 flex-1 overflow-y-auto p-2">
        <NSpin :show="loadingList || searching">
          <!-- Global Search Results -->
          <div v-if="isSearchMode" class="flex flex-col gap-1">
            <div
              v-if="!searching && searchHits.length === 0"
              class="px-2 py-8 text-center text-xs"
              style="color: var(--g-text-tertiary)"
            >
              {{ t('memory.noSearchHits') }}
            </div>
            <button
              v-for="hit in searchHits"
              :key="`${hit.scope}:${hit.scope_id}:${hit.path}`"
              type="button"
              class="mem-file-row g-transition flex flex-col gap-0.5 border border-transparent px-2.5 py-2 text-left cursor-pointer"
              :class="{
                'is-active':
                  selectedScope === hit.scope &&
                  selectedScopeId === hit.scope_id &&
                  selectedPath === hit.path,
              }"
              @click="openFile(hit.scope, hit.scope_id, hit.path, hit.scope_label)"
            >
              <div class="flex items-center justify-between gap-2">
                <span class="truncate text-xs font-medium" style="color: var(--g-text-primary)">
                  {{ hit.title }}
                </span>
                <span
                  class="shrink-0 font-mono text-[10px]"
                  style="color: var(--g-primary-text)"
                >
                  {{ hit.scope }}
                </span>
              </div>
              <div class="truncate font-mono text-[10px]" style="color: var(--g-text-tertiary)">
                {{ hit.scope_label }} / {{ hit.path }}
              </div>
              <div
                v-if="hit.snippet"
                class="line-clamp-2 text-[11px] leading-snug"
                style="color: var(--g-text-secondary)"
              >
                {{ hit.snippet }}
              </div>
            </button>
          </div>

          <!-- Scope Tree View -->
          <div v-else class="flex flex-col gap-2">
            <div
              v-if="currentGroups.length === 0"
              class="px-3 py-10 text-center text-xs leading-relaxed"
              style="color: var(--g-text-tertiary)"
            >
              {{
                activeScopeTab === 'shared'
                  ? t('memory.emptyShared')
                  : t('memory.emptyScope')
              }}
            </div>

            <div
              v-for="grp in currentGroups"
              :key="groupKey(grp.scope, grp.scope_id)"
              class="flex flex-col"
            >
              <!-- Group Header (Agent / Shared Blackboards only; Global sits flush) -->
              <div
                v-if="grp.scope !== 'global'"
                class="group flex items-center justify-between gap-1.5 px-1.5 py-1.5"
                style="border-radius: var(--g-radius-xs)"
              >
                <button
                  type="button"
                  class="flex min-w-0 flex-1 items-center gap-1.5 border-0 bg-transparent p-0 text-left cursor-pointer"
                  @click="toggleGroup(grp)"
                >
                  <NIcon
                    :size="15"
                    :component="isGroupExpanded(grp) ? ExpandMoreOutlined : ChevronRightOutlined"
                    style="color: var(--g-text-tertiary)"
                  />
                  <NIcon
                    :size="15"
                    :component="grp.scope === 'agent' ? SmartToyOutlined : ForumOutlined"
                    style="color: var(--g-primary-text)"
                  />
                  <span
                    class="truncate text-xs font-medium"
                    style="color: var(--g-text-primary)"
                  >
                    {{ grp.label }}
                  </span>
                  <span
                    class="shrink-0 font-mono text-[10px]"
                    style="color: var(--g-text-tertiary)"
                  >
                    ({{ grp.file_count }})
                  </span>
                </button>

                <NTooltip>
                  <template #trigger>
                    <NButton
                      size="tiny"
                      quaternary
                      @click.stop="openCreateModal(grp.scope, grp.scope_id)"
                    >
                      <template #icon><NIcon :size="14" :component="NoteAddOutlined" /></template>
                    </NButton>
                  </template>
                  {{ t('memory.newNote') }}
                </NTooltip>
              </div>

              <!-- Files Inside Scope -->
              <div
                v-if="isGroupExpanded(grp)"
                class="flex flex-col gap-0.5"
                :class="grp.scope === 'global' ? '' : 'pl-2'"
              >
                <div
                  v-if="grp.files.length === 0"
                  class="px-3 text-[11px]"
                  :class="grp.scope === 'global' ? 'py-10 text-center' : 'py-2'"
                  style="color: var(--g-text-tertiary)"
                >
                  {{ t('memory.noFilesYet') }}
                </div>

                <div
                  v-for="file in grp.files"
                  :key="file.path"
                  class="mem-file-row group g-transition flex items-center justify-between gap-1.5 border border-transparent px-2 py-1.5 cursor-pointer"
                  :class="{
                    'is-active':
                      selectedScope === grp.scope &&
                      selectedScopeId === grp.scope_id &&
                      selectedPath === file.path,
                  }"
                  @click="openFile(grp.scope, grp.scope_id, file.path, grp.label)"
                >
                  <div class="flex min-w-0 flex-1 items-center gap-1.5">
                    <NIcon
                      :size="14"
                      :component="file.is_index ? MenuBookOutlined : DescriptionOutlined"
                      class="shrink-0"
                      :style="{
                        color: file.is_index ? 'var(--g-primary-text)' : 'var(--g-text-tertiary)',
                      }"
                    />
                    <div class="min-w-0 flex-1">
                      <div
                        class="truncate text-xs"
                        :class="file.is_index ? 'font-mono font-medium' : ''"
                        style="color: var(--g-text-primary)"
                      >
                        {{ file.is_index ? file.path : file.title }}
                      </div>
                      <div
                        class="truncate font-mono text-[10px]"
                        style="color: var(--g-text-tertiary)"
                      >
                        {{ file.path }}
                        <template v-if="file.author"> · {{ file.author }}</template>
                      </div>
                    </div>
                  </div>

                  <NButton
                    v-if="!file.is_index"
                    size="tiny"
                    quaternary
                    type="error"
                    class="opacity-0 transition-opacity group-hover:opacity-100"
                    @click.stop="confirmDeleteFile(grp.scope, grp.scope_id, file.path)"
                  >
                    <template #icon><NIcon :size="13" :component="DeleteOutlineOutlined" /></template>
                  </NButton>
                </div>
              </div>
            </div>
          </div>
        </NSpin>
      </div>
    </aside>

    <!-- RIGHT PANE: Markdown Viewer & Editor -->
    <main class="flex min-w-0 flex-1 flex-col">
      <template v-if="activeFile">
        <!-- Toolbar -->
        <header
          class="flex h-12 shrink-0 items-center justify-between gap-3 border-b px-4"
          style="border-color: var(--g-border-subtle)"
        >
          <div class="flex min-w-0 items-center gap-2">
            <span
              class="shrink-0 border px-1.5 py-0.5 font-mono text-[10px] uppercase"
              style="
                background: var(--g-primary-soft);
                border-color: var(--g-border);
                color: var(--g-primary-text);
                border-radius: var(--g-radius-xs);
              "
            >
              {{ selectedScope }}
            </span>
            <span class="truncate font-mono text-xs font-medium" style="color: var(--g-text-primary)">
              {{ selectedPath }}
            </span>
            <span
              v-if="isDirty"
              class="inline-block h-2 w-2 shrink-0"
              style="background: var(--g-warning); border-radius: 50%"
              :title="t('memory.unsavedTitle')"
            />
            <span class="shrink-0 font-mono text-[11px]" style="color: var(--g-text-tertiary)">
              {{ formatBytes(activeFile.size) }}
            </span>
          </div>

          <div class="flex shrink-0 items-center gap-2">
            <NRadioGroup v-model:value="viewMode" size="small">
              <NRadioButton value="preview">{{ t('skills.preview') }}</NRadioButton>
              <NRadioButton value="edit">{{ t('skills.edit') }}</NRadioButton>
            </NRadioGroup>

            <NButton
              size="small"
              type="primary"
              :disabled="!isDirty"
              :loading="savingFile"
              @click="saveActiveFile"
            >
              <template #icon><NIcon :component="SaveOutlined" /></template>
              {{ t('common.save') }}
            </NButton>

            <NButton
              v-if="!activeFile.is_index"
              size="small"
              quaternary
              type="error"
              @click="confirmDeleteFile(selectedScope, selectedScopeId, selectedPath)"
            >
              <template #icon><NIcon :component="DeleteOutlineOutlined" /></template>
            </NButton>
          </div>
        </header>

        <!-- Compact Frontmatter Metadata Strip -->
        <div
          v-if="!activeFile.is_index && (activeFile.memory_type || activeFile.author || activeFile.tags.length)"
          class="flex flex-wrap items-center gap-2 border-b px-4 py-1.5 text-[11px]"
          style="background: var(--g-bg-canvas); border-color: var(--g-border-subtle); color: var(--g-text-secondary)"
        >
          <span v-if="activeFile.memory_type" class="font-mono">
            type: <strong>{{ activeFile.memory_type }}</strong>
          </span>
          <span v-if="activeFile.author">· author: {{ activeFile.author }}</span>
          <span
            v-for="tag in activeFile.tags"
            :key="tag"
            class="border px-1.5 py-0 font-mono text-[10px]"
            style="
              border-color: var(--g-border);
              background: var(--g-bg-surface);
              border-radius: var(--g-radius-xs);
            "
          >
            #{{ tag }}
          </span>
        </div>

        <!-- Body -->
        <div class="relative min-h-0 flex-1 overflow-hidden">
          <NSpin :show="loadingFile" class="h-full" content-class="h-full">
            <div v-if="viewMode === 'preview'" class="h-full overflow-y-auto px-6 py-5">
              <div class="mx-auto max-w-3xl">
                <AppMarkdown :content="draftContent" />
              </div>
            </div>
            <textarea
              v-else
              v-model="draftContent"
              spellcheck="false"
              class="h-full w-full resize-none border-0 p-5 font-mono text-xs leading-relaxed outline-none"
              style="background: var(--g-bg-surface); color: var(--g-text-primary)"
              @keydown="handleEditorKeydown"
            />
          </NSpin>
        </div>
      </template>

      <div v-else class="flex flex-1 items-center justify-center p-8">
        <AppEmptyState :title="t('memory.emptyTitle')" :description="t('memory.emptyDesc')">
          <template #icon><NIcon :size="24" :component="MenuBookOutlined" /></template>
          <template #action>
            <NButton type="primary" size="small" @click="openQuickCreate">
              <template #icon><NIcon :component="AddOutlined" /></template>
              {{ t('memory.newNote') }}
            </NButton>
          </template>
        </AppEmptyState>
      </div>
    </main>

    <!-- Create New Memory Note Modal -->
    <NModal
      v-model:show="showCreateModal"
      preset="card"
      :title="t('memory.newNote')"
      :style="{ width: '500px', maxWidth: '94vw' }"
    >
      <div class="flex flex-col gap-3">
        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <div class="mb-1 text-xs" style="color: var(--g-text-secondary)">
              {{ t('memory.filePathLabel') }}
            </div>
            <NInput
              v-model:value="createPath"
              class="font-mono text-xs"
              :placeholder="t('memory.filePathPh')"
            />
          </div>
          <div>
            <div class="mb-1 text-xs" style="color: var(--g-text-secondary)">
              {{ t('memory.typeLabel') }}
            </div>
            <NSelect v-model:value="createType" :options="memoryTypeOptions" />
          </div>
        </div>

        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <div class="mb-1 text-xs" style="color: var(--g-text-secondary)">
              {{ t('memory.titleLabel') }}
            </div>
            <NInput v-model:value="createTitle" :placeholder="t('memory.titlePh')" />
          </div>
          <div>
            <div class="mb-1 text-xs" style="color: var(--g-text-secondary)">
              {{ t('memory.tagsLabel') }}
            </div>
            <NInput v-model:value="createTags" class="font-mono text-xs" placeholder="style, api, rule" />
          </div>
        </div>

        <div>
          <div class="mb-1 text-xs" style="color: var(--g-text-secondary)">
            {{ t('memory.contentLabel') }}
          </div>
          <NInput
            v-model:value="createBody"
            type="textarea"
            :rows="6"
            class="font-mono text-xs"
            :placeholder="t('memory.contentPh')"
          />
        </div>
      </div>

      <template #footer>
        <div class="flex justify-end gap-2">
          <NButton @click="showCreateModal = false">{{ t('common.cancel') }}</NButton>
          <NButton type="primary" :loading="creating" @click="confirmCreate">
            {{ t('common.create') }}
          </NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.scope-tab {
  color: var(--g-text-secondary);
  font-weight: 500;
  border-bottom: 2px solid transparent;
}
.scope-tab:hover {
  color: var(--g-text-primary);
  background: var(--g-bg-hover);
}
.scope-tab.is-active {
  color: var(--g-primary-text);
  border-bottom-color: var(--g-primary);
  background: var(--g-primary-soft);
}
.mem-file-row {
  border-radius: var(--g-radius-xs);
}
.mem-file-row:hover {
  background: var(--g-bg-hover);
}
.mem-file-row.is-active {
  background: var(--g-primary-soft);
  border-color: var(--g-border);
}
</style>
