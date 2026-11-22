<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  NButton,
  NCheckbox,
  NIcon,
  NInput,
  NPagination,
  NSelect,
  NSwitch,
  NTabPane,
  NTabs,
  NTooltip,
  useDialog,
  useMessage,
} from 'naive-ui'
import {
  AddOutlined,
  ContentCopyOutlined,
  DeleteOutlineOutlined,
  DescriptionOutlined,
  FlagOutlined,
  PauseOutlined,
  PlayArrowOutlined,
  RefreshOutlined,
  SaveOutlined,
  SearchOutlined,
  SendOutlined,
  StopOutlined,
  TerminalOutlined,
} from '@vicons/material'
import AppFilterToolbar from '@/components/common/AppFilterToolbar.vue'
import AppStatusBadge, { type BadgeVariant } from '@/components/common/AppStatusBadge.vue'
import AppEmptyState from '@/components/common/AppEmptyState.vue'
import TraceEventCard from '@/components/trace/TraceEventCard.vue'
import SessionCreateDrawer from '@/views/workbench/SessionCreateDrawer.vue'
import WriteupModal from '@/views/workbench/WriteupModal.vue'
import { SessionTraceSocket, sessionsApi } from '@/api/sessions'
import { extractApiError } from '@/api/client'
import type {
  SessionDetail,
  SessionStatus,
  SessionSummary,
  TraceEventItem,
  WriteupDTO,
} from '@/types/session'

const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()

// Left session list state
const loadingList = ref(false)
const sessions = ref<SessionSummary[]>([])
const totalSessions = ref(0)
const listPage = ref(1)
const listPageSize = ref(10)
const listSearch = ref('')
const listStatus = ref<'all' | SessionStatus>('all')
let searchTimer: number | undefined

const sessionStatusOptions = computed(() => [
  { label: `${t('common.status')}: ${t('common.all')}`, value: 'all' },
  ...(['pending', 'running', 'paused', 'solved', 'failed', 'stopped'] as SessionStatus[]).map(
    (s) => ({
      label: t(`workbench.status.${s}`),
      value: s,
    }),
  ),
])

// Active session state
const activeSessionId = ref<string | null>(null)
const activeSession = ref<SessionDetail | null>(null)
const traceEvents = ref<TraceEventItem[]>([])
const wsConnected = ref(false)
const autoScroll = ref(true)
const traceFilter = ref<'all' | 'thought' | 'tool' | 'plan' | 'hitl'>('all')
const traceScrollEl = ref<HTMLElement | null>(null)

// Controls state
const createDrawerVisible = ref(false)
const writeupModalVisible = ref(false)
const actionBusy = ref(false)

// HITL Steering state
const steeringText = ref('')
const resumeOnSteer = ref(true)
const sendingSteer = ref(false)

// Right panel inspector
const inspectorTab = ref('scratchpad')
const savingScratchpad = ref(false)
const scratchpadForm = reactive({
  endpoints_text: '',
  clues_text: '',
  vulns_text: '',
  ruled_out_text: '',
  notes_md: '',
})

// Direct operator sandbox shell
const shellCmd = ref('')
const shellRunning = ref(false)
const shellOutput = ref('')

let activeSocket: SessionTraceSocket | null = null

const isSessionLive = computed(
  () => activeSession.value?.status === 'running' || activeSession.value?.status === 'paused',
)

const filteredEvents = computed(() => {
  return traceEvents.value.filter((ev) => {
    // Hide noisy raw token_usage ticks in card stream (reflected live in telemetry strip)
    if (ev.event_type === 'token_usage') return false
    if (traceFilter.value === 'all') return true
    if (traceFilter.value === 'thought') return ev.event_type === 'thought'
    if (traceFilter.value === 'tool')
      return ev.event_type === 'tool_call' || ev.event_type === 'tool_result'
    if (traceFilter.value === 'plan')
      return ev.event_type === 'plan_update' || ev.event_type === 'reflexion'
    if (traceFilter.value === 'hitl')
      return (
        ev.event_type === 'human_steering' ||
        ev.event_type === 'guardrail' ||
        ev.event_type === 'flag_detected' ||
        ev.event_type === 'error'
      )
    return true
  })
})

