<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NButton,
  NIcon,
  NInput,
  NModal,
  NRadioButton,
  NRadioGroup,
  NSpin,
  NTooltip,
  useDialog,
  useMessage,
} from 'naive-ui'
import {
  AddOutlined,
  AutoAwesomeOutlined,
  ChevronRightOutlined,
  CreateNewFolderOutlined,
  DeleteOutlineOutlined,
  DescriptionOutlined,
  ExpandMoreOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  NoteAddOutlined,
  RefreshOutlined,
  SaveOutlined,
  SearchOutlined,
  UploadFileOutlined,
} from '@vicons/material'
import AppEmptyState from '@/components/common/AppEmptyState.vue'
import AppMarkdown from '@/components/common/AppMarkdown.vue'
import SkillTreeNode from '@/components/skills/SkillTreeNode.vue'
import { skillsApi } from '@/api/skills'
import { extractApiError } from '@/api/client'
import type { SkillFileContent, SkillFileNode, SkillSummaryItem } from '@/types/skill'

const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()

const loadingList = ref(false)
const loadingFile = ref(false)
const savingFile = ref(false)
const uploadingZip = ref(false)

const rootDir = ref('')
const skills = ref<SkillSummaryItem[]>([])
const searchQuery = ref('')

// Tree expand state
const expandedSkills = ref<Record<string, boolean>>({})
const expandedDirs = ref<Record<string, boolean>>({})

// Active file state
const selectedSkill = ref('')
const selectedPath = ref('')
const activeFile = ref<SkillFileContent | null>(null)
const draftContent = ref('')
const viewMode = ref<'preview' | 'edit'>('preview')

// New Skill modal
const showNewSkillModal = ref(false)
const newSkillName = ref('')
const newSkillDesc = ref('')
const creatingSkill = ref(false)

// New File modal
const showNewFileModal = ref(false)
const newFileSkill = ref('')
const newFilePath = ref('')
const creatingFile = ref(false)

const zipInputRef = ref<HTMLInputElement | null>(null)

const isMarkdownFile = computed(() => selectedPath.value.toLowerCase().endsWith('.md'))
const isDirty = computed(
  () => Boolean(activeFile.value && !activeFile.value.is_binary && draftContent.value !== activeFile.value.content),
)

const filteredSkills = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return skills.value
  return skills.value.filter((sk) => {
    if (
      sk.name.toLowerCase().includes(q) ||
      sk.title.toLowerCase().includes(q) ||
      sk.description.toLowerCase().includes(q)
    ) {
      return true
    }
    return hasMatchingNode(sk.tree, q)
  })
})

function hasMatchingNode(nodes: SkillFileNode[], q: string): boolean {
  for (const n of nodes) {
    if (n.name.toLowerCase().includes(q) || n.path.toLowerCase().includes(q)) return true
    if (n.children.length > 0 && hasMatchingNode(n.children, q)) return true
  }
  return false
}

function collectFirstFile(nodes: SkillFileNode[]): string | null {
  const skillMd = nodes.find((n) => n.kind === 'file' && n.name.toLowerCase() === 'skill.md')
  if (skillMd) return skillMd.path
  for (const n of nodes) {
    if (n.kind === 'file') return n.path
    if (n.kind === 'dir') {
      const inner = collectFirstFile(n.children)
      if (inner) return inner
    }
  }
  return null
}

