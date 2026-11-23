<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NForm,
  NFormItem,
  NIcon,
  NInput,
  NInputNumber,
  NSelect,
  NSlider,
  NSwitch,
  useMessage,
  type FormInst,
  type FormRules,
} from 'naive-ui'
import { BoltOutlined, SaveOutlined } from '@vicons/material'
import AppFormDrawer from '@/components/common/AppFormDrawer.vue'
import AppSecretInput from '@/components/common/AppSecretInput.vue'
import { modelsApi } from '@/api/models'
import { extractApiError } from '@/api/client'
import {
  MODEL_PROVIDERS,
  findProviderById,
  type ProviderGroupKey,
} from '@/constants/modelPresets'
import type { LLMModel, LLMModelCreatePayload, LLMModelTestResponse, ModelType } from '@/types/llm'
import type { SelectGroupOption } from 'naive-ui'

const props = defineProps<{
  show: boolean
  editingModel: LLMModel | null
  defaultType?: ModelType
}>()

const emit = defineEmits<{
  (e: 'update:show', val: boolean): void
  (e: 'saved'): void
}>()

const { t } = useI18n()
const message = useMessage()
const formRef = ref<FormInst | null>(null)
const saving = ref(false)
const testing = ref(false)
const inlineTestResult = ref<LLMModelTestResponse | null>(null)

const providerSelectOptions = computed<SelectGroupOption[]>(() => {
  const groups: ProviderGroupKey[] = ['global', 'china', 'gateway', 'local']
  return groups.map((groupKey) => ({
    type: 'group',
    label: t(`models.form.presetGroups.${groupKey}`),
    key: groupKey,
    children: MODEL_PROVIDERS.filter((p) => p.group === groupKey).map((p) => ({
      label: p.label,
      value: p.id,
    })),
  }))
})

const modelIdPlaceholder = computed(() => {
  const provider = findProviderById(form.provider)
  const example = provider?.examples[form.model_type]
  return example ? `e.g. ${example}` : t('models.form.modelIdPlaceholder')
})

const form = reactive({
  name: '',
  model_type: 'chat' as ModelType,
  provider: 'openai_compat',
  base_url: 'https://api.openai.com/v1',
  api_key: '',
  model_id: '',
  description: '',
  context_window: 128000,
  max_output_tokens: 4096,
  temperature: 0.2,
  input_price_per_1m: 0,
  output_price_per_1m: 0,
  extra_params_text: '{}',
  is_active: true,
  is_default: false,
})

const isEdit = computed(() => Boolean(props.editingModel))
const drawerTitle = computed(() =>
  isEdit.value ? t('models.editModel') : t('models.addModel'),
)

const modelTypeOptions = computed(() => [
  { label: t('models.tabChat'), value: 'chat' },
  { label: t('models.tabEmbedding'), value: 'embedding' },
  { label: t('models.tabRerank'), value: 'rerank' },
])

const rules = computed<FormRules>(() => ({
  name: [{ required: true, message: t('models.form.requiredField'), trigger: 'blur' }],
  base_url: [
    { required: true, message: t('models.form.requiredField'), trigger: 'blur' },
    {
      validator: (_rule, value: string) => {
        if (!value || (!value.startsWith('http://') && !value.startsWith('https://'))) {
          return new Error('URL must start with http:// or https://')
        }
        return true
      },
      trigger: 'blur',
    },
  ],
  model_id: [{ required: true, message: t('models.form.requiredField'), trigger: 'blur' }],
}))

function resetForm(): void {
  inlineTestResult.value = null
  if (props.editingModel) {
    const m = props.editingModel
    form.name = m.name
    form.model_type = m.model_type
    form.provider = m.provider
    form.base_url = m.base_url
    form.api_key = ''
    form.model_id = m.model_id
    form.description = m.description
    form.context_window = m.context_window
    form.max_output_tokens = m.max_output_tokens
    form.temperature = m.temperature
    form.input_price_per_1m = m.input_price_per_1m
    form.output_price_per_1m = m.output_price_per_1m
    form.extra_params_text = JSON.stringify(m.extra_params || {}, null, 2)
    form.is_active = m.is_active
    form.is_default = m.is_default
  } else {
    form.name = ''
    form.model_type = props.defaultType || 'chat'
    form.provider = 'openai'
    form.base_url = 'https://api.openai.com/v1'
    form.api_key = ''
    form.model_id = ''
    form.description = ''
    form.context_window = 128000
    form.max_output_tokens = 4096
    form.temperature = 0.2
    form.input_price_per_1m = 0
    form.output_price_per_1m = 0
    form.extra_params_text = '{}'
    form.is_active = true
    form.is_default = false
  }
}

