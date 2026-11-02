/** R7 regression: real transport/store, controlled response order, no sleeps. */
import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { expect, it, vi } from 'vitest'
import { authApi, systemApi } from '@/api/endpoints'
import { useSessionStore } from '@/stores/session'

it('keeps the new session when an older protected request returns 401 late', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const session = useSessionStore()
  const owner = {
    id: 1,
    username: 'owner',
    displayName: 'Review owner',
    role: 'OWNER' as const,
  }
  session.applySession(owner, '2026-10-29T00:00:00Z')
  let releaseOldResponse!: (response: Response) => void
  const delayedResponse = new Promise<Response>((resolve) => {
    releaseOldResponse = resolve
  })
  const unauthorized = () =>
    new Response(
      JSON.stringify({
        code: 'NOT_AUTHENTICATED',
        message: '登录已失效，请重新登录。',
      }),
      { status: 401 },
    )
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockReturnValueOnce(delayedResponse)
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            user: owner,
            expiresAt: '2026-10-31T00:00:00Z',
            csrfToken: 'new-test-csrf',
          }),
        ),
      ),
  )
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

const owner = {
  id: 1,
  username: 'owner',
  displayName: 'Old profile',
  role: 'OWNER' as const,
}
const newOwner = { ...owner, displayName: 'New profile' }
const expiry = '2026-10-31T00:00:00Z'
const setupPayload = {
  username: 'owner',
  displayName: 'New profile',
  password: 'test-password-only',
  passwordConfirm: 'test-password-only',
}
function ok(profile = owner) {
  return new Response(
    JSON.stringify({
      user: profile,
      expiresAt: expiry,
      csrfToken: 'test-csrf',
    }),
  )
}
function unauthorized() {
  return new Response(
    JSON.stringify({ code: 'NOT_AUTHENTICATED', message: '登录已失效。' }),
    { status: 401 },
  )
}
function deferred() {
  let resolve!: (value: Response) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<Response>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}
async function withSession(
  run: (session: ReturnType<typeof useSessionStore>) => Promise<void>,
) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const session = useSessionStore()
  session.applySession(owner, expiry)
  try {
    await run(session)
  } finally {
    disposePinia(pinia)
    vi.unstubAllGlobals()
  }
}

it('still invalidates a current 401 after a successful refresh of the same session', () =>
  withSession(async (session) => {
    const delayed = deferred()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockReturnValueOnce(delayed.promise).mockResolvedValueOnce(ok()),
    )
    const request = systemApi.status().catch((error: unknown) => error)
    await session.refresh()
    expect(session.state).toBe('authenticated')
    delayed.resolve(unauthorized())
    expect(await request).toMatchObject({ status: 401 })
    expect(session.state).toBe('anonymous')
    expect(session.user).toBeNull()
    expect(session.expiresAt).toBeNull()
  }))

it.each(['success', '401', '503', 'network'] as const)(
  'late refresh success cannot undo logout (%s)',
  (outcome) =>
    withSession(async (session) => {
      const delayed = deferred()
      const fetchMock = vi.fn().mockReturnValueOnce(delayed.promise)
      if (outcome === 'network')
        fetchMock.mockRejectedValueOnce(new TypeError('offline'))
      else
        fetchMock.mockResolvedValueOnce(
          outcome === 'success'
            ? new Response(null, { status: 204 })
            : outcome === '401'
              ? unauthorized()
              : new Response('{}', { status: 503 }),
        )
      vi.stubGlobal('fetch', fetchMock)
      const refresh = session.refresh()
      if (outcome === 'network' || outcome === '503')
        await expect(session.logout()).rejects.toBeInstanceOf(Error)
      else await session.logout()
      const lastError = session.lastError
      delayed.resolve(ok())
      await refresh
      expect(session.state).toBe('anonymous')
      expect(session.user).toBeNull()
      expect(session.lastError).toBe(lastError)
    }),
)

it.each(['success', '401', 'network'] as const)(
  'late refresh %s cannot overwrite a new login',
  (outcome) =>
    withSession(async (session) => {
      const delayed = deferred()
      vi.stubGlobal(
        'fetch',
        vi
          .fn()
          .mockReturnValueOnce(delayed.promise)
          .mockResolvedValueOnce(ok(newOwner)),
      )
      const refresh = session.refresh()
      await session.login('owner', 'test-password-only')
      if (outcome === 'network') delayed.reject(new TypeError('offline'))
      else delayed.resolve(outcome === '401' ? unauthorized() : ok())
      await refresh
      expect(session.state).toBe('authenticated')
      expect(session.user?.displayName).toBe('New profile')
      expect(session.lastError).toBeNull()
    }),
)

