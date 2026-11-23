<script setup lang="ts">
import { computed, onActivated, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
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
  ArrowForwardOutlined,
  BuildOutlined,
  ContentCopyOutlined,
  DeleteOutlineOutlined,
  ModelTrainingOutlined,
  RefreshOutlined,
  SearchOutlined,
  SmartToyOutlined,
  StarBorderOutlined,
  StarOutlined,
  TerminalOutlined,
} from '@vicons/material'
import AppFilterToolbar from '@/components/common/AppFilterToolbar.vue'
import AppSurfaceCard from '@/components/common/AppSurfaceCard.vue'
import AppEmptyState from '@/components/common/AppEmptyState.vue'
import AgentFormDrawer from '@/views/studio/AgentFormDrawer.vue'
import { agentsApi } from '@/api/agents'
import { extractApiError } from '@/api/client'
import type { AgentItem } from '@/types/agent'

const router = useRouter()
const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()

const loading = ref(false)
const agents = ref<AgentItem[]>([])
const statusFilter = ref<'all' | 'active' | 'disabled'>('all')
const searchQuery = ref('')
let searchTimer: number | undefined

const drawerShow = ref(false)

const statusOptions = computed(() => [
  { label: `${t('common.status')}: ${t('common.all')}`, value: 'all' },
  { label: t('common.enabled'), value: 'active' },
  { label: t('common.disabled'), value: 'disabled' },
])

async function loadAgents(): Promise<void> {
  loading.value = true
  try {
    const res = await agentsApi.list({
      is_active: statusFilter.value === 'all' ? undefined : statusFilter.value === 'active',
      search: searchQuery.value.trim() || undefined,
      page: 1,
      page_size: 100,
    })
    agents.value = res.items
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    loading.value = false
  }
}

function openDetail(agent: AgentItem): void {
  router.push(`/studio/agents/${agent.id}`)
}

async function handleToggle(item: AgentItem): Promise<void> {
  try {
    const updated = await agentsApi.toggle(item.id)
    const idx = agents.value.findIndex((a) => a.id === item.id)
    if (idx !== -1) agents.value[idx] = updated
  } catch (err) {
    message.error(extractApiError(err))
  }
}

async function handleSetDefault(item: AgentItem): Promise<void> {
  try {
    await agentsApi.setDefault(item.id)
    message.success(t('common.saveSuccess'))
    await loadAgents()
  } catch (err) {
    message.error(extractApiError(err))
  }
}

async function handleClone(item: AgentItem): Promise<void> {
  try {
    await agentsApi.clone(item.id)
    message.success(t('common.saveSuccess'))
    await loadAgents()
  } catch (err) {
    message.error(extractApiError(err))
  }
}

function confirmDelete(item: AgentItem): void {
  dialog.warning({
    title: t('common.delete'),
    content: t('agents.deleteConfirm', { name: item.name }),
    positiveText: t('common.confirmDelete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await agentsApi.remove(item.id)
        message.success(t('common.deleteSuccess'))
        await loadAgents()
      } catch (err) {
        message.error(extractApiError(err))
      }
    },
  })
}

watch(statusFilter, () => {
  loadAgents()
})

watch(searchQuery, () => {
  if (searchTimer) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    loadAgents()
  }, 240)
})

