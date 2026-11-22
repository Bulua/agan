<script setup lang="ts">
import { computed, nextTick, onActivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NIcon,
  NInput,
  NModal,
  NPagination,
  NPopover,
  NTooltip,
  useDialog,
  useMessage,
} from 'naive-ui'
import {
  AddOutlined,
  ArrowDownwardOutlined,
  ArrowUpwardOutlined,
  AutoAwesomeOutlined,
  ChatBubbleOutlineOutlined,
  CheckOutlined,
  CloseOutlined,
  CompressOutlined,
  ContentCopyOutlined,
  DeleteOutlineOutlined,
  EditOutlined,
  KeyboardArrowDownOutlined,
  QueueOutlined,
  RefreshOutlined,
  SearchOutlined,
  SmartToyOutlined,
  StopOutlined,
  SubdirectoryArrowRightOutlined,
  TuneOutlined,
} from '@vicons/material'
import AppMarkdown from '@/components/common/AppMarkdown.vue'
import AppStatusBadge from '@/components/common/AppStatusBadge.vue'
import AgentActivityTimeline from '@/components/chat/AgentActivityTimeline.vue'
import { chatApi, type StreamEventCallbacks } from '@/api/chat'
import { agentsApi } from '@/api/agents'
import { extractApiError } from '@/api/client'
import type { AgentItem } from '@/types/agent'
import type { ChatConversationSummary, ChatMessage } from '@/types/chat'

const route = useRoute()
const router = useRouter()
const { t, tm } = useI18n()
const message = useMessage()
const dialog = useDialog()

// Agents (replaces raw model selection)
const agents = ref<AgentItem[]>([])
const selectedAgentId = ref<string | null>(null)

// Left conversation list
const loadingList = ref(false)
const conversations = ref<ChatConversationSummary[]>([])
const totalConversations = ref(0)
const listPage = ref(1)
const listPageSize = ref(20)
const searchQuery = ref('')
let searchTimer: number | undefined

// Active conversation & messages
const activeConvId = ref<string | null>(null)
const activeTitle = ref<string>('')
const systemPrompt = ref<string>('')
const activeTotalTokens = ref<number>(0)
const contextTokensEstimate = ref<number>(0)
const contextWindow = ref<number>(128000)
const compacting = ref<boolean>(false)
const expandedCompactIds = ref<Record<string, boolean>>({})
const messages = ref<ChatMessage[]>([])
const loadingMessages = ref(false)

interface QueuedPrompt {
  id: string
  content: string
}

// Streaming, queue & composer
const draftInput = ref('')
const streaming = ref(false)
const steeringBusy = ref(false)
const queuedByConv = ref<Record<string, QueuedPrompt[]>>({})
const scrollContainer = ref<HTMLElement | null>(null)
const showScrollBottomBtn = ref(false)
let isNearBottom = true
let abortController: AbortController | null = null

const activeQueue = computed<QueuedPrompt[]>(() => {
  const key = activeConvId.value || '__draft__'
  return queuedByConv.value[key] || []
})

// Rename modal
const renameModalShow = ref(false)
const renameTargetId = ref<string | null>(null)
const renameDraft = ref('')

const agentPickerShow = ref(false)

const selectedAgent = computed(
  () => agents.value.find((a) => a.id === selectedAgentId.value) || null,
)

const starterCards = computed(() => {
  const raw = tm('chat.starters') as Array<{ title: string; prompt: string }>
  return Array.isArray(raw) ? raw : []
})

function handleMessagesScroll(): void {
  const el = scrollContainer.value
  if (!el) return
  const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
  isNearBottom = distFromBottom < 80
  showScrollBottomBtn.value = distFromBottom > 140
}

async function scrollToBottom(force = false): Promise<void> {
  if (!force && !isNearBottom) return
  await nextTick()
  if (scrollContainer.value) {
    scrollContainer.value.scrollTop = scrollContainer.value.scrollHeight
    isNearBottom = true
    showScrollBottomBtn.value = false
  }
}

async function loadAgents(): Promise<void> {
  try {
    const res = await agentsApi.list({ is_active: true, page: 1, page_size: 100 })
    agents.value = res.items
    const queryAgentId = typeof route.query.agent === 'string' ? route.query.agent : null
    if (queryAgentId && res.items.some((a) => a.id === queryAgentId)) {
      selectedAgentId.value = queryAgentId
    } else if (!selectedAgentId.value && res.items.length > 0) {
      const def = res.items.find((a) => a.is_default) || res.items[0]
      selectedAgentId.value = def.id
    }
  } catch (err) {
    message.error(extractApiError(err))
  }
}

async function loadConversations(): Promise<void> {
  loadingList.value = true
  try {
    const res = await chatApi.list({
      search: searchQuery.value.trim() || undefined,
      page: listPage.value,
      page_size: listPageSize.value,
    })
    conversations.value = res.items
    totalConversations.value = res.total
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    loadingList.value = false
  }
}

const ACTIVE_CHAT_STORAGE_KEY = 'agan_active_chat_id'
let bgStatusPollTimer: number | undefined

function persistActiveConvId(id: string | null): void {
  try {
    if (id) sessionStorage.setItem(ACTIVE_CHAT_STORAGE_KEY, id)
    else sessionStorage.removeItem(ACTIVE_CHAT_STORAGE_KEY)
  } catch {
    // ignore storage restrictions
  }
}

function detachLocalStream(): void {
  if (abortController) {
    abortController.abort()
    abortController = null
  }
  streaming.value = false
}

async function stopStreaming(): Promise<void> {
  const convId = activeConvId.value
  if (convId && streaming.value) {
    try {
      await chatApi.stopRun(convId)
    } catch {
      // fallback to local detach
    }
  }
  detachLocalStream()
  await loadConversations()
}

function enqueueDraft(overrideText?: string): void {
  const text = (overrideText ?? draftInput.value).trim()
  if (!text) return
  const key = activeConvId.value || '__draft__'
  if (!queuedByConv.value[key]) queuedByConv.value[key] = []
  queuedByConv.value[key].push({ id: `q-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`, content: text })
  if (overrideText === undefined) {
    draftInput.value = ''
  }
}

function removeQueuedItem(itemId: string): void {
  const key = activeConvId.value || '__draft__'
  const list = queuedByConv.value[key]
  if (!list) return
  queuedByConv.value[key] = list.filter((x) => x.id !== itemId)
}

