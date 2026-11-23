<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { NAlert, NButton, NIcon, NInput, NModal } from 'naive-ui'
import { PlayArrowOutlined } from '@vicons/material'
import AppStatusBadge from '@/components/common/AppStatusBadge.vue'
import { modelsApi } from '@/api/models'
import { extractApiError } from '@/api/client'
import type { LLMModel, LLMModelTestResponse } from '@/types/llm'

const props = defineProps<{
  show: boolean
  model: LLMModel | null
}>()

const emit = defineEmits<{
  (e: 'update:show', val: boolean): void
  (e: 'tested'): void
}>()

const { t } = useI18n()

const promptInput = ref("Reply concisely with 'OK' and confirm your model name.")
const rerankQuery = ref('Flask Jinja2 Server-Side Template Injection payload')
const rerankDocsText = ref(
  [
    'Flask Jinja2 SSTI allows remote code execution via {{config.__class__.__init__.__globals__}}.',
    'RSA Wiener attack recovers small private exponent d using continued fractions.',
    'Buffer overflow with NX disabled allows shellcode execution on the stack.',
  ].join('\n'),
)

const loading = ref(false)
const result = ref<LLMModelTestResponse | null>(null)

watch(
  () => [props.show, props.model],
  ([visible]) => {
    if (visible) {
      result.value = null
    }
  },
)

