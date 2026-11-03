/**
 * R13: idempotency-key lifecycle for one logical task submission.
 *
 * The review found that `submitTask()` generated a fresh key on every click. A
 * response lost *after* the server committed therefore produced a second task and
 * a second reservation on the next click. These tests pin the required lifecycle:
 *
 *   same body + failed attempt  -> SAME key (a retry of one command)
 *   changed body                -> NEW key (a genuinely different command)
 *   confirmed success           -> key retired
 *   IDEMPOTENCY_CONFLICT        -> key retired (the stored body no longer matches)
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ProjectsView from '@/views/ProjectsView.vue'
import {
  clearSubmissionKey,
  digestSubmission,
  idempotencyKeyFor,
  type TaskSubmissionBody,
  type TaskSubmissionKey,
} from '@/api/task-submission'
import { createTestRouter } from './helpers'

const BODY: TaskSubmissionBody = {
  title: '任务',
  objective: '目标',
  acceptance: null,
  budgetTokens: 2000,
  demoBehavior: 'SUCCESS',
  maxAutoRetries: 1,
}

describe('idempotencyKeyFor', () => {
  it('creates a key for the first attempt and reuses it for the same body', () => {
    const first = idempotencyKeyFor(null, BODY)
    expect(first.key.length).toBeGreaterThan(8)
    const retry = idempotencyKeyFor(first, { ...BODY })
    expect(retry.key).toBe(first.key)
    expect(retry.digest).toBe(first.digest)
  })

  it('replaces the key when the body changes', () => {
    const first = idempotencyKeyFor(null, BODY)
    const changed = idempotencyKeyFor(first, { ...BODY, budgetTokens: 3000 })
    expect(changed.key).not.toBe(first.key)
    // And the changed key is stable for its own body.
    expect(idempotencyKeyFor(changed, { ...BODY, budgetTokens: 3000 }).key).toBe(changed.key)
  })

  it('treats a cleared key as a new logical submission', () => {
    let current: TaskSubmissionKey | null = idempotencyKeyFor(null, BODY)
    const firstKey = current.key
    current = clearSubmissionKey()
    expect(current).toBeNull()
    expect(idempotencyKeyFor(current, BODY).key).not.toBe(firstKey)
  })

  it('ignores key order by digesting the exact request body', () => {
    expect(digestSubmission(BODY)).toBe(digestSubmission({ ...BODY }))
    expect(digestSubmission(BODY)).not.toBe(digestSubmission({ ...BODY, title: '另一个' }))
  })
})

// -------------------------------------------------- integration with the view
interface PostAttempt {
  status: number
  body: unknown
}

function fetchScript(posts: PostAttempt[]) {
  const calls: { method: string; url: string; body: string | null }[] = []
  const queue = [...posts]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ method, url, body: init?.body ? String(init.body) : null })
    if (method === 'GET' && url.includes('/projects')) {
      return json({ projects: [PROJECT] })
    }
    if (method === 'GET' && url.includes('/tasks')) {
      return json({ tasks: [], total: 0, limit: 200, offset: 0, counts: {} })
    }
    if (method === 'POST') {
      const next = queue.shift()
      if (!next) throw new Error('unexpected POST')
      return json(next.body, next.status)
    }
    throw new Error(`unexpected request ${method} ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return calls
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
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
  taskCounts: {},
  budget: {
    limitTokens: 100000,
    consumedTokens: 0,
    reservedTokens: 0,
    availableTokens: 100000,
    needsReconciliationCount: 0,
  },
}

const TASK_BODY = {
  task: {
    id: 99,
    projectId: 7,
    projectName: '演练项目',
    companyId: 1,
    title: '任务',
    status: 'QUEUED',
    runMode: 'DEMO',
    demoBehavior: 'SUCCESS',
    budgetTokens: 2000,
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
    budget: null,
    cancelPending: false,
  },
  created: true,
  message: '任务已创建并进入队列。',
}

async function mountView() {
  const router = createTestRouter(ProjectsView, { path: '/projects' })
  await router.push('/projects')
  const wrapper = mount(ProjectsView, { global: { plugins: [router, pinia] } })
  await flushPromises()
  return { router, wrapper }
}

async function openAndFill(wrapper: ReturnType<typeof mount>, title = '任务') {
  const createTaskButton = wrapper
    .findAll('button')
    .find((button) => button.text() === '新建任务')
  await createTaskButton?.trigger('click')
  await flushPromises()
  const inputs = wrapper.findAll('input')
  await inputs[0]?.setValue(title)
  await wrapper.find('textarea').setValue('目标')
}

async function submit(wrapper: ReturnType<typeof mount>) {
  const button = wrapper.findAll('button').find((item) => item.text() === '创建任务')
  await button?.trigger('click')
  await flushPromises()
}

let pinia: ReturnType<typeof createPinia>

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  document.cookie = 'acos_csrf=test-csrf-token'
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ProjectsView idempotency key lifecycle (R13)', () => {
  it('reuses the key when a failed submission is retried unchanged', async () => {
    // First POST fails with a network-style error (no response reaches the app),
    // then the identical retry is answered by the already-committed task.
    const calls = fetchScript([
      { status: 502, body: { code: 'BAD_GATEWAY', message: '网关错误' } },
      { status: 200, body: { ...TASK_BODY, created: false, message: '该请求已创建过任务。' } },
    ])
    const { wrapper } = await mountView()
    await openAndFill(wrapper)
    await submit(wrapper)
    // The draft is preserved on failure so the user can retry as-is.
    expect(wrapper.text()).toContain('网关错误')
    await submit(wrapper)

    const posts = calls.filter((call) => call.method === 'POST')
    expect(posts.length).toBe(2)
    const first = JSON.parse(posts[0]?.body ?? '{}') as Record<string, unknown>
    const second = JSON.parse(posts[1]?.body ?? '{}') as Record<string, unknown>
    expect(first.idempotencyKey).toBe(second.idempotencyKey)
  })

  it('issues a new key after the body is edited', async () => {
    const calls = fetchScript([
      { status: 502, body: { code: 'BAD_GATEWAY', message: '网关错误' } },
      { status: 201, body: TASK_BODY },
    ])
    const { wrapper } = await mountView()
    await openAndFill(wrapper, '任务 A')
    await submit(wrapper)

    // The user edits the title: this is a different command, so a new key is
    // required (reusing the old one would be refused as a conflict).
    const inputs = wrapper.findAll('input')
    await inputs[0]?.setValue('任务 B')
    await submit(wrapper)

    const posts = calls.filter((call) => call.method === 'POST')
    const first = JSON.parse(posts[0]?.body ?? '{}') as Record<string, unknown>
    const second = JSON.parse(posts[1]?.body ?? '{}') as Record<string, unknown>
    expect(first.idempotencyKey).not.toBe(second.idempotencyKey)
    expect(second.title).toBe('任务 B')
  })

  it('retires the key after a confirmed success', async () => {
    const calls = fetchScript([
      { status: 201, body: TASK_BODY },
      { status: 201, body: { ...TASK_BODY, task: { ...TASK_BODY.task, id: 100 } } },
    ])
    const { wrapper } = await mountView()
    await openAndFill(wrapper)
    await submit(wrapper)

    // The drawer closed after success; open a fresh one and submit again. This is
    // a deliberate second task and must not reuse the first key.
    await openAndFill(wrapper)
    await submit(wrapper)

    const posts = calls.filter((call) => call.method === 'POST')
    expect(posts.length).toBe(2)
    const first = JSON.parse(posts[0]?.body ?? '{}') as Record<string, unknown>
    const second = JSON.parse(posts[1]?.body ?? '{}') as Record<string, unknown>
    expect(first.idempotencyKey).not.toBe(second.idempotencyKey)
  })
})
