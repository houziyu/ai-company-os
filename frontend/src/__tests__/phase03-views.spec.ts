/**
 * Phase 03 frontend tests.
 *
 * These cover what a screenshot cannot: that a status is labelled with Chinese
 * text and never with colour alone, that unknown usage renders as 待核对 instead
 * of 0, that the task page distinguishes a live stream from a dropped one, that
 * every submission carries an idempotency key, and that the board groups by the
 * server's status rather than by a local guess.
 */
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createTestRouter } from './helpers'
import BudgetMeter from '@/components/BudgetMeter.vue'
import TaskDetailView from '@/views/TaskDetailView.vue'
import ProjectsView from '@/views/ProjectsView.vue'
import {
  BOARD_COLUMNS,
  demoBehaviorLabel,
  formatTokensOrPending,
  ledgerKind,
  taskStatus,
} from '@/api/task-presentation'
import type { BudgetUsage, TaskDetail, TaskSummary } from '@/api/task-types'

interface RouteScript {
  method: string
  path: string
  status?: number
  body: unknown
}

function scriptFetch(routes: RouteScript[]) {
  const calls: { method: string; url: string; body: string | null }[] = []
  const pending = routes.map((route) => ({ ...route, used: false }))
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ method, url, body: init?.body ? String(init.body) : null })
    const match = pending.find(
      (route) => !route.used && route.method === method && url.includes(route.path),
    )
    if (!match) throw new Error(`unexpected request ${method} ${url}`)
    match.used = true
    return new Response(JSON.stringify(match.body), {
      status: match.status ?? 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return calls
}

/** EventSource is not implemented in jsdom; a controllable double is enough to
 *  assert that the page distinguishes live from disconnected. */
class FakeEventSource {
  static instances: FakeEventSource[] = []
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 2
  url: string
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  closed = false
  readyState = 0
  private listeners = new Map<string, ((event: MessageEvent<string>) => void)[]>()

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) {
    const current = this.listeners.get(type) ?? []
    current.push(listener)
    this.listeners.set(type, current)
  }

  emit(type: string, data: unknown) {
    const payload = JSON.stringify(data)
    for (const listener of this.listeners.get(type) ?? []) {
      listener(new MessageEvent(type, { data: payload }))
    }
  }

  open() {
    this.readyState = 1
    this.onopen?.()
  }

  fail() {
    this.readyState = 0
    this.onerror?.()
  }

  /** Simulate the fatal case: the browser gives up on a non-2xx response. */
  failFatally() {
    this.close()
    this.onerror?.()
  }

  close() {
    this.closed = true
    this.readyState = 2
  }
}

const BUDGET: BudgetUsage = {
  limitTokens: 100000,
  consumedTokens: 900,
  reservedTokens: 100,
  availableTokens: 99000,
  needsReconciliationCount: 0,
}

const PROJECT = {
  id: 7,
  key: 'DEMO',
  name: '演练项目',
  objective: '验证调度与预算。',
  status: 'ACTIVE' as const,
  runMode: 'DEMO' as const,
  version: 1,
  createdAt: '2026-11-02T02:00:00Z',
  updatedAt: '2026-11-02T02:00:00Z',
  taskCounts: { QUEUED: 1, SUCCEEDED: 2 },
  budget: BUDGET,
}

function taskSummary(overrides: Partial<TaskSummary> = {}): TaskSummary {
  return {
    id: 11,
    projectId: 7,
    projectName: '演练项目',
    companyId: 1,
    title: '演练任务',
    status: 'QUEUED',
    runMode: 'DEMO',
    demoBehavior: 'SUCCESS',
    budgetTokens: 1000,
    attemptCount: 0,
    maxAutoRetries: 1,
    assigneeAgentId: null,
    assigneeName: null,
    currentExecutionId: null,
    currentExecutionStatus: null,
    lastErrorCode: null,
    lastErrorMessage: null,
    cancelRequestedAt: null,
    createdAt: '2026-11-02T02:10:00Z',
    updatedAt: '2026-11-02T02:10:00Z',
    startedAt: null,
    finishedAt: null,
    budget: BUDGET,
    cancelPending: false,
    ...overrides,
  }
}

