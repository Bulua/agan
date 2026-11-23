<script setup lang="ts">
import { computed, onActivated, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NDatePicker,
  NForm,
  NFormItem,
  NIcon,
  NInput,
  NSpin,
  NTooltip,
  useDialog,
  useMessage,
} from 'naive-ui'
import {
  AddOutlined,
  DeleteOutlineOutlined,
  DescriptionOutlined,
  EditOutlined,
  RefreshOutlined,
  SaveOutlined,
  SearchOutlined,
} from '@vicons/material'
import { extractApiError } from '@/api/client'
import { promptsApi } from '@/api/prompts'
import AppEmptyState from '@/components/common/AppEmptyState.vue'
import AppFilterToolbar from '@/components/common/AppFilterToolbar.vue'
import AppFormDrawer from '@/components/common/AppFormDrawer.vue'
import AppPaginationBar from '@/components/common/AppPaginationBar.vue'
import AppSurfaceCard from '@/components/common/AppSurfaceCard.vue'
import type { PromptTemplate } from '@/types/prompt'

const { t, locale } = useI18n()
const dialog = useDialog()
const message = useMessage()
const templates = ref<PromptTemplate[]>([])
const editing = ref<PromptTemplate | null>(null)
const drawerShow = ref(false)
const loading = ref(false)
const saving = ref(false)
const loadError = ref('')
const searchQuery = ref('')
const createdRange = ref<[number, number] | null>(null)
const page = ref(1)
const pageSize = ref(10)
const form = reactive({ name: '', content: '' })
let latestLoad = 0

const filteredTemplates = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase(locale.value)
  const namedTemplates = query
    ? templates.value.filter(({ name }) => name.toLocaleLowerCase(locale.value).includes(query))
    : templates.value
  if (!createdRange.value) return namedTemplates

  const [start, end] = createdRange.value
  const from = new Date(start).setHours(0, 0, 0, 0)
  const nextDay = new Date(end)
  nextDay.setDate(nextDay.getDate() + 1)
  const until = nextDay.setHours(0, 0, 0, 0)
  return namedTemplates.filter(({ created_at }) => {
    const created = Date.parse(created_at)
    return created >= from && created < until
  })
})

const total = computed(() => filteredTemplates.value.length)
const visibleTemplates = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return filteredTemplates.value.slice(start, start + pageSize.value)
})

watch([searchQuery, createdRange], () => {
  page.value = 1
})

watch([total, pageSize], () => {
  const lastPage = Math.max(1, Math.ceil(total.value / pageSize.value))
  page.value = Math.min(page.value, lastPage)
})

const dateFormatter = computed(() =>
  new Intl.DateTimeFormat(locale.value, { dateStyle: 'medium', timeStyle: 'medium' }),
)

function countCharacters(value: string): number {
  return Array.from(value).length
}

async function loadTemplates(): Promise<void> {
  const load = ++latestLoad
  loading.value = true
  loadError.value = ''
  try {
    const items = await promptsApi.list()
    if (load === latestLoad) templates.value = items
  } catch (err) {
    if (load === latestLoad) loadError.value = extractApiError(err, t('common.operationFailed'))
  } finally {
    if (load === latestLoad) loading.value = false
  }
}

function openEditor(template: PromptTemplate | null = null): void {
  editing.value = template
  form.name = template?.name ?? ''
  form.content = template?.content ?? ''
  drawerShow.value = true
}

async function saveTemplate(): Promise<void> {
  if (saving.value || !form.name.trim() || !form.content.trim()) return
  saving.value = true
  try {
    const payload = { name: form.name.trim(), content: form.content }
    if (editing.value) {
      await promptsApi.update(editing.value.id, payload)
    } else {
      await promptsApi.create(payload)
    }
    drawerShow.value = false
    message.success(t('common.saveSuccess'))
    await loadTemplates()
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    saving.value = false
  }
}

function confirmDelete(template: PromptTemplate): void {
  dialog.warning({
    title: t('common.confirmDelete'),
    content: t('prompts.deleteDescription', { name: template.name }),
    positiveText: t('common.delete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await promptsApi.remove(template.id)
        message.success(t('common.deleteSuccess'))
        await loadTemplates()
      } catch (err) {
        message.error(extractApiError(err, t('common.operationFailed')))
      }
    },
  })
}

onActivated(loadTemplates)
</script>

