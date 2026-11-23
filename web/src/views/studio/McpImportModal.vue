<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { NButton, NCheckbox, NIcon, NInput, NModal, useMessage } from 'naive-ui'
import { FileUploadOutlined } from '@vicons/material'
import { mcpApi } from '@/api/mcp'
import { extractApiError } from '@/api/client'

const props = defineProps<{
  show: boolean
}>()

const emit = defineEmits<{
  (e: 'update:show', val: boolean): void
  (e: 'imported'): void
}>()

const { t } = useI18n()
const message = useMessage()

const SAMPLE_JSON = JSON.stringify(
  {
    mcpServers: {
      filesystem: {
        command: 'npx',
        args: ['-y', '@modelcontextprotocol/server-filesystem', '.'],
      },
      'remote-mcp': {
        url: 'http://127.0.0.1:8080/mcp',
        headers: { Authorization: 'Bearer YOUR_TOKEN' },
      },
    },
  },
  null,
  2,
)

const jsonText = ref(SAMPLE_JSON)
const overwriteExisting = ref(false)
const importing = ref(false)

watch(
  () => props.show,
  (visible) => {
    if (visible && !jsonText.value.trim()) {
      jsonText.value = SAMPLE_JSON
    }
  },
)

async function handleImport(): Promise<void> {
  let parsed: any
  try {
    parsed = JSON.parse(jsonText.value)
  } catch {
    message.error(t('models.form.invalidJson'))
    return
  }

  const serversObj =
    parsed && typeof parsed === 'object' && parsed.mcpServers && typeof parsed.mcpServers === 'object'
      ? parsed.mcpServers
      : parsed

  if (!serversObj || typeof serversObj !== 'object' || Array.isArray(serversObj)) {
    message.error(t('models.form.invalidJson'))
    return
  }

  importing.value = true
  try {
    const res = await mcpApi.importJson(serversObj, overwriteExisting.value)
    message.success(
      t('mcp.importModal.success', {
        created: res.created,
        updated: res.updated,
        skipped: res.skipped,
      }),
    )
    emit('imported')
    emit('update:show', false)
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    importing.value = false
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="t('mcp.importModal.title')"
    :style="{ width: '640px', maxWidth: '94vw' }"
    @update:show="emit('update:show', $event)"
  >
    <div class="flex flex-col gap-3">
      <p class="m-0 text-xs leading-relaxed" style="color: var(--g-text-secondary)">
        {{ t('mcp.importModal.hint') }}
      </p>

      <NInput
        v-model:value="jsonText"
        type="textarea"
        :rows="12"
        class="font-mono text-xs"
      />

      <div class="flex items-center justify-between gap-3">
        <NCheckbox v-model:checked="overwriteExisting">
          <span class="text-xs">{{ t('mcp.importModal.overwrite') }}</span>
        </NCheckbox>

        <div class="flex items-center gap-2">
          <NButton size="small" @click="emit('update:show', false)">{{ t('common.cancel') }}</NButton>
          <NButton size="small" type="primary" :loading="importing" @click="handleImport">
            <template #icon><NIcon :component="FileUploadOutlined" /></template>
            {{ t('mcp.importModal.importBtn') }}
          </NButton>
        </div>
      </div>
    </div>
  </NModal>
</template>
