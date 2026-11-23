<script setup lang="ts">
import { computed, ref } from 'vue'
import { NButton, NIcon, useMessage } from 'naive-ui'
import {
  AutoFixHighOutlined,
  BuildOutlined,
  CheckCircleOutlineOutlined,
  ContentCopyOutlined,
  ErrorOutlineOutlined,
  FlagOutlined,
  ListAltOutlined,
  MemoryOutlined,
  PersonOutlineOutlined,
  PsychologyOutlined,
  ShieldOutlined,
  TerminalOutlined,
} from '@vicons/material'
import { useI18n } from 'vue-i18n'
import AppStatusBadge, { type BadgeVariant } from '@/components/common/AppStatusBadge.vue'
import type { TraceEventItem } from '@/types/session'
import type { Component } from 'vue'

const props = defineProps<{
  event: TraceEventItem
}>()

const { t } = useI18n()
const message = useMessage()
const showReasoning = ref(false)
const expanded = ref(true)

const meta = computed<{
  label: string
  accentColor: string
  bgTint?: string
  icon: Component
  badgeVariant: BadgeVariant
}>(() => {
  switch (props.event.event_type) {
    case 'thought':
      return {
        label: 'THOUGHT',
        accentColor: 'var(--g-primary)',
        icon: PsychologyOutlined,
        badgeVariant: 'info',
      }
    case 'plan_update':
      return {
        label: 'PLAN',
        accentColor: 'var(--g-primary)',
        icon: ListAltOutlined,
        badgeVariant: 'info',
      }
    case 'tool_call':
      return {
        label: `TOOL CALL · ${props.event.payload?.tool_name || ''}`,
        accentColor: 'var(--g-border-strong)',
        icon: BuildOutlined,
        badgeVariant: 'neutral',
      }
    case 'tool_result':
      return {
        label: `OUTPUT · ${props.event.payload?.tool_name || ''}`,
        accentColor: props.event.payload?.success ? 'var(--g-success)' : 'var(--g-warning)',
        icon: TerminalOutlined,
        badgeVariant: props.event.payload?.success ? 'success' : 'warning',
      }
    case 'reflexion':
      return {
        label: 'SELF-REFLEXION',
        accentColor: 'var(--g-warning)',
        bgTint: 'var(--g-warning-soft)',
        icon: AutoFixHighOutlined,
        badgeVariant: 'warning',
      }
    case 'human_steering':
      return {
        label: 'HUMAN STEERING',
        accentColor: 'var(--g-primary)',
        bgTint: 'var(--g-primary-soft)',
        icon: PersonOutlineOutlined,
        badgeVariant: 'info',
      }
    case 'flag_detected':
      return {
        label: 'FLAG DETECTED',
        accentColor: 'var(--g-success)',
        bgTint: 'var(--g-success-soft)',
        icon: FlagOutlined,
        badgeVariant: 'success',
      }
    case 'scratchpad_update':
      return {
        label: 'SCRATCHPAD',
        accentColor: 'var(--g-primary)',
        icon: MemoryOutlined,
        badgeVariant: 'info',
      }
    case 'guardrail':
      return {
        label: 'GUARDRAIL',
        accentColor: 'var(--g-warning)',
        bgTint: 'var(--g-warning-soft)',
        icon: ShieldOutlined,
        badgeVariant: 'warning',
      }
    case 'error':
      return {
        label: 'ERROR',
        accentColor: 'var(--g-error)',
        bgTint: 'var(--g-error-soft)',
        icon: ErrorOutlineOutlined,
        badgeVariant: 'error',
      }
    default:
      return {
        label: props.event.event_type.toUpperCase(),
        accentColor: 'var(--g-border)',
        icon: CheckCircleOutlineOutlined,
        badgeVariant: 'neutral',
      }
  }
})

const timeLabel = computed(() => {
  try {
    return new Date(props.event.timestamp).toLocaleTimeString([], {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  } catch {
    return ''
  }
})

async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    message.success(t('common.copied'))
  } catch {
    message.warning(t('common.operationFailed'))
  }
}

function stepStatusVariant(st: string): BadgeVariant {
  if (st === 'done') return 'success'
  if (st === 'in_progress') return 'info'
  if (st === 'failed') return 'error'
  return 'neutral'
}
</script>