<template>
  <div>
    <AppFilterToolbar>
      <template #filters>
        <div class="w-full sm:w-52">
          <NInput
            v-model:value="searchQuery"
            size="small"
            clearable
            :placeholder="t('prompts.search')"
            :input-props="{ 'aria-label': t('prompts.search') }"
          >
            <template #prefix><NIcon :component="SearchOutlined" /></template>
          </NInput>
        </div>
        <div
          class="flex w-full flex-wrap items-center gap-x-2 gap-y-1.5 sm:w-auto"
          role="group"
          aria-labelledby="prompt-created-range-label"
        >
          <span id="prompt-created-range-label" class="prompt-meta shrink-0 text-xs">
            {{ t('prompts.createdAt') }}
          </span>
          <NDatePicker
            v-model:value="createdRange"
            class="w-full sm:w-[17.5rem]"
            type="daterange"
            size="small"
            clearable
            :start-placeholder="t('prompts.startDate')"
            :end-placeholder="t('prompts.endDate')"
          />
        </div>
      </template>
      <template #actions>
        <NButton size="small" secondary :loading="loading" @click="loadTemplates">
          <template #icon><NIcon :component="RefreshOutlined" /></template>
          {{ t('common.refresh') }}
        </NButton>
        <NButton size="small" type="primary" :disabled="saving" @click="openEditor()">
          <template #icon><NIcon :component="AddOutlined" /></template>
          {{ t('prompts.add') }}
        </NButton>
      </template>
    </AppFilterToolbar>

    <NAlert v-if="loadError" type="error" class="mb-3">{{ loadError }}</NAlert>
    <AppSurfaceCard no-padding>
      <NSpin :show="loading" class="min-h-20">
        <div class="overflow-x-auto">
          <table class="w-full min-w-[868px] table-fixed border-collapse text-left text-xs" :aria-label="t('nav.prompts')">
            <thead class="prompt-table-head">
              <tr>
                <th scope="col" class="w-40 px-4 py-2.5 font-medium">{{ t('prompts.name') }}</th>
                <th scope="col" class="px-4 py-2.5 font-medium">{{ t('prompts.content') }}</th>
                <th scope="col" class="w-24 px-3 py-2.5 text-right font-medium">{{ t('prompts.characters') }}</th>
                <th scope="col" class="w-48 px-4 py-2.5 font-medium">{{ t('prompts.createdAt') }}</th>
                <th scope="col" class="w-48 px-4 py-2.5 font-medium">{{ t('prompts.updatedAt') }}</th>
                <th scope="col" class="w-24 px-4 py-2.5 text-right font-medium">{{ t('common.actions') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="template in visibleTemplates" :key="template.id" class="prompt-row g-transition">
                <th scope="row" class="px-4 py-2.5 font-normal">
                  <div class="prompt-name truncate text-[13px] font-medium" :title="template.name">
                    {{ template.name }}
                  </div>
                </th>
                <td class="px-4 py-2.5">
                  <div class="prompt-meta line-clamp-2 break-words leading-relaxed">
                    {{ template.content.trim() }}
                  </div>
                </td>
                <td class="prompt-meta px-3 text-right tabular-nums">
                  {{ template.character_count.toLocaleString(locale) }}
                </td>
                <td class="prompt-meta whitespace-nowrap px-4 tabular-nums">
                  <time :datetime="template.created_at">{{ dateFormatter.format(new Date(template.created_at)) }}</time>
                </td>
                <td class="prompt-meta whitespace-nowrap px-4 tabular-nums">
                  <time :datetime="template.updated_at">{{ dateFormatter.format(new Date(template.updated_at)) }}</time>
                </td>
                <td class="px-4 text-right">
                  <div class="inline-flex items-center gap-1">
                    <NTooltip>
                      <template #trigger>
                        <NButton size="tiny" quaternary :aria-label="t('common.edit')" :disabled="saving" @click="openEditor(template)">
                          <template #icon><NIcon :component="EditOutlined" /></template>
                        </NButton>
                      </template>
                      {{ t('common.edit') }}
                    </NTooltip>
                    <NTooltip>
                      <template #trigger>
                        <NButton size="tiny" quaternary type="error" :aria-label="t('common.delete')" :disabled="saving" @click="confirmDelete(template)">
                          <template #icon><NIcon :component="DeleteOutlineOutlined" /></template>
                        </NButton>
                      </template>
                      {{ t('common.delete') }}
                    </NTooltip>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <AppEmptyState
          v-if="!loading && !loadError && !total"
          :title="t(templates.length ? 'prompts.noMatches' : 'prompts.empty')"
        >
          <template #icon><NIcon :size="22" :component="DescriptionOutlined" /></template>
        </AppEmptyState>
      </NSpin>
      <AppPaginationBar v-model:page="page" v-model:page-size="pageSize" :total="total" />
    </AppSurfaceCard>

    <AppFormDrawer
      :show="drawerShow"
      :title="t(editing ? 'prompts.edit' : 'prompts.add')"
      :mask-closable="!saving"
      @update:show="drawerShow = $event"
    >
      <NForm :model="form" label-placement="top" :disabled="saving">
        <NFormItem :label="t('prompts.name')" required>
          <NInput v-model:value="form.name" :maxlength="120" />
        </NFormItem>
        <NFormItem :label="t('prompts.content')" required>
          <NInput
            v-model:value="form.content"
            type="textarea"
            :rows="14"
            :maxlength="20000"
            :count-graphemes="countCharacters"
            show-count
            class="font-mono"
          />
        </NFormItem>
      </NForm>
      <template #footer>
        <NButton :disabled="saving" @click="drawerShow = false">{{ t('common.cancel') }}</NButton>
        <NButton
          type="primary"
          :loading="saving"
          :disabled="!form.name.trim() || !form.content.trim()"
          @click="saveTemplate"
        >
          <template #icon><NIcon :component="SaveOutlined" /></template>
          {{ t('common.save') }}
        </NButton>
      </template>
    </AppFormDrawer>
  </div>
</template>

<style scoped>
.prompt-table-head {
  background: var(--g-bg-canvas);
  border-bottom: 1px solid var(--g-border-subtle);
  color: var(--g-text-secondary);
}

.prompt-row + .prompt-row {
  border-top: 1px solid var(--g-border-subtle);
}

.prompt-row:hover {
  background: var(--g-bg-subtle);
}

.prompt-name {
  color: var(--g-text-primary);
}

.prompt-meta {
  color: var(--g-text-secondary);
}
</style>
