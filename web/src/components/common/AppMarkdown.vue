<script setup lang="ts">
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps<{
  content: string
}>()

marked.setOptions({
  breaks: true,
  gfm: true,
})

const renderedHtml = computed(() => {
  if (!props.content) return ''
  const raw = marked.parse(props.content) as string
  return DOMPurify.sanitize(raw)
})
</script>

<template>
  <div class="g-markdown" v-html="renderedHtml" />
</template>

<style scoped>
.g-markdown {
  font-size: 14px;
  line-height: 1.7;
  color: var(--g-text-primary);
  word-break: break-word;
}

.g-markdown :deep(p) {
  margin: 0 0 0.75em;
}

.g-markdown :deep(p:last-child) {
  margin-bottom: 0;
}

.g-markdown :deep(h1),
.g-markdown :deep(h2),
.g-markdown :deep(h3),
.g-markdown :deep(h4) {
  margin: 1.1em 0 0.5em;
  font-weight: 600;
  color: var(--g-text-primary);
  line-height: 1.35;
}

.g-markdown :deep(h1) {
  font-size: 1.25em;
}
.g-markdown :deep(h2) {
  font-size: 1.12em;
}
.g-markdown :deep(h3) {
  font-size: 1.02em;
}

.g-markdown :deep(ul),
.g-markdown :deep(ol) {
  margin: 0.4em 0 0.8em;
  padding-left: 1.4em;
}

.g-markdown :deep(li) {
  margin: 0.25em 0;
}

.g-markdown :deep(code) {
  font-family: 'JetBrains Mono', 'Roboto Mono', Consolas, monospace;
  font-size: 0.88em;
  padding: 0.12em 0.36em;
  background: var(--g-bg-subtle);
  border: 1px solid var(--g-border-subtle);
  border-radius: 2px;
}

.g-markdown :deep(pre) {
  margin: 0.75em 0;
  padding: 12px 14px;
  overflow-x: auto;
  background: var(--g-bg-canvas);
  border: 1px solid var(--g-border);
  border-radius: 4px;
}

.g-markdown :deep(pre code) {
  padding: 0;
  border: none;
  background: transparent;
  font-size: 12.5px;
  line-height: 1.6;
}

.g-markdown :deep(blockquote) {
  margin: 0.75em 0;
  padding: 0.4em 0.9em;
  border-left: 3px solid var(--g-primary);
  background: var(--g-bg-subtle);
  color: var(--g-text-secondary);
}

.g-markdown :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.75em 0;
  font-size: 13px;
}

.g-markdown :deep(th),
.g-markdown :deep(td) {
  border: 1px solid var(--g-border);
  padding: 6px 10px;
  text-align: left;
}

.g-markdown :deep(th) {
  background: var(--g-bg-subtle);
  font-weight: 600;
}

.g-markdown :deep(a) {
  color: var(--g-primary-text);
  text-decoration: underline;
}
</style>
