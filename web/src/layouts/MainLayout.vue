<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { NButton, NDropdown, NIcon, NTooltip } from 'naive-ui'
import {
  AutoAwesomeOutlined,
  BuildOutlined,
  ChatBubbleOutlineOutlined,
  DarkModeOutlined,
  DashboardOutlined,
  DescriptionOutlined,
  FlagOutlined,
  HubOutlined,
  LightModeOutlined,
  MenuBookOutlined,
  MenuOpenOutlined,
  MenuOutlined,
  MemoryOutlined,
  ModelTrainingOutlined,
  SmartToyOutlined,
  TerminalOutlined,
  TranslateOutlined,
} from '@vicons/material'
import { useAppStore } from '@/stores/app'
import AppStatusBadge from '@/components/common/AppStatusBadge.vue'
import brandLogo from '@/assets/logo.png'
import type { Component } from 'vue'

interface NavItem {
  key: string
  path: string
  labelKey: string
  icon: Component
  ready?: boolean
}

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const appStore = useAppStore()

const workspaceNav: NavItem[] = [
  { key: 'chat', path: '/chat', labelKey: 'nav.chat', icon: ChatBubbleOutlineOutlined, ready: true },
  { key: 'workbench', path: '/workbench', labelKey: 'nav.workbench', icon: TerminalOutlined, ready: true },
  { key: 'platforms', path: '/platforms', labelKey: 'nav.platforms', icon: FlagOutlined },
  { key: 'dashboard', path: '/dashboard', labelKey: 'nav.dashboard', icon: DashboardOutlined },
]

const studioNav: NavItem[] = [
  { key: 'models', path: '/studio/models', labelKey: 'nav.models', icon: ModelTrainingOutlined, ready: true },
  { key: 'tools', path: '/studio/tools', labelKey: 'nav.tools', icon: BuildOutlined, ready: true },
  { key: 'agents', path: '/studio/agents', labelKey: 'nav.agents', icon: SmartToyOutlined, ready: true },
  { key: 'mcp', path: '/studio/mcp', labelKey: 'nav.mcp', icon: HubOutlined, ready: true },
  { key: 'prompts', path: '/studio/prompts', labelKey: 'nav.prompts', icon: DescriptionOutlined },
  { key: 'skills', path: '/studio/skills', labelKey: 'nav.skills', icon: AutoAwesomeOutlined, ready: true },
  { key: 'knowledge', path: '/studio/knowledge', labelKey: 'nav.knowledge', icon: MenuBookOutlined },
  { key: 'memory', path: '/studio/memory', labelKey: 'nav.memory', icon: MemoryOutlined, ready: true },
]

const localeOptions = computed(() => [
  { label: '简体中文 (zh-CN)', key: 'zh-CN' },
  { label: 'English (en-US)', key: 'en-US' },
])

function handleLocaleSelect(key: string): void {
  if (key === 'zh-CN' || key === 'en-US') {
    appStore.changeLocale(key)
  }
}

function isActive(path: string): boolean {
  return route.path === path || route.path.startsWith(`${path}/`)
}

let healthTimer: number | undefined

onMounted(() => {
  appStore.checkBackendHealth()
  healthTimer = window.setInterval(() => {
    appStore.checkBackendHealth()
  }, 15000)
})

onUnmounted(() => {
  if (healthTimer) window.clearInterval(healthTimer)
})
</script>

