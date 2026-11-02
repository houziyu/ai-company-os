/**
 * Session store tests. The store must never report "authenticated" from local
 * guesswork, and a network failure must not be rendered as "logged out".
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, NetworkError } from '@/api/client'
import { useSessionStore } from '@/stores/session'

vi.mock('@/api/endpoints', () => ({
  authApi: {
    session: vi.fn(),
    login: vi.fn(),
    logout: vi.fn(),
    setupStatus: vi.fn(),
    setup: vi.fn(),
  },
  systemApi: {
    readiness: vi.fn(),
    status: vi.fn(),
  },
}))

const { authApi } = await import('@/api/endpoints')

const owner = {
  id: 1,
  username: 'owner',
  displayName: '所有者',
  role: 'OWNER' as const,
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('session store', () => {
  it('starts unknown so the router never guesses', () => {
    const store = useSessionStore()
    expect(store.state).toBe('unknown')
    expect(store.isAuthenticated).toBe(false)
  })

  it('becomes authenticated from the server response', async () => {
    vi.mocked(authApi.session).mockResolvedValue({
      user: owner,
      expiresAt: '2026-10-30T00:00:00Z',
      sessionCreatedAt: '2026-10-29T00:00:00Z',
    })
    const store = useSessionStore()
    await store.refresh()
    expect(store.state).toBe('authenticated')
    expect(store.user?.displayName).toBe('所有者')
  })

  it('becomes anonymous on 401', async () => {
    vi.mocked(authApi.session).mockRejectedValue(
      new ApiError(401, { code: 'NOT_AUTHENTICATED', message: '登录已失效。' }),
    )
    const store = useSessionStore()
    await store.refresh()
    expect(store.state).toBe('anonymous')
  })

  it('keeps state unknown and rethrows on network failure', async () => {
    vi.mocked(authApi.session).mockRejectedValue(new NetworkError())
    const store = useSessionStore()
    await expect(store.refresh()).rejects.toBeInstanceOf(NetworkError)
    expect(store.state).toBe('unknown')
    expect(store.lastError).toContain('无法连接服务')
  })

  it('clears the session locally even if logout request fails', async () => {
    vi.mocked(authApi.logout).mockRejectedValue(new NetworkError())
    const store = useSessionStore()
    store.applySession(owner, '2026-10-30T00:00:00Z')
    expect(store.isAuthenticated).toBe(true)

    await expect(store.logout()).rejects.toBeInstanceOf(NetworkError)
    expect(store.state).toBe('anonymous')
    expect(store.user).toBeNull()
  })

  it('stores login result without re-fetching', async () => {
    vi.mocked(authApi.login).mockResolvedValue({
      user: owner,
      expiresAt: '2026-10-30T00:00:00Z',
      csrfToken: 'csrf',
    })
    const store = useSessionStore()
    await store.login('owner', 'secret-password')
    expect(store.isAuthenticated).toBe(true)
    expect(authApi.session).not.toHaveBeenCalled()
  })
})
