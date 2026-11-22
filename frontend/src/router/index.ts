import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import MainLayout from '@/layouts/MainLayout.vue'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: MainLayout,
    redirect: '/chat',
    children: [
      {
        path: 'chat',
        name: 'Chat',
        component: () => import('@/views/chat/ChatView.vue'),
      },
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/ModulePlaceholderView.vue'),
        meta: { moduleKey: 'dashboard' },
      },
      {
        path: 'platforms',
        name: 'Platforms',
        component: () => import('@/views/ModulePlaceholderView.vue'),
        meta: { moduleKey: 'platforms' },
      },
      {
        path: 'workbench',
        name: 'Workbench',
        component: () => import('@/views/workbench/SolveWorkbenchView.vue'),
      },
      {
        path: 'studio/models',
        name: 'StudioModels',
        component: () => import('@/views/studio/ModelsView.vue'),
      },
      {
        path: 'studio/tools',
        name: 'StudioTools',
        component: () => import('@/views/studio/ToolsView.vue'),
      },
      {
        path: 'studio/mcp',
        name: 'StudioMcp',
        component: () => import('@/views/studio/McpView.vue'),
      },
      {
        path: 'studio/prompts',
        name: 'StudioPrompts',
        component: () => import('@/views/ModulePlaceholderView.vue'),
        meta: { moduleKey: 'prompts' },
      },
      {
        path: 'studio/skills',
        name: 'StudioSkills',
        component: () => import('@/views/studio/SkillsView.vue'),
      },
      {
        path: 'studio/knowledge',
        name: 'StudioKnowledge',
        component: () => import('@/views/ModulePlaceholderView.vue'),
        meta: { moduleKey: 'knowledge' },
      },
      {
        path: 'studio/memory',
        name: 'StudioMemory',
        component: () => import('@/views/studio/MemoryView.vue'),
      },
      {
        path: 'studio/agents',
        name: 'StudioAgents',
        component: () => import('@/views/studio/AgentsView.vue'),
      },
      {
        path: 'studio/agents/:id',
        name: 'StudioAgentDetail',
        component: () => import('@/views/studio/AgentDetailView.vue'),
      },
    ],
  },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
