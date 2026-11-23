<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { NAlert, NButton, NIcon, NInput, NModal, NTabPane, NTabs, useMessage } from 'naive-ui'
import { PlayArrowOutlined, SyncOutlined } from '@vicons/material'
import { mcpApi } from '@/api/mcp'
import { extractApiError } from '@/api/client'
import type { MCPDiscoveredTool, MCPServerItem, MCPToolCallTestResponse } from '@/types/mcp'

const props = defineProps<{
  show: boolean
  server: MCPServerItem | null
}>()

const emit = defineEmits<{
  (e: 'update:show', val: boolean): void
  (e: 'synced', updated: MCPServerItem): void
}>()

const { t } = useI18n()
const message = useMessage()

const localServer = ref<MCPServerItem | null>(null)
const selectedToolName = ref<string | null>(null)
const activeTab = ref('run')
const argsText = ref('{}')
const syncing = ref(false)
const running = ref(false)
const testResult = ref<MCPToolCallTestResponse | null>(null)

const tools = computed<MCPDiscoveredTool[]>(() => localServer.value?.discovered_tools || [])
const selectedTool = computed(
  () => tools.value.find((x) => x.name === selectedToolName.value) || null,
)

function buildSampleArgs(tool: MCPDiscoveredTool): string {
  const propsDef = tool.inputSchema?.properties || {}
  const sample: Record<string, any> = {}
  for (const [k, def] of Object.entries<any>(propsDef)) {
    const tpe = def?.type
    if (tpe === 'number' || tpe === 'integer') sample[k] = 0
    else if (tpe === 'boolean') sample[k] = false
    else if (tpe === 'array') sample[k] = []
    else if (tpe === 'object') sample[k] = {}
    else sample[k] = ''
  }
  return JSON.stringify(sample, null, 2)
}

function selectTool(tool: MCPDiscoveredTool): void {
  selectedToolName.value = tool.name
  testResult.value = null
  argsText.value = buildSampleArgs(tool)
}

watch(
  () => [props.show, props.server],
  ([visible]) => {
    if (visible && props.server) {
      localServer.value = props.server
      testResult.value = null
      if (props.server.discovered_tools.length > 0) {
        selectTool(props.server.discovered_tools[0])
      } else {
        selectedToolName.value = null
        argsText.value = '{}'
      }
    }
  },
  { immediate: true },
)

async function handleSync(): Promise<void> {
  if (!localServer.value) return
  syncing.value = true
  try {
    const updated = await mcpApi.refresh(localServer.value.id)
    localServer.value = updated
    emit('synced', updated)
    if (updated.last_status === 'connected') {
      message.success(t('mcp.syncSuccess', { count: updated.tool_count }))
      if (updated.discovered_tools.length > 0 && !selectedToolName.value) {
        selectTool(updated.discovered_tools[0])
      }
    } else {
      message.error(updated.last_error || t('common.operationFailed'))
    }
  } catch (err) {
    message.error(extractApiError(err))
  } finally {
    syncing.value = false
  }
}

