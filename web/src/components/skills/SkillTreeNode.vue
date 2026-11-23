<script setup lang="ts">
import { computed } from 'vue'
import { NIcon } from 'naive-ui'
import {
  ArticleOutlined,
  ChevronRightOutlined,
  CodeOutlined,
  DataObjectOutlined,
  DescriptionOutlined,
  ExpandMoreOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  TerminalOutlined,
} from '@vicons/material'
import type { SkillFileNode } from '@/types/skill'

defineOptions({ name: 'SkillTreeNode' })

const props = defineProps<{
  skillName: string
  node: SkillFileNode
  depth: number
  selectedSkill: string
  selectedPath: string
  expandedDirs: Record<string, boolean>
}>()

const emit = defineEmits<{
  (e: 'toggle-dir', key: string): void
  (e: 'select-file', skillName: string, path: string): void
}>()

const dirKey = computed(() => `${props.skillName}/${props.node.path}`)
const isExpanded = computed(() => Boolean(props.expandedDirs[dirKey.value]))
const isActive = computed(
  () =>
    props.node.kind === 'file' &&
    props.selectedSkill === props.skillName &&
    props.selectedPath === props.node.path,
)

const fileIcon = computed(() => {
  const lower = props.node.name.toLowerCase()
  if (lower.endsWith('.md')) return ArticleOutlined
  if (lower.endsWith('.py') || lower.endsWith('.ts') || lower.endsWith('.js') || lower.endsWith('.c'))
    return CodeOutlined
  if (lower.endsWith('.sh') || lower.endsWith('.bash')) return TerminalOutlined
  if (lower.endsWith('.json') || lower.endsWith('.yaml') || lower.endsWith('.yml'))
    return DataObjectOutlined
  return DescriptionOutlined
})

const fileIconColor = computed(() => {
  const lower = props.node.name.toLowerCase()
  if (lower === 'skill.md') return 'var(--g-primary-text)'
  if (lower.endsWith('.md')) return 'var(--g-text-secondary)'
  if (lower.endsWith('.py')) return '#3b82f6'
  if (lower.endsWith('.sh')) return '#16a34a'
  if (lower.endsWith('.json') || lower.endsWith('.yaml') || lower.endsWith('.yml')) return '#d97706'
  return 'var(--g-text-tertiary)'
})

function handleClick(): void {
  if (props.node.kind === 'dir') {
    emit('toggle-dir', dirKey.value)
  } else {
    emit('select-file', props.skillName, props.node.path)
  }
}
</script>

<template>
  <div>
    <button
      type="button"
      class="tree-row g-transition flex w-full cursor-pointer items-center gap-1.5 border-0 px-2 py-1 text-left text-xs"
      :class="{ 'is-active': isActive }"
      :style="{ paddingLeft: `${12 + depth * 14}px` }"
      :title="node.path"
      @click="handleClick"
    >
      <template v-if="node.kind === 'dir'">
        <NIcon
          :size="14"
          :component="isExpanded ? ExpandMoreOutlined : ChevronRightOutlined"
          class="shrink-0"
          style="color: var(--g-text-tertiary)"
        />
        <NIcon
          :size="14"
          :component="isExpanded ? FolderOpenOutlined : FolderOutlined"
          class="shrink-0"
          style="color: var(--g-text-secondary)"
        />
        <span class="min-w-0 flex-1 truncate font-medium" style="color: var(--g-text-primary)">
          {{ node.name }}
        </span>
      </template>

      <template v-else>
        <span class="inline-block w-3.5 shrink-0" />
        <NIcon
          :size="14"
          :component="fileIcon"
          class="shrink-0"
          :style="{ color: fileIconColor }"
        />
        <span
          class="min-w-0 flex-1 truncate font-mono text-[12px]"
          :style="{
            color: isActive ? 'var(--g-primary-text)' : 'var(--g-text-primary)',
            fontWeight: isActive || node.name.toLowerCase() === 'skill.md' ? 500 : 400,
          }"
        >
          {{ node.name }}
        </span>
      </template>
    </button>

    <div v-if="node.kind === 'dir' && isExpanded">
      <SkillTreeNode
        v-for="child in node.children"
        :key="child.path"
        :skill-name="skillName"
        :node="child"
        :depth="depth + 1"
        :selected-skill="selectedSkill"
        :selected-path="selectedPath"
        :expanded-dirs="expandedDirs"
        @toggle-dir="(k) => emit('toggle-dir', k)"
        @select-file="(s, p) => emit('select-file', s, p)"
      />
    </div>
  </div>
</template>

<style scoped>
.tree-row {
  background: transparent;
  border-radius: var(--g-radius-xs);
}
.tree-row:hover {
  background: var(--g-bg-hover);
}
.tree-row.is-active {
  background: var(--g-primary-soft);
}
</style>