function expandAllDirsForSkill(sk: SkillSummaryItem): void {
  const walk = (nodes: SkillFileNode[]) => {
    for (const n of nodes) {
      if (n.kind === 'dir') {
        expandedDirs.value[`${sk.name}/${n.path}`] = true
        walk(n.children)
      }
    }
  }
  walk(sk.tree)
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

async function fetchSkills(autoSelect = false): Promise<void> {
  loadingList.value = true
  try {
    const res = await skillsApi.list()
    rootDir.value = res.root_dir
    skills.value = res.items

    for (const sk of res.items) {
      if (expandedSkills.value[sk.name] === undefined) {
        expandedSkills.value[sk.name] = true
      }
      expandAllDirsForSkill(sk)
    }

    if (autoSelect && !selectedSkill.value && res.items.length > 0) {
      const first = res.items[0]
      const firstFile = collectFirstFile(first.tree)
      if (firstFile) {
        await openFile(first.name, firstFile)
      }
    }
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    loadingList.value = false
  }
}

async function openFile(skillName: string, path: string, force = false): Promise<void> {
  if (
    !force &&
    selectedSkill.value === skillName &&
    selectedPath.value === path &&
    activeFile.value
  ) {
    return
  }

  const doOpen = async () => {
    loadingFile.value = true
    selectedSkill.value = skillName
    selectedPath.value = path
    expandedSkills.value[skillName] = true
    try {
      const data = await skillsApi.readFile(skillName, path)
      activeFile.value = data
      draftContent.value = data.content
      viewMode.value = path.toLowerCase().endsWith('.md') ? 'preview' : 'edit'
    } catch (err) {
      activeFile.value = null
      draftContent.value = ''
      message.error(extractApiError(err, t('common.operationFailed')))
    } finally {
      loadingFile.value = false
    }
  }

  if (!force && isDirty.value) {
    dialog.warning({
      title: t('skills.unsavedTitle'),
      content: t('skills.unsavedDiscardConfirm'),
      positiveText: t('skills.discardChanges'),
      negativeText: t('common.cancel'),
      onPositiveClick: () => {
        doOpen()
      },
    })
    return
  }
  await doOpen()
}

function toggleSkillFolder(sk: SkillSummaryItem): void {
  const next = !expandedSkills.value[sk.name]
  expandedSkills.value[sk.name] = next
  if (next) expandAllDirsForSkill(sk)
  if (selectedSkill.value !== sk.name) {
    const firstFile = collectFirstFile(sk.tree)
    if (firstFile) openFile(sk.name, firstFile)
  }
}

function toggleDir(key: string): void {
  expandedDirs.value[key] = !expandedDirs.value[key]
}

async function handleSaveFile(): Promise<void> {
  if (!activeFile.value || activeFile.value.is_binary || !selectedSkill.value || !selectedPath.value)
    return
  savingFile.value = true
  try {
    const updated = await skillsApi.writeFile(
      selectedSkill.value,
      selectedPath.value,
      draftContent.value,
    )
    activeFile.value = updated
    draftContent.value = updated.content
    message.success(t('common.saveSuccess'))
    await fetchSkills(false)
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    savingFile.value = false
  }
}

function handleEditorKeydown(e: KeyboardEvent): void {
  if (e.key === 'Tab') {
    e.preventDefault()
    const el = e.target as HTMLTextAreaElement
    const start = el.selectionStart
    const end = el.selectionEnd
    draftContent.value = `${draftContent.value.slice(0, start)}  ${draftContent.value.slice(end)}`
    requestAnimationFrame(() => {
      el.selectionStart = el.selectionEnd = start + 2
    })
  }
}

function openCreateSkillModal(): void {
  newSkillName.value = ''
  newSkillDesc.value = ''
  showNewSkillModal.value = true
}

async function handleCreateSkill(): Promise<void> {
  const cleanName = newSkillName.value.trim()
  if (!cleanName) {
    message.warning(t('models.form.requiredField'))
    return
  }
  creatingSkill.value = true
  try {
    const created = await skillsApi.create({
      name: cleanName,
      description: newSkillDesc.value.trim(),
    })
    showNewSkillModal.value = false
    message.success(t('common.saveSuccess'))
    await fetchSkills(false)
    expandedSkills.value[created.name] = true
    await openFile(created.name, 'SKILL.md', true)
    viewMode.value = 'edit'
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    creatingSkill.value = false
  }
}

function openCreateFileModal(skillName: string): void {
  newFileSkill.value = skillName
  newFilePath.value = ''
  showNewFileModal.value = true
}

async function handleCreateFile(): Promise<void> {
  const cleanPath = newFilePath.value.trim().replace(/^\/+/, '')
  if (!cleanPath || !newFileSkill.value) {
    message.warning(t('models.form.requiredField'))
    return
  }
  creatingFile.value = true
  try {
    const starter = cleanPath.toLowerCase().endsWith('.md')
      ? `# ${cleanPath.split('/').pop()?.replace(/\.md$/i, '') || 'Notes'}\n\n`
      : ''
    await skillsApi.writeFile(newFileSkill.value, cleanPath, starter)
    showNewFileModal.value = false
    message.success(t('common.saveSuccess'))
    await fetchSkills(false)
    await openFile(newFileSkill.value, cleanPath, true)
    viewMode.value = 'edit'
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    creatingFile.value = false
  }
}

function handleDeleteFile(): void {
  if (!selectedSkill.value || !selectedPath.value) return
  const sName = selectedSkill.value
  const fPath = selectedPath.value
  dialog.warning({
    title: t('common.delete'),
    content: t('skills.deleteFileConfirm', { path: `${sName}/${fPath}` }),
    positiveText: t('common.confirmDelete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await skillsApi.deleteFile(sName, fPath)
        message.success(t('common.deleteSuccess'))
        activeFile.value = null
        selectedPath.value = ''
        draftContent.value = ''
        await fetchSkills(false)
        const sk = skills.value.find((x) => x.name === sName)
        const fallback = sk ? collectFirstFile(sk.tree) : null
        if (sk && fallback) {
          await openFile(sk.name, fallback, true)
        }
      } catch (err) {
        message.error(extractApiError(err))
      }
    },
  })
}

