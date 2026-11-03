import { defineAsyncComponent, defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import type { RouteLocationNormalized } from 'vue-router'

import AppShell from '@/layouts/AppShell.vue'
import { useSessionStore } from '@/stores/session'

/**
 * Every authenticated page renders inside AppShell. The async component is
 * created once at module scope so re-renders do not remount the view, while
 * keeping each page's own bundle lazy-loaded.
 */
function withShell(loader: () => Promise<unknown>) {
  const view = defineAsyncComponent(loader as never)
  return defineComponent({
    name: 'ShellWrapper',
    setup() {
      return () => h(AppShell, null, { default: () => h(view) })
    },
  })
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      redirect: '/overview',
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true, title: '登录' },
    },
    {
      path: '/setup',
      name: 'setup',
      component: () => import('@/views/SetupView.vue'),
      meta: { public: true, title: '初始化' },
    },
    {
      // Company wizard: the only authenticated page outside AppShell, because
      // the navigation makes no sense before a company exists.
      path: '/setup/company',
      name: 'company-setup',
      component: () => import('@/views/CompanySetupView.vue'),
      meta: { title: '创建公司' },
    },
    {
      path: '/overview',
      name: 'overview',
      component: withShell(() => import('@/views/OverviewView.vue')),
      meta: { title: '公司概览' },
    },
    {
      path: '/organization',
      name: 'organization',
      component: withShell(() => import('@/views/OrganizationView.vue')),
      meta: { title: '组织' },
    },
    {
      path: '/models',
      name: 'models',
      component: withShell(() => import('@/views/ModelsView.vue')),
      meta: { title: '模型中心' },
    },
    {
      path: '/projects',
      name: 'projects',
      component: withShell(() => import('@/views/ProjectsView.vue')),
      meta: { title: '项目与任务' },
    },
    {
      path: '/tasks/:id(\\d+)',
      name: 'task-detail',
      component: withShell(() => import('@/views/TaskDetailView.vue')),
      meta: { title: '任务详情' },
    },
    {
      path: '/executions/:id(\\d+)',
      name: 'execution-detail',
      component: withShell(() => import('@/views/ExecutionDetailView.vue')),
      meta: { title: '执行详情' },
    },
    {
      path: '/proposals',
      name: 'proposals',
      component: withShell(() => import('@/views/ProposalsView.vue')),
      meta: { title: '提案中心' },
    },
    {
      path: '/knowledge',
      name: 'knowledge',
      component: withShell(() => import('@/views/KnowledgeView.vue')),
      meta: { title: '知识' },
    },
    {
      path: '/budgets',
      name: 'budgets',
      component: withShell(() => import('@/views/BudgetsView.vue')),
      meta: { title: '预算' },
    },
    {
      path: '/audit',
      name: 'audit',
      component: withShell(() => import('@/views/AuditView.vue')),
      meta: { title: '审计' },
    },
    {
      path: '/settings',
      name: 'settings',
      component: withShell(() => import('@/views/SettingsView.vue')),
      meta: { title: '设置' },
    },
    {
      // Development-only design reference; hidden from production navigation.
      path: '/showcase',
      name: 'showcase',
      component: withShell(() => import('@/views/ComponentsShowcaseView.vue')),
      meta: { title: '组件展示', public: true },
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/views/NotFoundView.vue'),
      meta: { public: true, title: '页面不存在' },
    },
  ],
  scrollBehavior() {
    return { top: 0 }
  },
})

function isPublic(to: RouteLocationNormalized): boolean {
  return to.meta.public === true
}

/** Where an authenticated owner lands: the wizard until a company exists. */
function nextAuthenticatedStep(companyInitialized: boolean | null) {
  return companyInitialized === false
    ? { name: 'company-setup' }
    : { name: 'overview' }
}

router.beforeEach(async (to) => {
  const session = useSessionStore()

  if (session.state === 'unknown') {
    try {
      await session.refresh()
    } catch {
      // Network failure: keep state unknown and let the view render its own
      // "cannot confirm" error. Do not redirect to login on a guess.
      if (!isPublic(to)) {
        return { name: 'login', query: { next: to.fullPath } }
      }
      return true
    }
  }

  // First-run: send everyone to setup while no owner exists.
  if (session.setupComplete === null) {
    try {
      await session.loadSetupStatus()
    } catch {
      return true
    }
  }

  if (session.setupComplete === false && to.name !== 'setup') {
    return { name: 'setup' }
  }

  if (session.setupComplete === true && to.name === 'setup') {
    return session.isAuthenticated
      ? nextAuthenticatedStep(session.companyInitialized)
      : { name: 'login' }
  }

  // An authenticated owner without a company belongs in the wizard. Requires
  // the companyInitialized fact, which loadSetupStatus populates; when it is
  // still unknown the request is not blocked on a guess.
  if (
    session.setupComplete === true &&
    session.companyInitialized === false &&
    session.isAuthenticated &&
    to.name !== 'company-setup' &&
    !isPublic(to)
  ) {
    return { name: 'company-setup' }
  }

  if (to.name === 'company-setup') {
    if (!session.isAuthenticated) {
      return { name: 'login', query: { next: to.fullPath } }
    }
    if (session.companyInitialized === true) {
      return { name: 'overview' }
    }
  }

  if (!isPublic(to) && !session.isAuthenticated) {
    return { name: 'login', query: { next: to.fullPath } }
  }

  if (isPublic(to) && session.isAuthenticated && (to.name === 'login' || to.name === 'setup')) {
    return nextAuthenticatedStep(session.companyInitialized)
  }

  return true
})

router.afterEach((to) => {
  const title = typeof to.meta.title === 'string' ? to.meta.title : ''
  document.title = title ? `${title} · AI Company OS` : 'AI Company OS'
})

export default router
