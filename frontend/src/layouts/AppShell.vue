<script setup lang="ts">
/**
 * AppShell -- the desktop workbench frame.
 *
 * Layout contract (UI-SPEC.md §4):
 * - >=1280px: sidebar 240, topbar 64, content padding 32, max width 1600.
 * - 768-1279px: sidebar 72 icon mode with accessible names.
 * - <768px: topbar 56, sidebar becomes a drawer, padding 16.
 *
 * Navigation has one disabled placeholder per not-yet-implemented phase; each
 * is still keyboard reachable and announces its state instead of silently
 * looking clickable.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElDrawer, ElTooltip } from 'element-plus'
import {
  Document,
  Grid,
  Histogram,
  List,
  Memo,
  Setting,
  Tickets,
  User,
  Wallet,
} from '@element-plus/icons-vue'

import AppButton from '@/components/AppButton.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import ErrorState from '@/components/ErrorState.vue'
import { useLogout } from '@/composables/useLogout'
import { useSessionStore } from '@/stores/session'
import { useServiceHealth } from '@/composables/useServiceHealth'
import { governanceApi } from '@/api/endpoints'
import { ApiError, NetworkError } from '@/api/client'
import type { CompanyStateResponse } from '@/api/task-types'

interface NavItem {
  label: string
  path: string
  icon: unknown
  /** Phase in which the page gains real content; null when already live. */
  availableFromPhase: number | null
}

const navItems: NavItem[] = [
  {
    label: '公司概览',
    path: '/overview',
    icon: Grid,
    availableFromPhase: null,
  },
  { label: '组织', path: '/organization', icon: User, availableFromPhase: null },
  { label: '模型中心', path: '/models', icon: Memo, availableFromPhase: null },
  {
    label: '项目与任务',
    path: '/projects',
    icon: Tickets,
    availableFromPhase: 3,
  },
  {
    label: '提案中心',
    path: '/proposals',
    icon: Document,
    availableFromPhase: 6,
  },
  { label: '知识', path: '/knowledge', icon: List, availableFromPhase: 7 },
  { label: '预算', path: '/budgets', icon: Wallet, availableFromPhase: 3 },
  { label: '审计', path: '/audit', icon: Histogram, availableFromPhase: null },
]

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
// The topbar connection badge must report a measured state, not a claim.
const health = useServiceHealth()

const drawerOpen = ref(false)
const { loggingOut, logoutError, logout: handleLogout } = useLogout()
const isNarrow = ref(false)
const isCollapsed = ref(false)

let mediaQuery: MediaQueryList | null = null
let collapsedQuery: MediaQueryList | null = null

// Company governance state. Read from the server (never assumed), and only when
// a company exists: before initialization `/company/state` legitimately 404s.
const companyState = ref<CompanyStateResponse | null>(null)
const pausingCompany = ref(false)
const companyActionError = ref<string | null>(null)

async function loadCompanyState() {
  if (!session.isAuthenticated || session.companyInitialized === false) return
  try {
    companyState.value = await governanceApi.state()
  } catch (error) {
    // 404 before the wizard is normal; anything else leaves the badge hidden
    // rather than showing a guessed status.
    if (!(error instanceof ApiError && error.status === 404)) {
      companyState.value = null
    }
  }
}

async function toggleCompanyPause() {
  if (!companyState.value || pausingCompany.value) return
  pausingCompany.value = true
  companyActionError.value = null
  try {
    companyState.value =
      companyState.value.status === 'PAUSED'
        ? await governanceApi.resume()
        : await governanceApi.pause('从顶部栏暂停公司')
  } catch (error) {
    if (error instanceof ApiError) companyActionError.value = error.message
    else if (error instanceof NetworkError) companyActionError.value = error.message
    else companyActionError.value = '公司状态更新失败，请稍后重试。'
  } finally {
    pausingCompany.value = false
  }
}

