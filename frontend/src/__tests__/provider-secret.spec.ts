/**
 * Provider credentials typed in the model centre (ADR-0006).
 *
 * What the browser must guarantee:
 *
 * 1. The key box is a masked input, empty on open — a saved key is never sent
 *    to the browser, so there is nothing to prefill and no reveal control.
 * 2. An empty box on edit sends no credential fields at all, so saving an
 *    unrelated change cannot erase the stored key.
 * 3. A typed key is sent once and never written back into the DOM.
 * 4. Every credential state has its own Chinese label; "无法解密" is never
 *    collapsed into "未配置" because the fix is different.
 */
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ElTable, ElTableColumn } from 'element-plus'

import { createTestRouter } from './helpers'

const globalComponents = { ElTable, ElTableColumn }

const EMPTY_CENTRE = {
  providers: [],
  models: [],
  assignments: [],
  recentProbes: [],
  usage: {
    probeCalls: 0,
    measuredCalls: 0,
    unmeasuredCalls: 0,
    promptTokens: null,
    completionTokens: null,
  },
  deepseekPreset: {
    kind: 'DEEPSEEK',
    baseUrl: 'https://api.deepseek.com',
    secretRef: 'DEEPSEEK_API_KEY',
    suggestedModel: 'deepseek-v4.1-flash',
    notes: 'DeepSeek 的 OpenAI 兼容接口。',
  },
}

interface ScriptedCall {
  method: string
  url: string
  body: Record<string, unknown> | null
}

/** Script GET /models/centre with the given providers, plus any extra responses. */
function scriptFetch(
  providers: Record<string, unknown>[] = [],
  extra: { method: string; path: string; status?: number; body: unknown }[] = [],
) {
  const calls: ScriptedCall[] = []
  const routes = extra.map((route) => ({ ...route, used: false }))
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      const rawBody = init?.body ? JSON.parse(String(init.body)) : null
      calls.push({ method, url, body: rawBody })
      if (url.includes('/models/centre') && method === 'GET') {
        return new Response(JSON.stringify({ ...EMPTY_CENTRE, providers }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      const match = routes.find(
        (route) => !route.used && route.method === method && url.includes(route.path),
      )
      if (!match) throw new Error(`unexpected request ${method} ${url}`)
      match.used = true
      return new Response(JSON.stringify(match.body), {
        status: match.status ?? 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }),
  )
  return calls
}

function providerFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: 7,
    name: 'DeepSeek 供应商',
    kind: 'DEEPSEEK',
    version: 2,
    baseUrl: 'https://api.deepseek.com',
    secretRef: null,
    secretSource: 'DB',
    secretState: 'CONFIGURED',
    secretConfigured: true,
    status: 'ENABLED',
    notes: null,
    timeoutSeconds: 30,
    modelCount: 0,
    createdAt: '2026-11-03T00:00:00Z',
    updatedAt: '2026-11-03T00:00:00Z',
    ...overrides,
  }
}

let pinia: ReturnType<typeof createPinia>

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  document.cookie = 'acos_csrf=test-csrf-token'
})

afterEach(() => {
  disposePinia(pinia)
  vi.unstubAllGlobals()
})

async function mountView(options: { route?: string; name?: string } = {}) {
  const view = (await import('@/views/ModelsView.vue')).default
  const router = createTestRouter(view, {
    path: options.route ?? '/models',
    name: options.name ?? 'models',
  })
  const wrapper = mount(view, {
    global: { plugins: [pinia, router], components: globalComponents },
  })
  await flushPromises()
  return wrapper
}

async function readCentre(
  providers: Record<string, unknown>[],
  extra: { method: string; path: string; status?: number; body: unknown }[] = [],
) {
  const calls = scriptFetch(providers, extra)
  const wrapper = await mountView()
  return { wrapper, calls }
}