onMounted(loadAgents)
onActivated(loadAgents)
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
            :placeholder="t('agents.searchPlaceholder')"
          >
            <template #prefix><NIcon :component="SearchOutlined" /></template>
          </NInput>
        </div>
        <NSelect
          v-model:value="statusFilter"
          size="small"
          :options="statusOptions"
          :style="{ width: '140px' }"
        />
      </template>

      <template #actions>
        <NButton secondary size="small" :loading="loading" @click="loadAgents">
          <template #icon><NIcon :component="RefreshOutlined" /></template>
          {{ t('common.refresh') }}
        </NButton>
        <NButton type="primary" size="small" @click="drawerShow = true">
          <template #icon><NIcon :component="AddOutlined" /></template>
          {{ t('agents.addAgent') }}
        </NButton>
      </template>
    </AppFilterToolbar>

    <!-- Empty State -->
    <AppSurfaceCard v-if="!loading && agents.length === 0">
      <AppEmptyState :title="t('agents.emptyTitle')" :description="t('agents.emptyDesc')">
        <template #icon><NIcon :size="22" :component="SmartToyOutlined" /></template>
        <template #action>
          <NButton type="primary" @click="drawerShow = true">
            <template #icon><NIcon :component="AddOutlined" /></template>
            {{ t('agents.addAgent') }}
          </NButton>
        </template>
      </AppEmptyState>
    </AppSurfaceCard>

    <!-- Google-Style Crisp Card Grid -->
    <template v-else>
      <TransitionGroup
        name="g-list"
        tag="div"
        class="grid grid-cols-1 gap-3.5 md:grid-cols-2 xl:grid-cols-3"
      >
        <article
          v-for="item in agents"
          :key="item.id"
          class="agent-card g-transition group flex cursor-pointer flex-col justify-between border"
          @click="openDetail(item)"
        >
          <!-- Card Header & Body -->
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
                  <NIcon :size="19" :component="SmartToyOutlined" />
                </div>
                <div class="min-w-0">
                  <div class="flex items-center gap-1.5">
                    <h3
                      class="m-0 truncate text-[14px] font-medium"
                      style="color: var(--g-text-primary)"
                    >
                      {{ item.name }}
                    </h3>
                    <span
                      v-if="item.is_default"
                      class="inline-flex shrink-0 items-center gap-0.5 px-1.5 py-0.5 text-[10px] font-medium"
                      style="
                        background: var(--g-primary-soft);
                        color: var(--g-primary-text);
                        border-radius: var(--g-radius-xs);
                      "
                    >
                      <NIcon :size="10" :component="StarOutlined" />
                      {{ t('common.default') }}
                    </span>
                  </div>
                </div>
              </div>

              <div class="shrink-0" @click.stop>
                <NSwitch
                  size="small"
                  :value="item.is_active"
                  @update:value="handleToggle(item)"
                />
              </div>
            </div>

            <!-- Description -->
            <p
              class="mt-3 mb-3 line-clamp-2 min-h-[34px] text-xs leading-relaxed"
              style="color: var(--g-text-secondary)"
            >
              {{ item.description || item.system_prompt || '—' }}
            </p>

            <!-- Compact Capability Metadata -->
            <div
              class="flex flex-col gap-1.5 border p-2.5 font-mono text-[11px]"
              style="
                background: var(--g-bg-canvas);
                border-color: var(--g-border-subtle);
                border-radius: var(--g-radius-xs);
              "
            >
              <div class="flex items-center gap-1.5 truncate" style="color: var(--g-text-primary)">
                <NIcon :size="13" :component="ModelTrainingOutlined" style="color: var(--g-text-tertiary)" />
                <span class="truncate">{{ item.chat_model_name || '—' }}</span>
              </div>
              <div class="flex items-center justify-between gap-2" style="color: var(--g-text-secondary)">
                <span class="flex items-center gap-1">
                  <NIcon :size="13" :component="TerminalOutlined" style="color: var(--g-text-tertiary)" />
                  {{ item.loop_engine }} · {{ item.sandbox_mode }}
                </span>
                <span class="flex items-center gap-1">
                  <NIcon :size="13" :component="BuildOutlined" style="color: var(--g-text-tertiary)" />
                  {{ t('agents.detail.toolsBoundCount', { count: item.tools.length }) }}
                </span>
              </div>
            </div>
          </div>

          <!-- Card Footer -->
          <div
            class="flex items-center justify-between gap-2 border-t px-4 py-2.5"
            style="border-color: var(--g-border-subtle)"
          >
            <div class="flex min-w-0 flex-wrap items-center gap-1">
              <span
                v-for="tool in item.tools.slice(0, 2)"
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
              <span
                v-if="item.tools.length > 2"
                class="px-1.5 py-0.5 font-mono text-[10px]"
                style="
                  background: var(--g-primary-soft);
                  color: var(--g-primary-text);
                  border-radius: var(--g-radius-xs);
                "
              >
                +{{ item.tools.length - 2 }}
              </span>
            </div>

            <div class="flex shrink-0 items-center gap-0.5" @click.stop>
              <NTooltip v-if="!item.is_default">
                <template #trigger>
                  <NButton size="tiny" quaternary @click="handleSetDefault(item)">
                    <template #icon><NIcon :size="14" :component="StarBorderOutlined" /></template>
                  </NButton>
                </template>
                {{ t('common.setDefault') }}
              </NTooltip>
              <NTooltip>
                <template #trigger>
                  <NButton size="tiny" quaternary @click="handleClone(item)">
                    <template #icon><NIcon :size="14" :component="ContentCopyOutlined" /></template>
                  </NButton>
                </template>
                {{ t('agents.clone') }}
              </NTooltip>
              <NTooltip>
                <template #trigger>
                  <NButton size="tiny" quaternary type="error" @click="confirmDelete(item)">
                    <template #icon><NIcon :size="14" :component="DeleteOutlineOutlined" /></template>
                  </NButton>
                </template>
                {{ t('common.delete') }}
              </NTooltip>
              <span
                class="ml-1 inline-flex items-center gap-0.5 text-[11px] font-medium opacity-75 transition-opacity group-hover:opacity-100"
                style="color: var(--g-primary-text)"
                @click="openDetail(item)"
              >
                {{ t('agents.detail.configure') }}
                <NIcon :size="13" :component="ArrowForwardOutlined" />
              </span>
            </div>
          </div>
        </article>
      </TransitionGroup>
    </template>

    <!-- Fast Create Drawer -->
    <AgentFormDrawer v-model:show="drawerShow" :editing-agent="null" @saved="loadAgents" />
  </div>
</template>

<style scoped>
.agent-card {
  background: var(--g-bg-surface);
  border-color: var(--g-border);
  border-radius: var(--g-radius-sm);
}

.agent-card:hover {
  border-color: var(--g-primary);
  box-shadow: var(--g-shadow-sm);
  transform: translateY(-1px);
}
</style>
