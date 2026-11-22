<script setup lang="ts">
import { computed } from 'vue'
import {
  NConfigProvider,
  NDialogProvider,
  NMessageProvider,
  darkTheme,
  dateEnUS,
  dateZhCN,
  enUS,
  zhCN,
} from 'naive-ui'
import { useAppStore } from '@/stores/app'
import { googleDarkThemeOverrides, googleLightThemeOverrides } from '@/theme/googleTheme'

const appStore = useAppStore()

const naiveTheme = computed(() => (appStore.themeMode === 'dark' ? darkTheme : null))
const themeOverrides = computed(() =>
  appStore.themeMode === 'dark' ? googleDarkThemeOverrides : googleLightThemeOverrides,
)
const naiveLocale = computed(() => (appStore.locale === 'zh-CN' ? zhCN : enUS))
const naiveDateLocale = computed(() => (appStore.locale === 'zh-CN' ? dateZhCN : dateEnUS))
</script>

<template>
  <NConfigProvider
    :theme="naiveTheme"
    :theme-overrides="themeOverrides"
    :locale="naiveLocale"
    :date-locale="naiveDateLocale"
  >
    <NMessageProvider placement="top-right">
      <NDialogProvider>
        <RouterView />
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
</template>