describe('provider credentials', () => {
  it('marks the key input as a password field with no value and no reveal action', async () => {
    const { wrapper } = await readCentre([providerFixture()])

    // Open the create drawer from the provider tab.
    const createButton = wrapper.findAll('button').find((b) => b.text().includes('添加供应商'))!
    await createButton.trigger('click')
    await flushPromises()

    const inputs = wrapper.findAll('input[type="password"]')
    expect(inputs).toHaveLength(1)
    const keyInput = inputs[0]!
    expect((keyInput.element as HTMLInputElement).value).toBe('')
    expect(keyInput.attributes('autocomplete')).toBe('new-password')
    // No control claims to reveal a stored credential.
    expect(wrapper.text()).not.toContain('显示密钥')
    expect(wrapper.text()).not.toContain('查看密钥')
  })

  it('labels every credential state distinctly', async () => {
    const { wrapper } = await readCentre([
      providerFixture({ id: 1, name: 'A', secretState: 'CONFIGURED', secretConfigured: true }),
      providerFixture({
        id: 2,
        name: 'B',
        secretRef: 'DEEPSEEK_API_KEY',
        secretSource: 'ENV',
        secretState: 'MISSING_ENV',
        secretConfigured: false,
      }),
      providerFixture({
        id: 3,
        name: 'C',
        secretState: 'UNDECRYPTABLE',
        secretConfigured: false,
      }),
      providerFixture({ id: 4, name: 'D', secretState: 'NONE', secretConfigured: false }),
    ])

    const text = wrapper.text()
    expect(text).toContain('已配置（加密保存）')
    expect(text).toContain('未配置（环境变量缺失）')
    // "Cannot decrypt" is a different fix from "not configured" and must say so.
    expect(text).toContain('无法解密')
    expect(text).toContain('未配置')
  })

  it('does not send credential fields when the edit box is left empty', async () => {
    const { wrapper, calls } = await readCentre([providerFixture()], [
      { method: 'PATCH', path: '/providers/7', body: providerFixture() },
    ])

    const editButton = wrapper.findAll('button').find((b) => b.text() === '编辑')!
    await editButton.trigger('click')
    await flushPromises()

    // The stored key is not prefilled: there is nothing to show or leak.
    const password = wrapper.find('input[type="password"]')
    expect((password.element as HTMLInputElement).value).toBe('')

    await wrapper.findAll('button').find((b) => b.text().includes('保存配置'))!.trigger('click')
    await flushPromises()

    const patch = calls.find((call) => call.method === 'PATCH' && call.url.includes('/providers/7'))
    expect(patch).toBeDefined()
    expect(patch!.body).toMatchObject({ expectedVersion: 2, changeSecretValue: false })
    // An unrelated save must not carry an empty value that could clear the key.
    expect(patch!.body!.secretValue).toBeNull()
  })

  it('sends a typed key once and never writes it back into the DOM', async () => {
    const { wrapper, calls } = await readCentre([providerFixture()], [
      { method: 'PATCH', path: '/providers/7', body: providerFixture() },
    ])

    const editButton = wrapper.findAll('button').find((b) => b.text() === '编辑')!
    await editButton.trigger('click')
    await flushPromises()

    const password = wrapper.find('input[type="password"]')
    await password.setValue('sk-typed-in-browser-0123456789')

    await wrapper.findAll('button').find((b) => b.text().includes('保存配置'))!.trigger('click')
    await flushPromises()

    const patch = calls.find((call) => call.method === 'PATCH' && call.url.includes('/providers/7'))
    expect(patch!.body).toMatchObject({
      changeSecretValue: true,
      secretValue: 'sk-typed-in-browser-0123456789',
    })
    // The drawer closed after saving; the submitted key is gone from both the
    // rendered markup and the form state, so reopening starts blank.
    expect(wrapper.html()).not.toContain('sk-typed-in-browser-0123456789')
    const remaining = wrapper.find('input[type="password"]')
    expect((remaining.element as HTMLInputElement).value).toBe('')
  })

  it('clears the saved key only after an explicit confirmation action', async () => {
    const { wrapper, calls } = await readCentre(
      [providerFixture({ secretState: 'CONFIGURED', secretConfigured: true })],
      [
        {
          method: 'PATCH',
          path: '/providers/7',
          body: providerFixture({ secretState: 'NONE', secretConfigured: false }),
        },
      ],
    )

    const editButton = wrapper.findAll('button').find((b) => b.text() === '编辑')!
    await editButton.trigger('click')
    await flushPromises()

    const clearButton = wrapper
      .findAll('button')
      .find((b) => b.text().includes('清除已保存的密钥'))!
    await clearButton.trigger('click')
    await flushPromises()
    // The key box is disabled while a clear is pending, so one action cannot
    // both clear and set a value.
    expect(wrapper.find('input[type="password"]').attributes('disabled')).toBeDefined()

    await wrapper.findAll('button').find((b) => b.text().includes('保存配置'))!.trigger('click')
    await flushPromises()

    const patch = calls.find((call) => call.method === 'PATCH' && call.url.includes('/providers/7'))
    expect(patch!.body).toMatchObject({ changeSecretValue: true, secretValue: null })
  })

  it('keeps a legacy environment reference read-only and explains how to replace it', async () => {
    const { wrapper } = await readCentre([
      providerFixture({
        secretRef: 'DEEPSEEK_API_KEY',
        secretSource: 'ENV',
        secretState: 'CONFIGURED',
        secretConfigured: true,
      }),
    ])

    const editButton = wrapper.findAll('button').find((b) => b.text() === '编辑')!
    await editButton.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('来自服务端环境变量')
    expect(wrapper.text()).toContain('DEEPSEEK_API_KEY')
    // No editable environment-name field: the forward path is typing a key.
    expect(wrapper.findAll('input[placeholder="DEEPSEEK_API_KEY"]')).toHaveLength(0)
    expect(wrapper.findAll('input[type="password"]')).toHaveLength(1)
  })
})
