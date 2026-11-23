<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NForm,
  NFormItem,
  NIcon,
  NInput,
  NInputNumber,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSwitch,
  NUpload,
  useMessage,
  type FormInst,
  type FormRules,
  type UploadFileInfo,
} from 'naive-ui'
import { AutoAwesomeOutlined, PlayArrowOutlined, UploadFileOutlined } from '@vicons/material'
import AppFormDrawer from '@/components/common/AppFormDrawer.vue'
import { modelsApi } from '@/api/models'
import { sessionsApi } from '@/api/sessions'
import { extractApiError } from '@/api/client'
import type { LLMModel } from '@/types/llm'
import type { LoopEngineType, SandboxMode, SessionDetail } from '@/types/session'

const props = defineProps<{
  show: boolean
}>()

const emit = defineEmits<{
  (e: 'update:show', val: boolean): void
  (e: 'created', session: SessionDetail): void
}>()

const router = useRouter()
const { t } = useI18n()
const message = useMessage()

const formRef = ref<FormInst | null>(null)
const submitting = ref(false)
const chatModels = ref<LLMModel[]>([])
const loadingModels = ref(false)
const pendingFiles = ref<UploadFileInfo[]>([])
const startImmediately = ref(true)

const form = reactive({
  title: '',
  category: 'misc',
  chat_model_id: '' as string,
  loop_engine: 'react' as LoopEngineType,
  sandbox_mode: 'simulated' as SandboxMode,
  sandbox_image: 'agan-ctf-sandbox:latest',
  description: '',
  target_url: '',
  target_host: '',
  target_port: '',
  flag_regex: 'flag\\{[^}\\s]+\\}',
  max_steps: 20,
  token_budget: 200000,
  repeat_threshold: 3,
})

const categoryOptions = [
  { label: 'Web', value: 'web' },
  { label: 'Pwn', value: 'pwn' },
  { label: 'Crypto', value: 'crypto' },
  { label: 'Reverse', value: 'reverse' },
  { label: 'Misc', value: 'misc' },
]

const engineOptions = [
  { label: 'ReAct (Thought → Action → Observation)', value: 'react' },
  { label: 'Plan-and-Execute (Plan → Sub-loop → Dynamic Replan)', value: 'plan_execute' },
  { label: 'Reflexion (Failure-triggered Self-Critique)', value: 'reflexion' },
]

const modelSelectOptions = computed(() =>
  chatModels.value.map((m) => ({
    label: `${m.name} (${m.model_id})${m.is_default ? ' ★' : ''}`,
    value: m.id,
  })),
)

const rules = computed<FormRules>(() => ({
  title: [{ required: true, message: t('models.form.requiredField'), trigger: 'blur' }],
  chat_model_id: [{ required: true, message: t('models.form.requiredField'), trigger: 'change' }],
}))

async function loadChatModels(): Promise<void> {
  loadingModels.value = true
  try {
    const res = await modelsApi.list({ model_type: 'chat', is_active: true, page: 1, page_size: 100 })
    chatModels.value = res.items
    if (!form.chat_model_id && res.items.length > 0) {
      const def = res.items.find((m) => m.is_default) || res.items[0]
      form.chat_model_id = def.id
    }
  } catch (err) {
    message.error(extractApiError(err))
  } finally {
    loadingModels.value = false
  }
}

function fillDemoChallenge(): void {
  form.title = 'Warmup — Double Encoded Backup'
  form.category = 'misc'
  form.sandbox_mode = 'simulated'
  form.description =
    'A backup file was left inside `/workspace/challenge`. Inspect the directory, locate the encoded secret, decode it completely, and submit the recovered flag.'
  form.flag_regex = 'flag\\{[^}\\s]+\\}'
}

watch(
  () => props.show,
  (visible) => {
    if (visible) {
      pendingFiles.value = []
      loadChatModels()
    }
  },
)