function handleDeleteSkill(sk: SkillSummaryItem): void {
  dialog.warning({
    title: t('common.delete'),
    content: t('skills.deleteConfirm', { name: sk.name }),
    positiveText: t('common.confirmDelete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await skillsApi.remove(sk.name)
        message.success(t('common.deleteSuccess'))
        if (selectedSkill.value === sk.name) {
          selectedSkill.value = ''
          selectedPath.value = ''
          activeFile.value = null
          draftContent.value = ''
        }
        await fetchSkills(true)
      } catch (err) {
        message.error(extractApiError(err))
      }
    },
  })
}

function triggerZipUpload(): void {
  zipInputRef.value?.click()
}

async function onZipSelected(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return

  const doUpload = async (overwrite: boolean) => {
    uploadingZip.value = true
    try {
      const imported = await skillsApi.uploadZip(file, { overwrite })
      message.success(t('skills.uploadSuccess', { name: imported.name }))
      await fetchSkills(false)
      expandedSkills.value[imported.name] = true
      expandAllDirsForSkill(imported)
      const firstFile = collectFirstFile(imported.tree)
      if (firstFile) await openFile(imported.name, firstFile, true)
    } catch (err: any) {
      if (!overwrite && err?.response?.status === 409) {
        dialog.warning({
          title: t('skills.uploadZip'),
          content: t('skills.overwriteZipConfirm'),
          positiveText: t('skills.overwriteBtn'),
          negativeText: t('common.cancel'),
          onPositiveClick: () => {
            doUpload(true)
          },
        })
      } else {
        message.error(extractApiError(err, t('common.operationFailed')))
      }
    } finally {
      uploadingZip.value = false
    }
  }

  await doUpload(false)
}

function handleGlobalKeydown(e: KeyboardEvent): void {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
    if (activeFile.value && !activeFile.value.is_binary) {
      e.preventDefault()
      handleSaveFile()
    }
  }
}

onMounted(() => {
  fetchSkills(true)
  window.addEventListener('keydown', handleGlobalKeydown)
})

onActivated(() => {
  fetchSkills(false)
  window.addEventListener('keydown', handleGlobalKeydown)
})

onDeactivated(() => {
  window.removeEventListener('keydown', handleGlobalKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleGlobalKeydown)
})
</script>

