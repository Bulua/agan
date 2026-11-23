<script setup lang="ts">
import { computed, onActivated, onMounted, ref, watch } from 'vue'
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
  BuildOutlined,
  CodeOutlined,
  DeleteOutlineOutlined,
  EditOutlined,
  FileUploadOutlined,
  HubOutlined,
  RefreshOutlined,
  SearchOutlined,
  SyncOutlined,
} from '@vicons/material'
import AppFilterToolbar from '@/components/common/AppFilterToolbar.vue'
import AppSurfaceCard from '@/components/common/AppSurfaceCard.vue'
import AppStatusBadge, { type BadgeVariant } from '@/components/common/AppStatusBadge.vue'
import AppEmptyState from '@/components/common/AppEmptyState.vue'
import McpFormDrawer from '@/views/studio/McpFormDrawer.vue'
import McpToolsModal from '@/views/studio/McpToolsModal.vue'
import McpImportModal from '@/views/studio/McpImportModal.vue'
import { mcpApi } from '@/api/mcp'
import { extractApiError } from '@/api/client'
import type { MCPServerItem, MCPTransport } from '@/types/mcp'

const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()

const loading = ref(false)
const servers = ref<MCPServerItem[]>([])
const transportFilter = ref<'all' | MCPTransport>('all')
const statusFilter = ref<'all' | 'enabled' | 'disabled'>('all')
const searchQuery = ref('')
let searchTimer: number | undefined

const counts = ref({
  all: 0,
  stdio: 0,
  streamable_http: 0,
  sse: 0,
  enabled: 0,
})

const syncingIds = ref<Record<string, boolean>>({})
const drawerShow = ref(false)
const editingServer = ref<MCPServerItem | null>(null)
const toolsModalShow = ref(false)
const inspectingServer = ref<MCPServerItem | null>(null)
const importModalShow = ref(false)

const transportOptions = computed(() => [
  { label: `${t('mcp.tabAll')} (${counts.value.all})`, value: 'all' },
  { label: `Stdio (${counts.value.stdio})`, value: 'stdio' },
  { label: `Streamable HTTP (${counts.value.streamable_http})`, value: 'streamable_http' },
  { label: `SSE (${counts.value.sse})`, value: 'sse' },
])

const statusOptions = computed(() => [
  { label: `${t('common.status')}: ${t('common.all')}`, value: 'all' },
  { label: t('common.enabled'), value: 'enabled' },
  { label: t('common.disabled'), value: 'disabled' },
])

async function loadServers(): Promise<void> {
  loading.value = true
  try {
    const res = await mcpApi.list({
      transport: transportFilter.value === 'all' ? undefined : transportFilter.value,
      is_enabled: statusFilter.value === 'all' ? undefined : statusFilter.value === 'enabled',
      search: searchQuery.value.trim() || undefined,
      page: 1,
      page_size: 100,
    })
    servers.value = res.items
    counts.value = res.transport_counts
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editingServer.value = null
  drawerShow.value = true
}

function openEdit(item: MCPServerItem): void {
  editingServer.value = item
  drawerShow.value = true
}

function openTools(item: MCPServerItem): void {
  inspectingServer.value = item
  toolsModalShow.value = true
}

async function handleToggle(item: MCPServerItem): Promise<void> {
  try {
    const updated = await mcpApi.toggle(item.id)
    const idx = servers.value.findIndex((s) => s.id === item.id)
    if (idx !== -1) servers.value[idx] = updated
  } catch (err) {
    message.error(extractApiError(err))
  }
}

async function handleSync(item: MCPServerItem): Promise<void> {
  syncingIds.value[item.id] = true
  try {
    const updated = await mcpApi.refresh(item.id)
    const idx = servers.value.findIndex((s) => s.id === item.id)
    if (idx !== -1) servers.value[idx] = updated
    if (updated.last_status === 'connected') {
      message.success(t('mcp.syncSuccess', { count: updated.tool_count }))
    } else {
      message.error(updated.last_error || t('common.operationFailed'))
    }
  } catch (err) {
    message.error(extractApiError(err))
  } finally {
    syncingIds.value[item.id] = false
  }
}

function handleModalSynced(updated: MCPServerItem): void {
  const idx = servers.value.findIndex((s) => s.id === updated.id)
  if (idx !== -1) servers.value[idx] = updated
}

function confirmDelete(item: MCPServerItem): void {
  dialog.warning({
    title: t('common.delete'),
    content: t('mcp.deleteConfirm', { name: item.name }),
    positiveText: t('common.confirmDelete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await mcpApi.remove(item.id)
        message.success(t('common.deleteSuccess'))
        await loadServers()
      } catch (err) {
        message.error(extractApiError(err))
      }
    },
  })
}

