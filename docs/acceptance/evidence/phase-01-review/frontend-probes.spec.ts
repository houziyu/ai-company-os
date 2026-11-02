/** Temporary review probes; retained outside the default suite after execution. */
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import { useSessionStore } from '@/stores/session'
import SetupView from '@/views/SetupView.vue'
import SettingsView from '@/views/SettingsView.vue'
import router from '@/router'

vi.mock('@/api/endpoints', () => ({
  authApi: { session: vi.fn(), login: vi.fn(), logout: vi.fn(), setupStatus: vi.fn(), setup: vi.fn() },
  systemApi: { readiness: vi.fn(), status: vi.fn() },
}))
vi.mock('@/layouts/AppShell.vue', () => ({ default: { template: '<slot />' } }))
const { authApi, systemApi } = await import('@/api/endpoints')

beforeEach(() => { vi.resetAllMocks(); setActivePinia(createPinia()) })

it('routes a losing setup request to login without a page reload', async () => {
  const session = useSessionStore()
  session.state = 'anonymous'
  session.setupComplete = false
  await router.push('/setup')
  vi.mocked(authApi.setup).mockRejectedValue(new ApiError(409, {
    code: 'SETUP_ALREADY_COMPLETED', message: '所有者已存在',
  }))
  const wrapper = mount(SetupView, { global: { plugins: [router] } })
  await wrapper.get('input[name="username"]').setValue('owner')
  await wrapper.get('input[name="displayName"]').setValue('Review')
  await wrapper.get('input[name="password"]').setValue('review-password-only')
  await wrapper.get('input[name="passwordConfirm"]').setValue('review-password-only')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  const finalPath = router.currentRoute.value.path
  wrapper.unmount()
  expect(finalPath).toBe('/login')
})

it('allows signing in after a protected endpoint reports an expired session', async () => {
  const session = useSessionStore()
  session.applySession({ id: 1, username: 'owner', displayName: 'Review', role: 'OWNER' },
    '2026-10-29T00:00:00Z')
  await router.push('/settings')
  vi.mocked(systemApi.status).mockRejectedValue(new ApiError(401, {
    code: 'NOT_AUTHENTICATED', message: '登录已失效，请重新登录。',
  }))
  const wrapper = mount(SettingsView, { global: { plugins: [router] } })
  await flushPromises()
  await router.push('/login')
  const finalPath = router.currentRoute.value.path
  wrapper.unmount()
  expect(finalPath).toBe('/login')
})
