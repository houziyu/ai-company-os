import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'

/** Shared feedback for both logout entry points. */
export function useLogout() {
  const session = useSessionStore()
  const router = useRouter()
  const loggingOut = ref(false)
  const logoutError = ref<string | null>(null)

  async function logout(): Promise<boolean> {
    if (loggingOut.value) return false
    loggingOut.value = true
    logoutError.value = null
    try {
      await session.logout()
      await router.replace('/login')
      return true
    } catch {
      logoutError.value = session.lastError ?? '注销结果尚未确认，请重试注销。'
      return false
    } finally {
      loggingOut.value = false
    }
  }

  return { loggingOut, logoutError, logout }
}
