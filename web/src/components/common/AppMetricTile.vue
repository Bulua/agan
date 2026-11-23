<script setup lang="ts">
defineProps<{
  label: string
  value: string | number
  hint?: string
  accent?: 'primary' | 'success' | 'warning' | 'neutral'
}>()
</script>

<template>
  <div
    class="g-transition flex flex-col justify-between border px-4 py-3.5"
    style="
      background: var(--g-bg-surface);
      border-color: var(--g-border);
      border-radius: var(--g-radius-sm);
    "
  >
    <div class="flex items-center justify-between gap-2">
      <span class="text-xs font-medium" style="color: var(--g-text-secondary)">{{ label }}</span>
      <span
        v-if="$slots.icon"
        class="flex h-6 w-6 items-center justify-center"
        :style="{
          color:
            accent === 'success'
              ? 'var(--g-success)'
              : accent === 'warning'
                ? 'var(--g-warning)'
                : accent === 'primary'
                  ? 'var(--g-primary-text)'
                  : 'var(--g-text-secondary)',
        }"
      >
        <slot name="icon" />
      </span>
    </div>
    <div class="mt-2 flex items-baseline justify-between gap-2">
      <span class="font-mono text-2xl font-medium tracking-tight" style="color: var(--g-text-primary)">
        {{ value }}
      </span>
      <span v-if="hint" class="text-[11px]" style="color: var(--g-text-tertiary)">
        {{ hint }}
      </span>
    </div>
  </div>
</template>