async function handleSubmit(): Promise<void> {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }

  submitting.value = true
  try {
    const targetInfo: Record<string, string> = {}
    if (form.target_url.trim()) targetInfo.url = form.target_url.trim()
    if (form.target_host.trim()) targetInfo.host = form.target_host.trim()
    if (form.target_port.trim()) targetInfo.port = form.target_port.trim()

    let created = await sessionsApi.create({
      title: form.title.trim(),
      category: form.category,
      description: form.description,
      target_info: targetInfo,
      flag_regex: form.flag_regex.trim() || 'flag\\{[^}\\s]+\\}',
      chat_model_id: form.chat_model_id,
      loop_engine: form.loop_engine,
      sandbox_mode: form.sandbox_mode,
      sandbox_image: form.sandbox_image.trim() || 'agan-ctf-sandbox:latest',
      guardrail_config: {
        max_steps: form.max_steps,
        step_timeout_sec: 120,
        token_budget: form.token_budget,
        cost_budget_usd: 5.0,
        repeat_threshold: form.repeat_threshold,
      },
    })

    // Upload any attached challenge files
    for (const item of pendingFiles.value) {
      if (item.file) {
        created = await sessionsApi.uploadAttachment(created.id, item.file)
      }
    }

    if (startImmediately.value) {
      created = await sessionsApi.start(created.id)
    }

    emit('created', created)
    emit('update:show', false)
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <AppFormDrawer
    :show="show"
    :title="t('workbench.create.title')"
    :width="580"
    @update:show="emit('update:show', $event)"
  >
    <NAlert
      v-if="!loadingModels && chatModels.length === 0"
      type="warning"
      class="mb-4"
    >
      <div class="flex items-center justify-between gap-3">
        <span class="text-xs">{{ t('workbench.create.noChatModel') }}</span>
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

    <div class="mb-3 flex justify-end">
      <NButton size="tiny" secondary @click="fillDemoChallenge">
        <template #icon><NIcon :component="AutoAwesomeOutlined" /></template>
        {{ t('workbench.create.loadDemo') }}
      </NButton>
    </div>

    <NForm ref="formRef" :model="form" :rules="rules" label-placement="top" size="medium">
      <div class="grid grid-cols-1 gap-x-4 sm:grid-cols-3">
        <NFormItem :label="t('workbench.create.challengeTitle')" path="title" class="sm:col-span-2">
          <NInput v-model:value="form.title" :placeholder="t('workbench.create.challengeTitlePh')" />
        </NFormItem>
        <NFormItem :label="t('workbench.create.category')">
          <NSelect v-model:value="form.category" :options="categoryOptions" />
        </NFormItem>
      </div>

      <NFormItem :label="t('workbench.create.chatModel')" path="chat_model_id">
        <NSelect
          v-model:value="form.chat_model_id"
          :loading="loadingModels"
          :options="modelSelectOptions"
          filterable
        />
      </NFormItem>

      <NFormItem :label="t('workbench.create.loopEngine')">
        <NSelect v-model:value="form.loop_engine" :options="engineOptions" />
      </NFormItem>

      <NFormItem :label="t('workbench.create.sandboxMode')">
        <NRadioGroup v-model:value="form.sandbox_mode" size="small" class="w-full">
          <NRadioButton value="simulated">{{ t('workbench.create.sandboxSim') }}</NRadioButton>
          <NRadioButton value="docker">{{ t('workbench.create.sandboxDocker') }}</NRadioButton>
        </NRadioGroup>
      </NFormItem>

      <NFormItem v-if="form.sandbox_mode === 'docker'" :label="t('workbench.create.sandboxImage')">
        <NInput v-model:value="form.sandbox_image" class="font-mono" />
      </NFormItem>

      <NFormItem :label="t('workbench.create.description')">
        <NInput
          v-model:value="form.description"
          type="textarea"
          :rows="3"
          :placeholder="t('workbench.create.descriptionPh')"
        />
      </NFormItem>

      <div class="grid grid-cols-1 gap-x-3 sm:grid-cols-3">
        <NFormItem :label="t('workbench.create.targetUrl')" class="sm:col-span-3">
          <NInput v-model:value="form.target_url" class="font-mono" placeholder="http://target:8080" />
        </NFormItem>
        <NFormItem :label="t('workbench.create.targetHost')" class="sm:col-span-2">
          <NInput v-model:value="form.target_host" class="font-mono" placeholder="127.0.0.1" />
        </NFormItem>
        <NFormItem :label="t('workbench.create.targetPort')">
          <NInput v-model:value="form.target_port" class="font-mono" placeholder="9999" />
        </NFormItem>
      </div>

      <NFormItem :label="t('workbench.create.flagRegex')">
        <NInput v-model:value="form.flag_regex" class="font-mono" />
      </NFormItem>

      <div
        class="mb-4 grid grid-cols-3 gap-3 border p-3"
        style="
          background: var(--g-bg-canvas);
          border-color: var(--g-border-subtle);
          border-radius: var(--g-radius-sm);
        "
      >
        <NFormItem :label="t('workbench.create.maxSteps')" class="mb-0">
          <NInputNumber v-model:value="form.max_steps" :min="1" :max="200" class="w-full" />
        </NFormItem>
        <NFormItem :label="t('workbench.create.tokenBudget')" class="mb-0">
          <NInputNumber v-model:value="form.token_budget" :min="0" :step="50000" class="w-full" />
        </NFormItem>
        <NFormItem :label="t('workbench.create.repeatThreshold')" class="mb-0">
          <NInputNumber v-model:value="form.repeat_threshold" :min="2" :max="10" class="w-full" />
        </NFormItem>
      </div>

      <NFormItem :label="t('workbench.create.attachments')">
        <NUpload
          v-model:file-list="pendingFiles"
          :default-upload="false"
          multiple
        >
          <NButton size="small" secondary>
            <template #icon><NIcon :component="UploadFileOutlined" /></template>
            {{ t('common.add') }}
          </NButton>
        </NUpload>
      </NFormItem>

      <div class="flex items-center gap-2 pt-1">
        <NSwitch v-model:value="startImmediately" />
        <span class="text-xs" style="color: var(--g-text-primary)">
          {{ t('workbench.create.startNow') }}
        </span>
      </div>
    </NForm>

    <template #footer>
      <div />
      <div class="flex items-center gap-2">
        <NButton @click="emit('update:show', false)">{{ t('common.cancel') }}</NButton>
        <NButton
          type="primary"
          :loading="submitting"
          :disabled="chatModels.length === 0"
          @click="handleSubmit"
        >
          <template #icon><NIcon :component="PlayArrowOutlined" /></template>
          {{ startImmediately ? t('workbench.actions.start') : t('common.create') }}
        </NButton>
      </div>
    </template>
  </AppFormDrawer>
</template>