<template>
  <article
    class="g-transition border"
    :style="{
      background: meta.bgTint || 'var(--g-bg-surface)',
      borderColor: 'var(--g-border)',
      borderLeftWidth: '3px',
      borderLeftColor: meta.accentColor,
      borderRadius: 'var(--g-radius-sm)',
    }"
  >
    <!-- Compact Event Header -->
    <header
      class="flex cursor-pointer items-center justify-between gap-2 px-3 py-2 select-none"
      @click="expanded = !expanded"
    >
      <div class="flex min-w-0 items-center gap-2">
        <NIcon :size="15" :component="meta.icon" :style="{ color: meta.accentColor }" />
        <AppStatusBadge :variant="meta.badgeVariant" :label="meta.label" :dot="false" />
        <span class="font-mono text-[11px]" style="color: var(--g-text-tertiary)">
          #{{ event.seq_id }} · step {{ event.step_index }}
        </span>
        <span
          v-if="event.agent_name && event.agent_name !== 'main'"
          class="font-mono text-[11px]"
          style="color: var(--g-text-secondary)"
        >
          @{{ event.agent_name }}
        </span>
      </div>
      <div class="flex shrink-0 items-center gap-2.5 font-mono text-[11px]" style="color: var(--g-text-tertiary)">
        <span v-if="event.payload?.metadata?.duration_ms != null">
          {{ event.payload.metadata.duration_ms }}ms
        </span>
        <span v-if="event.payload?.metadata?.exit_code != null">
          exit={{ event.payload.metadata.exit_code }}
        </span>
        <span>{{ timeLabel }}</span>
      </div>
    </header>

    <!-- Event Body -->
    <div
      v-if="expanded"
      class="border-t px-3 py-2.5 text-xs leading-relaxed"
      style="border-color: var(--g-border-subtle)"
    >
      <!-- THOUGHT -->
      <template v-if="event.event_type === 'thought'">
        <div v-if="event.payload.reasoning_content" class="mb-2">
          <button
            type="button"
            class="mb-1 border-0 bg-transparent p-0 font-mono text-[11px] underline cursor-pointer"
            style="color: var(--g-primary-text)"
            @click="showReasoning = !showReasoning"
          >
            {{ showReasoning ? 'Hide Reasoning Chain' : 'Show Reasoning Chain' }}
          </button>
          <pre
            v-if="showReasoning"
            class="m-0 mb-2 max-h-56 overflow-y-auto border p-2 whitespace-pre-wrap font-mono text-[11px]"
            style="
              background: var(--g-bg-canvas);
              border-color: var(--g-border-subtle);
              color: var(--g-text-secondary);
              border-radius: var(--g-radius-xs);
            "
          >{{ event.payload.reasoning_content }}</pre>
        </div>
        <div class="whitespace-pre-wrap" style="color: var(--g-text-primary)">
          {{ event.payload.content || '(thinking...)' }}
        </div>
      </template>

      <!-- TOOL CALL -->
      <template v-else-if="event.event_type === 'tool_call'">
        <pre
          v-if="event.payload.arguments?.command"
          class="m-0 overflow-x-auto border px-2.5 py-2 font-mono text-xs"
          style="
            background: var(--g-bg-canvas);
            border-color: var(--g-border-subtle);
            color: var(--g-primary-text);
            border-radius: var(--g-radius-xs);
          "
        >$ {{ event.payload.arguments.command }}</pre>
        <pre
          v-else
          class="m-0 max-h-56 overflow-auto border p-2 font-mono text-[11px]"
          style="
            background: var(--g-bg-canvas);
            border-color: var(--g-border-subtle);
            color: var(--g-text-primary);
            border-radius: var(--g-radius-xs);
          "
        >{{ JSON.stringify(event.payload.arguments || {}, null, 2) }}</pre>
      </template>

      <!-- TOOL RESULT -->
      <template v-else-if="event.event_type === 'tool_result'">
        <pre
          class="m-0 max-h-72 overflow-auto border p-2.5 whitespace-pre-wrap break-words font-mono text-[11px] leading-5"
          style="
            background: var(--g-bg-canvas);
            border-color: var(--g-border-subtle);
            color: var(--g-text-primary);
            border-radius: var(--g-radius-xs);
          "
        >{{ event.payload.output || event.payload.error || '(no output)' }}</pre>
        <div
          v-if="event.payload.metadata?.truncated"
          class="mt-1.5 font-mono text-[11px]"
          style="color: var(--g-warning)"
        >
          Truncated output saved at: {{ event.payload.metadata.full_log_path }}
        </div>
      </template>

      <!-- PLAN UPDATE -->
      <template v-else-if="event.event_type === 'plan_update'">
        <p
          v-if="event.payload.strategy_overview"
          class="mt-0 mb-2 text-xs"
          style="color: var(--g-text-secondary)"
        >
          {{ event.payload.strategy_overview }}
        </p>
        <div class="flex flex-col gap-1.5">
          <div
            v-for="s in event.payload.steps || []"
            :key="s.id"
            class="flex items-start justify-between gap-2 border px-2.5 py-1.5"
            style="
              background: var(--g-bg-canvas);
              border-color: var(--g-border-subtle);
              border-radius: var(--g-radius-xs);
            "
          >
            <div class="min-w-0">
              <span class="font-mono font-medium" style="color: var(--g-text-primary)">
                {{ s.id }}. {{ s.title }}
              </span>
              <div class="text-[11px]" style="color: var(--g-text-secondary)">{{ s.goal }}</div>
            </div>
            <AppStatusBadge :variant="stepStatusVariant(s.status)" :label="s.status" />
          </div>
        </div>
      </template>

      <!-- REFLEXION -->
      <template v-else-if="event.event_type === 'reflexion'">
        <div class="mb-1 font-mono text-[11px]" style="color: var(--g-warning)">
          Trigger: {{ event.payload.trigger }}
        </div>
        <div class="whitespace-pre-wrap" style="color: var(--g-text-primary)">
          {{ event.payload.critique }}
        </div>
      </template>

      <!-- HUMAN STEERING -->
      <template v-else-if="event.event_type === 'human_steering'">
        <div class="whitespace-pre-wrap font-medium" style="color: var(--g-text-primary)">
          {{ event.payload.message }}
        </div>
      </template>

      <!-- FLAG DETECTED -->
      <template v-else-if="event.event_type === 'flag_detected'">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <code class="font-mono text-sm font-semibold" style="color: var(--g-success)">
            {{ event.payload.flag }}
          </code>
          <NButton size="tiny" secondary @click="copyText(event.payload.flag)">
            <template #icon><NIcon :component="ContentCopyOutlined" /></template>
            {{ t('common.copy') }}
          </NButton>
        </div>
      </template>

      <!-- GUARDRAIL / ERROR / STATUS / FALLBACK -->
      <template v-else>
        <pre
          class="m-0 whitespace-pre-wrap break-all font-mono text-[11px]"
          style="color: var(--g-text-secondary)"
        >{{ event.payload.reason || event.payload.message || JSON.stringify(event.payload, null, 2) }}</pre>
      </template>
    </div>
  </article>
</template>
