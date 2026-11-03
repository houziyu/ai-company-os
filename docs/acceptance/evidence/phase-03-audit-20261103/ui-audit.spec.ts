import { flushPromises, mount } from '../../../../frontend/node_modules/@vue/test-utils/dist/vue-test-utils.esm-bundler.mjs'
import { createTestRouter } from '@/__tests__/helpers'
import TaskDetailView from '@/views/TaskDetailView.vue'
import { tasksApi } from '@/api/endpoints'
import { NetworkError } from '@/api/client'

vi.mock('@/api/endpoints', () => ({ tasksApi: { get: vi.fn() } }))

class EventSourceDouble {
  static CLOSED = 2
  static instances: EventSourceDouble[] = []
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  listeners = new Map<string, EventListener>()
  constructor() { EventSourceDouble.instances.push(this) }
  addEventListener(type: string, callback: EventListener) { this.listeners.set(type, callback) }
  close() { this.readyState = 2 }
}

it('marks the task snapshot stale when the final event arrives but its GET fails', async () => {
  vi.stubGlobal('EventSource', EventSourceDouble)
  vi.mocked(tasksApi.get).mockResolvedValueOnce({
    id: 11, projectId: 7, projectName: 'audit', companyId: 1, title: 'audit task',
    status: 'RUNNING', runMode: 'DEMO', demoBehavior: 'SUCCESS', budgetTokens: 1000,
    attemptCount: 1, maxAutoRetries: 0, assigneeAgentId: null, assigneeName: null,
    currentExecutionId: null, currentExecutionStatus: null, lastErrorCode: null,
    lastErrorMessage: null, cancelRequestedAt: null, cancelPending: false,
    createdAt: '2026-11-03T01:00:00Z', updatedAt: '2026-11-03T01:00:00Z',
    startedAt: '2026-11-03T01:00:00Z', finishedAt: null, budget: null,
    objective: 'audit', acceptance: null, resultSummary: null,
    executions: [], events: [], idempotencyKey: 'audit-key',
  })
  vi.mocked(tasksApi.get).mockRejectedValueOnce(new NetworkError('网络中断'))
  const router = createTestRouter(TaskDetailView, { path: '/tasks/:id', name: 'task-detail' })
  await router.push('/tasks/11')
  const wrapper = mount(TaskDetailView, { global: { plugins: [router] } })
  try {
    await flushPromises()
    const source = EventSourceDouble.instances.at(-1)!
    source.onopen?.()
    vi.useFakeTimers()
    source.listeners.get('TASK_SUCCEEDED')?.(new MessageEvent('TASK_SUCCEEDED', {
      data: JSON.stringify({ id: 20, taskId: 11 }),
    }))
    await vi.advanceTimersByTimeAsync(301)
    await flushPromises()
    expect(tasksApi.get).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('执行中')
    // The notification connection being alive does not prove the GET snapshot
    // is current. The failed re-read must be visible, with a retry path.
    expect(wrapper.text()).not.toContain('已连接，实时更新中')
  } finally {
    wrapper.unmount()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  }
})
