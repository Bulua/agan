<script setup lang="ts">
defineProps<{
  title?: string
  subtitle?: string
  noPadding?: boolean
  hoverable?: boolean
}>()
</script>

<template>
  <section
    class="g-transition border"
    :class="[hoverable ? 'hover:shadow-sm' : '']"
    style="
      background: var(--g-bg-surface);
      border-color: var(--g-border);
      border-radius: var(--g-radius-sm);
    "
  >
    <header
      v-if="title || $slots.header || $slots.extra"
      class="flex items-center justify-between gap-3 border-b px-4 py-3"
      style="border-color: var(--g-border-subtle)"
    >
      <div class="min-w-0">
        <slot name="header">
          <h2 class="m-0 text-sm font-medium" style="color: var(--g-text-primary)">
            {{ title }}
          </h2>
          <p v-if="subtitle" class="mt-0.5 mb-0 text-xs" style="color: var(--g-text-secondary)">
            {{ subtitle }}
          </p>
        </slot>
      </div>
      <div v-if="$slots.extra" class="flex shrink-0 items-center gap-2">
        <slot name="extra" />
      </div>
    </header>
    <div :class="noPadding ? '' : 'p-4'">
      <slot />
    </div>
  </section>
</template>
