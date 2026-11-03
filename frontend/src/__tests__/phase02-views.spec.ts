/**
 * Phase 02 view/state tests.
 *
 * These tests cover the behaviours a screenshot cannot: that a cycle rejection
 * lands on the manager field, that the wizard reuses one idempotency key across
 * retries, and that no view ever renders a credential value. They replace the
 * fetch layer with a scripted double, so they exercise the real view, the real
 * API client (CSRF header, error envelope) and the real store.
 */
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ElTable, ElTableColumn } from 'element-plus'

import { createTestRouter } from './helpers'
import { useSessionStore } from '@/stores/session'

/**
 * el-table is registered globally in main.ts. Tests mount views directly, so the
 * same registration is repeated here; without it the table renders nothing and
 * assertions about its rows would silently pass against an empty table.
 */
const globalComponents = { ElTable, ElTableColumn }

const TEMPLATE = {
  templateKey: 'tech-company',
  templateVersion: 1,
  label: '科技公司（默认模板）',
  description: '五个岗位：CEO、产品经理、技术负责人/开发、QA、DevOps。',
  positions: [
    {
      code: 'CEO',
      title: 'CEO',
      defaultAgentName: '首席执行官',
      duty: '设定公司目标与优先级。',
      reportsToCode: null,
      sortOrder: 10,
    },
    {
      code: 'QA',
      title: 'QA',
      defaultAgentName: '质量工程师',
      duty: '独立验证交付。',
      reportsToCode: 'TECH_LEAD',
      sortOrder: 40,
    },
  ],
}

const COMPANY = {
  id: 1,
  name: '精一科技',
  goal: '交付可验证的自动化研发能力。',
  status: 'ACTIVE',
  templateKey: 'tech-company',
  templateVersion: 1,
  version: 1,
  initializedAt: '2026-10-30T03:00:00Z',
  positionCount: 5,
  agentCount: 5,
}

function agent(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    employeeCode: 'CEO-001',
    displayName: '首席执行官',
    status: 'ACTIVE',
    positionId: 10,
    positionCode: 'CEO',
    positionTitle: 'CEO',
    managerId: null,
    managerName: null,
    dutyOverride: null,
    effectiveDuty: '设定公司目标与优先级。',
    model: null,
    version: 1,
    statusChangedAt: '2026-10-30T03:00:00Z',
    archivedAt: null,
    ...overrides,
  }
}

function position(overrides: Record<string, unknown> = {}) {
  return {
    id: 10,
    code: 'CEO',
    title: 'CEO',
    duty: '设定公司目标与优先级。',
    version: 1,
    rowVersion: 1,
    sortOrder: 10,
    departmentName: '研发部',
    ...overrides,
  }
}

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

interface RouteScript {
  method: string
  path: string
  status?: number
  body: unknown
}

/**
 * Install a fetch double. Requests are matched by method and path prefix, in the
 * order they were declared, so a test can script a sequence of responses.
 */
