<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NButton,
  NForm,
  NFormItem,
  NIcon,
  NInput,
  NInputNumber,
  NRadioButton,
  NRadioGroup,
  NSwitch,
  useMessage,
  type FormInst,
  type FormRules,
} from 'naive-ui'
import { SaveOutlined } from '@vicons/material'
import AppFormDrawer from '@/components/common/AppFormDrawer.vue'
import { mcpApi } from '@/api/mcp'
import { extractApiError } from '@/api/client'
import type { MCPServerItem, MCPTransport } from '@/types/mcp'

const props = defineProps<{
  show: boolean
  editingServer: MCPServerItem | null
}>()

const emit = defineEmits<{
  (e: 'update:show', val: boolean): void
  (e: 'saved'): void
}>()

const { t } = useI18n()
const message = useMessage()
const formRef = ref<FormInst | null>(null)
const saving = ref(false)

const form = reactive({
  name: '',
  description: '',
  transport: 'stdio' as MCPTransport,
  command: '',
  args_text: '',
  env_text: '',
  url: '',
  headers_text: '',
  timeout_sec: 30,
  is_enabled: true,
})

const isEdit = computed(() => Boolean(props.editingServer))

const rules = computed<FormRules>(() => ({
  name: [{ required: true, message: t('models.form.requiredField'), trigger: 'blur' }],
}))

function applyPreset(kind: 'filesystem' | 'fetch' | 'http'): void {
  if (kind === 'filesystem') {
    form.name = form.name || 'filesystem'
    form.description = 'Official MCP Filesystem server'
    form.transport = 'stdio'
    form.command = 'npx'
    form.args_text = '-y\n@modelcontextprotocol/server-filesystem\n.'
    form.url = ''
  } else if (kind === 'fetch') {
    form.name = form.name || 'fetch'
    form.description = 'Web fetch & HTML-to-Markdown MCP server'
    form.transport = 'stdio'
    form.command = 'uvx'
    form.args_text = 'mcp-server-fetch'
    form.url = ''
  } else {
    form.name = form.name || 'remote-mcp'
    form.description = 'Remote Streamable HTTP MCP endpoint'
    form.transport = 'streamable_http'
    form.url = 'http://127.0.0.1:8080/mcp'
    form.command = ''
    form.args_text = ''
  }
}

function resetForm(): void {
  if (props.editingServer) {
    const s = props.editingServer
    form.name = s.name
    form.description = s.description
    form.transport = s.transport
    form.command = s.command
    form.args_text = (s.args || []).join('\n')
    form.env_text = ''
    form.url = s.url
    form.headers_text = ''
    form.timeout_sec = s.timeout_sec || 30
    form.is_enabled = s.is_enabled
  } else {
    form.name = ''
    form.description = ''
    form.transport = 'stdio'
    form.command = ''
    form.args_text = ''
    form.env_text = ''
    form.url = ''
    form.headers_text = ''
    form.timeout_sec = 30
    form.is_enabled = true
  }
}

watch(
  () => [props.show, props.editingServer],
  ([visible]) => {
    if (visible) resetForm()
  },
  { immediate: true },
)