async function steerActiveRun(overrideText?: string, removeQueueId?: string): Promise<void> {
  const convId = activeConvId.value
  const text = (overrideText ?? draftInput.value).trim()
  if (!convId || !text || !streaming.value || steeringBusy.value) return

  steeringBusy.value = true
  try {
    await chatApi.steerRun(convId, text)
    if (overrideText === undefined) {
      draftInput.value = ''
    }
    if (removeQueueId) {
      removeQueuedItem(removeQueueId)
    }
    message.success(t('chat.steeredToast'))
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    steeringBusy.value = false
  }
}

function popNextQueuedPrompt(convId: string): QueuedPrompt | null {
  const list = queuedByConv.value[convId]
  if (!list || list.length === 0) return null
  const [first, ...rest] = list
  queuedByConv.value[convId] = rest
  return first
}

function buildStreamCallbacks(
  asstTracker: { id: string },
  targetConvId: string,
  onFinishedFlag?: (stopped: boolean) => void,
): StreamEventCallbacks {
  const pushPendingSteerBubble = (content: string): void => {
    const clean = content.trim()
    if (!clean) return
    messages.value.push({
      id: `tmp-steer-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      conversation_id: targetConvId,
      role: 'user',
      content: clean,
      reasoning_content: '',
      tool_calls_json: [],
      model_id_used: '',
      prompt_tokens: 0,
      completion_tokens: 0,
      cost_usd: 0,
      latency_ms: 0,
      created_at: new Date().toISOString(),
    })
  }

  return {
    onMeta: (meta) => {
      if (activeConvId.value !== targetConvId) return
      if (meta.title) activeTitle.value = meta.title
      if (meta.started_at_ms) {
        const target = messages.value.find((m) => m.id === asstTracker.id)
        if (target) target.started_at_ms = meta.started_at_ms
      }
      if (meta.user_message) {
        const uIdx = messages.value.findIndex((m) => m.id.startsWith('tmp-user-'))
        if (uIdx !== -1) messages.value[uIdx] = meta.user_message
      }
      if (!conversations.value.some((c) => c.id === targetConvId)) {
        void loadConversations()
      }
    },
    onSnapshot: (snap) => {
      if (activeConvId.value !== targetConvId) return
      if (snap.title) activeTitle.value = snap.title
      const target = messages.value.find((m) => m.id === asstTracker.id)
      if (target) {
        target.content = snap.content || ''
        target.reasoning_content = snap.reasoning_content || ''
        target.tool_calls_json = Array.isArray(snap.tool_calls)
          ? snap.tool_calls.map((tc) => ({ ...tc }))
          : []
        if (snap.started_at_ms) target.started_at_ms = snap.started_at_ms
      }
      if (Array.isArray(snap.pending_steerings)) {
        for (const text of snap.pending_steerings) {
          pushPendingSteerBubble(text)
        }
      }
      scrollToBottom(true)
    },
    onReasoningDelta: (delta) => {
      if (activeConvId.value !== targetConvId) return
      const target = messages.value.find((m) => m.id === asstTracker.id)
      if (target) target.reasoning_content += delta
      scrollToBottom()
    },
    onToolCall: (tool) => {
      if (activeConvId.value !== targetConvId) return
      const target = messages.value.find((m) => m.id === asstTracker.id)
      if (target) {
        const idx = target.tool_calls_json.findIndex((x) => x.id === tool.id)
        if (idx !== -1) target.tool_calls_json[idx] = { ...tool }
        else target.tool_calls_json.push({ ...tool })
      }
      scrollToBottom()
    },
    onToolResult: (tool) => {
      if (activeConvId.value !== targetConvId) return
      const target = messages.value.find((m) => m.id === asstTracker.id)
      if (target) {
        const idx = target.tool_calls_json.findIndex((x) => x.id === tool.id)
        if (idx !== -1) target.tool_calls_json[idx] = { ...tool }
        else target.tool_calls_json.push({ ...tool })
      }
      scrollToBottom()
    },
    onContentDelta: (delta) => {
      if (activeConvId.value !== targetConvId) return
      const target = messages.value.find((m) => m.id === asstTracker.id)
      if (target) target.content += delta
      scrollToBottom()
    },
    onSteeringPending: (content) => {
      if (activeConvId.value !== targetConvId) return
      pushPendingSteerBubble(content)
      scrollToBottom(true)
    },
    onSegmentDone: (seg) => {
      if (activeConvId.value !== targetConvId) return
      const idx = messages.value.findIndex((m) => m.id === asstTracker.id)
      if (idx !== -1) {
        if (seg.assistant_message) {
          messages.value[idx] = { ...seg.assistant_message, streaming: false }
          activeTotalTokens.value +=
            (seg.assistant_message.prompt_tokens || 0) +
            (seg.assistant_message.completion_tokens || 0)
        } else {
          messages.value.splice(idx, 1)
        }
      }
    },
    onSteeringCommitted: (payload) => {
      if (activeConvId.value !== targetConvId) return
      // Replace temporary pending steer user bubbles with persisted chronological User messages
      messages.value = messages.value.filter((m) => !m.id.startsWith('tmp-steer-'))
      for (const uMsg of payload.user_messages || []) {
        messages.value.push(uMsg)
      }
      // Open a fresh live streaming Assistant message right below the User steering bubble
      const nextAsstId = `tmp-asst-seg-${Date.now()}`
      asstTracker.id = nextAsstId
      messages.value.push({
        id: nextAsstId,
        conversation_id: targetConvId,
        role: 'assistant',
        content: '',
        reasoning_content: '',
        tool_calls_json: [],
        model_id_used: '',
        prompt_tokens: 0,
        completion_tokens: 0,
        cost_usd: 0,
        latency_ms: 0,
        created_at: new Date().toISOString(),
        streaming: true,
        started_at_ms: payload.started_at_ms || Date.now(),
      })
      scrollToBottom(true)
    },
    onContextCompacted: (stats) => {
      if (activeConvId.value !== targetConvId) return
      if (stats && typeof stats.tokens_after === 'number') {
        contextTokensEstimate.value = stats.tokens_after
      }
    },
    onSubagentUpdate: (sub) => {
      if (activeConvId.value !== targetConvId || !sub) return
      const target = messages.value.find((m) => m.id === asstTracker.id)
      if (!target) return
      // Sync live progress onto matching running spawn_subagent / send_agent_message record
      const match = [...target.tool_calls_json]
        .reverse()
        .find(
          (tc) =>
            (tc.tool_name === 'spawn_subagent' || tc.tool_name === 'send_agent_message') &&
            ((tc.metadata?.subagent_id && tc.metadata.subagent_id === sub.subagent_id) ||
              (tc.status === 'running' &&
                String(tc.arguments?.role || '').trim() === sub.role)),
        )
      if (match) {
        match.metadata = {
          ...(match.metadata || {}),
          subagent_id: sub.subagent_id,
          role: sub.role,
          status: sub.status,
          current_action: sub.current_action,
          step_count: sub.step_count,
          subagent_steps: sub.steps,
          duration_ms: sub.duration_ms,
        }
      }
    },
    onTitleUpdated: (payload) => {
      if (!payload?.title) return
      if (activeConvId.value === payload.conversation_id) {
        activeTitle.value = payload.title
      }
      const row = conversations.value.find((c) => c.id === payload.conversation_id)
      if (row) {
        row.title = payload.title
      }
    },
    onDone: (done) => {
      onFinishedFlag?.(Boolean(done.stopped))
      if (activeConvId.value !== targetConvId) return
      if (done.title) activeTitle.value = done.title
      const idx = messages.value.findIndex((m) => m.id === asstTracker.id)
      if (idx !== -1) {
        if (done.assistant_message) {
          messages.value[idx] = { ...done.assistant_message, streaming: false }
          activeTotalTokens.value +=
            (done.assistant_message.prompt_tokens || 0) +
            (done.assistant_message.completion_tokens || 0)
          if (done.assistant_message.prompt_tokens > 0) {
            contextTokensEstimate.value =
              done.assistant_message.prompt_tokens +
              (done.assistant_message.completion_tokens || 0)
          }
        } else if (done.stopped && !messages.value[idx].content && !messages.value[idx].reasoning_content) {
          messages.value.splice(idx, 1)
        } else {
          messages.value[idx].streaming = false
        }
      }
    },
    onError: (errMsg) => {
      if (activeConvId.value !== targetConvId) return
      const target = messages.value.find((m) => m.id === asstTracker.id)
      if (target && !target.content) {
        target.content = `**Error:** ${errMsg}`
      }
      message.error(errMsg)
    },
  }
}

async function attachToRunningConversation(convId: string): Promise<void> {
  const asstTracker = { id: `tmp-attach-${Date.now()}` }
  const assistantMsg: ChatMessage = {
    id: asstTracker.id,
    conversation_id: convId,
    role: 'assistant',
    content: '',
    reasoning_content: '',
    tool_calls_json: [],
    model_id_used: '',
    prompt_tokens: 0,
    completion_tokens: 0,
    cost_usd: 0,
    latency_ms: 0,
    created_at: new Date().toISOString(),
    streaming: true,
    started_at_ms: Date.now(),
  }
  messages.value.push(assistantMsg)
  await scrollToBottom(true)

  const myController = new AbortController()
  abortController = myController
  streaming.value = true
  let finishedCleanly = false

  try {
    await chatApi.attachStream(
      convId,
      buildStreamCallbacks(asstTracker, convId, (stopped) => {
        finishedCleanly = !stopped
      }),
      myController.signal,
    )
  } catch (err: any) {
    if (err?.name !== 'AbortError' && activeConvId.value === convId) {
      message.error(extractApiError(err, t('common.operationFailed')))
    }
  } finally {
    let nextQueued: QueuedPrompt | null = null
    if (abortController === myController) {
      const target = messages.value.find((m) => m.id === asstTracker.id)
      if (target) target.streaming = false
      streaming.value = false
      abortController = null
      if (finishedCleanly && activeConvId.value === convId) {
        nextQueued = popNextQueuedPrompt(convId)
      }
    }
    await loadConversations()
    if (nextQueued && activeConvId.value === convId) {
      void sendPrompt(nextQueued.content)
    }
  }
}

function startNewDraft(): void {
  // Detach local listener only — any running background conversation continues uninterrupted
  detachLocalStream()
  activeConvId.value = null
  persistActiveConvId(null)
  activeTitle.value = ''
  systemPrompt.value = ''
  activeTotalTokens.value = 0
  contextTokensEstimate.value = 0
  contextWindow.value = 128000
  messages.value = []
}

async function selectConversation(id: string, forceReload = false): Promise<void> {
  if (!forceReload && activeConvId.value === id && !loadingMessages.value) return
  // Detach local SSE listener only (background task in previous conversation keeps running!)
  detachLocalStream()
  activeConvId.value = id
  persistActiveConvId(id)
  loadingMessages.value = true
  let shouldAttach = false
  try {
    const detail = await chatApi.get(id)
    if (activeConvId.value !== id) return
    activeTitle.value = detail.title
    systemPrompt.value = detail.system_prompt || ''
    activeTotalTokens.value = detail.total_tokens
    contextTokensEstimate.value = detail.context_tokens_estimate || 0
    contextWindow.value = detail.context_window || 128000
    if (detail.agent_id && agents.value.some((a) => a.id === detail.agent_id)) {
      selectedAgentId.value = detail.agent_id
    }
    messages.value = detail.messages
    shouldAttach = Boolean(detail.is_running)
    await scrollToBottom(true)
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    loadingMessages.value = false
  }
  if (shouldAttach && activeConvId.value === id) {
    void attachToRunningConversation(id)
  }
}

function formatKTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k`
  return String(n)
}

function getCompactMeta(msg: ChatMessage): {
  tokens_saved: number
  summarized_messages: number
  stage: string
} {
  const raw = (msg.tool_calls_json?.[0] as Record<string, any> | undefined) || {}
  return {
    tokens_saved: Number(raw.tokens_saved || 0),
    summarized_messages: Number(raw.summarized_messages || 0),
    stage: String(raw.stage || 'manual'),
  }
}

function toggleCompactSummary(id: string): void {
  expandedCompactIds.value[id] = !expandedCompactIds.value[id]
}

async function handleCompact(instructions = ''): Promise<void> {
  if (streaming.value || compacting.value) return
  if (!activeConvId.value || messages.value.length < 2) {
    message.warning(t('chat.compactNeedConv'))
    return
  }
  compacting.value = true
  try {
    const res = await chatApi.compact(activeConvId.value, {
      instructions: instructions.trim() || undefined,
    })
    contextTokensEstimate.value = res.context_tokens_estimate
    contextWindow.value = res.context_window
    if (res.compact_message) {
      messages.value.push(res.compact_message)
      await scrollToBottom(true)
    }
    message.success(
      t('chat.compactSuccess', {
        saved: res.tokens_saved.toLocaleString(),
        count: res.summarized_messages,
      }),
    )
  } catch (err) {
    message.error(extractApiError(err, t('common.operationFailed')))
  } finally {
    compacting.value = false
  }
}

async function saveSystemPrompt(): Promise<void> {
  if (!activeConvId.value) return
  try {
    await chatApi.update(activeConvId.value, { system_prompt: systemPrompt.value })
    message.success(t('common.saveSuccess'))
  } catch (err) {
    message.error(extractApiError(err))
  }
}

async function handleAgentChange(agentId: string): Promise<void> {
  agentPickerShow.value = false
  selectedAgentId.value = agentId
  if (activeConvId.value) {
    try {
      await chatApi.update(activeConvId.value, { agent_id: agentId })
    } catch {
      // non-fatal; also passed on next message send
    }
  }
}

function openRename(conv: { id: string; title: string }): void {
  renameTargetId.value = conv.id
  renameDraft.value = conv.title
  renameModalShow.value = true
}

async function confirmRename(): Promise<void> {
  if (!renameTargetId.value || !renameDraft.value.trim()) return
  try {
    const updated = await chatApi.update(renameTargetId.value, { title: renameDraft.value.trim() })
    if (activeConvId.value === updated.id) {
      activeTitle.value = updated.title
    }
    renameModalShow.value = false
    await loadConversations()
  } catch (err) {
    message.error(extractApiError(err))
  }
}

function confirmDelete(conv: ChatConversationSummary): void {
  dialog.warning({
    title: t('common.delete'),
    content: t('chat.deleteConfirm', { title: conv.title }),
    positiveText: t('common.confirmDelete'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      try {
        await chatApi.remove(conv.id)
        if (activeConvId.value === conv.id) {
          startNewDraft()
        }
        message.success(t('common.deleteSuccess'))
        await loadConversations()
      } catch (err) {
        message.error(extractApiError(err))
      }
    },
  })
}

async function sendPrompt(overrideText?: string, regenerate = false): Promise<void> {
  if (streaming.value) return
  if (!selectedAgentId.value) {
    message.warning(t('chat.noAgentWarning'))
    return
  }

  const text = (overrideText ?? draftInput.value).trim()
  if (!regenerate && !text) return

  // Slash command: `/compact` or `/compact <focus instructions>`
  if (!regenerate && /^\/compact(\s|$)/i.test(text)) {
    const instructions = text.replace(/^\/compact\s*/i, '').trim()
    draftInput.value = ''
    await handleCompact(instructions)
    return
  }

  let convId = activeConvId.value
  if (!convId) {
    try {
      const created = await chatApi.create({
        title: 'New Chat',
        agent_id: selectedAgentId.value,
        system_prompt: systemPrompt.value,
      })
      convId = created.id
      activeConvId.value = created.id
      persistActiveConvId(created.id)
      activeTitle.value = created.title
    } catch (err) {
      message.error(extractApiError(err, t('common.operationFailed')))
      return
    }
  }

  if (!regenerate) {
    draftInput.value = ''
    const tempUserMsg: ChatMessage = {
      id: `tmp-user-${Date.now()}`,
      conversation_id: convId,
      role: 'user',
      content: text,
      reasoning_content: '',
      tool_calls_json: [],
      model_id_used: '',
      prompt_tokens: 0,
      completion_tokens: 0,
      cost_usd: 0,
      latency_ms: 0,
      created_at: new Date().toISOString(),
    }
    messages.value.push(tempUserMsg)
  } else if (
    messages.value.length > 0 &&
    messages.value[messages.value.length - 1].role === 'assistant'
  ) {
    messages.value.pop()
  }

  const asstTracker = { id: `tmp-asst-${Date.now()}` }
  const assistantMsg: ChatMessage = {
    id: asstTracker.id,
    conversation_id: convId,
    role: 'assistant',
    content: '',
    reasoning_content: '',
    tool_calls_json: [],
    model_id_used: '',
    prompt_tokens: 0,
    completion_tokens: 0,
    cost_usd: 0,
    latency_ms: 0,
    created_at: new Date().toISOString(),
    streaming: true,
    started_at_ms: Date.now(),
  }
  messages.value.push(assistantMsg)
  await scrollToBottom(true)

  const myController = new AbortController()
  abortController = myController
  streaming.value = true
  let finishedCleanly = false
  // Refresh sidebar quickly so the new conversation / running indicator appears right away
  void loadConversations()

  try {
    await chatApi.streamMessage(
      convId,
      {
        content: regenerate ? '' : text,
        agent_id: selectedAgentId.value,
        system_prompt: systemPrompt.value,
        regenerate,
      },
      buildStreamCallbacks(asstTracker, convId, (stopped) => {
        finishedCleanly = !stopped
      }),
      myController.signal,
    )
  } catch (err: any) {
    if (err?.name !== 'AbortError' && activeConvId.value === convId) {
      const errText = extractApiError(err, t('common.operationFailed'))
      const target = messages.value.find((m) => m.id === asstTracker.id)
      if (target && !target.content) target.content = `**Error:** ${errText}`
      message.error(errText)
    }
  } finally {
    let nextQueued: QueuedPrompt | null = null
    if (abortController === myController) {
      const target = messages.value.find((m) => m.id === asstTracker.id)
      if (target) target.streaming = false
      streaming.value = false
      abortController = null
      if (finishedCleanly && activeConvId.value === convId) {
        nextQueued = popNextQueuedPrompt(convId)
      }
    }
    await loadConversations()
    if (nextQueued && activeConvId.value === convId) {
      void sendPrompt(nextQueued.content)
    }
  }
}

function handleComposerKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    if (streaming.value) {
      if (e.ctrlKey || e.metaKey) {
        void steerActiveRun()
      } else {
        enqueueDraft()
      }
    } else {
      void sendPrompt()
    }
  }
}

async function copyMessage(content: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(content)
    message.success(t('common.copied'))
  } catch {
    message.warning(t('common.operationFailed'))
  }
}

