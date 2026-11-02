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

describe('R8 partial save', () => {
  it.each(['/models/centre', '/organization'])('retains the saved version and draft when %s refresh fails', async (failedPath) => {
    let current = makeAgent()
    const expectedVersions: unknown[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.endsWith(failedPath) && expectedVersions.length > 0) {
          return response({ code: 'UNAVAILABLE', message: 'read unavailable' }, 503)
        }
        if (url.endsWith('/organization')) {
          return response({
            company,
            departmentName: 'review',
            departmentPurpose: 'review',
            positions: [position],
            agents: [current],
          })
        }
        if (url.endsWith('/models/centre')) {
          return response(centreBody())
        }
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
    expect((document.querySelector('#field-agent-display-name') as HTMLInputElement).value).toBe('changed-name')
    expect((document.querySelector('#field-agent-model') as HTMLSelectElement).value).toBe('1')
    expect(current.version).toBe(3)
    // The unsaved half is reported as a warning, not as a plain failure.
    expect(document.querySelector('[data-testid=agent-partial-save]')).not.toBeNull()
    // And no conflict error is shown: the retry succeeded.
    expect(document.querySelector('[data-testid=agent-save-error]')?.textContent ?? '').not.toContain(
      '请刷新',
    )
  })

})
