<script setup lang="ts">
import { NDrawer, NDrawerContent } from 'naive-ui'

withDefaults(
  defineProps<{
    show: boolean
    title: string
    width?: number | string
    maskClosable?: boolean
  }>(),
  {
    width: 560,
    maskClosable: true,
  },
)

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
}>()
</script>

<template>
  <NDrawer
    :show="show"
    :width="width"
    placement="right"
    :mask-closable="maskClosable"
    @update:show="emit('update:show', $event)"
  >
    <NDrawerContent :title="title" closable :native-scrollbar="false">
      <slot />
      <template v-if="$slots.footer" #footer>
        <div class="flex w-full items-center justify-between gap-2">
          <slot name="footer" />
        </div>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>