function syncCollapsed() {
  isCollapsed.value = collapsedQuery?.matches ?? false
}

function syncNarrow() {
  isNarrow.value = mediaQuery?.matches ?? false
  if (!isNarrow.value) drawerOpen.value = false
}

onMounted(() => {
  // The company name comes from setup-status, which the router guard only loads
  // while the setup flag is unknown. After a plain login it is still null, so
  // fetch it once here to label the topbar with the real company.
  if (session.isAuthenticated && session.companyName === null) {
    void session.loadSetupStatus().catch(() => {
      // A failure here only costs the label; it must not interrupt the page.
    })
  }
  void loadCompanyState()
  mediaQuery = window.matchMedia('(max-width: 767px)')
  syncNarrow()
  mediaQuery.addEventListener('change', syncNarrow)
  collapsedQuery = window.matchMedia(
    '(min-width: 768px) and (max-width: 1279px)',
  )
  syncCollapsed()
  collapsedQuery.addEventListener('change', syncCollapsed)
})

onBeforeUnmount(() => {
  mediaQuery?.removeEventListener('change', syncNarrow)
  collapsedQuery?.removeEventListener('change', syncCollapsed)
})

const displayName = computed(() => session.user?.displayName ?? '所有者')
// The topbar previously hard-coded the product name. Now that a company exists,
// show its real name; before initialization the product name is accurate.
const companyLabel = computed(() => session.companyName ?? 'AI Company OS')

// Labels state what was measured. "检查中" is not a success state, and a failed
// poll says "连接中断" rather than silently keeping the last good value.
const connectionState = computed(() => health.state.value)

const connectionLabel = computed(() => {
  switch (health.state.value) {
    case 'connected':
      return '服务已连接'
    case 'disconnected':
      return '连接中断'
    default:
      return '检查中'
  }
})

// A tooltip gives the measurement time; it never replaces the visible label.
const connectionTitle = computed(() => {
  const at = health.lastCheckedAt.value
  if (!at) return '正在检查服务连接状态'
  const time = at.toLocaleTimeString('zh-CN', { hour12: false })
  return health.detail.value
    ? `最近检查 ${time}：${health.detail.value}`
    : `最近检查 ${time}：数据库与迁移均正常`
})

const connectionTone = computed<'success' | 'warning' | 'neutral'>(() => {
  switch (health.state.value) {
    case 'connected':
      return 'success'
    case 'disconnected':
      return 'warning'
    default:
      return 'neutral'
  }
})

function isActive(path: string): boolean {
  return route.path === path || route.path.startsWith(`${path}/`)
}

async function navigate(path: string) {
  drawerOpen.value = false
  if (route.path !== path) {
    await router.push(path)
  }
}
</script>

