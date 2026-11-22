<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NButton,
  NIcon,
  NInput,
  NSelect,
  NSwitch,
  NTooltip,
  useDialog,
  useMessage,
} from 'naive-ui'
import {
  AddOutlined,
  CodeOutlined,
  DeleteOutlineOutlined,
  EditOutlined,
  RefreshOutlined,
  SearchOutlined,
} from '@vicons/material'
import AppFilterToolbar from '@/components/common/AppFilterToolbar.vue'
import AppSurfaceCard from '@/components/common/AppSurfaceCard.vue'
import AppStatusBadge, { type BadgeVariant } from '@/components/common/AppStatusBadge.vue'
import AppPaginationBar from '@/components/common/AppPaginationBar.vue'
import ToolFormDrawer from '@/views/studio/ToolFormDrawer.vue'
import ToolTestModal from '@/views/studio/ToolTestModal.vue'
import { toolsApi } from '@/api/tools'
import { extractApiError } from '@/api/client'
import type { ToolItem, ToolType } from '@/types/capability'

const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()

const loading = ref(false)
const tools = ref<ToolItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const typeFilter = ref<'all' | ToolType>('all')
const statusFilter = ref<'all' | 'enabled' | 'disabled'>('all')
const searchQuery = ref('')
let searchTimer: number | undefined

const counts = ref({
  all: 0,
  builtin: 0,
  custom_http: 0,
  custom_python: 0,
  enabled: 0,
})

const drawerShow = ref(false)
const editingTool = ref<ToolItem | null>(null)
const testModalShow = ref(false)
const testingTool = ref<ToolItem | null>(null)

const typeOptions = computed(() => [
  { label: `${t('tools.tabAll')} (${counts.value.all})`, value: 'all' },
  { label: `${t('tools.tabBuiltin')} (${counts.value.builtin})`, value: 'builtin' },
  { label: `${t('tools.tabHttp')} (${counts.value.custom_http})`, value: 'custom_http' },
  { label: `${t('tools.tabPython')} (${counts.value.custom_python})`, value: 'custom_python' },
])

const statusOptions = computed(() => [
  { label: `${t('common.status')}: ${t('common.all')}`, value: 'all' },
  { label: t('common.enabled'), value: 'enabled' },
  { label: t('common.disabled'), value: 'disabled' },
])

async function loadTools(): Promise<void> {
  loading.value = true
  try {
    const res = await toolsApi.list({
      tool_type: typeFilter.value === 'all' ? undefined : typeFilter.value,
      is_enabled:
        statusFilter.value === 'all' ? undefined : statusFilter.value === 'enabled',
      search: searchQuery.value.trim() || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    tools.value = res.items
    total.value = res.total
    counts.value = res.type_counts
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editingTool.value = null
  drawerShow.value = true
}

function openEdit(item: ToolItem): void {
  editingTool.value = item
  drawerShow.value = true
}

function openTest(item: ToolItem): void {
  testingTool.value = item
  testModalShow.value = true
}

async function handleToggle(item: ToolItem): Promise<void> {
  try {
    const updated = await toolsApi.toggle(item.id)
    const idx = tools.value.findIndex((x) => x.id === item.id)
    if (idx !== -1) tools.value[idx] = updated
    await loadTools()
  } catch (err) {
    message.error(extractApiError(err))
  }
}

function confirmDelete(item: ToolItem): void {
  dialog.warning({
    title: t('common.delete'),
    content: t('tools.deleteConfirm', { name: item.display_name }),
    positiveText: t('common.confirmDelete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await toolsApi.remove(item.id)
        message.success(t('common.deleteSuccess'))
        await loadTools()
      } catch (err) {
        message.error(extractApiError(err))
      }
    },
  })
}

function typeVariant(tt: ToolType): BadgeVariant {
  if (tt === 'builtin') return 'info'
  if (tt === 'custom_http') return 'success'
  return 'warning'
}

watch([typeFilter, statusFilter], () => {
  page.value = 1
  loadTools()
})

watch(searchQuery, () => {
  if (searchTimer) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    page.value = 1
    loadTools()
  }, 240)
})

onMounted(loadTools)
</script>

