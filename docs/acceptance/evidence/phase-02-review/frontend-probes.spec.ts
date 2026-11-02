/** Independent review evidence; moved out of the production test tree after execution. */
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { ElTable, ElTableColumn } from 'element-plus'

import ModelsView from '@/views/ModelsView.vue'
import OrganizationView from '@/views/OrganizationView.vue'
import { createTestRouter } from './helpers'

const company = { id: 1, name: 'review', goal: 'review', status: 'ACTIVE', version: 1,
  templateKey: 'tech-company', templateVersion: 1, initializedAt: '2026-10-30T00:00:00Z',
  positionCount: 1, agentCount: 1 }
const model = { id: 1, providerId: 1, providerName: 'review', providerStatus: 'ENABLED',
  providerSecretConfigured: false, logicalName: 'review', label: 'review-model', status: 'ENABLED',
  maxOutputTokens: null, supportsJsonOutput: null, resolvedName: null, lastProbeOutcome: null,
  lastProbeAt: null, lastProbeDetail: null, createdAt: '2026-10-30T00:00:00Z', updatedAt: '2026-10-30T00:00:00Z' }
const agent = { id: 1, employeeCode: 'CEO-001', displayName: 'review-agent', status: 'ACTIVE',
  positionId: 1, positionCode: 'CEO', positionTitle: 'CEO', managerId: null, managerName: null,
  dutyOverride: null, effectiveDuty: 'review-duty', model: null, version: 1,
  statusChangedAt: '2026-10-30T00:00:00Z', archivedAt: null }
const position = { id: 1, code: 'CEO', title: 'CEO', duty: 'review-duty', version: 1,
  rowVersion: 1, sortOrder: 1, departmentName: 'review' }
const centre = { providers: [], models: [model], assignments: [], recentProbes: [],
  usage: { probeCalls: 0, measuredCalls: 0, unmeasuredCalls: 0, promptTokens: null, completionTokens: null },
  deepseekPreset: { kind: 'DEEPSEEK', baseUrl: 'https://example.test', secretRef: 'REVIEW_KEY',
    suggestedModel: 'review', notes: 'review' } }

let pinia: ReturnType<typeof createPinia>
let cleanup: (() => void) | undefined
beforeEach(() => { pinia = createPinia(); setActivePinia(pinia) })
afterEach(() => { cleanup?.(); cleanup = undefined; disposePinia(pinia); vi.unstubAllGlobals() })

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function render(view: typeof ModelsView | typeof OrganizationView) {
  const router = createTestRouter(view)
  const wrapper = mount(view, { attachTo: document.body,
    global: { plugins: [pinia, router], components: { ElTable, ElTableColumn } } })
  cleanup = () => wrapper.unmount()
  return wrapper
}

it('R10: keyboard users can reach the third model-centre tab', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => response(centre)))
  const wrapper = render(ModelsView)
  await flushPromises()
  let tabs = wrapper.findAll('[role="tab"]')
  ;(tabs[0]!.element as HTMLElement).focus()
  await tabs[0]!.trigger('keydown', { key: 'ArrowRight' })
  await flushPromises()
  // Physical focus remains on tab 0, so the second arrow also goes there.
  ;(document.activeElement as HTMLElement).dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
  await flushPromises()
  tabs = wrapper.findAll('[role="tab"]')
  console.log('REVIEW R10', { selected: tabs.findIndex(tab => tab.attributes('aria-selected') === 'true'),
    focused: tabs.findIndex(tab => tab.element === document.activeElement) })
  expect(tabs[2]!.attributes('aria-selected')).toBe('true')
})

it('R11: selecting unbound must not silently keep the existing model', async () => {
  const bound = { ...agent, model: { modelId: 1, logicalName: 'review', label: 'review-model',
    providerId: 1, providerName: 'review', providerKind: 'DEEPSEEK', supportsJsonOutput: null } }
  const calls: { method: string; url: string; body: Record<string, unknown> }[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); const method = init?.method ?? 'GET'
    if (method !== 'GET') calls.push({ method, url, body: JSON.parse(String(init?.body)) })
    if (url.endsWith('/organization')) return response({ company, departmentName: 'review', departmentPurpose: 'review', positions: [position], agents: [bound] })
    if (url.endsWith('/models/centre')) return response(centre)
    if (url.endsWith('/history')) return response({ agentId: 1, entries: [] })
    return response(bound)
  }))
  const wrapper = render(OrganizationView)
  await flushPromises()
  await wrapper.get('.org__tree-button').trigger('click')
  await flushPromises()
  const select = document.querySelector('#field-agent-model') as HTMLSelectElement
  select.selectedIndex = 0
  select.dispatchEvent(new Event('change', { bubbles: true }))
  await flushPromises()
  const save = [...document.querySelectorAll('button')].find(button => button.textContent?.includes('保存员工资料'))!
  save.click()
  await flushPromises()
  const bindings = calls.filter(call => call.url.endsWith('/model-assignment'))
  console.log('REVIEW R11', { requestedBindings: bindings.length, displayedUnboundOption: true })
  expect(bindings.length).toBe(1)
})

it('R8: a failed model binding must not make the next profile save stale', async () => {
  let current = { ...agent }
  const expectedVersions: unknown[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/organization')) return response({ company, departmentName: 'review', departmentPurpose: 'review', positions: [position], agents: [current] })
    if (url.endsWith('/models/centre')) return response(centre)
    if (url.endsWith('/history')) return response({ agentId: 1, entries: [] })
    if (url.endsWith('/model-assignment')) return response({ code: 'MODEL_BINDING_UNAVAILABLE', message: '模型已停用', fieldErrors: [] }, 409)
    if (url.endsWith('/agents/1')) {
      const payload = JSON.parse(String(init?.body))
      expectedVersions.push(payload.expectedVersion)
      if (payload.expectedVersion !== current.version) return response({ code: 'CONCURRENT_UPDATE', message: '请刷新', fieldErrors: [] }, 409)
      current = { ...current, version: current.version + 1, displayName: payload.displayName }
      return response(current)
    }
    throw new Error(`Unexpected request ${url}`)
  }))
  const wrapper = render(OrganizationView)
  await flushPromises()
  await wrapper.get('.org__tree-button').trigger('click')
  await flushPromises()
  const name = document.querySelector('#field-agent-display-name') as HTMLInputElement
  name.value = 'changed-name'; name.dispatchEvent(new Event('input', { bubbles: true }))
  const select = document.querySelector('#field-agent-model') as HTMLSelectElement
  select.selectedIndex = 1; select.dispatchEvent(new Event('change', { bubbles: true }))
  await flushPromises()
  const save = [...document.querySelectorAll('button')].find(button => button.textContent?.includes('保存员工资料'))!
  save.click(); await flushPromises()
  save.click(); await flushPromises()
  console.log('REVIEW R8 UI', { expectedVersions, serverVersion: current.version })
  expect(expectedVersions).toEqual([1, 2])
})
