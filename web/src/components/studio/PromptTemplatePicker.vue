<script setup lang="ts">
import { computed, onActivated, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { NButton, NSelect } from 'naive-ui'
import { extractApiError } from '@/api/client'
import { promptsApi } from '@/api/prompts'
import type { PromptTemplate } from '@/types/prompt'

const emit = defineEmits<{ apply: [content: string] }>()
const { t } = useI18n()
const templates = ref<PromptTemplate[]>([])
const selectedId = ref<string | null>(null)
const loading = ref(false)
const error = ref('')

const options = computed(() => templates.value.map(({ id, name }) => ({ label: name, value: id })))
const selected = computed(() => templates.value.find((template) => template.id === selectedId.value))

async function loadTemplates(): Promise<void> {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    templates.value = await promptsApi.list()
    if (!selected.value) selectedId.value = null
  } catch (err) {
    error.value = extractApiError(err, t('common.operationFailed'))
  } finally {
    loading.value = false
  }
}

function applyTemplate(): void {
  if (selected.value) emit('apply', selected.value.content)
}

onMounted(loadTemplates)
onActivated(loadTemplates)
</script>

<template>
  <div class="flex flex-col gap-1.5">
    <div v-if="error" class="flex items-center gap-2">
      <span role="alert" class="min-w-0 break-words text-xs">{{ error }}</span>
      <NButton size="small" @click="loadTemplates">{{ t('common.refresh') }}</NButton>
    </div>
    <div v-else class="flex items-center gap-2">
      <NSelect
        v-model:value="selectedId"
        class="min-w-0 flex-1"
        size="small"
        filterable
        clearable
        :options="options"
        :loading="loading"
        :placeholder="t('prompts.selectTemplate')"
      >
        <template v-if="!options.length && !loading" #empty>{{ t('prompts.empty') }}</template>
      </NSelect>
      <NButton size="small" :disabled="loading || !selected" @click="applyTemplate">
        {{ t('prompts.applyTemplate') }}
      </NButton>
    </div>
  </div>
</template>
