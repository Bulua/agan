<script setup lang="ts">
/**
 * Reusable compact Google Cloud Console-style top filter & action bar.
 * Left slot: search inputs and filter selects.
 * Right slot: refresh and primary creation actions.
 */
</script>

<template>
  <div
    class="g-transition mb-4 flex flex-col gap-2.5 border px-3.5 py-2.5 lg:flex-row lg:items-center lg:justify-between"
    style="
      background: var(--g-bg-surface);
      border-color: var(--g-border);
      border-radius: var(--g-radius-sm);
    "
  >
    <div class="flex flex-1 flex-wrap items-center gap-2">
      <slot name="filters" />
    </div>
    <div v-if="$slots.actions" class="flex shrink-0 items-center justify-end gap-2">
      <slot name="actions" />
    </div>
  </div>
</template>
