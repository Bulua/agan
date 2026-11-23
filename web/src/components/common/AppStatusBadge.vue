<script setup lang="ts">
import { computed } from 'vue'

export type BadgeVariant = 'success' | 'warning' | 'error' | 'info' | 'neutral'

const props = withDefaults(
  defineProps<{
    variant?: BadgeVariant
    label: string
    dot?: boolean
    pulse?: boolean
  }>(),
  {
    variant: 'neutral',
    dot: true,
    pulse: false,
  },
)

const styleMap = computed(() => {
  switch (props.variant) {
    case 'success':
      return { bg: 'var(--g-success-soft)', fg: 'var(--g-success)' }
    case 'warning':
      return { bg: 'var(--g-warning-soft)', fg: 'var(--g-warning)' }
    case 'error':
      return { bg: 'var(--g-error-soft)', fg: 'var(--g-error)' }
    case 'info':
      return { bg: 'var(--g-primary-soft)', fg: 'var(--g-primary-text)' }
    default:
      return { bg: 'var(--g-bg-subtle)', fg: 'var(--g-text-secondary)' }
  }
})
</script>

<template>
  <span
    class="g-transition inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-medium leading-5"
    :style="{
      backgroundColor: styleMap.bg,
      color: styleMap.fg,
      borderRadius: 'var(--g-radius-xs)',
    }"
  >
    <span
      v-if="dot"
      class="inline-block h-1.5 w-1.5 shrink-0"
      :class="pulse ? 'animate-pulse' : ''"
      :style="{ backgroundColor: styleMap.fg, borderRadius: '1px' }"
    />
    <span>{{ label }}</span>
  </span>
</template>
