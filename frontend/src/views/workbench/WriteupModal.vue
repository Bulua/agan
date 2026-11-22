<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { NButton, NIcon, NModal, useMessage } from 'naive-ui'
import { AutoAwesomeOutlined, ContentCopyOutlined, DownloadOutlined } from '@vicons/material'
import { sessionsApi } from '@/api/sessions'
import { extractApiError } from '@/api/client'
import { useAppStore } from '@/stores/app'
import type { WriteupDTO } from '@/types/session'

const props = defineProps<{
  show: boolean
  sessionId: string
  writeup: WriteupDTO | null
}>()

const emit = defineEmits<{
  (e: 'update:show', val: boolean): void
  (e: 'generated', wu: WriteupDTO): void
}>()

const { t } = useI18n()
const appStore = useAppStore()
const message = useMessage()
const generating = ref(false)

async function handleGenerate(): Promise<void> {
  generating.value = true
  try {
    const wu = await sessionsApi.generateWriteup(props.sessionId, appStore.locale)
    emit('generated', wu)
    message.success(t('common.saveSuccess'))
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    generating.value = false
  }
}

async function copyMarkdown(): Promise<void> {
  if (!props.writeup) return
  try {
    await navigator.clipboard.writeText(props.writeup.content_md)
    message.success(t('common.copied'))
  } catch {
    message.warning(t('common.operationFailed'))
  }
}

function downloadMd(): void {
  window.open(sessionsApi.exportWriteupUrl(props.sessionId), '_blank')
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="t('workbench.writeup.title')"
    :style="{ width: '820px', maxWidth: '94vw' }"
    @update:show="emit('update:show', $event)"
  >
    <div class="flex flex-col gap-3">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="font-mono text-xs" style="color: var(--g-text-secondary)">
          <template v-if="writeup">
            Model: {{ writeup.model_used }}
          </template>
        </div>
        <div class="flex items-center gap-2">
          <NButton type="primary" size="small" :loading="generating" @click="handleGenerate">
            <template #icon><NIcon :component="AutoAwesomeOutlined" /></template>
            {{
              generating
                ? t('workbench.writeup.generating')
                : writeup
                  ? t('workbench.writeup.regenerate')
                  : t('workbench.writeup.generate')
            }}
          </NButton>
          <NButton v-if="writeup" size="small" secondary @click="copyMarkdown">
            <template #icon><NIcon :component="ContentCopyOutlined" /></template>
            {{ t('common.copy') }}
          </NButton>
          <NButton v-if="writeup" size="small" secondary @click="downloadMd">
            <template #icon><NIcon :component="DownloadOutlined" /></template>
            {{ t('workbench.writeup.exportMd') }}
          </NButton>
        </div>
      </div>

      <div
        v-if="!writeup"
        class="border p-8 text-center text-xs"
        style="
          background: var(--g-bg-canvas);
          border-color: var(--g-border-subtle);
          color: var(--g-text-secondary);
          border-radius: var(--g-radius-sm);
        "
      >
        {{ t('workbench.writeup.empty') }}
      </div>

      <pre
        v-else
        class="m-0 max-h-[62vh] overflow-y-auto border p-4 whitespace-pre-wrap break-words font-mono text-xs leading-relaxed"
        style="
          background: var(--g-bg-canvas);
          border-color: var(--g-border);
          color: var(--g-text-primary);
          border-radius: var(--g-radius-sm);
        "
      >{{ writeup.content_md }}</pre>
    </div>
  </NModal>
</template>
