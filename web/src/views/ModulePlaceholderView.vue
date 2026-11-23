<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { NButton, NIcon } from 'naive-ui'
import { ArrowForwardOutlined, LayersOutlined } from '@vicons/material'
import AppSurfaceCard from '@/components/common/AppSurfaceCard.vue'
import AppEmptyState from '@/components/common/AppEmptyState.vue'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const moduleKey = computed(() => String(route.meta.moduleKey || 'dashboard'))
const moduleTitle = computed(() => t(`placeholder.modules.${moduleKey.value}.title`))
const moduleDesc = computed(() => t(`placeholder.modules.${moduleKey.value}.desc`))
</script>

<template>
  <AppSurfaceCard>
    <AppEmptyState :title="moduleTitle" :description="moduleDesc">
      <template #icon>
        <NIcon :size="22" :component="LayersOutlined" />
      </template>
      <template #action>
        <NButton type="primary" @click="router.push('/chat')">
          <template #icon>
            <NIcon :component="ArrowForwardOutlined" />
          </template>
          {{ t('nav.chat') }}
        </NButton>
      </template>
    </AppEmptyState>
  </AppSurfaceCard>
</template>