watch(
  () => [props.show, props.editingModel],
  ([visible]) => {
    if (visible) resetForm()
  },
  { immediate: true },
)

function handleProviderChange(providerId: string): void {
  form.provider = providerId
  const provider = findProviderById(providerId)
  if (provider && provider.base_url) {
    form.base_url = provider.base_url
  }
}

function parseExtraParams(): Record<string, unknown> | null {
  const trimmed = form.extra_params_text.trim()
  if (!trimmed) return {}
  try {
    const parsed = JSON.parse(trimmed)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed
    }
    message.error(t('models.form.invalidJson'))
    return null
  } catch {
    message.error(t('models.form.invalidJson'))
    return null
  }
}

function buildPayload(): LLMModelCreatePayload | null {
  const extra = parseExtraParams()
  if (extra === null) return null
  return {
    name: form.name.trim(),
    model_type: form.model_type,
    provider: form.provider.trim() || 'openai_compat',
    base_url: form.base_url.trim(),
    api_key: form.api_key,
    model_id: form.model_id.trim(),
    description: form.description.trim(),
    context_window: form.context_window,
    max_output_tokens: form.max_output_tokens,
    temperature: form.temperature,
    input_price_per_1m: form.input_price_per_1m,
    output_price_per_1m: form.output_price_per_1m,
    extra_params: extra,
    is_active: form.is_active,
    is_default: form.is_default,
  }
}

async function handleTestInline(): Promise<void> {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }
  const payload = buildPayload()
  if (!payload) return

  testing.value = true
  inlineTestResult.value = null
  try {
    if (props.editingModel && !payload.api_key && props.editingModel.has_api_key) {
      // Reuse stored encrypted key when editing without retyping key
      inlineTestResult.value = await modelsApi.testSaved(props.editingModel.id)
    } else {
      inlineTestResult.value = await modelsApi.testUnsaved(payload)
    }
  } catch (err) {
    inlineTestResult.value = {
      ok: false,
      model_type: payload.model_type,
      latency_ms: 0,
      detail: extractApiError(err, t('common.operationFailed')),
      preview: {},
      usage: {},
    }
  } finally {
    testing.value = false
  }
}

