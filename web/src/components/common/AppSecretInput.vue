<script setup lang="ts">
import { ref } from 'vue'
import { NButton, NIcon, NInput, useMessage } from 'naive-ui'
import { ContentCopyOutlined, VisibilityOffOutlined, VisibilityOutlined } from '@vicons/material'
import { useI18n } from 'vue-i18n'

const props = withDefaults(
  defineProps<{
    modelValue: string
    placeholder?: string
    maskedHint?: string
    disabled?: boolean
  }>(),
  {
    placeholder: '',
    maskedHint: '',
    disabled: false,
  },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

const { t } = useI18n()
const message = useMessage()
const visible = ref(false)

async function handleCopy(): Promise<void> {
  const val = props.modelValue || props.maskedHint
  if (!val) return
  try {
    await navigator.clipboard.writeText(val)
    message.success(t('common.copied'))
  } catch {
    message.warning(t('common.operationFailed'))
  }
}
</script>

<template>
  <div class="w-full">
    <div class="flex items-center gap-1.5">
      <NInput
        :value="modelValue"
        :type="visible ? 'text' : 'password'"
        :placeholder="placeholder"
        :disabled="disabled"
        autocomplete="off"
        class="font-mono"
        @update:value="emit('update:modelValue', $event)"
      >
        <template #suffix>
          <NButton
            quaternary
            size="tiny"
            :focusable="false"
            @click="visible = !visible"
          >
            <template #icon>
              <NIcon :component="visible ? VisibilityOffOutlined : VisibilityOutlined" />
            </template>
          </NButton>
        </template>
      </NInput>
      <NButton
        v-if="modelValue"
        secondary
        size="medium"
        :title="t('common.copy')"
        @click="handleCopy"
      >
        <template #icon>
          <NIcon :component="ContentCopyOutlined" />
        </template>
      </NButton>
    </div>
    <p
      v-if="maskedHint && !modelValue"
      class="mt-1 mb-0 font-mono text-[11px]"
      style="color: var(--g-text-tertiary)"
    >
      {{ maskedHint }}
    </p>
  </div>
</template>
