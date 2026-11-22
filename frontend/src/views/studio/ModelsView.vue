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
  BoltOutlined,
  DeleteOutlineOutlined,
  EditOutlined,
  ModelTrainingOutlined,
  RefreshOutlined,
  SearchOutlined,
  StarBorderOutlined,
  StarOutlined,
} from '@vicons/material'
import AppFilterToolbar from '@/components/common/AppFilterToolbar.vue'
import AppSurfaceCard from '@/components/common/AppSurfaceCard.vue'
import AppStatusBadge, { type BadgeVariant } from '@/components/common/AppStatusBadge.vue'
import AppEmptyState from '@/components/common/AppEmptyState.vue'
import AppPaginationBar from '@/components/common/AppPaginationBar.vue'
import ModelFormDrawer from '@/views/studio/ModelFormDrawer.vue'
import ModelTestModal from '@/views/studio/ModelTestModal.vue'
import { modelsApi } from '@/api/models'
import { extractApiError } from '@/api/client'
import { MODEL_PROVIDERS } from '@/constants/modelPresets'
import type { LLMModel, ModelType } from '@/types/llm'

const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()

const loading = ref(false)
const models = ref<LLMModel[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const counts = ref({
  total: 0,
  chat: 0,
  embedding: 0,
  rerank: 0,
})

const typeFilter = ref<'all' | ModelType>('all')
const statusFilter = ref<'all' | 'active' | 'disabled'>('all')
const searchQuery = ref('')
let searchTimer: number | undefined

const drawerVisible = ref(false)
const editingModel = ref<LLMModel | null>(null)
const testModalVisible = ref(false)
const testingModel = ref<LLMModel | null>(null)

const typeOptions = computed(() => [
  { label: `${t('models.tabAll')} (${counts.value.total})`, value: 'all' },
  { label: `${t('models.tabChat')} (${counts.value.chat})`, value: 'chat' },
  { label: `${t('models.tabEmbedding')} (${counts.value.embedding})`, value: 'embedding' },
  { label: `${t('models.tabRerank')} (${counts.value.rerank})`, value: 'rerank' },
])

const statusOptions = computed(() => [
  { label: `${t('common.status')}: ${t('common.all')}`, value: 'all' },
  { label: t('common.enabled'), value: 'active' },
  { label: t('common.disabled'), value: 'disabled' },
])

async function loadModels(): Promise<void> {
  loading.value = true
  try {
    const res = await modelsApi.list({
      model_type: typeFilter.value === 'all' ? undefined : typeFilter.value,
      is_active:
        statusFilter.value === 'all' ? undefined : statusFilter.value === 'active',
      search: searchQuery.value.trim() || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    if (res.items.length === 0 && page.value > 1 && res.total > 0) {
      page.value = Math.max(1, res.total_pages)
      await loadModels()
      return
    }
    models.value = res.items
    total.value = res.total
    counts.value = {
      total: res.category_counts?.all ?? res.total,
      chat: res.category_counts?.chat ?? 0,
      embedding: res.category_counts?.embedding ?? 0,
      rerank: res.category_counts?.rerank ?? 0,
    }
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editingModel.value = null
  drawerVisible.value = true
}

function openEdit(model: LLMModel): void {
  editingModel.value = model
  drawerVisible.value = true
}

function openTest(model: LLMModel): void {
  testingModel.value = model
  testModalVisible.value = true
}

async function handleToggle(model: LLMModel): Promise<void> {
  try {
    const updated = await modelsApi.toggle(model.id)
    const idx = models.value.findIndex((m) => m.id === model.id)
    if (idx !== -1) models.value[idx] = updated
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  }
}

async function handleSetDefault(model: LLMModel): Promise<void> {
  try {
    await modelsApi.setDefault(model.id)
    await loadModels()
    message.success(t('common.saveSuccess'))
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  }
}

function confirmDelete(model: LLMModel): void {
  dialog.warning({
    title: t('models.deleteConfirmTitle'),
    content: t('models.deleteConfirmDesc', { name: model.name }),
    positiveText: t('common.confirmDelete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await modelsApi.remove(model.id)
        message.success(t('common.deleteSuccess'))
        await loadModels()
      } catch (err) {
        message.error(extractApiError(err, t('common.operationFailed')))
      }
    },
  })
}

function healthBadge(model: LLMModel): { variant: BadgeVariant; label: string } {
  if (model.last_test_status === 'ok') {
    const ms = model.last_test_latency_ms != null ? ` · ${model.last_test_latency_ms}ms` : ''
    return { variant: 'success', label: `${t('models.statusOk')}${ms}` }
  }
  if (model.last_test_status === 'error') {
    return { variant: 'error', label: t('models.statusError') }
  }
  return { variant: 'neutral', label: t('models.statusUntested') }
}

const PROVIDER_LABEL_MAP: Record<string, string> = Object.fromEntries(
  MODEL_PROVIDERS.map((p) => [p.id, p.label]),
)

function providerDisplayName(providerId: string): string {
  return PROVIDER_LABEL_MAP[providerId] || providerId || 'custom'
}

function typeBadgeVariant(type: ModelType): BadgeVariant {
  if (type === 'chat') return 'info'
  if (type === 'embedding') return 'success'
  return 'warning'
}

watch([typeFilter, statusFilter], () => {
  page.value = 1
  loadModels()
})

watch(searchQuery, () => {
  if (searchTimer) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    page.value = 1
    loadModels()
  }, 240)
})

onMounted(loadModels)
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
            :placeholder="t('models.searchPlaceholder')"
          >
            <template #prefix><NIcon :component="SearchOutlined" /></template>
          </NInput>
        </div>
        <NSelect
          v-model:value="typeFilter"
          size="small"
          :options="typeOptions"
          :style="{ width: '165px' }"
        />
        <NSelect
          v-model:value="statusFilter"
          size="small"
          :options="statusOptions"
          :style="{ width: '140px' }"
        />
      </template>

      <template #actions>
        <NButton secondary size="small" :loading="loading" @click="loadModels">
          <template #icon><NIcon :component="RefreshOutlined" /></template>
          {{ t('common.refresh') }}
        </NButton>
        <NButton type="primary" size="small" @click="openCreate">
          <template #icon><NIcon :component="AddOutlined" /></template>
          {{ t('models.addModel') }}
        </NButton>
      </template>
    </AppFilterToolbar>

    <!-- Main Table Surface -->
    <AppSurfaceCard no-padding>
      <AppEmptyState
        v-if="!loading && models.length === 0"
        :title="t('models.emptyTitle')"
        :description="t('models.emptyDesc')"
      >
        <template #icon><NIcon :size="22" :component="ModelTrainingOutlined" /></template>
        <template #action>
          <NButton type="primary" @click="openCreate">
            <template #icon><NIcon :component="AddOutlined" /></template>
            {{ t('models.addModel') }}
          </NButton>
        </template>
      </AppEmptyState>

      <template v-else>
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
                <th class="px-4 py-2.5">{{ t('models.colName') }}</th>
                <th class="px-3 py-2.5">{{ t('models.colProvider') }}</th>
                <th class="px-3 py-2.5">{{ t('models.colType') }}</th>
                <th class="px-3 py-2.5">{{ t('models.colEndpoint') }}</th>
                <th class="px-3 py-2.5">{{ t('models.colParams') }}</th>
                <th class="px-3 py-2.5">{{ t('models.colHealth') }}</th>
                <th class="px-3 py-2.5 text-center">{{ t('models.colActive') }}</th>
                <th class="px-4 py-2.5 text-right">{{ t('common.actions') }}</th>
              </tr>
            </thead>
            <TransitionGroup name="g-list" tag="tbody">
              <tr
                v-for="model in models"
                :key="model.id"
                class="model-row g-transition border-b"
                style="border-color: var(--g-border-subtle)"
              >
                <td class="px-4 py-3 align-middle">
                  <div class="flex items-center gap-2">
                    <span class="text-[13px] font-medium" style="color: var(--g-text-primary)">
                      {{ model.name }}
                    </span>
                    <span
                      v-if="model.is_default"
                      class="inline-flex items-center gap-0.5 px-1.5 py-0.5 text-[10px] font-medium"
                      style="
                        background: var(--g-primary-soft);
                        color: var(--g-primary-text);
                        border-radius: var(--g-radius-xs);
                      "
                    >
                      <NIcon :size="11" :component="StarOutlined" />
                      {{ t('common.default') }}
                    </span>
                  </div>
                  <div
                    v-if="model.description"
                    class="mt-0.5 max-w-[240px] truncate text-[11px]"
                    style="color: var(--g-text-tertiary)"
                  >
                    {{ model.description }}
                  </div>
                </td>

                <td class="px-3 py-3 align-middle">
                  <div
                    class="max-w-[180px] truncate text-xs font-medium"
                    style="color: var(--g-text-primary)"
                    :title="providerDisplayName(model.provider)"
                  >
                    {{ providerDisplayName(model.provider) }}
                  </div>
                  <div class="mt-0.5 font-mono text-[10px]" style="color: var(--g-text-tertiary)">
                    {{ model.provider }}
                  </div>
                </td>

                <td class="px-3 py-3 align-middle">
                  <AppStatusBadge
                    :variant="typeBadgeVariant(model.model_type)"
                    :label="model.model_type.toUpperCase()"
                    :dot="false"
                  />
                </td>

                <td class="px-3 py-3 align-middle font-mono">
                  <div class="text-xs font-medium" style="color: var(--g-text-primary)">
                    {{ model.model_id }}
                  </div>
                  <div
                    class="mt-0.5 max-w-[280px] truncate text-[11px]"
                    style="color: var(--g-text-secondary)"
                    :title="model.base_url"
                  >
                    {{ model.base_url }}
                  </div>
                  <div
                    v-if="model.has_api_key"
                    class="mt-0.5 text-[10px]"
                    style="color: var(--g-text-tertiary)"
                  >
                    Key: {{ model.api_key_masked }}
                  </div>
                </td>

                <td
                  class="px-3 py-3 align-middle font-mono text-[11px]"
                  style="color: var(--g-text-secondary)"
                >
                  <div v-if="model.model_type === 'chat'">
                    ctx {{ (model.context_window / 1000).toFixed(0) }}k · T={{ model.temperature }}
                  </div>
                  <div v-else-if="model.model_type === 'embedding'">
                    ctx {{ model.context_window.toLocaleString() }}
                  </div>
                  <div v-else>provider: {{ model.provider }}</div>
                  <div class="mt-0.5" style="color: var(--g-text-tertiary)">
                    ${{ model.input_price_per_1m }}/M in
                    <template v-if="model.model_type === 'chat'">
                      · ${{ model.output_price_per_1m }}/M out
                    </template>
                  </div>
                </td>

                <td class="px-3 py-3 align-middle">
                  <NTooltip :disabled="!model.last_test_message">
                    <template #trigger>
                      <AppStatusBadge
                        :variant="healthBadge(model).variant"
                        :label="healthBadge(model).label"
                      />
                    </template>
                    <span class="font-mono text-xs">{{ model.last_test_message }}</span>
                  </NTooltip>
                </td>

                <td class="px-3 py-3 text-center align-middle">
                  <NSwitch
                    size="small"
                    :value="model.is_active"
                    @update:value="handleToggle(model)"
                  />
                </td>

                <td class="px-4 py-3 text-right align-middle">
                  <div class="inline-flex items-center gap-1">
                    <NTooltip>
                      <template #trigger>
                        <NButton size="tiny" secondary type="primary" @click="openTest(model)">
                          <template #icon><NIcon :component="BoltOutlined" /></template>
                          {{ t('models.testConnection') }}
                        </NButton>
                      </template>
                      {{ t('models.testModal.title') }}
                    </NTooltip>

                    <NTooltip v-if="!model.is_default">
                      <template #trigger>
                        <NButton size="tiny" quaternary @click="handleSetDefault(model)">
                          <template #icon><NIcon :component="StarBorderOutlined" /></template>
                        </NButton>
                      </template>
                      {{ t('common.setDefault') }}
                    </NTooltip>

                    <NTooltip>
                      <template #trigger>
                        <NButton size="tiny" quaternary @click="openEdit(model)">
                          <template #icon><NIcon :component="EditOutlined" /></template>
                        </NButton>
                      </template>
                      {{ t('common.edit') }}
                    </NTooltip>

                    <NTooltip>
                      <template #trigger>
                        <NButton size="tiny" quaternary type="error" @click="confirmDelete(model)">
                          <template #icon><NIcon :component="DeleteOutlineOutlined" /></template>
                        </NButton>
                      </template>
                      {{ t('common.delete') }}
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
          @change="loadModels"
        />
      </template>
    </AppSurfaceCard>

    <ModelFormDrawer
      v-model:show="drawerVisible"
      :editing-model="editingModel"
      :default-type="typeFilter === 'all' ? 'chat' : typeFilter"
      @saved="loadModels"
    />

    <ModelTestModal v-model:show="testModalVisible" :model="testingModel" @tested="loadModels" />
  </div>
</template>

<style scoped>
.model-row:hover {
  background: var(--g-bg-hover);
}
</style>