<template>
  <div class="app-shell">
    <a class="app-shell__skip-link" href="#main-content">跳到主要内容</a>

    <!-- Desktop / tablet sidebar -->
    <aside class="app-shell__sidebar" aria-label="主导航">
      <div class="app-shell__brand">
        <span class="app-shell__brand-mark" aria-hidden="true">AI</span>
        <span class="app-shell__brand-name">AI Company OS</span>
      </div>
      <nav class="app-shell__nav">
        <template v-for="item in navItems" :key="item.path">
          <ElTooltip
            :content="item.label"
            placement="right"
            :trigger="['hover', 'focus']"
            :trigger-keys="[]"
            :disabled="!isCollapsed"
            :show-after="200"
          >
            <button
              type="button"
              class="app-shell__nav-item"
              :class="{ 'app-shell__nav-item--active': isActive(item.path) }"
              :aria-current="isActive(item.path) ? 'page' : undefined"
              :aria-label="item.label"
              @click="navigate(item.path)"
            >
              <ElIcon class="app-shell__nav-icon" aria-hidden="true">
                <component :is="item.icon" />
              </ElIcon>
              <span class="app-shell__nav-label">{{ item.label }}</span>
            </button>
          </ElTooltip>
        </template>
      </nav>
      <div class="app-shell__sidebar-foot">
        <ElTooltip
          content="设置"
          placement="right"
          :trigger="['hover', 'focus']"
          :trigger-keys="[]"
          :disabled="!isCollapsed"
          :show-after="200"
        >
          <button
            type="button"
            class="app-shell__nav-item"
            aria-label="设置"
            :aria-current="isActive('/settings') ? 'page' : undefined"
            @click="navigate('/settings')"
          >
            <ElIcon class="app-shell__nav-icon" aria-hidden="true">
              <Setting />
            </ElIcon>
            <span class="app-shell__nav-label">设置</span>
          </button>
        </ElTooltip>
      </div>
    </aside>

    <div class="app-shell__main">
      <header class="app-shell__topbar">
        <button
          type="button"
          class="app-shell__menu-button"
          aria-label="打开导航菜单"
          @click="drawerOpen = true"
        >
          <ElIcon aria-hidden="true"><List /></ElIcon>
        </button>

        <div class="app-shell__topbar-context">
          <span class="app-shell__company">{{ companyLabel }}</span>
          <StatusBadge
            :label="connectionLabel"
            :tone="connectionTone"
            :show-dot="connectionState !== 'checking'"
            class="app-shell__status"
            :title="connectionTitle"
          />
          <!-- Company pause is fixed in the topbar (UI-SPEC §5) so it is never
               hidden in a menu. The badge reports the server's state; the button
               performs a real command. It lives in the context group rather than
               the account group because it is a company fact, not an account
               action. -->
          <StatusBadge
            v-if="companyState"
            :label="companyState.status === 'PAUSED' ? '公司已暂停' : '公司运行中'"
            :tone="companyState.status === 'PAUSED' ? 'warning' : 'success'"
          />
          <AppButton
            v-if="companyState"
            size="small"
            :variant="companyState.status === 'PAUSED' ? 'primary' : 'secondary'"
            :loading="pausingCompany"
            :loading-text="companyState.status === 'PAUSED' ? '恢复中…' : '暂停中…'"
            @click="toggleCompanyPause"
          >
            {{ companyState.status === 'PAUSED' ? '恢复公司' : '暂停公司' }}
          </AppButton>
        </div>

        <div class="app-shell__topbar-actions">
          <span class="app-shell__user acos-text-secondary">{{
            displayName
          }}</span>
          <AppButton
            variant="secondary"
            size="small"
            :loading="loggingOut"
            loading-text="注销中…"
            @click="handleLogout"
          >
            注销
          </AppButton>
        </div>
      </header>

      <main id="main-content" class="app-shell__content" tabindex="-1">
        <div class="app-shell__content-inner">
          <ErrorState
            v-if="logoutError"
            title="注销结果未确认"
            :description="logoutError"
            retry-label="重试注销"
            :retrying="loggingOut"
            @retry="handleLogout"
          />
          <ErrorState
            v-else-if="session.state === 'anonymous'"
            title="请重新登录"
            :description="session.lastError ?? '当前没有有效登录会话。'"
            retry-label="进入登录页"
            @retry="router.push('/login')"
          />
          <p
            v-if="companyActionError"
            class="app-shell__governance-error"
            role="alert"
          >
            {{ companyActionError }}
          </p>
          <slot />
        </div>
      </main>
    </div>

    <!-- Mobile navigation drawer -->
    <ElDrawer
      v-model="drawerOpen"
      direction="ltr"
      size="264px"
      title="导航"
      class="app-shell__drawer"
    >
      <nav class="app-shell__drawer-nav" aria-label="移动导航">
        <button
          v-for="item in navItems"
          :key="item.path"
          type="button"
          class="app-shell__drawer-item"
          :class="{ 'app-shell__drawer-item--active': isActive(item.path) }"
          :aria-current="isActive(item.path) ? 'page' : undefined"
          @click="navigate(item.path)"
        >
          <ElIcon aria-hidden="true"><component :is="item.icon" /></ElIcon>
          <span>{{ item.label }}</span>
        </button>
        <button
          type="button"
          class="app-shell__drawer-item"
          :class="{ 'app-shell__drawer-item--active': isActive('/settings') }"
          @click="navigate('/settings')"
        >
          <ElIcon aria-hidden="true"><Setting /></ElIcon>
          <span>设置</span>
        </button>
      </nav>
    </ElDrawer>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  min-height: 100vh;
  background: var(--acos-color-bg);
}

