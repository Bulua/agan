<script setup lang="ts">
defineProps<{
  title: string
  subtitle?: string
  badge?: string
}>()
</script>

<template>
  <div
    class="g-transition mb-5 flex flex-col gap-3 border-b pb-4 sm:flex-row sm:items-start sm:justify-between"
    style="border-color: var(--g-border-subtle)"
  >
    <div class="min-w-0">
      <div class="flex items-center gap-2.5">
        <h1 class="m-0 text-xl font-medium tracking-tight" style="color: var(--g-text-primary)">
          {{ title }}
        </h1>
        <span
          v-if="badge"
          class="inline-flex items-center px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wider"
          style="
            background: var(--g-primary-soft);
            color: var(--g-primary-text);
            border-radius: var(--g-radius-xs);
          "
        >
          {{ badge }}
        </span>
      </div>
      <p
        v-if="subtitle"
        class="mt-1.5 mb-0 max-w-3xl text-sm leading-relaxed"
        style="color: var(--g-text-secondary)"
      >
        {{ subtitle }}
      </p>
    </div>
    <div v-if="$slots.actions" class="flex shrink-0 items-center gap-2">
      <slot name="actions" />
    </div>
  </div>
</template>
