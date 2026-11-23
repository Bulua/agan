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
  NSelect,
  NSwitch,
  useMessage,
  type FormInst,
  type FormRules,
} from 'naive-ui'
import { SaveOutlined } from '@vicons/material'
import AppFormDrawer from '@/components/common/AppFormDrawer.vue'
import { toolsApi } from '@/api/tools'
import { extractApiError } from '@/api/client'
import type { ToolItem, ToolType } from '@/types/capability'

const props = defineProps<{
  show: boolean
  editingTool: ToolItem | null
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
  display_name: '',
  description: '',
  tool_type: 'custom_http' as ToolType,
  category: 'custom',
  schema_text: JSON.stringify(
    {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Input parameter' },
      },
      required: ['query'],
    },
    null,
    2,
  ),
  http_method: 'GET',
  http_url: '',
  http_headers_text: '{}',
  http_body: '',
  python_code: 'print("Received args:", ARGS)',
  file_root_dir: '',
  file_max_size_kb: 512,
  file_allowed_extensions: '',
  file_allow_overwrite: true,
  web_provider: 'auto',
  web_base_url: '',
  web_api_key: '',
  web_api_key_masked: '',
  web_has_api_key: false,
  web_max_results: 5,
  web_max_chars: 12000,
  web_timeout: 15,
  is_enabled: true,
  requires_sandbox: false,
})

const FILE_TOOL_NAMES = new Set(['read_file', 'write_file', 'edit_file', 'list_files', 'grep_search'])
const isEdit = computed(() => Boolean(props.editingTool))
const isBuiltin = computed(() => Boolean(props.editingTool?.is_builtin))
const isBuiltinFileTool = computed(
  () => isBuiltin.value && FILE_TOOL_NAMES.has(props.editingTool?.name || ''),
)
const showFileSizeField = computed(
  () => isBuiltinFileTool.value && props.editingTool?.name !== 'list_files',
)
const showFileExtensionsField = computed(
  () =>
    isBuiltinFileTool.value &&
    ['read_file', 'write_file', 'edit_file'].includes(props.editingTool?.name || ''),
)
const isBuiltinWebSearch = computed(
  () => isBuiltin.value && props.editingTool?.name === 'web_search',
)
const isBuiltinWebFetch = computed(
  () => isBuiltin.value && props.editingTool?.name === 'web_fetch',
)
const hasBuiltinConfig = computed(
  () => isBuiltinFileTool.value || isBuiltinWebSearch.value || isBuiltinWebFetch.value,
)

const webProviderOptions = computed(() => [
  { label: t('tools.form.webProviderAuto'), value: 'auto' },
  { label: 'DuckDuckGo (Keyless)', value: 'duckduckgo' },
  { label: 'Tavily Search API', value: 'tavily' },
  { label: 'Brave Search API', value: 'brave' },
  { label: 'SearXNG (Self-hosted)', value: 'searxng' },
])

const toolTypeOptions = computed(() => [
  { label: t('tools.tabHttp'), value: 'custom_http' },
  { label: t('tools.tabPython'), value: 'custom_python' },
])

const methodOptions = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'].map((m) => ({
  label: m,
  value: m,
}))

const rules = computed<FormRules>(() => ({
  name: [{ required: true, message: t('models.form.requiredField'), trigger: 'blur' }],
  display_name: [{ required: true, message: t('models.form.requiredField'), trigger: 'blur' }],
}))