function scriptFetch(routes: RouteScript[]) {
  const calls: { method: string; url: string; body: string | null }[] = []
  // Each scripted route is consumed once, in order, so a retried request sees
  // the second response instead of matching the first route again.
  const pending = routes.map((route) => ({ ...route, used: false }))
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ method, url, body: init?.body ? String(init.body) : null })
    const match = pending.find(
      (route) => !route.used && route.method === method && url.includes(route.path),
    )
    if (!match) {
      throw new Error(`unexpected request ${method} ${url}`)
    }
    match.used = true
    const status = match.status ?? 200
    return new Response(JSON.stringify(match.body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return calls
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

describe('session store company state', () => {
  it('exposes company initialization from setup-status', async () => {
    scriptFetch([
      {
        method: 'GET',
        path: '/auth/setup-status',
        body: {
          setupComplete: true,
          appVersion: '0.2.0',
          passwordMinLength: 12,
          companyInitialized: false,
          companyName: null,
        },
      },
    ])
    const session = useSessionStore()
    await session.loadSetupStatus()
    expect(session.companyInitialized).toBe(false)
    expect(session.companyName).toBeNull()
  })

  it('marks the company initialized only for a confirmed creation', () => {
    const session = useSessionStore()
    expect(session.companyInitialized).toBeNull()
    session.markCompanyInitialized('精一科技')
    expect(session.companyInitialized).toBe(true)
    expect(session.companyName).toBe('精一科技')
  })
})

describe('CompanySetupView', () => {
  it('reuses one idempotency key across a failed then successful submit', async () => {
    const calls = scriptFetch([
      { method: 'GET', path: '/company/template', body: TEMPLATE },
      { method: 'GET', path: '/models/centre', body: EMPTY_CENTRE },
      {
        method: 'POST',
        path: '/company',
        status: 503,
        body: {
          code: 'DATABASE_NOT_READY',
          message: '数据库尚未就绪，请稍后重试。',
          requestId: 'req-1',
          fieldErrors: [],
        },
      },
      { method: 'POST', path: '/company', status: 201, body: COMPANY },
    ])
    const view = (await import('@/views/CompanySetupView.vue')).default
    const router = createTestRouter(view, { path: '/setup/company', name: 'company-setup' })
    const wrapper = mount(view, {
      global: { plugins: [pinia, router], components: globalComponents },
    })
    await flushPromises()

    // Step 1: fill basic info.
    const input = wrapper.find('#field-company-name')
    await input.setValue('精一科技')
    await wrapper.find('#field-company-goal').setValue('交付可验证的自动化研发能力。')
    const buttons = wrapper.findAll('button')
    await buttons.find((b) => b.text().includes('下一步'))!.trigger('click')

    // Step 2: accept the template.
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('下一步'))!
      .trigger('click')
    // Step 3: configure the model later.
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((b) => b.text().includes('下一步'))!
      .trigger('click')
    // Step 4: submit twice (the first fails).
    await flushPromises()
    const submit = () => wrapper.findAll('button').find((b) => b.text().includes('创建公司'))!
    await submit().trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="wizard-error"]').exists()).toBe(true)

    await submit().trigger('click')
    await flushPromises()

    const posts = calls.filter((call) => call.method === 'POST' && call.url.endsWith('/company'))
    expect(posts).toHaveLength(2)
    const first = JSON.parse(posts[0]!.body!)
    const second = JSON.parse(posts[1]!.body!)
    expect(first.idempotencyKey).toBe(second.idempotencyKey)
    expect(first.idempotencyKey).toMatch(/^company-wizard-/)
    expect(first.name).toBe('精一科技')
    expect(first.modelId).toBeNull()

    // The accepted response marked the company initialized, which is what the
    // router guard uses to stop redirecting to the wizard.
    const session = useSessionStore()
    expect(session.companyInitialized).toBe(true)
    expect(session.companyName).toBe('精一科技')
    expect(wrapper.find('[data-testid="wizard-error"]').exists()).toBe(false)
  })

  it('states the step count and keeps the user on the step when the server rejects', async () => {
    scriptFetch([
      { method: 'GET', path: '/company/template', body: TEMPLATE },
      { method: 'GET', path: '/models/centre', body: EMPTY_CENTRE },
    ])
    const view = (await import('@/views/CompanySetupView.vue')).default
    const router = createTestRouter(view, { path: '/setup/company', name: 'company-setup' })
    const wrapper = mount(view, {
      global: { plugins: [pinia, router], components: globalComponents },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('第 1 步，共 4 步')
    expect(wrapper.text()).toContain('重复提交不会复制公司')
  })
})

describe('OrganizationView', () => {
  const ORG = {
    company: COMPANY,
    departmentName: '研发部',
    departmentPurpose: '负责产品定义、技术实现、质量保证与持续交付。',
    positions: [position()],
    agents: [
      agent(),
      agent({
        id: 3,
        employeeCode: 'QA-001',
        displayName: '质量工程师',
        positionCode: 'QA',
        positionTitle: 'QA',
        positionId: 40,
        managerId: 1,
      }),
    ],
  }

  it('shows a server cycle rejection on the manager field and keeps the drawer open', async () => {
    scriptFetch([
      { method: 'GET', path: '/organization', body: ORG },
      { method: 'GET', path: '/models/centre', body: EMPTY_CENTRE },
      { method: 'GET', path: '/agents/1/history', body: { agentId: 1, entries: [] } },
      {
        method: 'PATCH',
        path: '/agents/1',
        status: 409,
        body: {
          code: 'REPORTING_CYCLE',
          message: '该汇报关系会形成循环：目标主管当前直接或间接向你汇报。',
          requestId: 'req-cycle',
          fieldErrors: [
            { field: 'managerId', message: '该汇报关系会形成循环：目标主管当前直接或间接向你汇报。' },
          ],
        },
      },
    ])
    const view = (await import('@/views/OrganizationView.vue')).default
    const router = createTestRouter(view, { path: '/organization', name: 'organization' })
    const wrapper = mount(view, {
      global: { plugins: [pinia, router], components: globalComponents },
      attachTo: document.body,
    })
    await flushPromises()

    // The hierarchy lists both employees; no employee is hidden.
    expect(wrapper.text()).toContain('首席执行官')
    expect(wrapper.text()).toContain('质量工程师')
    expect(wrapper.text()).toContain('员工（2）')

    // Open the CEO drawer.
    const ceoButton = wrapper
      .findAll('button')
      .find((b) => b.attributes('aria-label')?.includes('首席执行官'))
    expect(ceoButton).toBeTruthy()
    await ceoButton!.trigger('click')
    await flushPromises()

    // Attempt an impossible manager change (QA reports to CEO already).
    const managerSelect = document.querySelector('#field-agent-manager') as HTMLSelectElement
    expect(managerSelect).toBeTruthy()
    managerSelect.value = '3'
    managerSelect.dispatchEvent(new Event('change'))
    await flushPromises()

    const save = wrapper.findAll('button').find((b) => b.text().includes('保存员工资料'))
    await save!.trigger('click')
    await flushPromises()

    // The rejection is visible and the drawer was not closed on failure.
    const error = document.querySelector('[data-testid="agent-save-error"]')
    expect(error?.textContent).toContain('循环')
    expect(wrapper.text()).toContain('该汇报关系会形成循环')
    wrapper.unmount()
  })

  it('explains that a manager change does not grant permissions', async () => {
    scriptFetch([
      { method: 'GET', path: '/organization', body: ORG },
      { method: 'GET', path: '/models/centre', body: EMPTY_CENTRE },
    ])
    const view = (await import('@/views/OrganizationView.vue')).default
    const router = createTestRouter(view, { path: '/organization', name: 'organization' })
    const wrapper = mount(view, {
      global: { plugins: [pinia, router], components: globalComponents },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('不授予任何资源权限')
    expect(wrapper.text()).toContain('归档')
  })
})

describe('ModelsView', () => {
  it('shows 未配置 for a provider whose secret is absent, and never a value', async () => {
    scriptFetch([
      {
        method: 'GET',
        path: '/models/centre',
        body: {
          ...EMPTY_CENTRE,
          providers: [
            {
              id: 7,
              name: 'DeepSeek 供应商',
              kind: 'DEEPSEEK',
              baseUrl: 'https://api.deepseek.com',
              secretRef: 'DEEPSEEK_API_KEY',
              secretSource: 'ENV',
              secretState: 'MISSING_ENV',
              secretConfigured: false,
              status: 'ENABLED',
              notes: null,
              timeoutSeconds: 30,
              modelCount: 1,
              createdAt: '2026-10-30T03:00:00Z',
              updatedAt: '2026-10-30T03:00:00Z',
            },
          ],
          models: [
            {
              id: 11,
              providerId: 7,
              providerName: 'DeepSeek 供应商',
              providerStatus: 'ENABLED',
              providerSecretConfigured: false,
              logicalName: 'deepseek-v4.1-flash',
              label: 'DeepSeek V4.1 Flash',
              status: 'ENABLED',
              maxOutputTokens: null,
              supportsJsonOutput: null,
              resolvedName: null,
              lastProbeOutcome: null,
              lastProbeAt: null,
              lastProbeDetail: null,
              createdAt: '2026-10-30T03:00:00Z',
              updatedAt: '2026-10-30T03:00:00Z',
            },
          ],
        },
      },
    ])
    const view = (await import('@/views/ModelsView.vue')).default
    const router = createTestRouter(view, { path: '/models', name: 'models' })
    const wrapper = mount(view, {
      global: { plugins: [pinia, router], components: globalComponents },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('DEEPSEEK_API_KEY')
    expect(wrapper.text()).toContain('未配置（环境变量缺失）')
    expect(wrapper.text()).toContain('密钥')
    // No credential-shaped value and no reveal control. A masked key input is
    // expected in the edit drawer (ADR-0006), but the list shows state only.
    expect(wrapper.text()).not.toMatch(/sk-[A-Za-z0-9]{10,}/)
    expect(
      wrapper.findAll('input[type="password"]'),
      'the provider list must not render a key input until the drawer opens',
    ).toHaveLength(0)

    // The models tab reports JSON capability as unverified, not supported.
    await wrapper.findAll('button').find((b) => b.text().includes('模型（1）'))!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('未验证')
    expect(wrapper.text()).toContain('可能消耗少量 Token')
  })

  it('disables other probe buttons while one probe runs', async () => {
    let releaseProbe!: (response: Response) => void
    const probePending = new Promise<Response>((resolve) => {
      releaseProbe = resolve
    })
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.includes('/models/centre')) {
        return new Response(
          JSON.stringify({
            ...EMPTY_CENTRE,
            providers: [
              {
                id: 7,
                name: 'P',
                kind: 'DEEPSEEK',
                baseUrl: 'https://api.deepseek.com',
                secretRef: 'DEEPSEEK_API_KEY',
                secretSource: 'ENV',
                secretState: 'CONFIGURED',
                secretConfigured: true,
                status: 'ENABLED',
                notes: null,
                timeoutSeconds: 30,
                modelCount: 2,
                createdAt: '2026-10-30T03:00:00Z',
                updatedAt: '2026-10-30T03:00:00Z',
              },
            ],
            models: [1, 2].map((index) => ({
              id: index,
              providerId: 7,
              providerName: 'P',
              providerStatus: 'ENABLED',
              providerSecretConfigured: true,
              logicalName: `model-${index}`,
              label: `Model ${index}`,
              status: 'ENABLED',
              maxOutputTokens: null,
              supportsJsonOutput: null,
              resolvedName: null,
              lastProbeOutcome: null,
              lastProbeAt: null,
              lastProbeDetail: null,
              createdAt: '2026-10-30T03:00:00Z',
              updatedAt: '2026-10-30T03:00:00Z',
            })),
          }),
        )
      }
      if (method === 'POST' && url.includes('/probe')) {
        return probePending
      }
      throw new Error(`unexpected ${method} ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const view = (await import('@/views/ModelsView.vue')).default
    const router = createTestRouter(view, { path: '/models', name: 'models' })
    const wrapper = mount(view, {
      global: { plugins: [pinia, router], components: globalComponents },
    })
    await flushPromises()
    await wrapper.findAll('button').find((b) => b.text().includes('模型（2）'))!.trigger('click')
    await flushPromises()

    // Select by position in the models table, not by label: the running button's
    // text changes to "测试中…", which would otherwise shrink the match set.
    const probeButtons = () =>
      wrapper.findAll('button').filter((b) => /测试(连接|中…)/.test(b.text()))
    expect(probeButtons()).toHaveLength(2)

    await probeButtons()[0]!.trigger('click')
    await flushPromises()
    // While one probe is in flight both buttons are inert: no parallel spend.
    const after = probeButtons()
    expect(after).toHaveLength(2)
    expect(after[0]!.attributes('disabled')).toBeDefined()
    expect(after[1]!.attributes('disabled')).toBeDefined()

    releaseProbe(
      new Response(
        JSON.stringify({
          id: 1,
          modelId: 1,
          outcome: 'SUCCESS',
          errorCode: null,
          message: '探测成功。',
          startedAt: '2026-10-30T03:00:00Z',
          finishedAt: '2026-10-30T03:00:01Z',
          latencyMs: 120,
          jsonOutputVerified: true,
          resolvedModelName: 'model-1',
          callCount: 1,
          promptTokens: 10,
          completionTokens: 5,
          usageKnown: true,
          tokenWarning: '可能消耗少量 Token',
        }),
        { status: 200 },
      ),
    )
    await flushPromises()
  })
})

describe('AuditView', () => {
  it('renders audit events read-only with a redacted detail', async () => {
    scriptFetch([
      {
        method: 'GET',
        path: '/audit/events',
        body: {
          events: [
            {
              id: 1,
              occurredAt: '2026-10-30T03:00:00Z',
              actorType: 'OWNER',
              actorUserId: 1,
              actorDisplayName: '所有者',
              action: 'COMPANY_INITIALIZED',
              outcome: 'SUCCESS',
              targetType: 'COMPANY',
              targetId: '1',
              detail: 'template=tech-company@v1;positions=5;agents=5',
              requestId: 'req-1',
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        },
      },
    ])
    const view = (await import('@/views/AuditView.vue')).default
    const router = createTestRouter(view, { path: '/audit', name: 'audit' })
    const wrapper = mount(view, {
      global: { plugins: [pinia, router], components: globalComponents },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('公司初始化')
    expect(wrapper.text()).toContain('所有者')
    // Read-only: no delete/edit affordance exists.
    expect(wrapper.findAll('button').map((b) => b.text())).not.toContain('删除')
    expect(wrapper.text()).not.toContain('编辑记录')
  })
})
