/** Review regressions through the real transport, store, views and router guard. */
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import { useSessionStore } from '@/stores/session'
import SetupView from '@/views/SetupView.vue'
import SettingsView from '@/views/SettingsView.vue'
import OverviewView from '@/views/OverviewView.vue'
import router from '@/router'

vi.mock('@/layouts/AppShell.vue', () => ({
  default: { template: '<slot />' },
}))

beforeEach(() => {
  vi.resetAllMocks()
  vi.stubGlobal('scrollTo', vi.fn())
  setActivePinia(createPinia())
})

it('routes a losing setup request to login without a page reload', async () => {
  const session = useSessionStore()
  session.state = 'anonymous'
  session.setupComplete = false
  await router.push('/setup')
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: 'SETUP_ALREADY_COMPLETED',
          message: '所有者已存在',
        }),
        { status: 409 },
      ),
    ),
  )
  const wrapper = mount(SetupView, { global: { plugins: [router] } })
  await wrapper.get('input[name="username"]').setValue('owner')
  await wrapper.get('input[name="displayName"]').setValue('Review')
  await wrapper.get('input[name="password"]').setValue('review-password-only')
  await wrapper
    .get('input[name="passwordConfirm"]')
    .setValue('review-password-only')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/login'))
  const finalPath = router.currentRoute.value.path
  wrapper.unmount()
  expect(finalPath).toBe('/login')
})

it('allows signing in after a protected endpoint reports an expired session', async () => {
  const session = useSessionStore()
  session.applySession(
    { id: 1, username: 'owner', displayName: 'Review', role: 'OWNER' },
    '2026-10-29T00:00:00Z',
  )
  await router.push('/settings')
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: 'NOT_AUTHENTICATED',
          message: '登录已失效，请重新登录。',
        }),
        { status: 401 },
      ),
    ),
  )
  const wrapper = mount(SettingsView, { global: { plugins: [router] } })
  await flushPromises()
  await router.push('/login')
  const finalPath = router.currentRoute.value.path
  wrapper.unmount()
  expect(finalPath).toBe('/login')
  expect(session.user).toBeNull()
  expect(session.state).toBe('anonymous')
})

it('distinguishes API readiness from an unavailable worker', async () => {
  const session = useSessionStore()
  session.applySession(
    { id: 1, username: 'owner', displayName: 'Review', role: 'OWNER' },
    '2026-10-31T00:00:00Z',
  )
  await router.push('/overview')
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async (url: string) =>
        new Response(
          JSON.stringify(
            url.endsWith('/health/ready')
              ? {
                  ready: true,
                  checks: [
                    { name: 'database', status: 'ok', detail: null },
                    { name: 'migrations', status: 'ok', detail: null },
                    {
                      name: 'worker',
                      status: 'skip',
                      detail: '尚未收到任何 Worker 心跳。',
                    },
                  ],
                }
              : {
                  appVersion: '0.1.0',
                  databaseStatus: 'ok',
                  workerStatus: 'missing',
                },
          ),
        ),
    ),
  )
  const wrapper = mount(OverviewView, { global: { plugins: [router] } })
  await flushPromises()
  expect(wrapper.text()).toContain('API 可服务')
  expect(wrapper.text()).toContain('尚未收到任何 Worker 心跳')
  expect(wrapper.text()).not.toContain('全部就绪')
  wrapper.unmount()
})