function transportLabel(tr: MCPTransport): string {
  if (tr === 'stdio') return 'STDIO'
  if (tr === 'streamable_http') return 'HTTP'
  return 'SSE'
}

function statusVariant(st: string): BadgeVariant {
  if (st === 'connected') return 'success'
  if (st === 'error') return 'error'
  return 'neutral'
}

function statusText(item: MCPServerItem): string {
  if (item.last_status === 'connected') {
    return item.last_latency_ms != null
      ? `${t('mcp.statusConnected')} · ${item.last_latency_ms}ms`
      : t('mcp.statusConnected')
  }
  if (item.last_status === 'error') return t('mcp.statusError')
  return t('mcp.statusUnknown')
}

function endpointPreview(item: MCPServerItem): string {
  if (item.transport === 'stdio') {
    return [item.command, ...(item.args || [])].filter(Boolean).join(' ')
  }
  return item.url || '—'
}

watch([transportFilter, statusFilter], loadServers)

watch(searchQuery, () => {
  if (searchTimer) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(loadServers, 240)
})

onMounted(loadServers)
onActivated(loadServers)
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
            :placeholder="t('mcp.searchPlaceholder')"
          >
            <template #prefix><NIcon :component="SearchOutlined" /></template>
          </NInput>
        </div>
        <NSelect
          v-model:value="transportFilter"
          size="small"
          :options="transportOptions"
          :style="{ width: '185px' }"
        />
        <NSelect
          v-model:value="statusFilter"
          size="small"
          :options="statusOptions"
          :style="{ width: '140px' }"
        />
      </template>

      <template #actions>
        <NButton secondary size="small" :loading="loading" @click="loadServers">
          <template #icon><NIcon :component="RefreshOutlined" /></template>
          {{ t('common.refresh') }}
        </NButton>
        <NButton secondary size="small" @click="importModalShow = true">
          <template #icon><NIcon :component="FileUploadOutlined" /></template>
          {{ t('mcp.importJson') }}
        </NButton>
        <NButton type="primary" size="small" @click="openCreate">
          <template #icon><NIcon :component="AddOutlined" /></template>
          {{ t('mcp.addServer') }}
        </NButton>
      </template>
    </AppFilterToolbar>

    <!-- Empty State -->
    <AppSurfaceCard v-if="!loading && servers.length === 0">
      <AppEmptyState :title="t('mcp.emptyTitle')" :description="t('mcp.emptyDesc')">
        <template #icon><NIcon :size="22" :component="HubOutlined" /></template>
        <template #action>
          <div class="flex items-center gap-2">
            <NButton secondary @click="importModalShow = true">
              <template #icon><NIcon :component="FileUploadOutlined" /></template>
              {{ t('mcp.importJson') }}
            </NButton>
            <NButton type="primary" @click="openCreate">
              <template #icon><NIcon :component="AddOutlined" /></template>
              {{ t('mcp.addServer') }}
            </NButton>
          </div>
        </template>
      </AppEmptyState>
    </AppSurfaceCard>

    <!-- Google-Style Crisp MCP Server Card Grid -->
    <TransitionGroup
      v-else
      name="g-list"
      tag="div"
      class="grid grid-cols-1 gap-3.5 md:grid-cols-2 xl:grid-cols-3"
    >
      <article
        v-for="item in servers"
        :key="item.id"
        class="mcp-card g-transition flex flex-col justify-between border"
      >
        <div class="p-4">
          <div class="flex items-start justify-between gap-2.5">
            <div class="flex min-w-0 items-center gap-2.5">
              <div
                class="flex h-9 w-9 shrink-0 items-center justify-center border"
                style="
                  background: var(--g-primary-soft);
                  border-color: var(--g-border-subtle);
                  color: var(--g-primary-text);
                  border-radius: var(--g-radius-sm);
                "
              >
                <NIcon :size="19" :component="HubOutlined" />
              </div>
              <div class="min-w-0">
                <div class="flex items-center gap-1.5">
                  <h3
                    class="m-0 truncate text-[14px] font-medium"
                    style="color: var(--g-text-primary)"
                  >
                    {{ item.name }}
                  </h3>
                  <AppStatusBadge
                    variant="info"
                    :label="transportLabel(item.transport)"
                    :dot="false"
                  />
                </div>
                <div class="mt-1">
                  <AppStatusBadge
                    :variant="statusVariant(item.last_status)"
                    :label="statusText(item)"
                  />
                </div>
              </div>
            </div>

            <NSwitch
              size="small"
              :value="item.is_enabled"
              @update:value="handleToggle(item)"
            />
          </div>

          <p
            v-if="item.description"
            class="mt-2.5 mb-0 line-clamp-2 text-xs leading-relaxed"
            style="color: var(--g-text-secondary)"
          >
            {{ item.description }}
          </p>

          <!-- Endpoint / Command Box -->
          <div
            class="mt-3 truncate border px-2.5 py-2 font-mono text-[11px]"
            style="
              background: var(--g-bg-canvas);
              border-color: var(--g-border-subtle);
              color: var(--g-text-secondary);
              border-radius: var(--g-radius-xs);
            "
            :title="endpointPreview(item)"
          >
            {{ endpointPreview(item) }}
          </div>

          <!-- Error Preview if Last Sync Failed -->
          <div
            v-if="item.last_status === 'error' && item.last_error"
            class="mt-2 line-clamp-2 border px-2.5 py-1.5 font-mono text-[11px]"
            style="
              background: var(--g-error-soft);
              border-color: var(--g-error);
              color: var(--g-error);
              border-radius: var(--g-radius-xs);
            "
            :title="item.last_error"
          >
            {{ item.last_error }}
          </div>

          <!-- Discovered Tools Preview -->
          <div class="mt-3">
            <div
              class="mb-1.5 flex items-center gap-1 text-[11px] font-medium"
              style="color: var(--g-text-secondary)"
            >
              <NIcon :size="13" :component="BuildOutlined" />
              {{ t('mcp.toolsCount', { count: item.tool_count }) }}
            </div>
            <div v-if="item.discovered_tools.length > 0" class="flex flex-wrap gap-1">
              <span
                v-for="tool in item.discovered_tools.slice(0, 5)"
                :key="tool.name"
                class="cursor-pointer px-1.5 py-0.5 font-mono text-[10px]"
                style="
                  background: var(--g-bg-subtle);
                  color: var(--g-text-primary);
                  border-radius: var(--g-radius-xs);
                "
                @click="openTools(item)"
              >
                {{ tool.name }}
              </span>
              <span
                v-if="item.discovered_tools.length > 5"
                class="cursor-pointer px-1.5 py-0.5 font-mono text-[10px]"
                style="
                  background: var(--g-primary-soft);
                  color: var(--g-primary-text);
                  border-radius: var(--g-radius-xs);
                "
                @click="openTools(item)"
              >
                +{{ item.discovered_tools.length - 5 }}
              </span>
            </div>
            <div v-else class="text-[11px]" style="color: var(--g-text-tertiary)">
              {{ t('mcp.noToolsYet') }}
            </div>
          </div>
        </div>

        <!-- Card Footer Actions -->
        <div
          class="flex items-center justify-between gap-2 border-t px-4 py-2.5"
          style="border-color: var(--g-border-subtle)"
        >
          <div class="flex items-center gap-1.5">
            <NButton
              size="tiny"
              secondary
              type="primary"
              :loading="Boolean(syncingIds[item.id])"
              @click="handleSync(item)"
            >
              <template #icon><NIcon :component="SyncOutlined" /></template>
              {{ syncingIds[item.id] ? t('mcp.syncing') : t('mcp.syncTools') }}
            </NButton>
            <NButton size="tiny" secondary @click="openTools(item)">
              <template #icon><NIcon :component="CodeOutlined" /></template>
              {{ t('mcp.inspectTools') }}
            </NButton>
          </div>

          <div class="flex items-center gap-0.5">
            <NTooltip>
              <template #trigger>
                <NButton size="tiny" quaternary @click="openEdit(item)">
                  <template #icon><NIcon :size="14" :component="EditOutlined" /></template>
                </NButton>
              </template>
              {{ t('common.edit') }}
            </NTooltip>
            <NTooltip>
              <template #trigger>
                <NButton size="tiny" quaternary type="error" @click="confirmDelete(item)">
                  <template #icon><NIcon :size="14" :component="DeleteOutlineOutlined" /></template>
                </NButton>
              </template>
              {{ t('common.delete') }}
            </NTooltip>
          </div>
        </div>
      </article>
    </TransitionGroup>

    <McpFormDrawer v-model:show="drawerShow" :editing-server="editingServer" @saved="loadServers" />
    <McpToolsModal
      v-model:show="toolsModalShow"
      :server="inspectingServer"
      @synced="handleModalSynced"
    />
    <McpImportModal v-model:show="importModalShow" @imported="loadServers" />
  </div>
</template>

<style scoped>
.mcp-card {
  background: var(--g-bg-surface);
  border-color: var(--g-border);
  border-radius: var(--g-radius-sm);
}

.mcp-card:hover {
  border-color: var(--g-primary);
  box-shadow: var(--g-shadow-sm);
}
</style>