watch(searchQuery, () => {
  if (searchTimer) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => {
    listPage.value = 1
    loadConversations()
  }, 240)
})

onMounted(async () => {
  await Promise.all([loadAgents(), loadConversations()])
  // Restore active or currently-running conversation seamlessly after a full browser refresh (F5)
  let savedId: string | null = null
  try {
    savedId = sessionStorage.getItem(ACTIVE_CHAT_STORAGE_KEY)
  } catch {
    savedId = null
  }
  const targetConv =
    (savedId && conversations.value.find((c) => c.id === savedId)) ||
    conversations.value.find((c) => c.is_running)
  if (targetConv) {
    await selectConversation(targetConv.id, true)
  }

  bgStatusPollTimer = window.setInterval(() => {
    if (conversations.value.some((c) => c.is_running)) {
      void loadConversations()
    }
  }, 4000)
})

onActivated(async () => {
  // Keep current chat state & live stream intact while refreshing agents + sidebar running badges
  await Promise.all([loadAgents(), loadConversations()])
})

onUnmounted(() => {
  if (bgStatusPollTimer !== undefined) {
    window.clearInterval(bgStatusPollTimer)
    bgStatusPollTimer = undefined
  }
  // Detach local listener only — never cancel backend tasks on unmount
  detachLocalStream()
})
</script>