const latestPlan = computed<{
  strategy_overview: string
  steps: Array<{ id: number; title: string; goal: string; status: string; result_summary?: string }>
} | null>(() => {
  for (let i = traceEvents.value.length - 1; i >= 0; i--) {
    if (traceEvents.value[i].event_type === 'plan_update') {
      return traceEvents.value[i].payload as any
    }
  }
  return null
})

function statusVariant(st: SessionStatus | string): BadgeVariant {
  if (st === 'solved') return 'success'
  if (st === 'running') return 'info'
  if (st === 'paused') return 'warning'
  if (st === 'failed') return 'error'
  return 'neutral'
}

function syncScratchpadForm(sp?: SessionDetail['scratchpad']): void {
  scratchpadForm.endpoints_text = (sp?.endpoints_ports || []).join('\n')
  scratchpadForm.clues_text = (sp?.known_clues || []).join('\n')
  scratchpadForm.vulns_text = (sp?.vulnerabilities || []).join('\n')
  scratchpadForm.ruled_out_text = (sp?.ruled_out || []).join('\n')
  scratchpadForm.notes_md = sp?.notes_md || ''
}

function linesToArray(text: string): string[] {
  return text
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)
}

async function scrollTraceToBottom(): Promise<void> {
  if (!autoScroll.value) return
  await nextTick()
  if (traceScrollEl.value) {
    traceScrollEl.value.scrollTop = traceScrollEl.value.scrollHeight
  }
}

async function loadSessionList(): Promise<void> {
  loadingList.value = true
  try {
    const res = await sessionsApi.list({
      status: listStatus.value === 'all' ? undefined : listStatus.value,
      search: listSearch.value.trim() || undefined,
      page: listPage.value,
      page_size: listPageSize.value,
    })
    sessions.value = res.items
    totalSessions.value = res.total
    if (!activeSessionId.value && res.items.length > 0) {
      await selectSession(res.items[0].id)
    }
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    loadingList.value = false
  }
}

function disconnectSocket(): void {
  if (activeSocket) {
    activeSocket.close()
    activeSocket = null
  }
  wsConnected.value = false
}

function handleIncomingEvent(ev: TraceEventItem): void {
  if (traceEvents.value.some((existing) => existing.seq_id === ev.seq_id)) return
  traceEvents.value.push(ev)

  // Live-update active session telemetry & status without full reload
  if (activeSession.value && ev.session_id === activeSession.value.id) {
    if (ev.step_index > activeSession.value.current_step) {
      activeSession.value.current_step = ev.step_index
    }
    if (ev.event_type === 'token_usage' && ev.usage) {
      activeSession.value.total_prompt_tokens += ev.usage.prompt_tokens
      activeSession.value.total_completion_tokens += ev.usage.completion_tokens
      activeSession.value.total_tokens =
        activeSession.value.total_prompt_tokens + activeSession.value.total_completion_tokens
      activeSession.value.total_cost_usd = Number(
        (activeSession.value.total_cost_usd + ev.usage.cost_usd).toFixed(6),
      )
    }
    if (ev.event_type === 'flag_detected' && ev.payload?.flag) {
      activeSession.value.detected_flag = String(ev.payload.flag)
    }
    if (ev.event_type === 'scratchpad_update' && ev.payload?.scratchpad) {
      activeSession.value.scratchpad = ev.payload.scratchpad
      syncScratchpadForm(ev.payload.scratchpad)
    }
    if (ev.event_type === 'status' && ev.payload?.status) {
      activeSession.value.status = ev.payload.status
      if (ev.payload.detected_flag) {
        activeSession.value.detected_flag = ev.payload.detected_flag
      }
      // Keep left list status synced cleanly
      const idx = sessions.value.findIndex((s) => s.id === activeSession.value?.id)
      if (idx !== -1) {
        sessions.value[idx].status = ev.payload.status
        if (ev.payload.detected_flag) {
          sessions.value[idx].detected_flag = ev.payload.detected_flag
        }
      }
    }
  }

  scrollTraceToBottom()
}

