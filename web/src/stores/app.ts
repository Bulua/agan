import { defineStore } from 'pinia'
import { ref } from 'vue'
import { i18n, setAppLocale, type AppLocale } from '@/i18n'
import { apiClient } from '@/api/client'

export type ThemeMode = 'light' | 'dark'

const THEME_STORAGE_KEY = 'agan-theme'
const SIDEBAR_STORAGE_KEY = 'agan-sidebar-collapsed'

function detectInitialTheme(): ThemeMode {
  const saved = localStorage.getItem(THEME_STORAGE_KEY)
  if (saved === 'light' || saved === 'dark') {
    return saved
  }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export const useAppStore = defineStore('app', () => {
  const themeMode = ref<ThemeMode>(detectInitialTheme())
  const locale = ref<AppLocale>(i18n.global.locale.value as AppLocale)
  const sidebarCollapsed = ref<boolean>(localStorage.getItem(SIDEBAR_STORAGE_KEY) === 'true')
  const backendOnline = ref<boolean>(false)

  function applyTheme(mode: ThemeMode): void {
    themeMode.value = mode
    localStorage.setItem(THEME_STORAGE_KEY, mode)
    document.documentElement.classList.toggle('dark', mode === 'dark')
  }

  function toggleTheme(): void {
    applyTheme(themeMode.value === 'dark' ? 'light' : 'dark')
  }

  function changeLocale(next: AppLocale): void {
    locale.value = next
    setAppLocale(next)
  }

  function toggleSidebar(): void {
    sidebarCollapsed.value = !sidebarCollapsed.value
    localStorage.setItem(SIDEBAR_STORAGE_KEY, String(sidebarCollapsed.value))
  }

  async function checkBackendHealth(): Promise<void> {
    try {
      const { data } = await apiClient.get<{ status: string }>('/health', { timeout: 3000 })
      backendOnline.value = data?.status === 'ok'
    } catch {
      backendOnline.value = false
    }
  }

  // Initialize DOM classes immediately
  applyTheme(themeMode.value)
  document.documentElement.lang = locale.value

  return {
    themeMode,
    locale,
    sidebarCollapsed,
    backendOnline,
    applyTheme,
    toggleTheme,
    changeLocale,
    toggleSidebar,
    checkBackendHealth,
  }
})