function resetForm(): void {
  if (props.editingTool) {
    const item = props.editingTool
    form.name = item.name
    form.display_name = item.display_name
    form.description = item.description
    form.tool_type = item.tool_type
    form.category = item.category
    form.schema_text = JSON.stringify(item.parameters_schema || {}, null, 2)
    form.http_method = String(item.implementation_config?.method || 'GET')
    form.http_url = String(item.implementation_config?.url || '')
    form.http_headers_text = JSON.stringify(item.implementation_config?.headers || {}, null, 2)
    form.http_body = String(item.implementation_config?.body_template || '')
    form.python_code = String(item.implementation_config?.code || 'print(ARGS)')
    form.file_root_dir = String(item.implementation_config?.root_dir || '')
    form.file_max_size_kb = Number(item.implementation_config?.max_file_size_kb ?? 512)
    const exts = item.implementation_config?.allowed_extensions
    form.file_allowed_extensions = Array.isArray(exts) ? exts.join(', ') : String(exts || '')
    form.file_allow_overwrite =
      item.implementation_config?.allow_overwrite !== undefined
        ? Boolean(item.implementation_config.allow_overwrite)
        : true
    form.web_provider = String(item.implementation_config?.provider || 'auto')
    form.web_base_url = String(item.implementation_config?.base_url || '')
    form.web_api_key = ''
    form.web_has_api_key = Boolean(item.implementation_config?.has_api_key)
    form.web_api_key_masked = String(item.implementation_config?.api_key_masked || '')
    form.web_max_results = Number(item.implementation_config?.default_max_results ?? 5)
    form.web_max_chars = Number(item.implementation_config?.default_max_chars ?? 12000)
    form.web_timeout = Number(
      item.implementation_config?.timeout ?? (item.name === 'web_fetch' ? 20 : 15),
    )
    form.is_enabled = item.is_enabled
    form.requires_sandbox = item.requires_sandbox
  } else {
    form.name = ''
    form.display_name = ''
    form.description = ''
    form.tool_type = 'custom_http'
    form.category = 'custom'
    form.schema_text = JSON.stringify(
      {
        type: 'object',
        properties: {
          query: { type: 'string', description: 'Input parameter' },
        },
        required: ['query'],
      },
      null,
      2,
    )
    form.http_method = 'GET'
    form.http_url = ''
    form.http_headers_text = '{}'
    form.http_body = ''
    form.python_code = 'print("Received args:", ARGS)'
    form.file_root_dir = ''
    form.file_max_size_kb = 512
    form.file_allowed_extensions = ''
    form.file_allow_overwrite = true
    form.is_enabled = true
    form.requires_sandbox = false
  }
}