<template>
  <div class="flex h-screen w-screen flex-col overflow-hidden">
    <!-- Google Console-style Top AppBar -->
    <header
      class="g-transition z-20 flex h-[52px] shrink-0 items-center justify-between border-b px-3"
      style="background: var(--g-bg-surface); border-color: var(--g-border)"
    >
      <div class="flex items-center gap-2.5">
        <NTooltip placement="bottom">
          <template #trigger>
            <NButton quaternary size="small" @click="appStore.toggleSidebar()">
              <template #icon>
                <NIcon :size="19" :component="appStore.sidebarCollapsed ? MenuOutlined : MenuOpenOutlined" />
              </template>
            </NButton>
          </template>
          {{ appStore.sidebarCollapsed ? t('app.expandNav') : t('app.collapseNav') }}
        </NTooltip>

        <div
          class="flex cursor-pointer items-center gap-2 select-none"
          @click="router.push('/studio/models')"
        >
          <span
            class="brand-logo-box g-transition flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden"
          >
            <img
              :src="brandLogo"
              :alt="t('app.brand')"
              class="h-7 w-7 object-contain"
              style="image-rendering: pixelated"
            />
          </span>
          <div class="flex items-baseline gap-2">
            <span class="text-[15px] font-medium tracking-tight" style="color: var(--g-text-primary)">
              {{ t('app.brand') }}
            </span>
            <span class="hidden text-xs sm:inline" style="color: var(--g-text-secondary)">
              {{ t('app.subtitle') }}
            </span>
          </div>
        </div>
      </div>

      <div class="flex items-center gap-2">
        <AppStatusBadge
          :variant="appStore.backendOnline ? 'success' : 'error'"
          :label="appStore.backendOnline ? t('app.backendOnline') : t('app.backendOffline')"
          :pulse="appStore.backendOnline"
        />

        <div class="mx-1 h-4 w-px" style="background: var(--g-border)" />

        <NDropdown
          trigger="click"
          :options="localeOptions"
          :value="appStore.locale"
          @select="handleLocaleSelect"
        >
          <NButton quaternary size="small">
            <template #icon>
              <NIcon :size="17" :component="TranslateOutlined" />
            </template>
            <span class="text-xs font-medium">
              {{ appStore.locale === 'zh-CN' ? '中文' : 'EN' }}
            </span>
          </NButton>
        </NDropdown>

        <NTooltip placement="bottom">
          <template #trigger>
            <NButton quaternary size="small" @click="appStore.toggleTheme()">
              <template #icon>
                <NIcon
                  :size="18"
                  :component="appStore.themeMode === 'dark' ? LightModeOutlined : DarkModeOutlined"
                />
              </template>
            </NButton>
          </template>
          {{ appStore.themeMode === 'dark' ? t('app.lightMode') : t('app.darkMode') }}
        </NTooltip>
      </div>
    </header>

    <!-- Body: Collapsible Navigation Rail + Main Content -->
    <div class="flex min-h-0 flex-1 overflow-hidden">
      <aside
        class="g-transition flex shrink-0 flex-col border-r overflow-y-auto overflow-x-hidden select-none"
        :style="{
          width: appStore.sidebarCollapsed ? '58px' : '236px',
          background: 'var(--g-bg-surface)',
          borderColor: 'var(--g-border)',
        }"
      >
        <nav class="flex flex-1 flex-col py-3">
          <!-- Section: Operations -->
          <div class="mb-4">
            <div
              v-if="!appStore.sidebarCollapsed"
              class="px-4 pb-1.5 text-[11px] font-medium uppercase tracking-wider"
              style="color: var(--g-text-tertiary)"
            >
              {{ t('nav.sectionWorkspace') }}
            </div>
            <div class="flex flex-col gap-0.5 px-2">
              <NTooltip
                v-for="item in workspaceNav"
                :key="item.key"
                placement="right"
                :disabled="!appStore.sidebarCollapsed"
              >
                <template #trigger>
                  <button
                    type="button"
                    class="nav-item g-transition relative flex w-full items-center gap-2.5 px-2.5 py-2 text-left text-[13px]"
                    :class="isActive(item.path) ? 'nav-item-active' : ''"
                    @click="router.push(item.path)"
                  >
                    <NIcon :size="18" :component="item.icon" class="shrink-0" />
                    <span v-if="!appStore.sidebarCollapsed" class="flex-1 truncate">
                      {{ t(item.labelKey) }}
                    </span>
                    <span
                      v-if="!appStore.sidebarCollapsed && item.ready"
                      class="px-1.5 py-0.5 text-[10px] font-medium"
                      style="
                        background: var(--g-success-soft);
                        color: var(--g-success);
                        border-radius: var(--g-radius-xs);
                      "
                    >
                      {{ t('nav.readyBadge') }}
                    </span>
                  </button>
                </template>
                {{ t(item.labelKey) }}
              </NTooltip>
            </div>
          </div>

          <!-- Divider -->
          <div class="mx-3 mb-4 h-px" style="background: var(--g-border-subtle)" />

          <!-- Section: Capability Studio -->
          <div>
            <div
              v-if="!appStore.sidebarCollapsed"
              class="px-4 pb-1.5 text-[11px] font-medium uppercase tracking-wider"
              style="color: var(--g-text-tertiary)"
            >
              {{ t('nav.sectionStudio') }}
            </div>
            <div class="flex flex-col gap-0.5 px-2">
              <NTooltip
                v-for="item in studioNav"
                :key="item.key"
                placement="right"
                :disabled="!appStore.sidebarCollapsed"
              >
                <template #trigger>
                  <button
                    type="button"
                    class="nav-item g-transition relative flex w-full items-center gap-2.5 px-2.5 py-2 text-left text-[13px]"
                    :class="isActive(item.path) ? 'nav-item-active' : ''"
                    @click="router.push(item.path)"
                  >
                    <NIcon :size="18" :component="item.icon" class="shrink-0" />
                    <span v-if="!appStore.sidebarCollapsed" class="flex-1 truncate">
                      {{ t(item.labelKey) }}
                    </span>
                    <span
                      v-if="!appStore.sidebarCollapsed && item.ready"
                      class="px-1.5 py-0.5 text-[10px] font-medium"
                      style="
                        background: var(--g-success-soft);
                        color: var(--g-success);
                        border-radius: var(--g-radius-xs);
                      "
                    >
                      {{ t('nav.readyBadge') }}
                    </span>
                  </button>
                </template>
                {{ t(item.labelKey) }}
              </NTooltip>
            </div>
          </div>
        </nav>

        <div
          v-if="!appStore.sidebarCollapsed"
          class="border-t px-4 py-2.5 font-mono text-[11px]"
          style="border-color: var(--g-border-subtle); color: var(--g-text-tertiary)"
        >
          AGAN Engine v0.1.0
        </div>
      </aside>

      <!-- Main Workspace Canvas with smooth route transitions -->
      <main
        class="g-transition flex-1 overflow-y-auto px-5 py-5 md:px-7 md:py-6"
        style="background: var(--g-bg-canvas)"
      >
        <div class="mx-auto max-w-[1440px]">
          <RouterView v-slot="{ Component, route: currentRoute }">
            <Transition name="g-page" mode="out-in">
              <KeepAlive :max="15">
                <component :is="Component" :key="currentRoute.path" />
              </KeepAlive>
            </Transition>
          </RouterView>
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
.brand-logo-box {
  border-radius: var(--g-radius-sm);
}

:global(html.dark) .brand-logo-box {
  background: rgba(232, 234, 237, 0.92);
  padding: 2px;
}

.nav-item {
  border: none;
  background: transparent;
  color: var(--g-text-secondary);
  border-radius: var(--g-radius-xs);
  cursor: pointer;
}

.nav-item:hover {
  background: var(--g-bg-hover);
  color: var(--g-text-primary);
}

.nav-item-active {
  background: var(--g-bg-active);
  color: var(--g-primary-text);
  font-weight: 500;
}

.nav-item-active::before {
  content: '';
  position: absolute;
  left: -8px;
  top: 4px;
  bottom: 4px;
  width: 3px;
  background: var(--g-primary);
  border-radius: 0 2px 2px 0;
}
</style>
