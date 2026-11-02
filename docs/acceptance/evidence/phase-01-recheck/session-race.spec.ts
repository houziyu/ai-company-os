/** Independent review: delayed old responses must not clear a new login. */
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { expect, it, vi } from 'vitest'
import { authApi, systemApi } from '@/api/endpoints'
import { useSessionStore } from '@/stores/session'

it('keeps the new session when an older protected request returns 401 late', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const session = useSessionStore()
  const owner = { id: 1, username: 'owner', displayName: 'Review owner', role: 'OWNER' }
  session.applySession(owner, '2026-10-29T00:00:00Z')
  let releaseOldResponse!: (response: Response) => void
  const delayedResponse = new Promise<Response>((resolve) => { releaseOldResponse = resolve })
  const unauthorized = () => new Response(JSON.stringify({
    code: 'NOT_AUTHENTICATED', message: '登录已失效，请重新登录。',
  }), { status: 401 })
  vi.stubGlobal('fetch', vi.fn()
    .mockReturnValueOnce(delayedResponse)
    .mockResolvedValueOnce(unauthorized())
    .mockResolvedValueOnce(new Response(JSON.stringify({
      user: owner, expiresAt: '2026-10-31T00:00:00Z', csrfToken: 'new-test-csrf',
    }))))
  try {
    // Two protected requests are in flight using an expired session.
    const oldRequest = systemApi.status().catch((error: unknown) => error)
    await expect(authApi.session()).rejects.toMatchObject({ status: 401 })
    expect(session.state).toBe('anonymous')
    // The user signs in after the first failure, before the delayed response.
    await session.login('owner', 'test-password-only')
    expect(session.state).toBe('authenticated')
    releaseOldResponse(unauthorized())
    await oldRequest
    expect(session.state).toBe('authenticated')
    expect(session.user?.username).toBe('owner')
  } finally {
    disposePinia(pinia)
    vi.unstubAllGlobals()
  }
})