.app-shell__skip-link {
  position: absolute;
  left: var(--acos-space-16);
  top: calc(-1 * var(--acos-space-48));
  z-index: var(--acos-z-toast);
  padding: var(--acos-space-8) var(--acos-space-16);
  background: var(--acos-color-primary);
  color: var(--acos-color-on-primary);
  border-radius: var(--acos-radius-control);
  transition: top var(--acos-motion-fast) ease;
}

.app-shell__skip-link:focus-visible {
  top: var(--acos-space-16);
}

/* ---------------- sidebar ---------------- */
.app-shell__sidebar {
  display: flex;
  flex-direction: column;
  width: var(--acos-layout-sidebar);
  flex: 0 0 var(--acos-layout-sidebar);
  background: var(--acos-color-surface);
  border-right: var(--acos-control-border-width) solid var(--acos-color-border);
  padding: var(--acos-space-24) var(--acos-space-12) var(--acos-space-16);
  position: sticky;
  top: 0;
  height: 100vh;
}

.app-shell__brand {
  display: flex;
  align-items: center;
  gap: var(--acos-space-8);
  padding: 0 var(--acos-space-8) var(--acos-space-24);
  font-weight: 600;
  font-size: var(--acos-font-subtitle-size);
  color: var(--acos-color-text);
}

.app-shell__brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: calc(var(--acos-space-24) + var(--acos-space-4));
  height: calc(var(--acos-space-24) + var(--acos-space-4));
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-primary-subtle);
  color: var(--acos-color-primary);
  font-size: var(--acos-font-caption-size);
  font-weight: 700;
  flex: 0 0 auto;
}

.app-shell__nav {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-4);
}

.app-shell__nav-item {
  display: flex;
  align-items: center;
  gap: var(--acos-space-8);
  width: 100%;
  height: var(--acos-layout-nav-item);
  padding: 0 var(--acos-space-12);
  border: 0;
  border-left: var(--acos-layout-nav-active-border) solid transparent;
  border-radius: var(--acos-radius-control);
  background: transparent;
  color: var(--acos-color-text-secondary);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition:
    background-color var(--acos-motion-fast) ease,
    color var(--acos-motion-fast) ease;
}

.app-shell__nav-item:hover {
  background: var(--acos-color-surface-subtle);
  color: var(--acos-color-text);
}

.app-shell__nav-item--active {
  background: var(--acos-color-primary-subtle);
  border-left-color: var(--acos-color-primary);
  color: var(--acos-color-primary);
  font-weight: 600;
}

.app-shell__nav-icon {
  flex: 0 0 auto;
  font-size: var(--acos-control-medium-icon);
}

.app-shell__nav-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-shell__sidebar-foot {
  margin-top: auto;
  padding-top: var(--acos-space-12);
  border-top: var(--acos-control-border-width) solid var(--acos-color-border);
}

/* ---------------- main ---------------- */
.app-shell__main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.app-shell__topbar {
  display: flex;
  align-items: center;
  gap: var(--acos-space-16);
  height: var(--acos-layout-topbar);
  padding: 0 var(--acos-layout-desktop-padding);
  background: var(--acos-color-surface);
  border-bottom: var(--acos-control-border-width) solid var(--acos-color-border);
  position: sticky;
  top: 0;
  z-index: var(--acos-z-sticky);
}