async function selectSession(id: string): Promise<void> {
  disconnectSocket()
  activeSessionId.value = id
  traceEvents.value = []
  shellOutput.value = ''

  try {
    const detail = await sessionsApi.get(id)
    activeSession.value = detail
    syncScratchpadForm(detail.scratchpad)

    activeSocket = new SessionTraceSocket(
      id,
      {
        onEvent: handleIncomingEvent,
        onStateChange: (ok) => {
          wsConnected.value = ok
        },
      },
      0,
    )
    activeSocket.connect()
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  }
}

async function handleSessionCreated(created: SessionDetail): Promise<void> {
  listPage.value = 1
  await loadSessionList()
  await selectSession(created.id)
}

async function handleStart(): Promise<void> {
  if (!activeSession.value) return
  actionBusy.value = true
  try {
    activeSession.value = await sessionsApi.start(activeSession.value.id)
    await loadSessionList()
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    actionBusy.value = false
  }
}

async function handlePause(): Promise<void> {
  if (!activeSession.value) return
  actionBusy.value = true
  try {
    activeSession.value = await sessionsApi.pause(activeSession.value.id)
  } catch (err) {
    message.error(extractApiError(err))
  } finally {
    actionBusy.value = false
  }
}

async function handleResume(): Promise<void> {
  if (!activeSession.value) return
  actionBusy.value = true
  try {
    activeSession.value = await sessionsApi.resume(activeSession.value.id)
  } catch (err) {
    message.error(extractApiError(err))
  } finally {
    actionBusy.value = false
  }
}

async function handleStop(): Promise<void> {
  if (!activeSession.value) return
  actionBusy.value = true
  try {
    activeSession.value = await sessionsApi.stop(activeSession.value.id, false)
    await loadSessionList()
  } catch (err) {
    message.error(extractApiError(err))
  } finally {
    actionBusy.value = false
  }
}

async function handleSendSteering(): Promise<void> {
  if (!activeSession.value || !steeringText.value.trim()) return
  sendingSteer.value = true
  try {
    await sessionsApi.steer(
      activeSession.value.id,
      steeringText.value.trim(),
      resumeOnSteer.value,
    )
    steeringText.value = ''
    message.success(t('workbench.steering.sentSuccess'))
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    sendingSteer.value = false
  }
}

async function handleSaveScratchpad(): Promise<void> {
  if (!activeSession.value) return
  savingScratchpad.value = true
  try {
    const updated = await sessionsApi.updateScratchpad(activeSession.value.id, {
      endpoints_ports: linesToArray(scratchpadForm.endpoints_text),
      known_clues: linesToArray(scratchpadForm.clues_text),
      vulnerabilities: linesToArray(scratchpadForm.vulns_text),
      ruled_out: linesToArray(scratchpadForm.ruled_out_text),
      notes_md: scratchpadForm.notes_md,
    })
    activeSession.value.scratchpad = updated
    message.success(t('workbench.scratchpad.synced'))
  } catch (err) {
    message.error(extractApiError(err))
  } finally {
    savingScratchpad.value = false
  }
}

async function handleExecShell(): Promise<void> {
  if (!activeSession.value || !shellCmd.value.trim()) return
  shellRunning.value = true
  try {
    const res = await sessionsApi.execSandbox(activeSession.value.id, shellCmd.value.trim())
    shellOutput.value = `[exit=${res.exit_code} | cwd=${res.cwd || '?'} | ${res.duration_ms}ms]\n${res.output || '(no output)'}`
    shellCmd.value = ''
  } catch (err) {
    shellOutput.value = extractApiError(err)
  } finally {
    shellRunning.value = false
  }
}

function handleDeleteSession(): void {
  if (!activeSession.value) return
  const target = activeSession.value
  dialog.warning({
    title: t('workbench.actions.delete'),
    content: t('workbench.actions.deleteConfirm', { title: target.title }),
    positiveText: t('common.confirmDelete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        disconnectSocket()
        await sessionsApi.remove(target.id)
        activeSession.value = null
        activeSessionId.value = null
        traceEvents.value = []
        message.success(t('common.deleteSuccess'))
        await loadSessionList()
      } catch (err) {
        message.error(extractApiError(err))
      }
    },
  })
}

async function copyFlag(flag: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(flag)
    message.success(t('common.copied'))
  } catch {
    message.warning(t('common.operationFailed'))
  }
}

function onWriteupGenerated(wu: WriteupDTO): void {
  if (activeSession.value) {
    activeSession.value.writeup = wu
  }
}