async function runTest(): Promise<void> {
  if (!props.model) return
  loading.value = true
  result.value = null
  try {
    const docs = rerankDocsText.value
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean)
    result.value = await modelsApi.testSaved(props.model.id, {
      prompt: promptInput.value,
      query: rerankQuery.value,
      documents: docs.length > 0 ? docs : ['CTF sample document'],
    })
    emit('tested')
  } catch (err) {
    result.value = {
      ok: false,
      model_type: props.model.model_type,
      latency_ms: 0,
      detail: extractApiError(err, t('common.operationFailed')),
      preview: {},
      usage: {},
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="`${t('models.testModal.title')} — ${model?.name || ''}`"
    :style="{ width: '640px', maxWidth: '94vw' }"
    @update:show="emit('update:show', $event)"
  >
    <div v-if="model" class="flex flex-col gap-4">
      <div
        class="flex flex-wrap items-center justify-between gap-2 border px-3 py-2 text-xs"
        style="
          background: var(--g-bg-canvas);
          border-color: var(--g-border-subtle);
          border-radius: var(--g-radius-sm);
        "
      >
        <div class="flex items-center gap-2 font-mono">
          <AppStatusBadge variant="info" :label="model.model_type.toUpperCase()" :dot="false" />
          <span style="color: var(--g-text-primary)">{{ model.model_id }}</span>
        </div>
        <span class="font-mono truncate" style="color: var(--g-text-secondary)">
          {{ model.base_url }}
        </span>
      </div>

      <!-- Input section tailored by model type -->
      <div v-if="model.model_type === 'chat' || model.model_type === 'embedding'">
        <label
          class="mb-1.5 block text-xs font-medium"
          style="color: var(--g-text-secondary)"
        >
          {{
            model.model_type === 'chat'
              ? t('models.testModal.chatPromptLabel')
              : t('models.testModal.embedInputLabel')
          }}
        </label>
        <NInput v-model:value="promptInput" type="textarea" :rows="3" />
      </div>

      <div v-else class="flex flex-col gap-3">
        <div>
          <label class="mb-1.5 block text-xs font-medium" style="color: var(--g-text-secondary)">
            {{ t('models.testModal.rerankQueryLabel') }}
          </label>
          <NInput v-model:value="rerankQuery" />
        </div>
        <div>
          <label class="mb-1.5 block text-xs font-medium" style="color: var(--g-text-secondary)">
            {{ t('models.testModal.rerankDocsLabel') }}
          </label>
          <NInput v-model:value="rerankDocsText" type="textarea" :rows="4" />
        </div>
      </div>

      <div class="flex justify-end">
        <NButton type="primary" :loading="loading" @click="runTest">
          <template #icon>
            <NIcon :component="PlayArrowOutlined" />
          </template>
          {{ loading ? t('models.testing') : t('models.testModal.runTest') }}
        </NButton>
      </div>

      <!-- Result diagnostics panel -->
      <Transition name="g-fade">
        <div v-if="result" class="flex flex-col gap-3">
          <NAlert
            :type="result.ok ? 'success' : 'error'"
            :title="
              result.ok ? t('models.testModal.resultSuccess') : t('models.testModal.resultFail')
            "
          >
            <div class="flex flex-wrap items-center gap-4 text-xs">
              <span>
                <strong>{{ t('models.testModal.latency') }}:</strong>
                <span class="ml-1 font-mono">{{ result.latency_ms }} ms</span>
              </span>
              <span v-if="result.preview?.resolved_model">
                <strong>{{ t('models.testModal.resolvedModel') }}:</strong>
                <span class="ml-1 font-mono">{{ result.preview.resolved_model }}</span>
              </span>
              <span v-if="result.usage?.total_tokens">
                <strong>{{ t('models.testModal.tokenUsage') }}:</strong>
                <span class="ml-1 font-mono">{{ result.usage.total_tokens }} tokens</span>
              </span>
            </div>
          </NAlert>

          <div
            v-if="!result.ok"
            class="border p-3 font-mono text-xs break-all"
            style="
              background: var(--g-error-soft);
              border-color: var(--g-error);
              color: var(--g-error);
              border-radius: var(--g-radius-sm);
            "
          >
            {{ result.detail }}
          </div>

          <!-- Chat Preview -->
          <div
            v-else-if="result.model_type === 'chat'"
            class="border p-3"
            style="
              background: var(--g-bg-canvas);
              border-color: var(--g-border);
              border-radius: var(--g-radius-sm);
            "
          >
            <div class="mb-1 text-[11px] font-medium uppercase" style="color: var(--g-text-tertiary)">
              {{ t('models.testModal.replyOutput') }}
            </div>
            <pre
              class="m-0 whitespace-pre-wrap font-mono text-xs leading-relaxed"
              style="color: var(--g-text-primary)"
            >{{ result.preview.reply || '(empty)' }}</pre>
          </div>

          <!-- Embedding Preview -->
          <div
            v-else-if="result.model_type === 'embedding'"
            class="border p-3"
            style="
              background: var(--g-bg-canvas);
              border-color: var(--g-border);
              border-radius: var(--g-radius-sm);
            "
          >
            <div class="mb-2 flex items-center justify-between text-xs">
              <span style="color: var(--g-text-secondary)">{{ t('models.testModal.vectorDims') }}</span>
              <span class="font-mono font-medium" style="color: var(--g-primary-text)">
                {{ result.preview.dimensions }}
              </span>
            </div>
            <div class="mb-1 text-[11px]" style="color: var(--g-text-tertiary)">
              {{ t('models.testModal.vectorPreview') }}
            </div>
            <code class="block font-mono text-xs break-all" style="color: var(--g-text-primary)">
              [{{ (result.preview.vector_head || []).join(', ') }}, ...]
            </code>
          </div>

          <!-- Rerank Preview -->
          <div
            v-else-if="result.model_type === 'rerank'"
            class="flex flex-col gap-2 border p-3"
            style="
              background: var(--g-bg-canvas);
              border-color: var(--g-border);
              border-radius: var(--g-radius-sm);
            "
          >
            <div class="text-[11px] font-medium uppercase" style="color: var(--g-text-tertiary)">
              {{ t('models.testModal.rerankScores') }}
            </div>
            <div
              v-for="item in result.preview.results || []"
              :key="item.index"
              class="border p-2.5 text-xs"
              style="
                background: var(--g-bg-surface);
                border-color: var(--g-border-subtle);
                border-radius: var(--g-radius-xs);
              "
            >
              <div class="mb-1 flex items-center justify-between font-mono">
                <span style="color: var(--g-text-secondary)">#{{ item.index }}</span>
                <span class="font-medium" style="color: var(--g-success)">
                  score: {{ item.score }}
                </span>
              </div>
              <div style="color: var(--g-text-primary)">{{ item.document }}</div>
            </div>
          </div>
        </div>
      </Transition>
    </div>
  </NModal>
</template>