<template>
  <div
    class="g-transition flex h-[calc(100vh-100px)] min-h-[540px] overflow-hidden border"
    style="
      background: var(--g-bg-surface);
      border-color: var(--g-border);
      border-radius: var(--g-radius-sm);
    "
  >
    <!-- LEFT SIDEBAR: Conversation List (Claude Web style) -->
    <aside
      class="flex w-[268px] shrink-0 flex-col border-r"
      style="background: var(--g-bg-canvas); border-color: var(--g-border)"
    >
      <div class="flex flex-col gap-2 border-b p-3" style="border-color: var(--g-border-subtle)">
        <NButton type="primary" block @click="startNewDraft">
          <template #icon><NIcon :component="AddOutlined" /></template>
          {{ t('chat.newChat') }}
        </NButton>
        <NInput
          v-model:value="searchQuery"
          size="small"
          clearable
          :placeholder="t('chat.searchPlaceholder')"
        >
          <template #prefix><NIcon :component="SearchOutlined" /></template>
        </NInput>
      </div>

      <div class="min-h-0 flex-1 overflow-y-auto p-2">
        <div
          v-if="!loadingList && conversations.length === 0"
          class="px-3 py-10 text-center text-xs"
          style="color: var(--g-text-tertiary)"
        >
          {{ t('chat.emptyList') }}
        </div>

        <TransitionGroup v-else name="g-list" tag="div" class="flex flex-col gap-0.5">
          <div
            v-for="conv in conversations"
            :key="conv.id"
            class="conv-item group g-transition flex items-center justify-between gap-1.5 px-2.5 py-2"
            :class="activeConvId === conv.id ? 'conv-item-active' : ''"
            @click="selectConversation(conv.id)"
          >
            <div class="flex min-w-0 flex-1 items-center gap-2">
              <span
                v-if="conv.is_running || (conv.id === activeConvId && streaming)"
                class="inline-block h-2 w-2 shrink-0 animate-pulse"
                style="background: var(--g-primary); border-radius: 50%"
              />
              <NIcon
                v-else
                :size="15"
                :component="ChatBubbleOutlineOutlined"
                class="shrink-0"
                style="color: var(--g-text-tertiary)"
              />
              <div class="min-w-0 flex-1">
                <div
                  class="truncate text-[13px]"
                  :class="activeConvId === conv.id ? 'font-medium' : ''"
                  style="color: var(--g-text-primary)"
                >
                  {{ conv.title }}
                </div>
                <div class="truncate font-mono text-[10px]" style="color: var(--g-text-tertiary)">
                  {{ conv.agent_name_snapshot || conv.model_name_snapshot || 'Agent' }} ·
                  {{ conv.message_count }} msg
                </div>
              </div>
            </div>

            <div
              class="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100"
              @click.stop
            >
              <NButton size="tiny" quaternary :title="t('chat.rename')" @click="openRename(conv)">
                <template #icon><NIcon :size="14" :component="EditOutlined" /></template>
              </NButton>
              <NButton
                size="tiny"
                quaternary
                type="error"
                :title="t('common.delete')"
                @click="confirmDelete(conv)"
              >
                <template #icon><NIcon :size="14" :component="DeleteOutlineOutlined" /></template>
              </NButton>
            </div>
          </div>
        </TransitionGroup>
      </div>

      <div
        v-if="totalConversations > listPageSize"
        class="flex items-center justify-center border-t py-2"
        style="border-color: var(--g-border-subtle)"
      >
        <NPagination
          size="small"
          simple
          :page="listPage"
          :page-size="listPageSize"
          :item-count="totalConversations"
          @update:page="
            (p) => {
              listPage = p
              loadConversations()
            }
          "
        />
      </div>
    </aside>

    <!-- RIGHT MAIN PANEL: Clean Centered Chat Stream + Agent Composer -->
    <section class="flex min-w-0 flex-1 flex-col" style="background: var(--g-bg-surface)">
      <!-- Minimal Top Conversation Bar -->
      <header
        class="flex h-12 shrink-0 items-center justify-between gap-3 border-b px-5"
        style="border-color: var(--g-border-subtle)"
      >
        <div class="flex min-w-0 items-center gap-2">
          <h2 class="m-0 truncate text-sm font-medium" style="color: var(--g-text-primary)">
            {{ activeTitle || t('chat.newChat') }}
          </h2>
          <NButton
            v-if="activeConvId"
            size="tiny"
            quaternary
            @click="openRename({ id: activeConvId, title: activeTitle })"
          >
            <template #icon><NIcon :size="14" :component="EditOutlined" /></template>
          </NButton>
          <AppStatusBadge
            v-if="selectedAgent"
            variant="info"
            :label="`${selectedAgent.name} (${selectedAgent.loop_engine})`"
            :dot="false"
          />
        </div>

        <div class="flex items-center gap-2">
          <span
            v-if="contextTokensEstimate > 0 || activeTotalTokens > 0"
            class="font-mono text-[11px]"
            style="color: var(--g-text-tertiary)"
            :title="`~${contextTokensEstimate.toLocaleString()} / ${contextWindow.toLocaleString()} context tokens`"
          >
            {{ formatKTokens(contextTokensEstimate || activeTotalTokens) }} /
            {{ formatKTokens(contextWindow) }}
          </span>

          <NTooltip v-if="activeConvId && messages.length >= 2">
            <template #trigger>
              <NButton
                size="tiny"
                secondary
                :loading="compacting"
                :disabled="streaming"
                @click="handleCompact()"
              >
                <template #icon><NIcon :component="CompressOutlined" /></template>
                {{ t('chat.compactBtn') }}
              </NButton>
            </template>
            {{ t('chat.compactTooltip') }}
          </NTooltip>

          <NPopover trigger="click" placement="bottom-end" :width="380">
            <template #trigger>
              <NButton size="tiny" secondary>
                <template #icon><NIcon :component="TuneOutlined" /></template>
                {{ t('chat.systemPrompt') }}
              </NButton>
            </template>
            <div class="flex flex-col gap-2.5 p-1">
              <div class="text-xs font-medium" style="color: var(--g-text-primary)">
                {{ t('chat.systemPrompt') }}
              </div>
              <NInput
                v-model:value="systemPrompt"
                type="textarea"
                :rows="4"
                :placeholder="t('chat.systemPromptPh')"
              />
              <div class="flex justify-end">
                <NButton v-if="activeConvId" size="tiny" type="primary" @click="saveSystemPrompt">
                  {{ t('common.save') }}
                </NButton>
              </div>
            </div>
          </NPopover>
        </div>
      </header>

      <!-- No Agent Warning Banner -->
      <div v-if="agents.length === 0" class="px-5 pt-3">
        <NAlert type="warning">
          <div class="flex items-center justify-between gap-3">
            <span class="text-xs">{{ t('chat.noAgentWarning') }}</span>
            <NButton size="tiny" type="primary" @click="router.push('/studio/agents')">
              {{ t('chat.goAgents') }}
            </NButton>
          </div>
        </NAlert>
      </div>

      <!-- Message Area -->
      <div class="relative min-h-0 flex-1">
        <div
          ref="scrollContainer"
          class="h-full overflow-y-auto px-4 py-6"
          @scroll="handleMessagesScroll"
        >
          <div class="mx-auto flex w-full max-w-[780px] flex-col gap-5">
          <!-- Claude-style Welcome Screen -->
          <div
            v-if="!loadingMessages && messages.length === 0"
            class="flex flex-col items-center justify-center py-14 text-center"
          >
            <div
              class="mb-4 flex h-11 w-11 items-center justify-center border"
              style="
                background: var(--g-primary-soft);
                border-color: var(--g-border);
                color: var(--g-primary-text);
                border-radius: var(--g-radius-sm);
              "
            >
              <NIcon :size="22" :component="AutoAwesomeOutlined" />
            </div>
            <h1 class="m-0 text-xl font-medium tracking-tight" style="color: var(--g-text-primary)">
              {{ t('chat.welcomeTitle') }}
            </h1>
            <p
              class="mt-2 mb-7 max-w-lg text-xs leading-relaxed"
              style="color: var(--g-text-secondary)"
            >
              {{ t('chat.welcomeSubtitle') }}
            </p>

            <div class="grid w-full grid-cols-1 gap-2.5 sm:grid-cols-3">
              <button
                v-for="(item, idx) in starterCards"
                :key="idx"
                type="button"
                class="starter-card g-transition border p-3.5 text-left"
                @click="sendPrompt(item.prompt)"
              >
                <div class="text-xs font-medium" style="color: var(--g-text-primary)">
                  {{ item.title }}
                </div>
                <div
                  class="mt-1.5 line-clamp-3 text-[11px] leading-relaxed"
                  style="color: var(--g-text-secondary)"
                >
                  {{ item.prompt }}
                </div>
              </button>
            </div>
          </div>

          <!-- Messages Stream -->
          <template v-else>
            <div
              v-for="(msg, index) in messages"
              :key="msg.id"
              class="flex flex-col"
              :class="
                msg.role === 'user'
                  ? 'items-end'
                  : msg.role === 'compact'
                    ? 'items-center'
                    : 'items-start'
              "
            >
              <!-- Context Compaction Divider Marker -->
              <div v-if="msg.role === 'compact'" class="my-1 flex w-full flex-col items-center">
                <div class="flex w-full items-center gap-3">
                  <div class="h-px flex-1" style="background: var(--g-border-subtle)" />
                  <button
                    type="button"
                    class="g-transition inline-flex items-center gap-1.5 border px-2.5 py-1 text-[11px] cursor-pointer"
                    style="
                      background: var(--g-bg-canvas);
                      border-color: var(--g-border);
                      color: var(--g-text-secondary);
                      border-radius: var(--g-radius-xs);
                    "
                    @click="toggleCompactSummary(msg.id)"
                  >
                    <NIcon :size="13" :component="CompressOutlined" style="color: var(--g-success)" />
                    <span>
                      {{
                        t('chat.compactBanner', {
                          saved: getCompactMeta(msg).tokens_saved.toLocaleString(),
                          count: getCompactMeta(msg).summarized_messages,
                        })
                      }}
                    </span>
                    <span class="font-medium" style="color: var(--g-primary-text)">
                      · {{ t('chat.compactViewSummary') }}
                      {{ expandedCompactIds[msg.id] ? '▴' : '▾' }}
                    </span>
                  </button>
                  <div class="h-px flex-1" style="background: var(--g-border-subtle)" />
                </div>

                <div
                  v-if="expandedCompactIds[msg.id]"
                  class="mt-2 w-full border p-3.5 text-xs"
                  style="
                    background: var(--g-bg-canvas);
                    border-color: var(--g-border-subtle);
                    border-radius: var(--g-radius-sm);
                  "
                >
                  <AppMarkdown :content="msg.content" />
                </div>
              </div>

              <!-- User Message -->
              <div
                v-else-if="msg.role === 'user'"
                class="max-w-[85%] border px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap"
                style="
                  background: var(--g-bg-subtle);
                  border-color: var(--g-border-subtle);
                  color: var(--g-text-primary);
                  border-radius: var(--g-radius-sm);
                "
              >
                {{ msg.content }}
              </div>

              <!-- Assistant Message -->
              <div v-else class="w-full">
                <!-- Unified Collapsible Activity Timeline (Worked for Xm / Explored files / Edited / Ran) -->
                <AgentActivityTimeline
                  :reasoning="msg.reasoning_content"
                  :tool-calls="msg.tool_calls_json"
                  :streaming="msg.streaming"
                  :latency-ms="msg.latency_ms"
                  :started-at-ms="msg.started_at_ms"
                  :has-content="Boolean(msg.content)"
                />

                <!-- Markdown Body -->
                <div class="px-0.5">
                  <AppMarkdown v-if="msg.content" :content="msg.content" />
                  <span
                    v-if="msg.streaming"
                    class="typing-cursor ml-0.5 inline-block h-4 w-1.5 align-middle"
                    style="background: var(--g-primary)"
                  />
                </div>

                <!-- Assistant Action Footer -->
                <div
                  v-if="!msg.streaming"
                  class="mt-2 flex flex-wrap items-center gap-2 text-[11px]"
                  style="color: var(--g-text-tertiary)"
                >
                  <NTooltip>
                    <template #trigger>
                      <NButton size="tiny" quaternary @click="copyMessage(msg.content)">
                        <template #icon><NIcon :size="13" :component="ContentCopyOutlined" /></template>
                      </NButton>
                    </template>
                    {{ t('common.copy') }}
                  </NTooltip>

                  <NTooltip v-if="index === messages.length - 1">
                    <template #trigger>
                      <NButton size="tiny" quaternary @click="sendPrompt('', true)">
                        <template #icon><NIcon :size="13" :component="RefreshOutlined" /></template>
                      </NButton>
                    </template>
                    {{ t('chat.regenerate') }}
                  </NTooltip>

                  <span v-if="msg.model_id_used" class="font-mono">{{ msg.model_id_used }}</span>
                  <span v-if="msg.latency_ms" class="font-mono">
                    · {{ (msg.latency_ms / 1000).toFixed(2) }}s
                  </span>
                  <span v-if="msg.prompt_tokens + msg.completion_tokens > 0" class="font-mono">
                    · {{ (msg.prompt_tokens + msg.completion_tokens).toLocaleString() }} tok
                  </span>
                </div>
              </div>
            </div>
          </template>
        </div>
        </div>

        <!-- Floating Scroll-to-Bottom Button -->
        <Transition name="g-fade">
          <button
            v-if="showScrollBottomBtn"
            type="button"
            class="scroll-bottom-btn g-transition absolute bottom-3 left-1/2 flex h-7 w-7 -translate-x-1/2 items-center justify-center border cursor-pointer"
            :title="t('chat.activity.scrollToBottom')"
            @click="scrollToBottom(true)"
          >
            <NIcon :size="15" :component="ArrowDownwardOutlined" />
          </button>
        </Transition>
      </div>

      <!-- Bottom Centered Minimalist Composer + Queued Messages Strip -->
      <div class="shrink-0 px-4 pt-1 pb-4">
        <div class="mx-auto flex w-full max-w-[780px] flex-col gap-1.5">
          <!-- Pending Message Queue Strip (Enter while streaming) -->
          <Transition name="g-fade">
            <div
              v-if="activeQueue.length > 0"
              class="flex flex-col gap-1 border px-3 py-2"
              style="
                background: var(--g-bg-canvas);
                border-color: var(--g-border-subtle);
                border-radius: var(--g-radius-sm);
              "
            >
              <div
                class="flex items-center gap-1.5 text-[11px] font-medium"
                style="color: var(--g-text-secondary)"
              >
                <NIcon :size="13" :component="QueueOutlined" style="color: var(--g-primary-text)" />
                <span>{{ t('chat.queueTitle') }} ({{ activeQueue.length }})</span>
              </div>

              <div
                v-for="item in activeQueue"
                :key="item.id"
                class="queue-row g-transition flex items-center justify-between gap-2 border px-2.5 py-1.5"
                style="
                  background: var(--g-bg-surface);
                  border-color: var(--g-border-subtle);
                  border-radius: var(--g-radius-xs);
                "
              >
                <span class="min-w-0 flex-1 truncate text-xs" style="color: var(--g-text-primary)">
                  {{ item.content }}
                </span>
                <div class="flex shrink-0 items-center gap-1">
                  <NButton
                    v-if="streaming"
                    size="tiny"
                    quaternary
                    type="primary"
                    :loading="steeringBusy"
                    @click="steerActiveRun(item.content, item.id)"
                  >
                    <template #icon>
                      <NIcon :size="13" :component="SubdirectoryArrowRightOutlined" />
                    </template>
                    {{ t('chat.steerNow') }}
                  </NButton>
                  <button
                    type="button"
                    class="inline-flex h-5 w-5 items-center justify-center border-0 bg-transparent cursor-pointer"
                    style="color: var(--g-text-tertiary)"
                    @click="removeQueuedItem(item.id)"
                  >
                    <NIcon :size="13" :component="CloseOutlined" />
                  </button>
                </div>
              </div>
            </div>
          </Transition>

          <div
            class="composer-box g-transition w-full border px-3.5 pt-2.5 pb-2"
            style="
              background: var(--g-bg-surface);
              border-color: var(--g-border);
              border-radius: var(--g-radius-sm);
              box-shadow: var(--g-shadow-sm);
            "
          >
            <textarea
              v-model="draftInput"
              rows="2"
              class="composer-textarea w-full resize-none border-0 bg-transparent py-0.5 text-sm leading-relaxed outline-none"
              :placeholder="streaming ? t('chat.streamingPlaceholder') : t('chat.inputPlaceholder')"
              @keydown="handleComposerKeydown"
            />

            <div class="mt-1.5 flex items-center justify-between gap-2">
              <!-- Minimal Inline Agent Picker (Agent name only) -->
              <NPopover
                v-model:show="agentPickerShow"
                trigger="click"
                placement="top-start"
                :show-arrow="false"
                :style="{ padding: '4px', minWidth: '180px', maxWidth: '280px' }"
              >
                <template #trigger>
                  <button
                    type="button"
                    class="agent-trigger g-transition inline-flex h-7 max-w-[260px] items-center gap-1.5 border-0 bg-transparent px-2 text-xs font-medium cursor-pointer"
                  >
                    <NIcon
                      :size="14"
                      :component="SmartToyOutlined"
                      class="shrink-0"
                      style="color: var(--g-text-secondary)"
                    />
                    <span class="truncate" style="color: var(--g-text-primary)">
                      {{ selectedAgent?.name || t('chat.selectAgent') }}
                    </span>
                    <NIcon
                      :size="15"
                      :component="KeyboardArrowDownOutlined"
                      class="shrink-0"
                      style="color: var(--g-text-tertiary)"
                    />
                  </button>
                </template>

                <div class="flex max-h-60 flex-col gap-0.5 overflow-y-auto">
                  <div
                    v-if="agents.length === 0"
                    class="px-2.5 py-2 text-xs"
                    style="color: var(--g-text-tertiary)"
                  >
                    {{ t('chat.selectAgent') }}
                  </div>
                  <button
                    v-for="ag in agents"
                    :key="ag.id"
                    type="button"
                    class="agent-option g-transition flex w-full items-center justify-between gap-2 border-0 bg-transparent px-2.5 py-1.5 text-left text-xs cursor-pointer"
                    :class="{ 'agent-option-active': ag.id === selectedAgentId }"
                    @click="handleAgentChange(ag.id)"
                  >
                    <span class="truncate" style="color: var(--g-text-primary)">
                      {{ ag.name }}
                    </span>
                    <NIcon
                      v-if="ag.id === selectedAgentId"
                      :size="14"
                      :component="CheckOutlined"
                      class="shrink-0"
                      style="color: var(--g-primary-text)"
                    />
                  </button>
                </div>
              </NPopover>

              <!-- Compact Actions (Queue / Immediate Steer / Stop / Send) -->
              <div class="flex items-center gap-1.5">
                <template v-if="streaming">
                  <span
                    v-if="!draftInput.trim()"
                    class="hidden text-[11px] sm:inline"
                    style="color: var(--g-text-tertiary)"
                  >
                    {{ t('chat.steerHint') }}
                  </span>
                  <template v-else>
                    <NButton size="tiny" secondary @click="enqueueDraft()">
                      <template #icon><NIcon :size="13" :component="QueueOutlined" /></template>
                      {{ t('chat.queueAction') }}
                    </NButton>
                    <NButton
                      size="tiny"
                      type="primary"
                      :loading="steeringBusy"
                      @click="steerActiveRun()"
                    >
                      <template #icon>
                        <NIcon :size="13" :component="SubdirectoryArrowRightOutlined" />
                      </template>
                      {{ t('chat.steerAction') }}
                    </NButton>
                  </template>
                  <button
                    type="button"
                    class="composer-action-stop g-transition inline-flex h-7 w-7 items-center justify-center border cursor-pointer"
                    :title="t('chat.stop')"
                    @click="stopStreaming"
                  >
                    <NIcon :size="15" :component="StopOutlined" />
                  </button>
                </template>
                <button
                  v-else
                  type="button"
                  class="composer-action-send g-transition inline-flex h-7 w-7 items-center justify-center border-0 cursor-pointer"
                  :disabled="!draftInput.trim() || !selectedAgentId"
                  :title="t('chat.send')"
                  @click="sendPrompt()"
                >
                  <NIcon :size="16" :component="ArrowUpwardOutlined" />
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Rename Conversation Modal -->
    <NModal
      v-model:show="renameModalShow"
      preset="dialog"
      :title="t('chat.renameTitle')"
      :positive-text="t('common.save')"
      :negative-text="t('common.cancel')"
      @positive-click="confirmRename"
    >
      <NInput v-model:value="renameDraft" @keydown.enter="confirmRename" />
    </NModal>
  </div>