it('ignores an old protected 401 after owner initialization succeeds', () =>
  withSession(async (session) => {
    session.clearSession()
    session.setupComplete = false
    const delayed = deferred()
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockReturnValueOnce(delayed.promise)
        .mockResolvedValueOnce(ok(newOwner)),
    )
    const request = systemApi.status().catch((error: unknown) => error)
    await session.completeSetup(setupPayload)
    delayed.resolve(unauthorized())
    expect(await request).toMatchObject({ status: 401 })
    expect(session.state).toBe('authenticated')
    expect(session.setupComplete).toBe(true)
  }))

it('does not restore an old setup-status cache after login succeeds', () =>
  withSession(async (session) => {
    const delayed = deferred()
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockReturnValueOnce(delayed.promise)
        .mockResolvedValueOnce(ok(newOwner)),
    )
    const setupStatus = session.loadSetupStatus()
    await session.login('owner', 'test-password-only')
    delayed.resolve(
      new Response(
        JSON.stringify({ setupComplete: false, passwordMinLength: 12 }),
      ),
    )
    expect(await setupStatus).toBe(true)
    expect(session.setupComplete).toBe(true)
  }))

it.each(['login', 'setup'] as const)(
  'late %s success cannot restore a signed out state',
  (command) =>
    withSession(async (session) => {
      const delayed = deferred()
      vi.stubGlobal(
        'fetch',
        vi
          .fn()
          .mockReturnValueOnce(delayed.promise)
          .mockResolvedValueOnce(new Response(null, { status: 204 })),
      )
      const pending =
        command === 'login'
          ? session.login('owner', 'test-password-only')
          : session.completeSetup(setupPayload)
      await session.logout()
      delayed.resolve(ok(newOwner))
      await pending
      expect(session.state).toBe('anonymous')
      expect(session.user).toBeNull()
    }),
)

it.each(['success', '401', '503', 'network'] as const)(
  'late logout %s cannot clear a newer login',
  (outcome) =>
    withSession(async (session) => {
      const delayed = deferred()
      vi.stubGlobal(
        'fetch',
        vi
          .fn()
          .mockReturnValueOnce(delayed.promise)
          .mockResolvedValueOnce(ok(newOwner)),
      )
      const pending = session.logout()
      await session.login('owner', 'test-password-only')
      if (outcome === 'network') delayed.reject(new TypeError('offline'))
      else
        delayed.resolve(
          outcome === 'success'
            ? new Response(null, { status: 204 })
            : outcome === '401'
              ? unauthorized()
              : new Response('{}', { status: 503 }),
        )
      await pending
      expect(session.state).toBe('authenticated')
      expect(session.user?.displayName).toBe('New profile')
      expect(session.lastError).toBeNull()
    }),
)

it('accepts a pending credential login after the old cookie receives a valid 401', () =>
  withSession(async (session) => {
    const oldRequest = deferred()
    const loginResponse = deferred()
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockReturnValueOnce(oldRequest.promise)
        .mockReturnValueOnce(loginResponse.promise),
    )
    const request = systemApi.status().catch((error: unknown) => error)
    const login = session.login('owner', 'test-password-only')
    oldRequest.resolve(unauthorized())
    expect(await request).toMatchObject({ status: 401 })
    expect(session.state).toBe('anonymous')
    loginResponse.resolve(ok(newOwner))
    await login
    expect(session.state).toBe('authenticated')
    expect(session.user?.displayName).toBe('New profile')
  }))

it('does not restore state after explicit clearing while a login is pending', () =>
  withSession(async (session) => {
    const delayed = deferred()
    vi.stubGlobal('fetch', vi.fn().mockReturnValueOnce(delayed.promise))
    const login = session.login('owner', 'test-password-only')
    session.clearSession()
    delayed.resolve(ok(newOwner))
    await login
    expect(session.state).toBe('anonymous')
    expect(session.user).toBeNull()
  }))
