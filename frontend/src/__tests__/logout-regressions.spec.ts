import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { ElIcon } from 'element-plus'
import AppShell from '@/layouts/AppShell.vue'
import SettingsView from '@/views/SettingsView.vue'
import router from '@/router'
import { useSessionStore } from '@/stores/session'

vi.mock('@/composables/useServiceHealth', () => ({
  useServiceHealth: () => ({
    state: ref('connected'),
    lastCheckedAt: ref(null),
    detail: ref(null),
  }),
}))

beforeEach(async () => {
  vi.stubGlobal('scrollTo', vi.fn())
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: query.includes('min-width: 768px'),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  )
  setActivePinia(createPinia())
  useSessionStore().applySession(
    { id: 1, username: 'owner', displayName: '所有者', role: 'OWNER' },
    '2026-10-31T00:00:00Z',
  )
  await router.push('/settings')
})

afterEach(() => vi.unstubAllGlobals())

for (const entry of ['topbar', 'settings']) {
  for (const failure of ['network', 'database']) {
    it(`${entry} shows ${failure} logout failure and retries to login`, async () => {
      let fail = true
      vi.stubGlobal(
        'fetch',
        vi.fn(async (url: string) => {
          if (url.endsWith('/auth/logout')) {
            if (fail && failure === 'network') throw new TypeError('offline')
            if (fail)
              return new Response(
                JSON.stringify({
                  code: 'DATABASE_NOT_READY',
                  message: '数据库未就绪',
                }),
                { status: 503 },
              )
            return new Response(null, { status: 204 })
          }
          return new Response(
            JSON.stringify({ workerStatus: 'ok', databaseStatus: 'ok' }),
          )
        }),
      )
      const wrapper = mount(AppShell, {
        slots: { default: SettingsView },
        attachTo: document.body,
        global: { plugins: [router], components: { ElIcon } },
      })
      await flushPromises()
      if (entry === 'topbar') {
        await wrapper.get('.app-shell__topbar-actions button').trigger('click')
      } else {
        await wrapper.get('.settings__actions button').trigger('click')
        await flushPromises()
        const confirm = document.querySelector<HTMLButtonElement>(
          '.confirm-dialog__footer .app-button--danger',
        )!
        expect(confirm).not.toBeNull()
        confirm.click()
      }
      await flushPromises()
      expect(document.body.textContent).toContain('服务器会话可能仍然有效')
      expect(router.currentRoute.value.path).toBe('/settings')
      expect(useSessionStore().isAuthenticated).toBe(false)
      fail = false
      const retry = [
        ...document.querySelectorAll<HTMLButtonElement>('button'),
      ].find((button) => button.textContent?.includes('重试注销'))!
      expect(retry).not.toBeUndefined()
      retry.click()
      await flushPromises()
      await vi.waitFor(() =>
        expect(router.currentRoute.value.path).toBe('/login'),
      )
      expect(router.currentRoute.value.path).toBe('/login')
      wrapper.unmount()
    })
  }
}

it('treats logout 401 as already signed out', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ code: 'NOT_AUTHENTICATED', message: '登录已失效' }),
          { status: 401 },
        ),
      ),
  )
  const wrapper = mount(AppShell, {
    global: { plugins: [router], components: { ElIcon } },
  })
  await wrapper.get('.app-shell__topbar-actions button').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/login')
  expect(wrapper.text()).not.toContain('注销结果未确认')
  wrapper.unmount()
})

it('names every collapsed navigation button and enables tablet tooltips', async () => {
  const wrapper = mount(AppShell, {
    global: { plugins: [router], components: { ElIcon } },
  })
  await flushPromises()
  const buttons = wrapper.findAll('.app-shell__nav-item')
  expect(buttons).toHaveLength(9)
  for (const button of buttons)
    expect(button.attributes('aria-label')).toBeTruthy()
  const tooltips = wrapper.findAllComponents({ name: 'ElTooltip' })
  expect(tooltips).toHaveLength(9)
  for (const tooltip of tooltips) {
    expect(tooltip.props('disabled')).toBe(false)
    expect(tooltip.props('trigger')).toEqual(['hover', 'focus'])
  }
  wrapper.unmount()
})
