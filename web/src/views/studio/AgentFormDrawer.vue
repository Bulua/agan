<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NCheckbox,
  NCheckboxGroup,
  NForm,
  NFormItem,
  NIcon,
  NInput,
  NInputNumber,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSwitch,
  useMessage,
  type FormInst,
  type FormRules,
} from 'naive-ui'
import { AutoAwesomeOutlined, SaveOutlined } from '@vicons/material'
import AppFormDrawer from '@/components/common/AppFormDrawer.vue'
import { agentsApi } from '@/api/agents'
import { modelsApi } from '@/api/models'
import { toolsApi } from '@/api/tools'
import { extractApiError } from '@/api/client'
import type { LLMModel } from '@/types/llm'
import type { ToolItem } from '@/types/capability'
import type { AgentItem, AgentLoopEngine } from '@/types/agent'

const props = defineProps<{
  show: boolean
  editingAgent: AgentItem | null
}>()

const emit = defineEmits<{
  (e: 'update:show', val: boolean): void
  (e: 'saved'): void
}>()

const router = useRouter()
const { t } = useI18n()
const message = useMessage()
const formRef = ref<FormInst | null>(null)
const saving = ref(false)
const chatModels = ref<LLMModel[]>([])
const availableTools = ref<ToolItem[]>([])

const DEFAULT_PROMPT_TEMPLATE =
  'You are AGAN, a senior CTF security researcher and AI assistant. Think step by step, use your bound tools cleanly when investigation is needed, and present clear, reproducible technical answers.'

const form = reactive({
  name: '',
  description: '',
  chat_model_id: '',
  loop_engine: 'react' as AgentLoopEngine,
  system_prompt: DEFAULT_PROMPT_TEMPLATE,
  sandbox_mode: 'simulated' as 'simulated' | 'docker',
  sandbox_image: 'agan-ctf-sandbox:latest',
  tool_ids: [] as string[],
  max_steps: 10,
  is_active: true,
  is_default: false,
})

const isEdit = computed(() => Boolean(props.editingAgent))

const loopOptions = computed(() =>
  (['direct_chat', 'react', 'plan_execute', 'reflexion'] as AgentLoopEngine[]).map((l) => ({
    label: t(`agents.loops.${l}`),
    value: l,
  })),
)

const modelOptions = computed(() =>
  chatModels.value.map((m) => ({
    label: `${m.name} (${m.model_id})${m.is_default ? ' ★' : ''}`,
    value: m.id,
  })),
)

const rules = computed<FormRules>(() => ({
  name: [{ required: true, message: t('models.form.requiredField'), trigger: 'blur' }],
  chat_model_id: [{ required: true, message: t('models.form.requiredField'), trigger: 'change' }],
}))

async function loadDependencies(): Promise<void> {
  try {
    const [mRes, tRes] = await Promise.all([
      modelsApi.list({ model_type: 'chat', is_active: true, page: 1, page_size: 100 }),
      toolsApi.list({ page: 1, page_size: 100 }),
    ])
    chatModels.value = mRes.items
    availableTools.value = tRes.items
    if (!form.chat_model_id && mRes.items.length > 0) {
      const def = mRes.items.find((m) => m.is_default) || mRes.items[0]
      form.chat_model_id = def.id
    }
  } catch (err) {
    message.error(extractApiError(err))
  }
}

function resetForm(): void {
  if (props.editingAgent) {
    const a = props.editingAgent
    form.name = a.name
    form.description = a.description
    form.chat_model_id = a.chat_model_id || ''
    form.loop_engine = (a.loop_engine as AgentLoopEngine) || 'react'
    form.system_prompt = a.system_prompt
    form.sandbox_mode = a.sandbox_mode === 'docker' ? 'docker' : 'simulated'
    form.sandbox_image = a.sandbox_image || 'agan-ctf-sandbox:latest'
    form.tool_ids = [...(a.tool_ids || [])]
    form.max_steps = Number(a.guardrail_config?.max_steps || 10)
    form.is_active = a.is_active
    form.is_default = a.is_default
  } else {
    form.name = ''
    form.description = ''
    form.chat_model_id = ''
    form.loop_engine = 'react'
    form.system_prompt = DEFAULT_PROMPT_TEMPLATE
    form.sandbox_mode = 'simulated'
    form.sandbox_image = 'agan-ctf-sandbox:latest'
    form.tool_ids = []
    form.max_steps = 10
    form.is_active = true
    form.is_default = false
  }
}

watch(
  () => [props.show, props.editingAgent],
  async ([visible]) => {
    if (visible) {
      resetForm()
      await loadDependencies()
    }
  },
  { immediate: true },
)

function fillDefaultPrompt(): void {
  form.system_prompt = DEFAULT_PROMPT_TEMPLATE
}

function selectAllEnabledTools(): void {
  form.tool_ids = availableTools.value.filter((t) => t.is_enabled).map((t) => t.id)
}

