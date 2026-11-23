<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { NAlert, NButton, NIcon, NInput, NModal, NTabPane, NTabs, useMessage } from 'naive-ui'
import { PlayArrowOutlined } from '@vicons/material'
import { toolsApi } from '@/api/tools'
import { extractApiError } from '@/api/client'
import type { ToolItem, ToolTestResponse } from '@/types/capability'

const props = defineProps<{
  show: boolean
  tool: ToolItem | null
}>()

const emit = defineEmits<{
  (e: 'update:show', val: boolean): void
}>()

const { t } = useI18n()
const message = useMessage()
const activeTab = ref('schema')
const argsText = ref('{}')
const running = ref(false)
const testResult = ref<ToolTestResponse | null>(null)

const fullOpenAISchema = computed(() => {
  if (!props.tool) return ''
  return JSON.stringify(
    {
      type: 'function',
      function: {
        name: props.tool.name,
        description: props.tool.description,
        parameters: props.tool.parameters_schema,
      },
    },
    null,
    2,
  )
})

watch(
  () => [props.show, props.tool],
  ([visible]) => {
    if (visible && props.tool) {
      testResult.value = null
      if (props.tool.name === 'bash_exec') {
        argsText.value = JSON.stringify({ command: 'ls -la /workspace/challenge' }, null, 2)
      } else if (props.tool.name === 'read_file') {
        const hasCustomRoot = Boolean(props.tool.implementation_config?.root_dir)
        argsText.value = JSON.stringify(
          {
            path: hasCustomRoot ? 'notes.txt' : '/workspace/challenge/README.txt',
            show_line_numbers: true,
          },
          null,
          2,
        )
      } else if (props.tool.name === 'write_file') {
        argsText.value = JSON.stringify(
          {
            path: 'notes.txt',
            content: 'Hello from AGAN persistent workspace!\n',
            mode: 'overwrite',
          },
          null,
          2,
        )
      } else if (props.tool.name === 'edit_file') {
        argsText.value = JSON.stringify(
          {
            path: 'notes.txt',
            old_text: 'Hello',
            new_text: 'Updated',
            replace_all: false,
          },
          null,
          2,
        )
      } else if (props.tool.name === 'list_files') {
        argsText.value = JSON.stringify({ path: '.', pattern: '*', max_depth: 3 }, null, 2)
      } else if (props.tool.name === 'grep_search') {
        argsText.value = JSON.stringify(
          { pattern: 'service_port', path: '.', case_sensitive: false },
          null,
          2,
        )
      } else if (props.tool.name === 'web_search') {
        argsText.value = JSON.stringify({ query: 'FastAPI SQLAlchemy async', max_results: 3 }, null, 2)
      } else if (props.tool.name === 'web_fetch') {
        argsText.value = JSON.stringify({ url: 'https://example.com', max_chars: 4000 }, null, 2)
      } else if (props.tool.name === 'create_skill') {
        argsText.value = JSON.stringify(
          {
            name: 'demo-skill-check',
            title: 'Demo Skill',
            description: 'Quick sample skill created via tool dry-run.',
            skill_md_content: '# Demo Skill\n\nStep 1: Verify workflow.\n',
          },
          null,
          2,
        )
      } else if (props.tool.name === 'edit_skill_file') {
        argsText.value = JSON.stringify({ action: 'list' }, null, 2)
      } else {
        const propsDef = props.tool.parameters_schema?.properties || {}
        const sample: Record<string, string> = {}
        for (const k of Object.keys(propsDef)) {
          sample[k] = ''
        }
        argsText.value = JSON.stringify(sample, null, 2)
      }
    }
  },
)

async function runDryTest(): Promise<void> {
  if (!props.tool) return
  let parsedArgs: Record<string, any> = {}
  try {
    parsedArgs = JSON.parse(argsText.value || '{}')
  } catch {
    message.error(t('models.form.invalidJson'))
    return
  }

  running.value = true
  testResult.value = null
  try {
    testResult.value = await toolsApi.test(props.tool.id, parsedArgs)
  } catch (err) {
    testResult.value = {
      success: false,
      output: '',
      error: extractApiError(err),
      metadata: {},
    }
  } finally {
    running.value = false
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="`${t('tools.testModal.title')} — ${tool?.name || ''}`"
    :style="{ width: '680px', maxWidth: '94vw' }"
    @update:show="emit('update:show', $event)"
  >
    <NTabs v-model:value="activeTab" type="line" size="small">
      <NTabPane name="schema" :tab="t('tools.testModal.schemaTab')">
        <pre
          class="m-0 max-h-[55vh] overflow-auto border p-3 font-mono text-xs leading-relaxed"
          style="
            background: var(--g-bg-canvas);
            border-color: var(--g-border);
            color: var(--g-text-primary);
            border-radius: var(--g-radius-sm);
          "
        >{{ fullOpenAISchema }}</pre>
      </NTabPane>

      <NTabPane name="run" :tab="t('tools.testModal.runTab')">
        <div class="flex flex-col gap-3">
          <div>
            <label class="mb-1.5 block text-xs font-medium" style="color: var(--g-text-secondary)">
              {{ t('tools.testModal.argsLabel') }}
            </label>
            <NInput
              v-model:value="argsText"
              type="textarea"
              :rows="5"
              class="font-mono text-xs"
            />
          </div>

          <div class="flex justify-end">
            <NButton type="primary" size="small" :loading="running" @click="runDryTest">
              <template #icon><NIcon :component="PlayArrowOutlined" /></template>
              {{ t('tools.testModal.runBtn') }}
            </NButton>
          </div>

          <Transition name="g-fade">
            <div v-if="testResult" class="flex flex-col gap-2">
              <NAlert
                :type="testResult.success ? 'success' : 'error'"
                :title="testResult.success ? 'Success' : 'Failed'"
              >
                <span v-if="testResult.error" class="font-mono text-xs">{{ testResult.error }}</span>
              </NAlert>
              <pre
                v-if="testResult.output"
                class="m-0 max-h-60 overflow-auto border p-3 whitespace-pre-wrap break-words font-mono text-xs"
                style="
                  background: var(--g-bg-canvas);
                  border-color: var(--g-border);
                  color: var(--g-text-primary);
                  border-radius: var(--g-radius-sm);
                "
              >{{ testResult.output }}</pre>
              <div
                v-if="testResult.metadata && Object.keys(testResult.metadata).length > 0"
                class="border px-3 py-2 font-mono text-[11px]"
                style="
                  background: var(--g-bg-canvas);
                  border-color: var(--g-border-subtle);
                  color: var(--g-text-secondary);
                  border-radius: var(--g-radius-sm);
                "
              >
                <div class="mb-1 text-[10px] font-semibold uppercase tracking-wide" style="color: var(--g-text-tertiary)">
                  {{ t('tools.testModal.metaLabel') }}
                </div>
                <div class="break-all whitespace-pre-wrap">{{ JSON.stringify(testResult.metadata, null, 2) }}</div>
              </div>
            </div>
          </Transition>
        </div>
      </NTabPane>
    </NTabs>
  </NModal>
</template>
