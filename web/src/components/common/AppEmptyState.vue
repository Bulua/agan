<script setup lang="ts">
defineProps<{
  title: string
  description?: string
}>()
</script>

<template>
  <div class="flex flex-col items-center justify-center px-6 py-14 text-center">
    <div
      v-if="$slots.icon"
      class="mb-3 flex h-11 w-11 items-center justify-center border"
      style="
        background: var(--g-bg-subtle);
        border-color: var(--g-border);
        color: var(--g-text-secondary);
        border-radius: var(--g-radius-sm);
      "
    >
      <slot name="icon" />
    </div>
    <h3 class="m-0 text-sm font-medium" style="color: var(--g-text-primary)">{{ title }}</h3>
    <p
      v-if="description"
      class="mt-1.5 mb-0 max-w-md text-xs leading-relaxed"
      style="color: var(--g-text-secondary)"
    >
      {{ description }}
    </p>
    <div v-if="$slots.action" class="mt-4">
      <slot name="action" />
    </div>
  </div>
</template>
