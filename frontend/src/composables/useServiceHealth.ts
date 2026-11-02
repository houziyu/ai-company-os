/**
 * Real service-connection state for the topbar.
 *
 * The badge must reflect an actual measurement, never a hardcoded claim
 * (docs/quality-gates.md: no fabricated status). It polls the readiness
 * endpoint, which also proves the database is reachable and migrated.
 *
 * Polling stops while the tab is hidden and refreshes immediately when it
 * becomes visible again, so a laptop waking from sleep does not keep showing a
 * stale "connected".
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { systemApi } from '@/api/endpoints'

export type ConnectionState = 'checking' | 'connected' | 'disconnected'

const POLL_INTERVAL_MS = 30_000

export function useServiceHealth() {
  const state = ref<ConnectionState>('checking')
  const lastCheckedAt = ref<Date | null>(null)
  const detail = ref<string | null>(null)

  let timer: number | null = null
  let disposed = false

  async function check() {
    try {
      const readiness = await systemApi.readiness()
      if (disposed) return
      state.value = readiness.ready ? 'connected' : 'disconnected'
      detail.value = readiness.ready ? null : '部分检查未就绪'
    } catch {
      if (disposed) return
      state.value = 'disconnected'
      detail.value = '无法连接服务'
    } finally {
      if (!disposed) lastCheckedAt.value = new Date()
    }
  }

  function startTimer() {
    stopTimer()
    timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void check()
    }, POLL_INTERVAL_MS)
  }

  function stopTimer() {
    if (timer !== null) {
      window.clearInterval(timer)
      timer = null
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') {
      void check()
      startTimer()
    } else {
      stopTimer()
    }
  }

  onMounted(() => {
    void check()
    startTimer()
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onBeforeUnmount(() => {
    disposed = true
    stopTimer()
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return { state, lastCheckedAt, detail, refresh: check }
}
