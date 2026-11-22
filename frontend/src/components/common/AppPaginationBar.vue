<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { NPagination } from 'naive-ui'

const props = withDefaults(
  defineProps<{
    page: number
    pageSize: number
    total: number
    pageSizes?: number[]
  }>(),
  {
    pageSizes: () => [10, 20, 50],
  },
)

const emit = defineEmits<{
  (e: 'update:page', value: number): void
  (e: 'update:pageSize', value: number): void
  (e: 'change'): void
}>()

const { t } = useI18n()

const rangeStart = computed(() => {
  if (props.total === 0) return 0
  return (props.page - 1) * props.pageSize + 1
})

const rangeEnd = computed(() => Math.min(props.page * props.pageSize, props.total))

function handlePageUpdate(nextPage: number): void {
  emit('update:page', nextPage)
  emit('change')
}

function handlePageSizeUpdate(nextSize: number): void {
  emit('update:pageSize', nextSize)
  emit('update:page', 1)
  emit('change')
}
</script>

<template>
  <div
    class="g-transition flex flex-col items-center justify-between gap-2.5 border-t px-4 py-2.5 sm:flex-row"
    style="background: var(--g-bg-surface); border-color: var(--g-border-subtle)"
  >
    <div class="font-mono text-xs" style="color: var(--g-text-secondary)">
      {{
        t('common.paginationSummary', {
          start: rangeStart,
          end: rangeEnd,
          total,
        })
      }}
    </div>

    <NPagination
      size="small"
      :page="page"
      :page-size="pageSize"
      :item-count="total"
      :page-sizes="pageSizes"
      show-size-picker
      :show-quick-jumper="total > pageSize * 5"
      @update:page="handlePageUpdate"
      @update:page-size="handlePageSizeUpdate"
    />
  </div>
</template>
