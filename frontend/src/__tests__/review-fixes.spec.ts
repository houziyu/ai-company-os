/**
 * Regressions for the Phase 02 independent review's frontend findings.
 *
 * Each test states the behaviour the review asked for and drives the real view,
 * the real API client (CSRF header, error envelope) and the real store; only the
 * HTTP responses are scripted. Server-side guarantees (unbind history, version
 * conflict) have their own PostgreSQL tests in `backend/tests/test_review_fixes.py`.
 */
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ElTable, ElTableColumn } from 'element-plus'

import { createTestRouter } from './helpers'

const globalComponents = { ElTable, ElTableColumn }

const company = {
  id: 1,
  name: 'review',
  goal: 'review',
  status: 'ACTIVE',
  version: 1,
  templateKey: 'tech-company',
  templateVersion: 1,
  initializedAt: '2026-10-30T00:00:00Z',
  positionCount: 1,
  agentCount: 1,
}

const model = {
  id: 1,
  providerId: 1,
  providerName: 'review',
  providerStatus: 'ENABLED',
  providerSecretConfigured: true,
  version: 1,
  logicalName: 'review',
  label: 'review-model',
  status: 'ENABLED',
  maxOutputTokens: null,
  supportsJsonOutput: null,
  probeEvidenceStale: false,
  resolvedName: null,
  lastProbeOutcome: null,
  lastProbeAt: null,
  lastProbeDetail: null,
  createdAt: '2026-10-30T00:00:00Z',
  updatedAt: '2026-10-30T00:00:00Z',
}

const provider = {
  id: 1,
  name: 'DeepSeek 预设',
  kind: 'DEEPSEEK',
  version: 3,
  baseUrl: 'https://api.deepseek.com',
  secretRef: 'DEEPSEEK_API_KEY',
  secretConfigured: false,
  status: 'ENABLED',
  notes: null,
  timeoutSeconds: 30,
  modelCount: 1,
  createdAt: '2026-10-30T00:00:00Z',
  updatedAt: '2026-10-30T00:00:00Z',
}

function makeAgent(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    employeeCode: 'CEO-001',
    displayName: 'review-agent',
    status: 'ACTIVE',
    positionId: 1,
    positionCode: 'CEO',
    positionTitle: 'CEO',
    managerId: null,
    managerName: null,
    dutyOverride: null,
    effectiveDuty: 'review-duty',
    model: null,
    version: 1,
    statusChangedAt: '2026-10-30T00:00:00Z',
    archivedAt: null,
    ...overrides,
  }
}

const position = {
  id: 1,
  code: 'CEO',
  title: 'CEO',
  duty: 'review-duty',
  version: 1,
  rowVersion: 1,
  sortOrder: 1,
  departmentName: 'review',
}

function centreBody(overrides: Record<string, unknown> = {}) {
  return {
    providers: [provider],
    models: [model],
    assignments: [],
    recentProbes: [],
    usage: {
      probeCalls: 0,
      measuredCalls: 0,
      partiallyMeasuredCalls: 0,
      unmeasuredCalls: 0,
      promptTokens: null,
      completionTokens: null,
      promptTokensUnreportedCalls: 0,
      completionTokensUnreportedCalls: 0,
    },
    deepseekPreset: {
      kind: 'DEEPSEEK',
      baseUrl: 'https://api.deepseek.com',
      secretRef: 'DEEPSEEK_API_KEY',
      suggestedModel: 'deepseek-v4.1-flash',
      notes: 'review',
    },
    ...overrides,
  }
}

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

let pinia: ReturnType<typeof createPinia>
let cleanup: (() => void) | undefined

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  document.cookie = 'acos_csrf=test-csrf'
})

afterEach(() => {
  cleanup?.()
  cleanup = undefined
  disposePinia(pinia)
  vi.unstubAllGlobals()
})

async function renderView(loader: () => Promise<{ default: unknown }>) {
  const view = (await loader()).default
  const router = createTestRouter(view)
  const wrapper = mount(view as never, {
    attachTo: document.body,
    global: { plugins: [pinia, router], components: globalComponents },
  })
  cleanup = () => wrapper.unmount()
  return wrapper
}