watch(
  () => [props.show, props.editingTool],
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

  let schemaObj: Record<string, any> = {}
  let implCfg: Record<string, any> = {}

  if (isBuiltinFileTool.value) {
    const parsedExts = form.file_allowed_extensions
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    const toolName = props.editingTool?.name || ''
    if (toolName === 'list_files') {
      implCfg = { root_dir: form.file_root_dir.trim() }
    } else if (toolName === 'grep_search') {
      implCfg = {
        root_dir: form.file_root_dir.trim(),
        max_file_size_kb: Math.max(1, Math.min(Number(form.file_max_size_kb || 512), 10240)),
      }
    } else {
      implCfg = {
        root_dir: form.file_root_dir.trim(),
        max_file_size_kb: Math.max(1, Math.min(Number(form.file_max_size_kb || 512), 10240)),
        allowed_extensions: parsedExts,
        ...(toolName === 'write_file' ? { allow_overwrite: form.file_allow_overwrite } : {}),
      }
    }
  } else if (isBuiltinWebSearch.value) {
    implCfg = {
      provider: form.web_provider,
      base_url: form.web_base_url.trim(),
      default_max_results: Math.max(1, Math.min(Number(form.web_max_results || 5), 10)),
      timeout: Math.max(3, Math.min(Number(form.web_timeout || 15), 60)),
      ...(form.web_api_key.trim() ? { api_key: form.web_api_key.trim() } : {}),
    }
  } else if (isBuiltinWebFetch.value) {
    implCfg = {
      default_max_chars: Math.max(1000, Math.min(Number(form.web_max_chars || 12000), 40000)),
      timeout: Math.max(3, Math.min(Number(form.web_timeout || 20), 60)),
    }
  } else if (!isBuiltin.value) {
    try {
      schemaObj = JSON.parse(form.schema_text || '{}')
    } catch {
      message.error(t('models.form.invalidJson'))
      return
    }
    if (form.tool_type === 'custom_http') {
      let headersObj: Record<string, string> = {}
      try {
        headersObj = JSON.parse(form.http_headers_text || '{}')
      } catch {
        message.error(t('models.form.invalidJson'))
        return
      }
      implCfg = {
        method: form.http_method,
        url: form.http_url.trim(),
        headers: headersObj,
        body_template: form.http_body || undefined,
      }
    } else if (form.tool_type === 'custom_python') {
      implCfg = { code: form.python_code, timeout: 30 }
    }
  }

  saving.value = true
  try {
    if (props.editingTool) {
      await toolsApi.update(props.editingTool.id, {
        display_name: form.display_name.trim(),
        description: form.description.trim(),
        category: form.category.trim() || 'custom',
        is_enabled: form.is_enabled,
        ...(hasBuiltinConfig.value
          ? { implementation_config: implCfg }
          : isBuiltin.value
            ? {}
            : {
                parameters_schema: schemaObj,
                implementation_config: implCfg,
                requires_sandbox: form.tool_type === 'custom_python' || form.requires_sandbox,
              }),
      })
    } else {
      await toolsApi.create({
        name: form.name.trim(),
        display_name: form.display_name.trim(),
        description: form.description.trim(),
        tool_type: form.tool_type,
        category: form.category.trim() || 'custom',
        parameters_schema: schemaObj,
        implementation_config: implCfg,
        is_enabled: form.is_enabled,
        requires_sandbox: form.tool_type === 'custom_python' || form.requires_sandbox,
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
    :title="isEdit ? t('tools.editTool') : t('tools.addTool')"
    :width="560"
    @update:show="emit('update:show', $event)"
  >
    <NForm ref="formRef" :model="form" :rules="rules" label-placement="top" size="medium">
      <div class="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
        <NFormItem :label="t('tools.form.name')" path="name">
          <NInput
            v-model:value="form.name"
            :disabled="isEdit"
            class="font-mono"
            :placeholder="t('tools.form.namePh')"
          />
        </NFormItem>
        <NFormItem :label="t('tools.form.displayName')" path="display_name">
          <NInput
            v-model:value="form.display_name"
            :placeholder="t('tools.form.displayNamePh')"
          />
        </NFormItem>
      </div>

      <div class="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
        <NFormItem v-if="!isBuiltin" :label="t('tools.form.toolType')">
          <NSelect
            v-model:value="form.tool_type"
            :disabled="isEdit"
            :options="toolTypeOptions"
          />
        </NFormItem>
        <NFormItem :label="t('tools.form.category')">
          <NInput v-model:value="form.category" class="font-mono" placeholder="network / crypto / custom" />
        </NFormItem>
      </div>

      <NFormItem :label="t('tools.form.description')">
        <NInput
          v-model:value="form.description"
          type="textarea"
          :rows="3"
          :placeholder="t('tools.form.descriptionPh')"
        />
      </NFormItem>

      <!-- Built-in File Access & Security Boundary Config -->
      <div
        v-if="isBuiltinFileTool"
        class="mb-4 flex flex-col border p-3.5"
        style="
          background: var(--g-bg-canvas);
          border-color: var(--g-border-subtle);
          border-radius: var(--g-radius-sm);
        "
      >
        <div class="mb-1 text-xs font-semibold" style="color: var(--g-text-primary)">
          {{ t('tools.form.filePolicyTitle') }}
        </div>
        <div class="mb-3 text-[11px] leading-relaxed" style="color: var(--g-text-secondary)">
          {{ t('tools.form.filePolicyHint') }}
        </div>

        <NFormItem :label="t('tools.form.rootDir')">
          <NInput
            v-model:value="form.file_root_dir"
            class="font-mono text-xs"
            clearable
            :placeholder="t('tools.form.rootDirPh')"
          />
        </NFormItem>

        <div
          v-if="showFileSizeField || showFileExtensionsField"
          class="grid grid-cols-1 gap-x-3 sm:grid-cols-2"
        >
          <NFormItem v-if="showFileSizeField" :label="t('tools.form.maxFileSizeKb')">
            <NInputNumber
              v-model:value="form.file_max_size_kb"
              :min="1"
              :max="10240"
              class="w-full"
            />
          </NFormItem>
          <NFormItem v-if="showFileExtensionsField" :label="t('tools.form.allowedExtensions')">
            <NInput
              v-model:value="form.file_allowed_extensions"
              class="font-mono text-xs"
              clearable
              :placeholder="t('tools.form.allowedExtensionsPh')"
            />
          </NFormItem>
        </div>

        <div
          v-if="editingTool?.name === 'write_file'"
          class="flex items-center gap-2 pt-0.5"
        >
          <NSwitch v-model:value="form.file_allow_overwrite" size="small" />
          <span class="text-xs" style="color: var(--g-text-secondary)">
            {{ t('tools.form.allowOverwrite') }}
          </span>
        </div>
      </div>

      <!-- Built-in Web Search Config -->
      <div
        v-if="isBuiltinWebSearch"
        class="mb-4 flex flex-col border p-3.5"
        style="
          background: var(--g-bg-canvas);
          border-color: var(--g-border-subtle);
          border-radius: var(--g-radius-sm);
        "
      >
        <div class="mb-1 text-xs font-semibold" style="color: var(--g-text-primary)">
          {{ t('tools.form.webSearchTitle') }}
        </div>
        <div class="mb-3 text-[11px] leading-relaxed" style="color: var(--g-text-secondary)">
          {{ t('tools.form.webSearchHint') }}
        </div>

        <div class="grid grid-cols-1 gap-x-3 sm:grid-cols-2">
          <NFormItem :label="t('tools.form.webProvider')">
            <NSelect v-model:value="form.web_provider" :options="webProviderOptions" />
          </NFormItem>
          <NFormItem :label="t('tools.form.webApiKey')">
            <NInput
              v-model:value="form.web_api_key"
              type="password"
              show-password-on="click"
              class="font-mono text-xs"
              clearable
              :placeholder="
                form.web_has_api_key
                  ? t('tools.form.webApiKeyKeepPh', { masked: form.web_api_key_masked })
                  : t('tools.form.webApiKeyPh')
              "
            />
          </NFormItem>
        </div>

        <NFormItem :label="t('tools.form.webBaseUrl')">
          <NInput
            v-model:value="form.web_base_url"
            class="font-mono text-xs"
            clearable
            :placeholder="t('tools.form.webBaseUrlPh')"
          />
        </NFormItem>

        <div class="grid grid-cols-1 gap-x-3 sm:grid-cols-2">
          <NFormItem :label="t('tools.form.webMaxResults')" class="mb-0">
            <NInputNumber v-model:value="form.web_max_results" :min="1" :max="10" class="w-full" />
          </NFormItem>
          <NFormItem :label="t('tools.form.webTimeout')" class="mb-0">
            <NInputNumber v-model:value="form.web_timeout" :min="3" :max="60" class="w-full" />
          </NFormItem>
        </div>
      </div>

      <!-- Built-in Web Fetch Config -->
      <div
        v-if="isBuiltinWebFetch"
        class="mb-4 flex flex-col border p-3.5"
        style="
          background: var(--g-bg-canvas);
          border-color: var(--g-border-subtle);
          border-radius: var(--g-radius-sm);
        "
      >
        <div class="mb-1 text-xs font-semibold" style="color: var(--g-text-primary)">
          {{ t('tools.form.webFetchTitle') }}
        </div>
        <div class="mb-3 text-[11px] leading-relaxed" style="color: var(--g-text-secondary)">
          {{ t('tools.form.webFetchHint') }}
        </div>

        <div class="grid grid-cols-1 gap-x-3 sm:grid-cols-2">
          <NFormItem :label="t('tools.form.webMaxChars')" class="mb-0">
            <NInputNumber
              v-model:value="form.web_max_chars"
              :min="1000"
              :max="40000"
              :step="1000"
              class="w-full"
            />
          </NFormItem>
          <NFormItem :label="t('tools.form.webTimeout')" class="mb-0">
            <NInputNumber v-model:value="form.web_timeout" :min="3" :max="60" class="w-full" />
          </NFormItem>
        </div>
      </div>

      <template v-if="!isBuiltin">
        <!-- Custom HTTP Config -->
        <div
          v-if="form.tool_type === 'custom_http'"
          class="mb-4 flex flex-col border p-3.5"
          style="
            background: var(--g-bg-canvas);
            border-color: var(--g-border-subtle);
            border-radius: var(--g-radius-sm);
          "
        >
          <div class="grid grid-cols-1 gap-x-3 sm:grid-cols-4">
            <NFormItem :label="t('tools.form.httpMethod')">
              <NSelect v-model:value="form.http_method" :options="methodOptions" />
            </NFormItem>
            <NFormItem :label="t('tools.form.httpUrl')" class="sm:col-span-3">
              <NInput
                v-model:value="form.http_url"
                class="font-mono"
                placeholder="https://api.example.com/search?q={{query}}"
              />
            </NFormItem>
          </div>
          <NFormItem :label="t('tools.form.httpHeaders')">
            <NInput
              v-model:value="form.http_headers_text"
              type="textarea"
              :rows="2"
              class="font-mono text-xs"
            />
          </NFormItem>
          <NFormItem :label="t('tools.form.httpBody')" class="mb-0">
            <NInput
              v-model:value="form.http_body"
              type="textarea"
              :rows="2"
              class="font-mono text-xs"
              placeholder='{"query": "{{query}}"}'
            />
          </NFormItem>
        </div>

        <!-- Custom Python Config -->
        <NFormItem v-else-if="form.tool_type === 'custom_python'" :label="t('tools.form.pythonCode')">
          <NInput
            v-model:value="form.python_code"
            type="textarea"
            :rows="6"
            class="font-mono text-xs"
          />
        </NFormItem>

        <NFormItem :label="t('tools.form.schema')">
          <NInput
            v-model:value="form.schema_text"
            type="textarea"
            :rows="6"
            class="font-mono text-xs"
          />
        </NFormItem>
      </template>

      <div class="flex items-center gap-2 pt-1">
        <NSwitch v-model:value="form.is_enabled" />
        <span class="text-xs" style="color: var(--g-text-primary)">
          {{ t('tools.form.isEnabled') }}
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