</template>

<style scoped>
.conv-item {
  border-radius: var(--g-radius-xs);
  cursor: pointer;
}

.conv-item:hover {
  background: var(--g-bg-hover);
}

.conv-item-active {
  background: var(--g-bg-active);
}

.starter-card {
  background: var(--g-bg-canvas);
  border-color: var(--g-border);
  border-radius: var(--g-radius-sm);
  cursor: pointer;
}

.starter-card:hover {
  border-color: var(--g-primary);
  background: var(--g-bg-hover);
}

.composer-box:focus-within {
  border-color: var(--g-primary) !important;
  box-shadow: var(--g-shadow-md) !important;
}

.agent-trigger {
  border-radius: var(--g-radius-xs);
}

.agent-trigger:hover {
  background: var(--g-bg-hover);
}

.agent-option {
  border-radius: var(--g-radius-xs);
}

.agent-option:hover {
  background: var(--g-bg-hover);
}

.agent-option-active {
  background: var(--g-bg-active);
  font-weight: 500;
}

.composer-action-send {
  background: var(--g-primary);
  color: #ffffff;
  border-radius: var(--g-radius-xs);
}

:global(html.dark) .composer-action-send {
  color: #202124;
}

.composer-action-send:hover:not(:disabled) {
  background: var(--g-primary-hover);
}

.composer-action-send:disabled {
  background: var(--g-bg-subtle);
  color: var(--g-text-tertiary);
  cursor: not-allowed;
}

.composer-action-stop {
  background: var(--g-error-soft);
  border-color: var(--g-error);
  color: var(--g-error);
  border-radius: var(--g-radius-xs);
}

.composer-action-stop:hover {
  opacity: 0.88;
}

.composer-textarea {
  color: var(--g-text-primary);
  font-family: inherit;
  max-height: 180px;
}

.composer-textarea::placeholder {
  color: var(--g-text-tertiary);
}

.scroll-bottom-btn {
  background: var(--g-bg-surface);
  border-color: var(--g-border);
  color: var(--g-text-secondary);
  border-radius: var(--g-radius-sm);
  box-shadow: var(--g-shadow-sm);
}

.scroll-bottom-btn:hover {
  border-color: var(--g-primary);
  color: var(--g-primary-text);
  background: var(--g-bg-hover);
}

.typing-cursor {
  animation: blink 0.9s steps(1) infinite;
}

@keyframes blink {
  0%,
  49% {
    opacity: 1;
  }
  50%,
  100% {
    opacity: 0;
  }
}
</style>