watch(listStatus, () => {
  listPage.value = 1
  loadSessionList()
})

watch(listSearch, () => {
  if (searchTimer) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    listPage.value = 1
    loadSessionList()
  }, 260)
})

onMounted(loadSessionList)
onUnmounted(disconnectSocket)
</script>

<template>
  <div class="flex h-[calc(100vh-100px)] min-h-[560px] flex-col">
    <AppFilterToolbar>
      <template #filters>
        <div class="w-full sm:w-72">
          <NInput
            v-model:value="listSearch"
            size="small"
            clearable
            :placeholder="t('workbench.searchSessions')"
          >
            <template #prefix><NIcon :component="SearchOutlined" /></template>
          </NInput>
        </div>
        <NSelect
          v-model:value="listStatus"
          size="small"
          :options="sessionStatusOptions"
          :style="{ width: '150px' }"
        />
      </template>

      <template #actions>
        <NButton secondary size="small" :loading="loadingList" @click="loadSessionList">
          <template #icon><NIcon :component="RefreshOutlined" /></template>
          {{ t('common.refresh') }}
        </NButton>
        <NButton type="primary" size="small" @click="createDrawerVisible = true">
          <template #icon><NIcon :component="AddOutlined" /></template>
          {{ t('workbench.newSession') }}
        </NButton>
      </template>
    </AppFilterToolbar>

    <!-- Three-Pane Workbench Grid -->
    <div class="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-12">
      <!-- LEFT PANE: Paginated Sessions List -->
      <aside
        class="flex min-h-0 flex-col border lg:col-span-3"
        style="
          background: var(--g-bg-surface);
          border-color: var(--g-border);
          border-radius: var(--g-radius-sm);
        "
      >
        <div class="min-h-0 flex-1 overflow-y-auto p-2">
          <div
            v-if="!loadingList && sessions.length === 0"
            class="px-3 py-10 text-center text-xs"
            style="color: var(--g-text-tertiary)"
          >
            {{ t('common.noData') }}
          </div>
          <TransitionGroup v-else name="g-list" tag="div" class="flex flex-col gap-1.5">
            <button
              v-for="s in sessions"
              :key="s.id"
              type="button"
              class="session-card g-transition w-full border p-2.5 text-left"
              :class="activeSessionId === s.id ? 'session-card-active' : ''"
              @click="selectSession(s.id)"
            >
              <div class="flex items-start justify-between gap-2">
                <span class="truncate text-xs font-medium" style="color: var(--g-text-primary)">
                  {{ s.title }}
                </span>
                <AppStatusBadge
                  :variant="statusVariant(s.status)"
                  :label="t(`workbench.status.${s.status}`)"
                  :pulse="s.status === 'running'"
                />
              </div>
              <div
                class="mt-1.5 flex items-center justify-between font-mono text-[10px]"
                style="color: var(--g-text-tertiary)"
              >
                <span>{{ s.category.toUpperCase() }} · {{ s.loop_engine }}</span>
                <span>step {{ s.current_step }}/{{ s.max_steps }}</span>
              </div>
              <div
                v-if="s.detected_flag"
                class="mt-1 truncate font-mono text-[10px] font-medium"
                style="color: var(--g-success)"
              >
                {{ s.detected_flag }}
              </div>
            </button>
          </TransitionGroup>
        </div>

        <div
          v-if="totalSessions > listPageSize"
          class="flex items-center justify-center border-t py-2"
          style="border-color: var(--g-border-subtle)"
        >
          <NPagination
            size="small"
            simple
            :page="listPage"
            :page-size="listPageSize"
            :item-count="totalSessions"
            @update:page="
              (p) => {
                listPage = p
                loadSessionList()
              }
            "
          />
        </div>
      </aside>

      <!-- CENTER PANE: Live Trace Stream + HITL Steering -->
      <section
        class="flex min-h-0 flex-col border lg:col-span-6"
        style="
          background: var(--g-bg-surface);
          border-color: var(--g-border);
          border-radius: var(--g-radius-sm);
        "
      >
        <AppEmptyState
          v-if="!activeSession"
          :title="t('workbench.noSessionTitle')"
          :description="t('workbench.noSessionDesc')"
        >
          <template #icon><NIcon :size="22" :component="TerminalOutlined" /></template>
          <template #action>
            <NButton type="primary" @click="createDrawerVisible = true">
              <template #icon><NIcon :component="AddOutlined" /></template>
              {{ t('workbench.newSession') }}
            </NButton>
          </template>
        </AppEmptyState>

        <template v-else>
          <!-- Session Command Bar -->
          <div
            class="flex flex-wrap items-center justify-between gap-2 border-b px-3.5 py-2.5"
            style="border-color: var(--g-border-subtle)"
          >
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <h2 class="m-0 truncate text-sm font-medium" style="color: var(--g-text-primary)">
                  {{ activeSession.title }}
                </h2>
                <AppStatusBadge
                  :variant="statusVariant(activeSession.status)"
                  :label="t(`workbench.status.${activeSession.status}`)"
                  :pulse="activeSession.status === 'running'"
                />
                <AppStatusBadge
                  :variant="wsConnected ? 'success' : 'neutral'"
                  :label="wsConnected ? t('workbench.wsConnected') : t('workbench.wsDisconnected')"
                />
              </div>
              <div class="mt-0.5 font-mono text-[11px]" style="color: var(--g-text-secondary)">
                {{ activeSession.model_name_snapshot }} · {{ activeSession.loop_engine }} ·
                {{ activeSession.sandbox_mode }}
              </div>
            </div>

            <div class="flex flex-wrap items-center gap-1.5">
              <NButton
                v-if="!isSessionLive"
                size="tiny"
                type="primary"
                :loading="actionBusy"
                @click="handleStart"
              >
                <template #icon><NIcon :component="PlayArrowOutlined" /></template>
                {{
                  activeSession.status === 'pending'
                    ? t('workbench.actions.start')
                    : t('workbench.actions.restart')
                }}
              </NButton>

              <NButton
                v-if="activeSession.status === 'running'"
                size="tiny"
                type="warning"
                secondary
                :loading="actionBusy"
                @click="handlePause"
              >
                <template #icon><NIcon :component="PauseOutlined" /></template>
                {{ t('workbench.actions.pause') }}
              </NButton>

              <NButton
                v-if="activeSession.status === 'paused'"
                size="tiny"
                type="primary"
                :loading="actionBusy"
                @click="handleResume"
              >
                <template #icon><NIcon :component="PlayArrowOutlined" /></template>
                {{ t('workbench.actions.resume') }}
              </NButton>

              <NButton
                v-if="isSessionLive"
                size="tiny"
                type="error"
                secondary
                :loading="actionBusy"
                @click="handleStop"
              >
                <template #icon><NIcon :component="StopOutlined" /></template>
                {{ t('workbench.actions.stop') }}
              </NButton>

              <NButton size="tiny" secondary @click="writeupModalVisible = true">
                <template #icon><NIcon :component="DescriptionOutlined" /></template>
                {{ t('workbench.actions.writeup') }}
              </NButton>

              <NTooltip>
                <template #trigger>
                  <NButton size="tiny" quaternary type="error" @click="handleDeleteSession">
                    <template #icon><NIcon :component="DeleteOutlineOutlined" /></template>
                  </NButton>
                </template>
                {{ t('workbench.actions.delete') }}
              </NTooltip>
            </div>
          </div>

          <!-- Telemetry & Filter Strip -->
          <div
            class="flex flex-wrap items-center justify-between gap-2 border-b px-3.5 py-2 text-[11px]"
            style="background: var(--g-bg-canvas); border-color: var(--g-border-subtle)"
          >
            <div class="flex flex-wrap items-center gap-3 font-mono" style="color: var(--g-text-secondary)">
              <span>
                {{ t('workbench.telemetry.step') }}:
                <strong style="color: var(--g-text-primary)">
                  {{ activeSession.current_step }}/{{ activeSession.max_steps }}
                </strong>
              </span>
              <span>
                {{ t('workbench.telemetry.tokens') }}:
                <strong style="color: var(--g-text-primary)">
                  {{ activeSession.total_tokens.toLocaleString() }}
                </strong>
              </span>
              <span>
                {{ t('workbench.telemetry.cost') }}:
                <strong style="color: var(--g-text-primary)">
                  ${{ activeSession.total_cost_usd.toFixed(4) }}
                </strong>
              </span>
            </div>

            <div class="flex items-center gap-1.5">
              <button
                v-for="f in (['all', 'thought', 'tool', 'plan', 'hitl'] as const)"
                :key="f"
                type="button"
                class="filter-chip g-transition px-2 py-0.5 text-[11px]"
                :class="traceFilter === f ? 'filter-chip-active' : ''"
                @click="traceFilter = f"
              >
                {{ t(`workbench.filters.${f}`) }}
              </button>
              <div class="ml-1 flex items-center gap-1">
                <NSwitch v-model:value="autoScroll" size="small" />
                <span style="color: var(--g-text-tertiary)">
                  {{ t('workbench.telemetry.autoScroll') }}
                </span>
              </div>
            </div>
          </div>

          <!-- Flag Banner if Captured -->
          <Transition name="g-fade">
            <div
              v-if="activeSession.detected_flag"
              class="flex items-center justify-between gap-2 border-b px-3.5 py-2"
              style="background: var(--g-success-soft); border-color: var(--g-success)"
            >
              <div class="flex min-w-0 items-center gap-2">
                <NIcon :size="16" :component="FlagOutlined" style="color: var(--g-success)" />
                <span class="text-xs font-medium" style="color: var(--g-success)">
                  {{ t('workbench.telemetry.flag') }}:
                </span>
                <code class="truncate font-mono text-xs font-semibold" style="color: var(--g-success)">
                  {{ activeSession.detected_flag }}
                </code>
              </div>
              <NButton size="tiny" secondary @click="copyFlag(activeSession.detected_flag)">
                <template #icon><NIcon :component="ContentCopyOutlined" /></template>
                {{ t('common.copy') }}
              </NButton>
            </div>
          </Transition>

          <!-- Scrollable Full-Chain Trace Feed -->
          <div
            ref="traceScrollEl"
            class="min-h-0 flex-1 overflow-y-auto p-3"
            style="background: var(--g-bg-canvas)"
          >
            <div
              v-if="filteredEvents.length === 0"
              class="py-12 text-center text-xs"
              style="color: var(--g-text-tertiary)"
            >
              {{ t('workbench.traceEmpty') }}
            </div>
            <TransitionGroup v-else name="g-list" tag="div" class="flex flex-col gap-2.5">
              <TraceEventCard v-for="ev in filteredEvents" :key="ev.seq_id" :event="ev" />
            </TransitionGroup>
          </div>

          <!-- Sticky HITL Steering Bar -->
          <div
            class="border-t p-3"
            style="background: var(--g-bg-surface); border-color: var(--g-border)"
          >
            <div class="flex gap-2">
              <NInput
                v-model:value="steeringText"
                type="textarea"
                :autosize="{ minRows: 1, maxRows: 3 }"
                :disabled="!isSessionLive"
                :placeholder="
                  isSessionLive
                    ? t('workbench.steering.placeholder')
                    : t('workbench.steering.inactiveHint')
                "
                @keydown.ctrl.enter.prevent="handleSendSteering"
              />
              <NButton
                type="primary"
                :loading="sendingSteer"
                :disabled="!isSessionLive || !steeringText.trim()"
                @click="handleSendSteering"
              >
                <template #icon><NIcon :component="SendOutlined" /></template>
                {{ t('workbench.steering.send') }}
              </NButton>
            </div>
            <div class="mt-1.5 flex items-center justify-between text-[11px]">
              <NCheckbox v-model:checked="resumeOnSteer" size="small">
                <span style="color: var(--g-text-secondary)">
                  {{ t('workbench.steering.resumeOnSend') }}
                </span>
              </NCheckbox>
              <span class="font-mono" style="color: var(--g-text-tertiary)">Ctrl + Enter</span>
            </div>
          </div>
        </template>
      </section>

      <!-- RIGHT PANE: Scratchpad / Brief & Direct Shell / Plan -->
      <aside
        class="flex min-h-0 flex-col border lg:col-span-3"
        style="
          background: var(--g-bg-surface);
          border-color: var(--g-border);
          border-radius: var(--g-radius-sm);
        "
      >
        <div
          v-if="!activeSession"
          class="flex flex-1 items-center justify-center p-6 text-center text-xs"
          style="color: var(--g-text-tertiary)"
        >
          {{ t('common.noData') }}
        </div>

        <NTabs
          v-else
          v-model:value="inspectorTab"
          type="line"
          size="small"
          class="flex min-h-0 flex-1 flex-col"
          pane-class="min-h-0 flex-1 overflow-y-auto p-3"
          :tabs-padding="12"
        >
          <!-- TAB 1: Working Scratchpad -->
          <NTabPane name="scratchpad" :tab="t('workbench.tabs.scratchpad')">
            <div class="flex flex-col gap-3">
              <p class="m-0 text-[11px] leading-relaxed" style="color: var(--g-text-secondary)">
                {{ t('workbench.scratchpad.desc') }}
              </p>

              <div>
                <label class="mb-1 block text-[11px] font-medium" style="color: var(--g-text-secondary)">
                  {{ t('workbench.scratchpad.endpoints') }}
                </label>
                <NInput
                  v-model:value="scratchpadForm.endpoints_text"
                  type="textarea"
                  :rows="2"
                  class="font-mono text-xs"
                />
              </div>

              <div>
                <label class="mb-1 block text-[11px] font-medium" style="color: var(--g-text-secondary)">
                  {{ t('workbench.scratchpad.clues') }}
                </label>
                <NInput
                  v-model:value="scratchpadForm.clues_text"
                  type="textarea"
                  :rows="3"
                  class="font-mono text-xs"
                />
              </div>

              <div>
                <label class="mb-1 block text-[11px] font-medium" style="color: var(--g-text-secondary)">
                  {{ t('workbench.scratchpad.vulns') }}
                </label>
                <NInput
                  v-model:value="scratchpadForm.vulns_text"
                  type="textarea"
                  :rows="2"
                  class="font-mono text-xs"
                />
              </div>

              <div>
                <label class="mb-1 block text-[11px] font-medium" style="color: var(--g-text-secondary)">
                  {{ t('workbench.scratchpad.ruledOut') }}
                </label>
                <NInput
                  v-model:value="scratchpadForm.ruled_out_text"
                  type="textarea"
                  :rows="2"
                  class="font-mono text-xs"
                />
              </div>

              <div>
                <label class="mb-1 block text-[11px] font-medium" style="color: var(--g-text-secondary)">
                  {{ t('workbench.scratchpad.notes') }}
                </label>
                <NInput
                  v-model:value="scratchpadForm.notes_md"
                  type="textarea"
                  :rows="3"
                  class="font-mono text-xs"
                />
              </div>

              <NButton
                type="primary"
                size="small"
                block
                :loading="savingScratchpad"
                @click="handleSaveScratchpad"
              >
                <template #icon><NIcon :component="SaveOutlined" /></template>
                {{ t('workbench.scratchpad.syncBtn') }}
              </NButton>
            </div>
          </NTabPane>

          <!-- TAB 2: Challenge Brief & Direct Sandbox Shell -->
          <NTabPane name="brief" :tab="t('workbench.tabs.briefShell')">
            <div class="flex flex-col gap-3 text-xs">
              <div
                class="border p-2.5"
                style="
                  background: var(--g-bg-canvas);
                  border-color: var(--g-border-subtle);
                  border-radius: var(--g-radius-xs);
                "
              >
                <div class="mb-1 text-[11px] font-semibold" style="color: var(--g-text-secondary)">
                  Description
                </div>
                <div class="whitespace-pre-wrap" style="color: var(--g-text-primary)">
                  {{ activeSession.description || '(None)' }}
                </div>
                <div
                  v-if="activeSession.attachments.length > 0"
                  class="mt-2 border-t pt-2 font-mono text-[11px]"
                  style="border-color: var(--g-border-subtle); color: var(--g-primary-text)"
                >
                  Attachments: {{ activeSession.attachments.join(', ') }}
                </div>
              </div>

              <div class="border-t pt-3" style="border-color: var(--g-border-subtle)">
                <div class="mb-1.5 text-xs font-medium" style="color: var(--g-text-primary)">
                  {{ t('workbench.shell.title') }}
                </div>
                <div class="flex gap-1.5">
                  <NInput
                    v-model:value="shellCmd"
                    size="small"
                    class="font-mono"
                    :disabled="!isSessionLive"
                    :placeholder="
                      isSessionLive
                        ? t('workbench.shell.placeholder')
                        : t('workbench.shell.inactiveHint')
                    "
                    @keydown.enter="handleExecShell"
                  />
                  <NButton
                    size="small"
                    type="primary"
                    :loading="shellRunning"
                    :disabled="!isSessionLive || !shellCmd.trim()"
                    @click="handleExecShell"
                  >
                    {{ t('workbench.shell.run') }}
                  </NButton>
                </div>
                <pre
                  v-if="shellOutput"
                  class="mt-2 mb-0 max-h-60 overflow-auto border p-2 whitespace-pre-wrap break-words font-mono text-[11px]"
                  style="
                    background: var(--g-bg-canvas);
                    border-color: var(--g-border);
                    color: var(--g-text-primary);
                    border-radius: var(--g-radius-xs);
                  "
                >{{ shellOutput }}</pre>
              </div>
            </div>
          </NTabPane>

          <!-- TAB 3: Live Plan Timeline -->
          <NTabPane name="plan" :tab="t('workbench.tabs.plan')">
            <div
              v-if="!latestPlan"
              class="py-8 text-center text-xs"
              style="color: var(--g-text-tertiary)"
            >
              {{ t('workbench.planEmpty') }}
            </div>
            <div v-else class="flex flex-col gap-2">
              <p
                v-if="latestPlan.strategy_overview"
                class="m-0 border p-2.5 text-xs"
                style="
                  background: var(--g-bg-canvas);
                  border-color: var(--g-border-subtle);
                  color: var(--g-text-secondary);
                  border-radius: var(--g-radius-xs);
                "
              >
                {{ latestPlan.strategy_overview }}
              </p>
              <div
                v-for="st in latestPlan.steps"
                :key="st.id"
                class="border p-2.5 text-xs"
                style="
                  background: var(--g-bg-surface);
                  border-color: var(--g-border);
                  border-radius: var(--g-radius-xs);
                "
              >
                <div class="flex items-center justify-between gap-2">
                  <span class="font-mono font-medium" style="color: var(--g-text-primary)">
                    #{{ st.id }} {{ st.title }}
                  </span>
                  <AppStatusBadge
                    :variant="
                      st.status === 'done'
                        ? 'success'
                        : st.status === 'in_progress'
                          ? 'info'
                          : st.status === 'failed'
                            ? 'error'
                            : 'neutral'
                    "
                    :label="st.status"
                  />
                </div>
                <div class="mt-1 text-[11px]" style="color: var(--g-text-secondary)">
                  {{ st.goal }}
                </div>
                <div
                  v-if="st.result_summary"
                  class="mt-1.5 border-t pt-1 font-mono text-[10px]"
                  style="border-color: var(--g-border-subtle); color: var(--g-text-tertiary)"
                >
                  {{ st.result_summary }}
                </div>
              </div>
            </div>
          </NTabPane>
        </NTabs>
      </aside>
    </div>

    <!-- Drawers & Modals -->
    <SessionCreateDrawer v-model:show="createDrawerVisible" @created="handleSessionCreated" />

    <WriteupModal
      v-if="activeSession"
      v-model:show="writeupModalVisible"
      :session-id="activeSession.id"
      :writeup="activeSession.writeup"
      @generated="onWriteupGenerated"
    />
  </div>
</template>

<style scoped>
.session-card {
  background: var(--g-bg-surface);
  border-color: var(--g-border-subtle);
  border-radius: var(--g-radius-xs);
  cursor: pointer;
}

.session-card:hover {
  background: var(--g-bg-hover);
  border-color: var(--g-border);
}

.session-card-active {
  background: var(--g-bg-active);
  border-color: var(--g-primary);
}

.filter-chip {
  border: 1px solid var(--g-border);
  background: var(--g-bg-surface);
  color: var(--g-text-secondary);
  border-radius: var(--g-radius-xs);
  cursor: pointer;
}

.filter-chip:hover {
  background: var(--g-bg-hover);
  color: var(--g-text-primary);
}

.filter-chip-active {
  border-color: var(--g-primary);
  background: var(--g-primary-soft);
  color: var(--g-primary-text);
  font-weight: 500;
}
</style>