function taskDetail(overrides: Partial<TaskDetail> = {}): TaskDetail {
  return {
    ...taskSummary(),
    objective: '执行一个确定的演练步骤。',
    acceptance: '两步完成且用量已登记。',
    resultSummary: null,
    executions: [],
    events: [
      {
        id: 1,
        eventType: 'TASK_CREATED',
        statusFrom: null,
        statusTo: 'QUEUED',
        actorType: 'OWNER',
        message: '任务已进入队列。',
        detail: null,
        createdAt: '2026-11-02T02:10:00Z',
      },
    ],
    idempotencyKey: 'key-1',
    ...overrides,
  }
}

let pinia: ReturnType<typeof createPinia>

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  document.cookie = 'acos_csrf=test-csrf-token'
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource as unknown as typeof EventSource)
})

afterEach(() => {
  disposePinia(pinia)
  vi.unstubAllGlobals()
})

// ------------------------------------------------------------------ mapping
describe('task status presentation', () => {
  it('gives every status Chinese text and a tone, never colour alone', () => {
    const statuses = [
      'QUEUED',
      'RUNNING',
      'WAITING_APPROVAL',
      'BLOCKED',
      'SUCCEEDED',
      'FAILED',
      'CANCELLED',
      'INTERRUPTED',
    ] as const
    for (const status of statuses) {
      const presentation = taskStatus(status)
      expect(presentation.label.length).toBeGreaterThan(1)
      expect(presentation.tone).toBeTruthy()
    }
  })

  it('renders unknown usage as 待核对 instead of zero', () => {
    expect(formatTokensOrPending(null, false)).toBe('待核对')
    expect(formatTokensOrPending(0, true)).toBe('0')
  })

  it('labels ledger kinds and demo behaviours in Chinese', () => {
    expect(ledgerKind('SETTLE')).toBe('结算')
    expect(demoBehaviorLabel('UNKNOWN_USAGE')).toContain('用量未知')
  })

  it('keeps every board column keyed by a real task status', () => {
    for (const column of BOARD_COLUMNS) {
      expect(taskStatus(column.status).label).toBe(column.title)
    }
  })
})

// -------------------------------------------------------------- BudgetMeter
describe('BudgetMeter', () => {
  it('shows all four amounts and flags pending reconciliation in words', () => {
    const wrapper = mount(BudgetMeter, {
      props: {
        budget: { ...BUDGET, needsReconciliationCount: 2, consumedTokens: 0 },
        label: '公司总额度',
      },
    })
    const text = wrapper.text()
    expect(text).toContain('上限')
    expect(text).toContain('已用')
    expect(text).toContain('预留')
    expect(text).toContain('可用')
    expect(text).toContain('2 笔预留的用量未获供应商确认')
    // The bar is decorative and carries an accessible summary.
    expect(wrapper.find('[role="img"]').attributes('aria-label')).toContain('额度使用')
  })
})