.app-shell__menu-button {
  display: none;
  align-items: center;
  justify-content: center;
  width: var(--acos-control-icon-button);
  height: var(--acos-control-icon-button);
  border: var(--acos-control-border-width) solid
    var(--acos-color-control-border);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-surface);
  color: var(--acos-color-text);
  cursor: pointer;
}

.app-shell__topbar-context {
  display: flex;
  align-items: center;
  gap: var(--acos-space-8);
  min-width: 0;
  flex-wrap: wrap;
}

.app-shell__company {
  font-weight: 600;
  color: var(--acos-color-text);
  white-space: nowrap;
}

.app-shell__topbar-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: var(--acos-space-12);
  flex-wrap: wrap;
  justify-content: flex-end;
}

.app-shell__governance-error {
  margin: 0 0 var(--acos-space-16);
  padding: var(--acos-space-12) var(--acos-space-16);
  background: var(--acos-color-danger-subtle);
  color: var(--acos-color-danger);
  border-radius: var(--acos-radius-control);
}

.app-shell__user {
  font-size: var(--acos-font-caption-size);
  white-space: nowrap;
}

.app-shell__content {
  flex: 1 1 auto;
  padding: var(--acos-layout-desktop-padding);
  outline: none;
}

.app-shell__content-inner {
  max-width: var(--acos-layout-content-max);
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-24);
}

/* ---------------- drawer ---------------- */
.app-shell__drawer-nav {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-4);
}

.app-shell__drawer-item {
  display: flex;
  align-items: center;
  gap: var(--acos-space-12);
  min-height: var(--acos-control-mobile-target);
  padding: 0 var(--acos-space-12);
  border: 0;
  border-radius: var(--acos-radius-control);
  background: transparent;
  color: var(--acos-color-text-secondary);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.app-shell__drawer-item--active {
  background: var(--acos-color-primary-subtle);
  color: var(--acos-color-primary);
  font-weight: 600;
}

/* ---------------- responsive ---------------- */
/* Tablet: icon mode with tooltips. */
@media (max-width: 1279px) and (min-width: 768px) {
  .app-shell__sidebar {
    width: var(--acos-layout-sidebar-collapsed);
    flex-basis: var(--acos-layout-sidebar-collapsed);
    padding: var(--acos-space-24) var(--acos-space-8) var(--acos-space-16);
    align-items: center;
  }

  .app-shell__brand {
    padding: 0 0 var(--acos-space-24);
  }

  .app-shell__brand-name,
  .app-shell__nav-label {
    display: none;
  }

  .app-shell__nav-item {
    justify-content: center;
    padding: 0;
  }

  .app-shell__nav {
    width: 100%;
  }

  .app-shell__sidebar-foot {
    width: 100%;
  }

  .app-shell__topbar {
    padding: 0 var(--acos-layout-tablet-padding);
  }

  .app-shell__content {
    padding: var(--acos-layout-tablet-padding);
  }
}

/* Mobile: drawer navigation. */
@media (max-width: 767px) {
  .app-shell__sidebar {
    display: none;
  }

  .app-shell__menu-button {
    display: inline-flex;
    /* Phones need a 44px touch target even though the desktop icon button is
       40px; only the hit area grows, the icon stays the same size. */
    min-width: var(--acos-control-mobile-target);
    min-height: var(--acos-control-mobile-target);
  }

  .app-shell__topbar {
    height: var(--acos-layout-topbar-mobile);
    padding: 0 var(--acos-layout-mobile-padding);
    gap: var(--acos-space-8);
  }

  .app-shell__company {
    display: none;
  }

  .app-shell__content {
    padding: var(--acos-layout-mobile-padding);
  }

  .app-shell__skip-link {
    display: inline-flex;
    align-items: center;
    min-height: var(--acos-control-mobile-target);
  }

  .app-shell__user {
    display: none;
  }
}
</style>