async function runToolCall(): Promise<void> {
  if (!localServer.value || !selectedTool.value) return
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
    testResult.value = await mcpApi.testTool(
      localServer.value.id,
      selectedTool.value.name,
      parsedArgs,
    )
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
    :title="`${t('mcp.toolsModal.title')} — ${localServer?.name || ''}`"
    :style="{ width: '860px', maxWidth: '96vw' }"
    @update:show="emit('update:show', $event)"
  >
    <template #header-extra>
      <NButton size="tiny" secondary type="primary" :loading="syncing" @click="handleSync">
        <template #icon><NIcon :component="SyncOutlined" /></template>
        {{ syncing ? t('mcp.syncing') : t('mcp.syncTools') }}
      </NButton>
    </template>

    <div
      v-if="tools.length === 0"
      class="py-10 text-center text-xs"
      style="color: var(--g-text-secondary)"
    >
      {{ t('mcp.toolsModal.empty') }}
    </div>

    <div v-else class="grid grid-cols-1 gap-3 md:grid-cols-12">
      <!-- Left Tool List -->
      <div
        class="flex max-h-[58vh] flex-col gap-1 overflow-y-auto border p-2 md:col-span-4"
        style="
          background: var(--g-bg-canvas);
          border-color: var(--g-border-subtle);
          border-radius: var(--g-radius-sm);
        "
      >
        <button
          v-for="tool in tools"
          :key="tool.name"
          type="button"
          class="mcp-tool-item g-transition flex flex-col border-0 bg-transparent px-2.5 py-2 text-left cursor-pointer"
          :class="{ 'mcp-tool-item-active': selectedToolName === tool.name }"
          @click="selectTool(tool)"
        >
          <span class="truncate font-mono text-xs font-medium" style="color: var(--g-text-primary)">
            {{ tool.name }}
          </span>
          <span
            v-if="tool.description"
            class="mt-0.5 line-clamp-2 text-[11px] leading-snug"
            style="color: var(--g-text-secondary)"
          >
            {{ tool.description }}
          </span>
        </button>
      </div>

      <!-- Right Schema + Live Call Panel -->
      <div class="flex flex-col md:col-span-8">
        <template v-if="selectedTool">
          <div class="mb-2">
            <div class="font-mono text-sm font-medium" style="color: var(--g-text-primary)">
              {{ selectedTool.name }}
            </div>
            <div
              v-if="selectedTool.description"
              class="mt-0.5 text-xs leading-relaxed"
              style="color: var(--g-text-secondary)"
            >
              {{ selectedTool.description }}
            </div>
          </div>

          <NTabs v-model:value="activeTab" type="line" size="small">
            <NTabPane name="run" :tab="t('mcp.toolsModal.runTab')">
              <div class="flex flex-col gap-3">
                <div>
                  <label
                    class="mb-1.5 block text-xs font-medium"
                    style="color: var(--g-text-secondary)"
                  >
                    {{ t('mcp.toolsModal.argsLabel') }}
                  </label>
                  <NInput
                    v-model:value="argsText"
                    type="textarea"
                    :rows="5"
                    class="font-mono text-xs"
                  />
                </div>

                <div class="flex justify-end">
                  <NButton type="primary" size="small" :loading="running" @click="runToolCall">
                    <template #icon><NIcon :component="PlayArrowOutlined" /></template>
                    {{ t('mcp.toolsModal.runBtn') }}
                  </NButton>
                </div>

                <Transition name="g-fade">
                  <div v-if="testResult" class="flex flex-col gap-2">
                    <NAlert
                      :type="testResult.success ? 'success' : 'error'"
                      :title="
                        testResult.success
                          ? `Success${testResult.metadata?.duration_ms ? ` · ${testResult.metadata.duration_ms}ms` : ''}`
                          : 'Failed'
                      "
                    >
                      <span v-if="testResult.error" class="font-mono text-xs">
                        {{ testResult.error }}
                      </span>
                    </NAlert>
                    <pre
                      v-if="testResult.output"
                      class="m-0 max-h-56 overflow-auto border p-3 whitespace-pre-wrap break-words font-mono text-xs"
                      style="
                        background: var(--g-bg-canvas);
                        border-color: var(--g-border);
                        color: var(--g-text-primary);
                        border-radius: var(--g-radius-sm);
                      "
                    >{{ testResult.output }}</pre>
                  </div>
                </Transition>
              </div>
            </NTabPane>

            <NTabPane name="schema" :tab="t('mcp.toolsModal.schemaTab')">
              <pre
                class="m-0 max-h-[45vh] overflow-auto border p-3 font-mono text-xs leading-relaxed"
                style="
                  background: var(--g-bg-canvas);
                  border-color: var(--g-border);
                  color: var(--g-text-primary);
                  border-radius: var(--g-radius-sm);
                "
              >{{ JSON.stringify(selectedTool.inputSchema, null, 2) }}</pre>
            </NTabPane>
          </NTabs>
        </template>
        <div
          v-else
          class="flex h-full items-center justify-center text-xs"
          style="color: var(--g-text-tertiary)"
        >
          {{ t('mcp.toolsModal.selectHint') }}
        </div>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.mcp-tool-item {
  border-radius: var(--g-radius-xs);
}
.mcp-tool-item:hover {
  background: var(--g-bg-hover);
}
.mcp-tool-item-active {
  background: var(--g-bg-active);
}
</style>