async function handleSave(): Promise<void> {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }
  const payload = buildPayload()
  if (!payload) return

  saving.value = true
  try {
    if (props.editingModel) {
      await modelsApi.update(props.editingModel.id, payload)
    } else {
      await modelsApi.create(payload)
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
    :title="drawerTitle"
    :width="560"
    @update:show="emit('update:show', $event)"
  >
    <NForm ref="formRef" :model="form" :rules="rules" label-placement="top" size="medium">
      <!-- Provider selection (decoupled from Model ID) -->
      <div class="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
        <NFormItem :label="t('models.form.provider')" path="provider">
          <NSelect
            :value="form.provider"
            filterable
            tag
            :placeholder="t('models.form.providerPlaceholder')"
            :options="providerSelectOptions"
            @update:value="handleProviderChange"
          />
        </NFormItem>
        <NFormItem :label="t('models.form.modelType')" path="model_type">
          <NSelect v-model:value="form.model_type" :options="modelTypeOptions" />
        </NFormItem>
      </div>

      <div class="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
        <NFormItem :label="t('models.form.name')" path="name">
          <NInput v-model:value="form.name" :placeholder="t('models.form.namePlaceholder')" />
        </NFormItem>
        <NFormItem :label="t('models.form.modelId')" path="model_id">
          <NInput
            v-model:value="form.model_id"
            class="font-mono"
            :placeholder="modelIdPlaceholder"
          />
        </NFormItem>
      </div>

      <NFormItem :label="t('models.form.baseUrl')" path="base_url">
        <NInput
          v-model:value="form.base_url"
          class="font-mono"
          :placeholder="t('models.form.baseUrlPlaceholder')"
        />
      </NFormItem>

      <NFormItem :label="t('models.form.apiKey')" path="api_key">
        <AppSecretInput
          v-model="form.api_key"
          :placeholder="
            isEdit && editingModel?.has_api_key
              ? t('models.form.apiKeyKeepHint')
              : t('models.form.apiKeyPlaceholder')
          "
          :masked-hint="
            isEdit && editingModel?.has_api_key
              ? `Current stored key: ${editingModel.api_key_masked}`
              : ''
          "
        />
      </NFormItem>

      <!-- Chat-specific inference parameters -->
      <div
        v-if="form.model_type === 'chat'"
        class="mb-4 border p-3.5"
        style="
          background: var(--g-bg-canvas);
          border-color: var(--g-border-subtle);
          border-radius: var(--g-radius-sm);
        "
      >
        <div class="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
          <NFormItem :label="t('models.form.contextWindow')">
            <NInputNumber
              v-model:value="form.context_window"
              :min="256"
              :max="2000000"
              :step="4096"
              class="w-full"
            />
          </NFormItem>
          <NFormItem :label="t('models.form.maxOutputTokens')">
            <NInputNumber
              v-model:value="form.max_output_tokens"
              :min="64"
              :max="128000"
              :step="512"
              class="w-full"
            />
          </NFormItem>
        </div>
        <NFormItem :label="`${t('models.form.temperature')}: ${form.temperature.toFixed(2)}`" class="mb-0">
          <NSlider v-model:value="form.temperature" :min="0" :max="2" :step="0.05" />
        </NFormItem>
      </div>

      <!-- Embedding context window -->
      <NFormItem v-else-if="form.model_type === 'embedding'" :label="t('models.form.contextWindow')">
        <NInputNumber
          v-model:value="form.context_window"
          :min="256"
          :max="100000"
          :step="512"
          class="w-full"
        />
      </NFormItem>

      <!-- Token Pricing -->
      <div class="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
        <NFormItem :label="t('models.form.inputPrice')">
          <NInputNumber
            v-model:value="form.input_price_per_1m"
            :min="0"
            :step="0.05"
            :precision="4"
            class="w-full"
          />
        </NFormItem>
        <NFormItem v-if="form.model_type === 'chat'" :label="t('models.form.outputPrice')">
          <NInputNumber
            v-model:value="form.output_price_per_1m"
            :min="0"
            :step="0.05"
            :precision="4"
            class="w-full"
          />
        </NFormItem>
      </div>

      <NFormItem :label="t('models.form.description')">
        <NInput
          v-model:value="form.description"
          type="textarea"
          :rows="2"
          :placeholder="t('models.form.descriptionPlaceholder')"
        />
      </NFormItem>

      <NFormItem :label="t('models.form.extraParams')">
        <div class="w-full">
          <NInput
            v-model:value="form.extra_params_text"
            type="textarea"
            :rows="3"
            class="font-mono text-xs"
            placeholder="{}"
          />
          <p class="mt-1 mb-0 text-[11px]" style="color: var(--g-text-tertiary)">
            {{ t('models.form.extraParamsHint') }}
          </p>
        </div>
      </NFormItem>

      <div class="flex flex-wrap items-center gap-6 pt-1">
        <div class="flex items-center gap-2">
          <NSwitch v-model:value="form.is_active" />
          <span class="text-xs" style="color: var(--g-text-primary)">
            {{ t('models.form.isActive') }}
          </span>
        </div>
        <div class="flex items-center gap-2">
          <NSwitch v-model:value="form.is_default" />
          <span class="text-xs" style="color: var(--g-text-primary)">
            {{ t('models.form.isDefault') }}
          </span>
        </div>
      </div>

      <!-- Inline Live Connection Test Result -->
      <Transition name="g-fade">
        <div v-if="inlineTestResult" class="mt-4">
          <NAlert
            :type="inlineTestResult.ok ? 'success' : 'error'"
            :title="
              inlineTestResult.ok
                ? `${t('models.testModal.resultSuccess')} (${inlineTestResult.latency_ms}ms)`
                : t('models.testModal.resultFail')
            "
            closable
            @close="inlineTestResult = null"
          >
            <div class="font-mono text-xs break-all">
              {{ inlineTestResult.detail }}
            </div>
            <div
              v-if="inlineTestResult.ok && inlineTestResult.preview?.reply"
              class="mt-1.5 text-xs"
            >
              {{ inlineTestResult.preview.reply }}
            </div>
          </NAlert>
        </div>
      </Transition>
    </NForm>

    <template #footer>
      <NButton secondary :loading="testing" @click="handleTestInline">
        <template #icon>
          <NIcon :component="BoltOutlined" />
        </template>
        {{ testing ? t('models.testing') : t('models.testBeforeSave') }}
      </NButton>

      <div class="flex items-center gap-2">
        <NButton @click="emit('update:show', false)">{{ t('common.cancel') }}</NButton>
        <NButton type="primary" :loading="saving" @click="handleSave">
          <template #icon>
            <NIcon :component="SaveOutlined" />
          </template>
          {{ t('common.save') }}
        </NButton>
      </div>
    </template>
  </AppFormDrawer>
</template>