async function handleSave(): Promise<void> {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }

  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      role: 'general' as const,
      description: form.description.trim(),
      chat_model_id: form.chat_model_id,
      loop_engine: form.loop_engine,
      system_prompt: form.system_prompt,
      temperature: null,
      sandbox_mode: form.sandbox_mode,
      sandbox_image: form.sandbox_image.trim() || 'agan-ctf-sandbox:latest',
      tool_ids: form.tool_ids,
      guardrail_config: {
        max_steps: form.max_steps,
        step_timeout_sec: 120,
        token_budget: 200000,
        cost_budget_usd: 5.0,
        repeat_threshold: 3,
      },
      is_active: form.is_active,
      is_default: form.is_default,
    }
    if (props.editingAgent) {
      await agentsApi.update(props.editingAgent.id, payload)
    } else {
      await agentsApi.create(payload)
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
    :title="isEdit ? t('agents.editAgent') : t('agents.addAgent')"
    :width="600"
    @update:show="emit('update:show', $event)"
  >
    <NAlert v-if="chatModels.length === 0" type="warning" class="mb-4">
      <div class="flex items-center justify-between gap-2">
        <span class="text-xs">{{ t('agents.form.noModelWarn') }}</span>
        <NButton
          size="tiny"
          type="primary"
          @click="
            () => {
              emit('update:show', false)
              router.push('/studio/models')
            }
          "
        >
          {{ t('workbench.create.goModels') }}
        </NButton>
      </div>
    </NAlert>

    <NForm ref="formRef" :model="form" :rules="rules" label-placement="top" size="medium">
      <NFormItem :label="t('agents.form.name')" path="name">
        <NInput v-model:value="form.name" :placeholder="t('agents.form.namePh')" />
      </NFormItem>

      <NFormItem :label="t('agents.form.chatModel')" path="chat_model_id">
        <NSelect v-model:value="form.chat_model_id" :options="modelOptions" filterable />
      </NFormItem>

      <NFormItem :label="t('agents.form.loopEngine')">
        <NSelect v-model:value="form.loop_engine" :options="loopOptions" />
      </NFormItem>

      <div class="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
        <NFormItem :label="t('agents.form.sandboxMode')">
          <NRadioGroup v-model:value="form.sandbox_mode" size="small">
            <NRadioButton value="simulated">Simulated</NRadioButton>
            <NRadioButton value="docker">Docker</NRadioButton>
          </NRadioGroup>
        </NFormItem>
        <NFormItem :label="t('agents.form.maxSteps')">
          <NInputNumber v-model:value="form.max_steps" :min="1" :max="100" class="w-full" />
        </NFormItem>
      </div>

      <NFormItem :label="t('agents.form.description')">
        <NInput
          v-model:value="form.description"
          :placeholder="t('agents.form.descriptionPh')"
        />
      </NFormItem>

      <NFormItem>
        <template #label>
          <div class="flex w-full items-center justify-between gap-2">
            <span>{{ t('agents.form.systemPrompt') }}</span>
            <NButton size="tiny" quaternary type="primary" @click="fillDefaultPrompt">
              <template #icon><NIcon :component="AutoAwesomeOutlined" /></template>
              {{ t('agents.form.fillRolePrompt') }}
            </NButton>
          </div>
        </template>
        <NInput
          v-model:value="form.system_prompt"
          type="textarea"
          :rows="4"
          :placeholder="t('agents.form.systemPromptPh')"
        />
      </NFormItem>

      <!-- Bound Tools Multi-Select -->
      <NFormItem>
        <template #label>
          <div class="flex w-full items-center justify-between gap-2">
            <span>{{ t('agents.form.tools') }} ({{ form.tool_ids.length }})</span>
            <div class="flex items-center gap-1">
              <NButton size="tiny" quaternary type="primary" @click="selectAllEnabledTools">
                {{ t('agents.form.selectAllTools') }}
              </NButton>
              <NButton size="tiny" quaternary @click="form.tool_ids = []">
                {{ t('agents.form.clearTools') }}
              </NButton>
            </div>
          </div>
        </template>
        <div
          class="w-full border p-3"
          style="
            background: var(--g-bg-canvas);
            border-color: var(--g-border-subtle);
            border-radius: var(--g-radius-sm);
          "
        >
          <NCheckboxGroup v-model:value="form.tool_ids">
            <div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <NCheckbox
                v-for="tool in availableTools"
                :key="tool.id"
                :value="tool.id"
                :disabled="!tool.is_enabled"
              >
                <div class="flex flex-col">
                  <span class="text-xs font-medium" style="color: var(--g-text-primary)">
                    {{ tool.display_name }}
                  </span>
                  <span class="font-mono text-[10px]" style="color: var(--g-text-tertiary)">
                    {{ tool.name }}
                  </span>
                </div>
              </NCheckbox>
            </div>
          </NCheckboxGroup>
        </div>
      </NFormItem>

      <div class="flex flex-wrap items-center gap-6 pt-1">
        <div class="flex items-center gap-2">
          <NSwitch v-model:value="form.is_active" />
          <span class="text-xs" style="color: var(--g-text-primary)">
            {{ t('agents.form.isActive') }}
          </span>
        </div>
        <div class="flex items-center gap-2">
          <NSwitch v-model:value="form.is_default" />
          <span class="text-xs" style="color: var(--g-text-primary)">
            {{ t('agents.form.isDefault') }}
          </span>
        </div>
      </div>
    </NForm>

    <template #footer>
      <div />
      <div class="flex items-center gap-2">
        <NButton @click="emit('update:show', false)">{{ t('common.cancel') }}</NButton>
        <NButton
          type="primary"
          :loading="saving"
          :disabled="chatModels.length === 0"
          @click="handleSave"
        >
          <template #icon><NIcon :component="SaveOutlined" /></template>
          {{ t('common.save') }}
        </NButton>
      </div>
    </template>
  </AppFormDrawer>
</template>
