import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { DomainEventEnvelope } from '@/api/task-types'

const EVENT_TYPES = [
  'PROJECT_CREATED', 'TASK_CREATED', 'TASK_STARTED', 'TASK_STEP_UPDATED',
  'TASK_CANCEL_REQUESTED', 'TASK_RETRY_SCHEDULED', 'TASK_REQUEUED',
  'TASK_SUCCEEDED', 'TASK_FAILED', 'TASK_CANCELLED', 'TASK_INTERRUPTED',
  'TASK_BLOCKED', 'COMPANY_PAUSED', 'COMPANY_RESUMED',
]
const BACKOFF = [1000, 2000, 4000, 8000, 15000]

/** Notifications invalidate a snapshot; only a successful GET makes it fresh. */
export function useTaskFeed(
  refresh: () => Promise<boolean>,
  matches: (event: DomainEventEnvelope) => boolean,
) {
  const state = ref<'connecting' | 'live' | 'disconnected'>('connecting')
  const stale = ref(false)
  const notice = computed(() => {
    if (stale.value) return '读取失败，显示旧数据；正在重试'
    if (state.value === 'live') return '已连接，实时更新中'
    if (state.value === 'connecting') return '正在建立实时连接'
    return '连接中断，结果可能未更新；正在补读事件'
  })
  let source: EventSource | null = null
  let cursor = 0
  let disposed = false
  let mounted = false
  let connectedBefore = false
  let reconnectAttempt = 0
  let readAttempt = 0
  let reconnectTimer: number | null = null
  let readTimer: number | null = null
  let reading = false
  let pending = false

  function scheduleRefresh(delay = 300) {
    if (disposed) return
    if (reading) { pending = true; return }
    if (readTimer !== null) return
    readTimer = window.setTimeout(() => {
      readTimer = null
      void reread()
    }, delay)
  }

  async function reread() {
    if (disposed) return
    reading = true
    pending = false
    let ok = false
    try { ok = await refresh() } catch { /* retain snapshot and retry below */ }
    reading = false
    if (disposed) return
    stale.value = !ok
    if (ok) {
      readAttempt = 0
      if (pending) scheduleRefresh()
    } else {
      scheduleRefresh(BACKOFF[Math.min(readAttempt++, BACKOFF.length - 1)])
    }
  }

  function open() {
    if (disposed || typeof EventSource === 'undefined') return
    state.value = 'connecting'
    const current = new EventSource(`/api/v1/events/stream?after=${cursor}`)
    source = current
    current.onopen = () => {
      if (disposed || source !== current) return
      state.value = 'live'
      reconnectAttempt = 0
      if (connectedBefore || stale.value) scheduleRefresh(0)
      connectedBefore = true
    }
    current.onerror = () => {
      if (disposed || source !== current) return
      state.value = 'disconnected'
      current.close()
      source = null
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null
        open()
      }, BACKOFF[Math.min(reconnectAttempt++, BACKOFF.length - 1)])
    }
    const handler = (event: Event) => {
      if (disposed || source !== current) return
      try {
        const payload = JSON.parse((event as MessageEvent<string>).data) as DomainEventEnvelope
        if (typeof payload.id === 'number') cursor = Math.max(cursor, payload.id)
        if (matches(payload)) scheduleRefresh()
      } catch { scheduleRefresh() }
    }
    for (const type of EVENT_TYPES) current.addEventListener(type, handler)
  }

  function close() {
    source?.close()
    source = null
    if (reconnectTimer !== null) window.clearTimeout(reconnectTimer)
    if (readTimer !== null) window.clearTimeout(readTimer)
    reconnectTimer = readTimer = null
  }

  function reset() {
    close()
    cursor = 0
    connectedBefore = false
    reconnectAttempt = readAttempt = 0
    stale.value = false
    if (mounted && !disposed) open()
  }

  function snapshotResult(ok: boolean) {
    if (disposed) return
    stale.value = !ok
    if (!ok) scheduleRefresh(1000)
  }

  onMounted(() => { mounted = true; open() })
  onBeforeUnmount(() => { disposed = true; close() })
  return { state, stale, notice, reset, scheduleRefresh, snapshotResult }
}