<template>
  <div>
    <!-- Compact Top Filter & Action Toolbar -->
    <AppFilterToolbar>
      <template #filters>
        <div class="w-full sm:w-72">
          <NInput
            v-model:value="searchQuery"
            clearable
            size="small"
            :placeholder="t('tools.searchPlaceholder')"
          >
            <template #prefix><NIcon :component="SearchOutlined" /></template>
          </NInput>
        </div>
        <NSelect
          v-model:value="typeFilter"
          size="small"
          :options="typeOptions"
          :style="{ width: '170px' }"
        />
        <NSelect
          v-model:value="statusFilter"
          size="small"
          :options="statusOptions"
          :style="{ width: '140px' }"
        />
      </template>

      <template #actions>
        <NButton secondary size="small" :loading="loading" @click="loadTools">
          <template #icon><NIcon :component="RefreshOutlined" /></template>
          {{ t('common.refresh') }}
        </NButton>
        <NButton type="primary" size="small" @click="openCreate">
          <template #icon><NIcon :component="AddOutlined" /></template>
          {{ t('tools.addTool') }}
        </NButton>
      </template>
    </AppFilterToolbar>

    <AppSurfaceCard no-padding>
      <div class="overflow-x-auto">
        <table class="w-full border-collapse text-left text-xs">
          <thead>
            <tr
              class="border-b text-[11px] font-semibold uppercase tracking-wide"
              style="
                background: var(--g-bg-canvas);
                border-color: var(--g-border-subtle);
                color: var(--g-text-secondary);
              "
            >
              <th class="px-4 py-2.5">{{ t('tools.colName') }}</th>
              <th class="px-3 py-2.5">{{ t('tools.colType') }}</th>
              <th class="px-3 py-2.5">{{ t('tools.colCategory') }}</th>
              <th class="px-3 py-2.5">{{ t('tools.colDesc') }}</th>
              <th class="px-3 py-2.5 text-center">{{ t('tools.colEnabled') }}</th>
              <th class="px-4 py-2.5 text-right">{{ t('common.actions') }}</th>
            </tr>
          </thead>
          <TransitionGroup name="g-list" tag="tbody">
            <tr
              v-for="item in tools"
              :key="item.id"
              class="tool-row g-transition border-b"
              style="border-color: var(--g-border-subtle)"
            >
              <td class="px-4 py-3 align-middle">
                <div class="text-[13px] font-medium" style="color: var(--g-text-primary)">
                  {{ item.display_name }}
                </div>
                <div class="mt-0.5 font-mono text-[11px]" style="color: var(--g-primary-text)">
                  {{ item.name }}
                </div>
              </td>

              <td class="px-3 py-3 align-middle">
                <AppStatusBadge
                  :variant="typeVariant(item.tool_type)"
                  :label="item.tool_type.toUpperCase()"
                  :dot="false"
                />
              </td>

              <td
                class="px-3 py-3 align-middle font-mono text-[11px]"
                style="color: var(--g-text-secondary)"
              >
                <div>{{ item.category }}</div>
                <div
                  v-if="item.name === 'read_file' || item.name === 'write_file'"
                  class="max-w-[180px] truncate text-[10px]"
                  style="color: var(--g-text-tertiary)"
                  :title="String(item.implementation_config?.root_dir || 'data/workspaces/<session>')"
                >
                  root: {{ item.implementation_config?.root_dir || 'data/workspaces/*' }}
                </div>
                <div
                  v-else-if="item.requires_sandbox"
                  class="text-[10px]"
                  style="color: var(--g-text-tertiary)"
                >
                  sandbox required
                </div>
              </td>

              <td class="px-3 py-3 align-middle">
                <div
                  class="max-w-md line-clamp-2 text-xs leading-relaxed"
                  style="color: var(--g-text-secondary)"
                >
                  {{ item.description }}
                </div>
              </td>

              <td class="px-3 py-3 text-center align-middle">
                <NSwitch size="small" :value="item.is_enabled" @update:value="handleToggle(item)" />
              </td>

              <td class="px-4 py-3 text-right align-middle">
                <div class="inline-flex items-center gap-1">
                  <NButton size="tiny" secondary type="primary" @click="openTest(item)">
                    <template #icon><NIcon :component="CodeOutlined" /></template>
                    {{ t('tools.schemaAndTest') }}
                  </NButton>
                  <NTooltip>
                    <template #trigger>
                      <NButton size="tiny" quaternary @click="openEdit(item)">
                        <template #icon><NIcon :component="EditOutlined" /></template>
                      </NButton>
                    </template>
                    {{ t('common.edit') }}
                  </NTooltip>
                  <NTooltip>
                    <template #trigger>
                      <NButton
                        size="tiny"
                        quaternary
                        type="error"
                        :disabled="item.is_builtin"
                        @click="confirmDelete(item)"
                      >
                        <template #icon><NIcon :component="DeleteOutlineOutlined" /></template>
                      </NButton>
                    </template>
                    {{ item.is_builtin ? t('tools.builtinProtected') : t('common.delete') }}
                  </NTooltip>
                </div>
              </td>
            </tr>
          </TransitionGroup>
        </table>
      </div>

      <AppPaginationBar
        v-model:page="page"
        v-model:page-size="pageSize"
        :total="total"
        @change="loadTools"
      />
    </AppSurfaceCard>

    <ToolFormDrawer v-model:show="drawerShow" :editing-tool="editingTool" @saved="loadTools" />
    <ToolTestModal v-model:show="testModalShow" :tool="testingTool" />
  </div>
</template>

<style scoped>
.tool-row:hover {
  background: var(--g-bg-hover);
}
</style>