describe('R10 tab keyboard', () => {
  it('ArrowRight moves selection and focus through all three tabs', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(centreBody())))
    const wrapper = await renderView(() => import('@/views/ModelsView.vue'))
    await flushPromises()

    let tabs = wrapper.findAll('[role="tab"]')
    expect(tabs).toHaveLength(3)
    ;(tabs[0]!.element as HTMLElement).focus()
    await tabs[0]!.trigger('keydown', { key: 'ArrowRight' })
    await flushPromises()

    // Selection AND focus move together; a roving tabindex makes this mandatory.
    tabs = wrapper.findAll('[role="tab"]')
    expect(tabs[1]!.attributes('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(tabs[1]!.element)

    // The second arrow continues from the focused (now second) tab.
    ;(document.activeElement as HTMLElement).dispatchEvent(
      new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }),
    )
    await flushPromises()
    tabs = wrapper.findAll('[role="tab"]')
    expect(tabs[2]!.attributes('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(tabs[2]!.element)
  })

  it('ArrowLeft wraps backwards and ArrowRight wraps forwards', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(centreBody())))
    const wrapper = await renderView(() => import('@/views/ModelsView.vue'))
    await flushPromises()

    const tabs = wrapper.findAll('[role="tab"]')
    ;(tabs[0]!.element as HTMLElement).focus()
    await tabs[0]!.trigger('keydown', { key: 'ArrowLeft' })
    await flushPromises()
    expect(wrapper.findAll('[role="tab"]')[2]!.attributes('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(wrapper.findAll('[role="tab"]')[2]!.element)

    await wrapper.findAll('[role="tab"]')[2]!.trigger('keydown', { key: 'ArrowRight' })
    await flushPromises()
    expect(wrapper.findAll('[role="tab"]')[0]!.attributes('aria-selected')).toBe('true')
  })

  it('Home and End jump to the first and last tab', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(centreBody())))
    const wrapper = await renderView(() => import('@/views/ModelsView.vue'))
    await flushPromises()

    await wrapper.findAll('[role="tab"]')[1]!.trigger('keydown', { key: 'End' })
    await flushPromises()
    expect(wrapper.findAll('[role="tab"]')[2]!.attributes('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(wrapper.findAll('[role="tab"]')[2]!.element)

    await wrapper.findAll('[role="tab"]')[2]!.trigger('keydown', { key: 'Home' })
    await flushPromises()
    expect(wrapper.findAll('[role="tab"]')[0]!.attributes('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(wrapper.findAll('[role="tab"]')[0]!.element)
  })

  it('keeps only the selected tab in the Tab order (roving tabindex)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(centreBody())))
    const wrapper = await renderView(() => import('@/views/ModelsView.vue'))
    await flushPromises()

    await wrapper.findAll('[role="tab"]')[1]!.trigger('click')
    await flushPromises()
    const tabs = wrapper.findAll('[role="tab"]')
    expect(tabs.map(tab => tab.attributes('tabindex'))).toEqual(['-1', '0', '-1'])
  })
})

describe('R11 unbind', () => {
  const boundAgent = makeAgent({
    model: {
      modelId: 1,
      logicalName: 'review',
      label: 'review-model',
      providerId: 1,
      providerName: 'review',
      providerKind: 'DEEPSEEK',
      supportsJsonOutput: null,
    },
  })

  it('selecting 未绑定 sends an explicit unbind request', async () => {
    const calls: { method: string; url: string }[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        const method = (init?.method ?? 'GET').toUpperCase()
        if (method !== 'GET') calls.push({ method, url })
        if (url.endsWith('/organization')) {
          return response({
            company,
            departmentName: 'review',
            departmentPurpose: 'review',
            positions: [position],
            agents: [boundAgent],
          })
        }
        if (url.endsWith('/models/centre')) return response(centreBody())
        if (url.endsWith('/history')) return response({ agentId: 1, entries: [] })
        return response(boundAgent)
      }),
    )
    const wrapper = await renderView(() => import('@/views/OrganizationView.vue'))
    await flushPromises()

    await wrapper.get('.org__tree-button').trigger('click')
    await flushPromises()

    const select = document.querySelector('#field-agent-model') as HTMLSelectElement
    select.selectedIndex = 0
    select.dispatchEvent(new Event('change', { bubbles: true }))
    await flushPromises()

    const save = [...document.querySelectorAll('button')].find(button =>
      button.textContent?.includes('保存员工资料'),
    )!
    save.click()
    await flushPromises()

    const unbinds = calls.filter(call => call.url.endsWith('/model-unbind'))
    expect(unbinds).toHaveLength(1)
    expect(unbinds[0]!.method).toBe('POST')
    // The "unbound" option must not be an assignment of some other model.
    expect(calls.filter(call => call.url.endsWith('/model-assignment'))).toHaveLength(0)
  })
})