// ------------------------------------------------------------ Projects view
describe('ProjectsView', () => {
  it('groups tasks by server status and shows the demo label on each card', async () => {
    scriptFetch([
      { method: 'GET', path: '/projects', body: { projects: [PROJECT] } },
      {
        method: 'GET',
        path: '/tasks',
        body: {
          tasks: [
            taskSummary({ id: 11, status: 'QUEUED', title: '排队任务' }),
            taskSummary({ id: 12, status: 'SUCCEEDED', title: '完成任务' }),
            taskSummary({
              id: 13,
              status: 'FAILED',
              title: '失败任务',
              lastErrorCode: 'SIMULATED_PERMANENT',
              lastErrorMessage: '演练：模拟永久失败（不自动重试）。',
            }),
          ],
          total: 3,
          limit: 200,
          offset: 0,
          counts: { QUEUED: 1, SUCCEEDED: 1, FAILED: 1 },
        },
      },
    ])
    const router = createTestRouter(ProjectsView, { path: '/projects' })
    await router.push('/projects')
    const wrapper = mount(ProjectsView, { global: { plugins: [router, pinia] } })
    await flushPromises()

    expect(wrapper.text()).toContain('排队任务')
    expect(wrapper.text()).toContain('完成任务')
    expect(wrapper.text()).toContain('演练：模拟永久失败')
    // The rehearsal label is on every card; a demo result must not read as real.
    expect(wrapper.text()).toContain('演练行为：正常完成')
    // The board renders one column per *populated* status: empty columns would
    // push the real cards off-screen. Their zero counts are stated in the note.
    expect(wrapper.find('#column-QUEUED').exists()).toBe(true)
    expect(wrapper.find('#column-SUCCEEDED').exists()).toBe(true)
    expect(wrapper.find('#column-FAILED').exists()).toBe(true)
    expect(wrapper.find('#column-CANCELLED').exists()).toBe(false)
    expect(wrapper.text()).toContain('空状态列不占宽度')
    expect(wrapper.text()).toContain('已取消 0')
    // The page never offers a way to set a status directly.
    expect(wrapper.find('select[name="status"]').exists()).toBe(false)
  })

  it('sends an idempotency key with task creation', async () => {
    const calls = scriptFetch([
      { method: 'GET', path: '/projects', body: { projects: [PROJECT] } },
      {
        method: 'GET',
        path: '/tasks',
        body: { tasks: [], total: 0, limit: 200, offset: 0, counts: {} },
      },
      {
        method: 'POST',
        path: '/tasks',
        status: 201,
        body: {
          task: taskSummary({ id: 99 }),
          created: true,
          message: '任务已创建并进入队列。',
        },
      },
    ])
    const router = createTestRouter(ProjectsView, { path: '/projects' })
    await router.push('/projects')
    const wrapper = mount(ProjectsView, { global: { plugins: [router, pinia] } })
    await flushPromises()

    // Open the task drawer and submit a minimal task.
    const buttons = wrapper.findAll('button')
    const createTaskButton = buttons.find((button) => button.text() === '新建任务')
    expect(createTaskButton).toBeTruthy()
    await createTaskButton?.trigger('click')
    await flushPromises()

    const inputs = wrapper.findAll('input')
    // The first two text inputs in the task drawer are title and objective.
    await inputs[0]?.setValue('新任务')
    await wrapper.find('textarea').setValue('目标')
    const submit = wrapper
      .findAll('button')
      .find((button) => button.text() === '创建任务')
    await submit?.trigger('click')
    await flushPromises()

    const post = calls.find((call) => call.method === 'POST')
    expect(post).toBeTruthy()
    const body = JSON.parse(post?.body ?? '{}') as Record<string, unknown>
    expect(typeof body.idempotencyKey).toBe('string')
    expect((body.idempotencyKey as string).length).toBeGreaterThan(8)
    expect(body.demoBehavior).toBe('SUCCESS')
  })
})