<template>
  <div
    class="skills-explorer flex overflow-hidden border"
    style="
      height: calc(100vh - 96px);
      min-height: 540px;
      background: var(--g-bg-surface);
      border-color: var(--g-border);
      border-radius: var(--g-radius-sm);
    "
  >
    <!-- Hidden ZIP input -->
    <input
      ref="zipInputRef"
      type="file"
      accept=".zip,application/zip"
      class="hidden"
      @change="onZipSelected"
    />

    <!-- LEFT PANE: Filesystem Directory Tree -->
    <aside
      class="flex w-[290px] shrink-0 flex-col border-r"
      style="background: var(--g-bg-canvas); border-color: var(--g-border)"
    >
      <!-- Compact Toolbar -->
      <div
        class="flex items-center gap-1.5 border-b px-2.5 py-2"
        style="border-color: var(--g-border-subtle)"
      >
        <NInput
          v-model:value="searchQuery"
          size="small"
          clearable
          :placeholder="t('skills.searchPlaceholder')"
          class="flex-1"
        >
          <template #prefix><NIcon :size="14" :component="SearchOutlined" /></template>
        </NInput>

        <NTooltip>
          <template #trigger>
            <NButton size="small" quaternary @click="openCreateSkillModal">
              <template #icon><NIcon :size="16" :component="CreateNewFolderOutlined" /></template>
            </NButton>
          </template>
          {{ t('skills.addSkill') }}
        </NTooltip>

        <NTooltip>
          <template #trigger>
            <NButton size="small" quaternary :loading="uploadingZip" @click="triggerZipUpload">
              <template #icon><NIcon :size="16" :component="UploadFileOutlined" /></template>
            </NButton>
          </template>
          {{ t('skills.uploadZip') }}
        </NTooltip>

        <NTooltip>
          <template #trigger>
            <NButton size="small" quaternary :loading="loadingList" @click="fetchSkills(false)">
              <template #icon><NIcon :size="16" :component="RefreshOutlined" /></template>
            </NButton>
          </template>
          {{ t('common.refresh') }}
        </NTooltip>
      </div>

      <!-- Subtle Root Directory Info -->
      <div
        class="flex items-center justify-between border-b px-3 py-1.5 font-mono text-[11px]"
        style="
          border-color: var(--g-border-subtle);
          color: var(--g-text-tertiary);
          background: var(--g-bg-surface);
        "
        :title="rootDir"
      >
        <span class="truncate">skills/</span>
        <span class="shrink-0">{{ t('skills.skillCount', { count: filteredSkills.length }) }}</span>
      </div>

      <!-- Directory Tree List -->
      <div class="min-h-0 flex-1 overflow-y-auto p-1.5">
        <NSpin :show="loadingList && skills.length === 0">
          <div
            v-if="!loadingList && filteredSkills.length === 0"
            class="px-3 py-8 text-center text-xs"
            style="color: var(--g-text-tertiary)"
          >
            {{ t('skills.emptyTitle') }}
          </div>

          <div v-else class="flex flex-col gap-0.5">
            <div v-for="sk in filteredSkills" :key="sk.name" class="select-none">
              <!-- Skill Root Row -->
              <div
                class="skill-root-row group g-transition flex items-center gap-1 px-1.5 py-1.5"
                :class="{ 'is-selected-skill': selectedSkill === sk.name }"
              >
                <button
                  type="button"
                  class="flex min-w-0 flex-1 cursor-pointer items-center gap-1.5 border-0 bg-transparent p-0 text-left"
                  :title="sk.description || sk.title"
                  @click="toggleSkillFolder(sk)"
                >
                  <NIcon
                    :size="15"
                    :component="expandedSkills[sk.name] ? ExpandMoreOutlined : ChevronRightOutlined"
                    class="shrink-0"
                    style="color: var(--g-text-tertiary)"
                  />
                  <NIcon
                    :size="15"
                    :component="expandedSkills[sk.name] ? FolderOpenOutlined : FolderOutlined"
                    class="shrink-0"
                    :style="{
                      color:
                        selectedSkill === sk.name
                          ? 'var(--g-primary-text)'
                          : 'var(--g-text-secondary)',
                    }"
                  />
                  <span
                    class="min-w-0 flex-1 truncate font-mono text-xs font-medium"
                    style="color: var(--g-text-primary)"
                  >
                    {{ sk.name }}
                  </span>
                </button>

                <!-- Hover Actions: New File / Delete Skill -->
                <div class="flex shrink-0 items-center opacity-0 transition-opacity group-hover:opacity-100">
                  <NTooltip>
                    <template #trigger>
                      <button
                        type="button"
                        class="icon-mini-btn flex h-5 w-5 cursor-pointer items-center justify-center border-0 bg-transparent"
                        @click.stop="openCreateFileModal(sk.name)"
                      >
                        <NIcon :size="14" :component="NoteAddOutlined" />
                      </button>
                    </template>
                    {{ t('skills.addFile') }}
                  </NTooltip>
                  <NTooltip>
                    <template #trigger>
                      <button
                        type="button"
                        class="icon-mini-btn danger flex h-5 w-5 cursor-pointer items-center justify-center border-0 bg-transparent"
                        @click.stop="handleDeleteSkill(sk)"
                      >
                        <NIcon :size="14" :component="DeleteOutlineOutlined" />
                      </button>
                    </template>
                    {{ t('skills.deleteSkill') }}
                  </NTooltip>
                </div>
              </div>

              <!-- Nested Children Tree -->
              <div v-if="expandedSkills[sk.name]" class="mb-1">
                <SkillTreeNode
                  v-for="node in sk.tree"
                  :key="node.path"
                  :skill-name="sk.name"
                  :node="node"
                  :depth="1"
                  :selected-skill="selectedSkill"
                  :selected-path="selectedPath"
                  :expanded-dirs="expandedDirs"
                  @toggle-dir="toggleDir"
                  @select-file="openFile"
                />
              </div>
            </div>
          </div>
        </NSpin>
      </div>
    </aside>

    <!-- RIGHT PANE: File Viewer & Editor -->
    <main class="flex min-w-0 flex-1 flex-col">
      <template v-if="selectedSkill && selectedPath">
        <!-- Single-Line File Header -->
        <div
          class="flex h-[44px] shrink-0 items-center justify-between gap-3 border-b px-4"
          style="border-color: var(--g-border-subtle); background: var(--g-bg-surface)"
        >
          <div class="flex min-w-0 items-center gap-2 font-mono text-xs">
            <span style="color: var(--g-text-secondary)">{{ selectedSkill }}</span>
            <span style="color: var(--g-text-tertiary)">/</span>
            <span class="truncate font-medium" style="color: var(--g-text-primary)">
              {{ selectedPath }}
            </span>
            <span
              v-if="activeFile"
              class="shrink-0 text-[11px]"
              style="color: var(--g-text-tertiary)"
            >
              ({{ formatSize(activeFile.size) }})
            </span>
            <span
              v-if="isDirty"
              class="inline-block h-2 w-2 shrink-0 rounded-full"
              style="background: var(--g-warning)"
              :title="t('agents.detail.unsaved')"
            />
          </div>

          <div class="flex shrink-0 items-center gap-2">
            <NRadioGroup
              v-if="isMarkdownFile && activeFile && !activeFile.is_binary"
              v-model:value="viewMode"
              size="small"
            >
              <NRadioButton value="preview">{{ t('skills.modePreview') }}</NRadioButton>
              <NRadioButton value="edit">{{ t('skills.modeEdit') }}</NRadioButton>
            </NRadioGroup>

            <NTooltip>
              <template #trigger>
                <NButton
                  size="small"
                  quaternary
                  @click="openCreateFileModal(selectedSkill)"
                >
                  <template #icon><NIcon :size="15" :component="AddOutlined" /></template>
                </NButton>
              </template>
              {{ t('skills.addFile') }}
            </NTooltip>

            <NButton
              v-if="activeFile && !activeFile.is_binary && (viewMode === 'edit' || isDirty)"
              size="small"
              type="primary"
              :disabled="!isDirty"
              :loading="savingFile"
              @click="handleSaveFile"
            >
              <template #icon><NIcon :size="15" :component="SaveOutlined" /></template>
              {{ t('common.save') }}
            </NButton>

            <NTooltip>
              <template #trigger>
                <NButton size="small" quaternary type="error" @click="handleDeleteFile">
                  <template #icon><NIcon :size="15" :component="DeleteOutlineOutlined" /></template>
                </NButton>
              </template>
              {{ t('skills.deleteFile') }}
            </NTooltip>
          </div>
        </div>

        <!-- File Body -->
        <NSpin :show="loadingFile" class="min-h-0 flex-1" content-class="h-full">
          <div v-if="activeFile?.is_binary" class="flex h-full items-center justify-center p-8">
            <AppEmptyState
              :title="t('skills.binaryFileTitle')"
              :description="
                t('skills.binaryFileDesc', {
                  size: formatSize(activeFile.size),
                  path: `/opt/agan/skills/${selectedSkill}/${selectedPath}`,
                })
              "
            >
              <template #icon><NIcon :size="24" :component="DescriptionOutlined" /></template>
            </AppEmptyState>
          </div>

          <!-- Markdown Rendered Preview -->
          <div
            v-else-if="isMarkdownFile && viewMode === 'preview'"
            class="h-full overflow-y-auto px-7 py-6"
          >
            <div class="mx-auto max-w-4xl">
              <AppMarkdown :content="draftContent" />
            </div>
          </div>

          <!-- Clean Monospace Code / Text Editor -->
          <div v-else class="flex h-full flex-col">
            <textarea
              v-model="draftContent"
              spellcheck="false"
              class="skill-code-editor min-h-0 flex-1 resize-none border-0 p-5 font-mono text-[13px] leading-relaxed outline-none"
              @keydown="handleEditorKeydown"
            />
          </div>
        </NSpin>
      </template>

      <!-- Empty Right Pane -->
      <div v-else class="flex flex-1 items-center justify-center p-8">
        <AppEmptyState :title="t('skills.selectFileTitle')" :description="t('skills.emptyDesc')">
          <template #icon><NIcon :size="24" :component="AutoAwesomeOutlined" /></template>
          <template #action>
            <div class="flex items-center gap-2">
              <NButton type="primary" size="small" @click="openCreateSkillModal">
                <template #icon><NIcon :component="CreateNewFolderOutlined" /></template>
                {{ t('skills.addSkill') }}
              </NButton>
              <NButton secondary size="small" @click="triggerZipUpload">
                <template #icon><NIcon :component="UploadFileOutlined" /></template>
                {{ t('skills.uploadZip') }}
              </NButton>
            </div>
          </template>
        </AppEmptyState>
      </div>
    </main>

    <!-- Lightweight Modal: New Skill Directory -->
    <NModal
      v-model:show="showNewSkillModal"
      preset="card"
      :title="t('skills.addSkill')"
      style="width: 420px; max-width: 92vw"
      :bordered="false"
    >
      <div class="flex flex-col gap-3">
        <div>
          <div class="mb-1 text-xs font-medium" style="color: var(--g-text-secondary)">
            {{ t('skills.skillNameLabel') }}
          </div>
          <NInput
            v-model:value="newSkillName"
            :placeholder="t('skills.skillNamePh')"
            @keydown.enter="handleCreateSkill"
          />
        </div>
        <div>
          <div class="mb-1 text-xs font-medium" style="color: var(--g-text-secondary)">
            {{ t('skills.skillDescLabel') }}
          </div>
          <NInput
            v-model:value="newSkillDesc"
            :placeholder="t('skills.skillDescPh')"
            @keydown.enter="handleCreateSkill"
          />
        </div>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <NButton size="small" @click="showNewSkillModal = false">{{ t('common.cancel') }}</NButton>
          <NButton size="small" type="primary" :loading="creatingSkill" @click="handleCreateSkill">
            {{ t('common.create') }}
          </NButton>
        </div>
      </template>
    </NModal>

    <!-- Lightweight Modal: New File inside a Skill -->
    <NModal
      v-model:show="showNewFileModal"
      preset="card"
      :title="`${t('skills.addFile')} (${newFileSkill})`"
      style="width: 420px; max-width: 92vw"
      :bordered="false"
    >
      <div>
        <div class="mb-1 text-xs font-medium" style="color: var(--g-text-secondary)">
          {{ t('skills.filePathLabel') }}
        </div>
        <NInput
          v-model:value="newFilePath"
          :placeholder="t('skills.filePathPh')"
          @keydown.enter="handleCreateFile"
        />
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <NButton size="small" @click="showNewFileModal = false">{{ t('common.cancel') }}</NButton>
          <NButton size="small" type="primary" :loading="creatingFile" @click="handleCreateFile">
            {{ t('common.create') }}
          </NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.skill-root-row {
  border-radius: var(--g-radius-xs);
}
.skill-root-row:hover {
  background: var(--g-bg-hover);
}
.skill-root-row.is-selected-skill {
  background: var(--g-bg-subtle);
}
.icon-mini-btn {
  color: var(--g-text-tertiary);
  border-radius: var(--g-radius-xs);
}
.icon-mini-btn:hover {
  background: var(--g-bg-surface);
  color: var(--g-text-primary);
}
.icon-mini-btn.danger:hover {
  color: var(--g-error);
}
.skill-code-editor {
  background: var(--g-bg-surface);
  color: var(--g-text-primary);
  tab-size: 2;
}
</style>