describe('R8 partial save', () => {
  it('after a successful profile save the next attempt uses the new version', async () => {
    let current = makeAgent()
    const expectedVersions: unknown[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.endsWith('/organization')) {
          return response({
            company,
            departmentName: 'review',
            departmentPurpose: 'review',
            positions: [position],
            agents: [current],
          })
        }
        if (url.endsWith('/models/centre')) return response(centreBody())
        if (url.endsWith('/history')) return response({ agentId: 1, entries: [] })
        if (url.endsWith('/model-assignment')) {
          return response(
            { code: 'MODEL_BINDING_UNAVAILABLE', message: '模型不可用', fieldErrors: [] },
            409,
          )
        }
        if (url.endsWith('/agents/1')) {
          const payload = JSON.parse(String(init?.body))
          expectedVersions.push(payload.expectedVersion)
          if (payload.expectedVersion !== current.version) {
            return response(
              { code: 'CONCURRENT_UPDATE', message: '请刷新', fieldErrors: [] },
              409,
            )
          }
          current = { ...current, version: current.version + 1, displayName: payload.displayName }
          return response(current)
        }
        throw new Error(`Unexpected request ${url}`)
      }),
    )
    const wrapper = await renderView(() => import('@/views/OrganizationView.vue'))
    await flushPromises()

    await wrapper.get('.org__tree-button').trigger('click')
    await flushPromises()

    const name = document.querySelector('#field-agent-display-name') as HTMLInputElement
    name.value = 'changed-name'
    name.dispatchEvent(new Event('input', { bubbles: true }))
    const select = document.querySelector('#field-agent-model') as HTMLSelectElement
    select.selectedIndex = 1
    select.dispatchEvent(new Event('change', { bubbles: true }))
    await flushPromises()

    const save = () =>
      [...document.querySelectorAll('button')].find(button =>
        button.textContent?.includes('保存员工资料'),
      )!

    save().click()
    await flushPromises()
    save().click()
    await flushPromises()

    // First attempt uses 1; the profile is saved (1 -> 2) and the binding fails.
    // The retry must therefore send 2, not the stale 1, and it succeeds (2 -> 3).
    // A stale retry would have produced a third entry (or a 409) instead.
    expect(expectedVersions).toEqual([1, 2])
    expect(current.version).toBe(3)
    // The unsaved half is reported as a warning, not as a plain failure.
    expect(document.querySelector('[data-testid=agent-partial-save]')).not.toBeNull()
    // And no conflict error is shown: the retry succeeded.
    expect(document.querySelector('[data-testid=agent-save-error]')?.textContent ?? '').not.toContain(
      '请刷新',
    )
  })

  it('keeps the unsaved model choice when the binding step fails', async () => {
    const agent = makeAgent()
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith('/organization')) {
          return response({
            company,
            departmentName: 'review',
            departmentPurpose: 'review',
            positions: [position],
            agents: [agent],
          })
        }
        if (url.endsWith('/models/centre')) return response(centreBody())
        if (url.endsWith('/history')) return response({ agentId: 1, entries: [] })
        if (url.endsWith('/model-assignment')) {
          return response(
            { code: 'MODEL_BINDING_UNAVAILABLE', message: '模型不可用', fieldErrors: [] },
            409,
          )
        }
        if (url.endsWith('/agents/1')) {
          return response({ ...agent, version: agent.version + 1 }, 200)
        }
        throw new Error(`Unexpected request ${url}`)
      }),
    )
    const wrapper = await renderView(() => import('@/views/OrganizationView.vue'))
    await flushPromises()
    await wrapper.get('.org__tree-button').trigger('click')
    await flushPromises()

    const select = document.querySelector('#field-agent-model') as HTMLSelectElement
    select.selectedIndex = 1
    select.dispatchEvent(new Event('change', { bubbles: true }))
    await flushPromises()

    const save = [...document.querySelectorAll('button')].find(button =>
      button.textContent?.includes('保存员工资料'),
    )!
    save.click()
    await flushPromises()

    const notice = document.querySelector('[data-testid=agent-partial-save]')
    expect(notice?.textContent).toContain('资料已保存')
    expect(notice?.textContent).toContain('模型绑定未保存')
    const selectAfter = document.querySelector('#field-agent-model') as HTMLSelectElement
    expect(selectAfter.value).toBe('1')
  })
})

describe('R7/R9 provider form contract', () => {
  it('sends expectedVersion and an explicit null when notes are cleared', async () => {
    const patches: Record<string, unknown>[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        const method = (init?.method ?? 'GET').toUpperCase()
        if (url.endsWith('/models/centre')) return response(centreBody())
        if (method === 'PATCH' && url.includes('/providers/')) {
          patches.push(JSON.parse(String(init?.body)))
          return response({ ...provider, notes: null, version: provider.version + 1 })
        }
        throw new Error(`Unexpected request ${method} ${url}`)
      }),
    )
    const wrapper = await renderView(() => import('@/views/ModelsView.vue'))
    await flushPromises()

    await wrapper.findAll('button').find(button => button.text() === '编辑')!.trigger('click')
    await flushPromises()

    const notes = document.querySelector('#field-provider-notes') as HTMLTextAreaElement
    expect(notes).not.toBeNull()
    notes.value = ''
    notes.dispatchEvent(new Event('input', { bubbles: true }))

    await wrapper
      .findAll('button')
      .find(button => button.text().includes('保存配置'))!
      .trigger('click')
    await flushPromises()

    expect(patches).toHaveLength(1)
    expect(patches[0]!.expectedVersion).toBe(provider.version)
    // Explicit null, not omitted: the server needs to distinguish the two.
    expect('notes' in patches[0]!).toBe(true)
    expect(patches[0]!.notes).toBeNull()
  })
})