// ---------------------------------------------------------- Task detail view
describe('TaskDetailView', () => {
  it('marks a dropped stream as possibly stale and keeps the task status', async () => {
    scriptFetch([
      { method: 'GET', path: '/tasks/11', body: taskDetail() },
    ])
    const router = createTestRouter(TaskDetailView, {
      path: '/tasks/:id',
      name: 'task-detail',
    })
    await router.push('/tasks/11')
    const wrapper = mount(TaskDetailView, { global: { plugins: [router, pinia] } })
    await flushPromises()

    expect(wrapper.text()).toContain('排队中')
    expect(wrapper.text()).toContain('正在建立实时连接')

    const source = FakeEventSource.instances.at(-1)
    expect(source).toBeTruthy()
    source?.open()
    await flushPromises()
    expect(wrapper.text()).toContain('已连接，实时更新中')

    source?.fail()
    await flushPromises()
    // A dropped connection is reported as possibly stale, not as success.
    expect(wrapper.text()).toContain('连接中断，结果可能未更新')
    expect(wrapper.text()).toContain('排队中')
  })

  it('reconnects after a fatal stream failure instead of staying disconnected', async () => {
    // Capture the reconnect timer instead of waiting a real second. Installing
    // fake timers after the timer is scheduled would not capture it, so the
    // callback is collected here and invoked explicitly.
    const realSetTimeout = window.setTimeout
    const scheduled: (() => void)[] = []
    vi.stubGlobal('setTimeout', ((handler: () => void) => {
      scheduled.push(handler)
      return scheduled.length
    }) as unknown as typeof window.setTimeout)
    try {
      scriptFetch([{ method: 'GET', path: '/tasks/11', body: taskDetail() }])
      const router = createTestRouter(TaskDetailView, {
        path: '/tasks/:id',
        name: 'task-detail',
      })
      await router.push('/tasks/11')
      const wrapper = mount(TaskDetailView, { global: { plugins: [router, pinia] } })
      await flushPromises()

      const first = FakeEventSource.instances.at(-1)
      first?.open()
      await flushPromises()
      expect(wrapper.text()).toContain('已连接，实时更新中')

      // A proxy 502 makes EventSource give up: it will not retry by itself, so
      // the page must schedule its own reconnect and recover without a reload.
      first?.failFatally()
      await flushPromises()
      expect(wrapper.text()).toContain('连接中断，结果可能未更新')
      expect(scheduled.length).toBeGreaterThan(0)

      // Run the scheduled reconnect (the last scheduled callback is the retry).
      scheduled[scheduled.length - 1]?.()
      await flushPromises()
      expect(FakeEventSource.instances.length).toBeGreaterThan(1)

      const second = FakeEventSource.instances.at(-1)
      second?.open()
      await flushPromises()
      expect(wrapper.text()).toContain('已连接，实时更新中')
    } finally {
      vi.stubGlobal('setTimeout', realSetTimeout)
      vi.unstubAllGlobals()
    }
  })

  it('shows unknown usage as 待核对 for an attempt without reported tokens', async () => {
    const detail = taskDetail({
      status: 'SUCCEEDED',
      attemptCount: 1,
      executions: [
        {
          id: 31,
          taskId: 11,
          attempt: 1,
          status: 'SUCCEEDED',
          runMode: 'DEMO',
          demoBehavior: 'UNKNOWN_USAGE',
          workerId: 'worker-1',
          fencingToken: 1,
          leaseExpiresAt: null,
          heartbeatAt: '2026-11-02T02:20:00Z',
          startedAt: '2026-11-02T02:20:00Z',
          finishedAt: '2026-11-02T02:21:00Z',
          reservedTokens: 1000,
          settledTokens: null,
          promptTokens: null,
          completionTokens: null,
          usageKnown: false,
          needsReconciliation: true,
          errorCode: null,
          errorMessage: null,
          resultSummary: '演练运行：完成，但供应商未返回用量。',
          interruptedUnknownSideEffects: false,
          recoveryNote: null,
          retriedFromExecutionId: null,
          steps: [
            {
              id: 41,
              sequence: 0,
              name: '准备执行上下文',
              status: 'SUCCEEDED',
              startedAt: '2026-11-02T02:20:00Z',
              finishedAt: '2026-11-02T02:20:30Z',
              estimatedTokens: 64,
              promptTokens: null,
              completionTokens: null,
              usageKnown: false,
              detail: '演练：步骤完成',
            },
          ],
        },
      ],
    })
    scriptFetch([{ method: 'GET', path: '/tasks/11', body: detail }])
    const router = createTestRouter(TaskDetailView, {
      path: '/tasks/:id',
      name: 'task-detail',
    })
    await router.push('/tasks/11')
    const wrapper = mount(TaskDetailView, { global: { plugins: [router, pinia] } })
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('待核对')
    expect(text).toContain('已按预留额度保守计入已用')
    expect(text).toContain('演练运行（确定性处理器，非真实模型输出）')
    // Success is still shown as success: unknown usage does not change the outcome.
    expect(text).toContain('已通过')
    // No cancel/retry affordance on a terminal task.
    expect(text).not.toContain('重试执行')
    expect(text).not.toContain('取消任务')
  })
})