async function handleSave(): Promise<void> {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }

  let envObj: Record<string, string> | undefined
  if (form.env_text.trim()) {
    try {
      const parsed = JSON.parse(form.env_text)
      if (typeof parsed !== 'object' || Array.isArray(parsed) || !parsed) {
        throw new Error('not object')
      }
      envObj = parsed
    } catch {
      message.error(t('models.form.invalidJson'))
      return
    }
  }

  let headersObj: Record<string, string> | undefined
  if (form.headers_text.trim()) {
    try {
      const parsed = JSON.parse(form.headers_text)
      if (typeof parsed !== 'object' || Array.isArray(parsed) || !parsed) {
        throw new Error('not object')
      }
      headersObj = parsed
    } catch {
      message.error(t('models.form.invalidJson'))
      return
    }
  }

  const argsList = form.args_text
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean)

  saving.value = true
  try {
    if (props.editingServer) {
      await mcpApi.update(props.editingServer.id, {
        name: form.name.trim(),
        description: form.description.trim(),
        transport: form.transport,
        command: form.command.trim(),
        args: argsList,
        ...(envObj !== undefined ? { env: envObj } : {}),
        url: form.url.trim(),
        ...(headersObj !== undefined ? { headers: headersObj } : {}),
        timeout_sec: form.timeout_sec,
        is_enabled: form.is_enabled,
      })
    } else {
      await mcpApi.create({
        name: form.name.trim(),
        description: form.description.trim(),
        transport: form.transport,
        command: form.command.trim(),
        args: argsList,
        env: envObj || {},
        url: form.url.trim(),
        headers: headersObj || {},
        timeout_sec: form.timeout_sec,
        is_enabled: form.is_enabled,
      })
    }
    message.success(t('common.saveSuccess'))
    emit('saved')
    emit('update:show', false)
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <AppFormDrawer
    :show="show"
    :title="isEdit ? t('mcp.editServer') : t('mcp.addServer')"
    :width="560"
    @update:show="emit('update:show', $event)"
  >
    <!-- Quick Example Presets (Create mode) -->
    <div
      v-if="!isEdit"
      class="mb-4 flex flex-wrap items-center gap-1.5 border p-2.5"
      style="
        background: var(--g-bg-canvas);
        border-color: var(--g-border-subtle);
        border-radius: var(--g-radius-sm);
      "
    >
      <span class="mr-1 text-[11px]" style="color: var(--g-text-secondary)">
        {{ t('mcp.form.presetsTitle') }}:
      </span>
      <NButton size="tiny" secondary @click="applyPreset('filesystem')">Filesystem (Stdio)</NButton>
      <NButton size="tiny" secondary @click="applyPreset('fetch')">Fetch (uvx)</NButton>
      <NButton size="tiny" secondary @click="applyPreset('http')">Streamable HTTP</NButton>
    </div>

    <NForm ref="formRef" :model="form" :rules="rules" label-placement="top" size="medium">
      <div class="grid grid-cols-1 gap-x-4 sm:grid-cols-3">
        <NFormItem :label="t('mcp.form.name')" path="name" class="sm:col-span-2">
          <NInput v-model:value="form.name" class="font-mono" :placeholder="t('mcp.form.namePh')" />
        </NFormItem>
        <NFormItem :label="t('mcp.form.timeout')">
          <NInputNumber v-model:value="form.timeout_sec" :min="3" :max="180" class="w-full" />
        </NFormItem>
      </div>

      <NFormItem :label="t('mcp.form.description')">
        <NInput v-model:value="form.description" :placeholder="t('mcp.form.descriptionPh')" />
      </NFormItem>

      <NFormItem :label="t('mcp.form.transport')">
        <NRadioGroup v-model:value="form.transport" size="small">
          <NRadioButton value="stdio">Stdio</NRadioButton>
          <NRadioButton value="streamable_http">Streamable HTTP</NRadioButton>
          <NRadioButton value="sse">SSE (Legacy)</NRadioButton>
        </NRadioGroup>
      </NFormItem>

      <!-- Stdio Transport Fields -->
      <div
        v-if="form.transport === 'stdio'"
        class="mb-4 flex flex-col border p-3.5"
        style="
          background: var(--g-bg-canvas);
          border-color: var(--g-border-subtle);
          border-radius: var(--g-radius-sm);
        "
      >
        <NFormItem :label="t('mcp.form.command')">
          <NInput
            v-model:value="form.command"
            class="font-mono"
            :placeholder="t('mcp.form.commandPh')"
          />
        </NFormItem>
        <NFormItem :label="t('mcp.form.args')">
          <NInput
            v-model:value="form.args_text"
            type="textarea"
            :rows="3"
            class="font-mono text-xs"
            :placeholder="t('mcp.form.argsPh')"
          />
        </NFormItem>
        <NFormItem :label="t('mcp.form.env')" class="mb-0">
          <div class="flex w-full flex-col gap-1">
            <NInput
              v-model:value="form.env_text"
              type="textarea"
              :rows="3"
              class="font-mono text-xs"
              placeholder='{"GITHUB_TOKEN": "ghp_..."}'
            />
            <span
              v-if="isEdit && editingServer?.has_env"
              class="font-mono text-[11px]"
              style="color: var(--g-text-tertiary)"
            >
              {{ t('mcp.form.envKeepHint') }} · {{ Object.keys(editingServer.env_masked).join(', ') }}
            </span>
          </div>
        </NFormItem>
      </div>

      <!-- Streamable HTTP / SSE Transport Fields -->
      <div
        v-else
        class="mb-4 flex flex-col border p-3.5"
        style="
          background: var(--g-bg-canvas);
          border-color: var(--g-border-subtle);
          border-radius: var(--g-radius-sm);
        "
      >
        <NFormItem :label="t('mcp.form.url')">
          <NInput v-model:value="form.url" class="font-mono" :placeholder="t('mcp.form.urlPh')" />
        </NFormItem>
        <NFormItem :label="t('mcp.form.headers')" class="mb-0">
          <div class="flex w-full flex-col gap-1">
            <NInput
              v-model:value="form.headers_text"
              type="textarea"
              :rows="3"
              class="font-mono text-xs"
              placeholder='{"Authorization": "Bearer ..."}'
            />
            <span
              v-if="isEdit && editingServer?.has_headers"
              class="font-mono text-[11px]"
              style="color: var(--g-text-tertiary)"
            >
              {{ t('mcp.form.headersKeepHint') }} ·
              {{ Object.keys(editingServer.headers_masked).join(', ') }}
            </span>
          </div>
        </NFormItem>
      </div>

      <div class="flex items-center gap-2 pt-1">
        <NSwitch v-model:value="form.is_enabled" />
        <span class="text-xs" style="color: var(--g-text-primary)">
          {{ t('mcp.form.isEnabled') }}
        </span>
      </div>
    </NForm>

    <template #footer>
      <div />
      <div class="flex items-center gap-2">
        <NButton @click="emit('update:show', false)">{{ t('common.cancel') }}</NButton>
        <NButton type="primary" :loading="saving" @click="handleSave">
          <template #icon><NIcon :component="SaveOutlined" /></template>
          {{ t('common.save') }}
        </NButton>
      </div>
    </template>
  </AppFormDrawer>
</template>
