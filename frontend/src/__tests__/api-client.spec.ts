/**
 * API client tests: the security-relevant behaviour is that writes carry the
 * CSRF header, 401 is distinguishable from validation errors, and network
 * failures are not silently converted into success.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, NetworkError, api, readCookie, request } from '@/api/client'
import { onAuthFailure } from '@/api/auth-failure'

function mockFetch(
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
) {
  // A Response body can only be read once, so each call gets a fresh instance.
  return vi.fn().mockImplementation(
    () =>
      new Response(status === 204 ? null : JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json', ...headers },
      }),
  )
}

afterEach(() => {
  vi.restoreAllMocks()
  document.cookie = 'acos_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/'
})

describe('readCookie', () => {
  it('reads a cookie value', () => {
    document.cookie = 'acos_csrf=token-value; path=/'
    expect(readCookie('acos_csrf')).toBe('token-value')
  })

  it('returns null when absent', () => {
    expect(readCookie('missing')).toBeNull()
  })
})

describe('request', () => {
  it('notifies session owners on protected 401, including malformed error bodies', async () => {
    const invalidate = vi.fn()
    const unsubscribe = onAuthFailure(invalidate)
    try {
      vi.stubGlobal(
        'fetch',
        vi
          .fn()
          .mockResolvedValue(new Response('unauthorized', { status: 401 })),
      )
      await expect(api.get('/system/status')).rejects.toBeInstanceOf(ApiError)
      expect(invalidate).toHaveBeenCalledTimes(1)
    } finally {
      unsubscribe()
    }
  })

  it('does not invalidate an existing session for wrong login credentials or permission errors', async () => {
    const invalidate = vi.fn()
    const unsubscribe = onAuthFailure(invalidate)
    try {
      vi.stubGlobal(
        'fetch',
        mockFetch(401, {
          code: 'INVALID_CREDENTIALS',
          message: '账号或密码不正确',
        }),
      )
      await expect(
        api.post('/auth/login', {}, { authenticated: false }),
      ).rejects.toBeInstanceOf(ApiError)
      vi.stubGlobal(
        'fetch',
        mockFetch(403, {
          code: 'CSRF_VALIDATION_FAILED',
          message: '请刷新后重试',
        }),
      )
      await expect(api.post('/auth/logout')).rejects.toBeInstanceOf(ApiError)
      expect(invalidate).not.toHaveBeenCalled()
    } finally {
      unsubscribe()
    }
  })
  it('sends the CSRF header on writes', async () => {
    document.cookie = 'acos_csrf=csrf-abc; path=/'
    const fetchMock = mockFetch(200, { ok: true })
    vi.stubGlobal('fetch', fetchMock)

    await api.post('/anything', { a: 1 })

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe(
      'csrf-abc',
    )
    expect(init.credentials).toBe('same-origin')
  })

  it('does not send a CSRF header on reads', async () => {
    document.cookie = 'acos_csrf=csrf-abc; path=/'
    const fetchMock = mockFetch(200, {})
    vi.stubGlobal('fetch', fetchMock)

    await api.get('/anything')

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(
      (init.headers as Record<string, string>)['X-CSRF-Token'],
    ).toBeUndefined()
  })

  it('maps error bodies to ApiError with fields and request id', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(422, {
        code: 'VALIDATION_FAILED',
        message: '提交的内容有误。',
        requestId: 'req-1',
        fieldErrors: [{ field: 'username', message: '请输入登录名。' }],
      }),
    )

    await expect(api.post('/auth/setup', {})).rejects.toMatchObject({
      code: 'VALIDATION_FAILED',
      requestId: 'req-1',
    })

    try {
      await api.post('/auth/setup', {})
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      const apiError = error as ApiError
      expect(apiError.fieldErrorFor('username')).toBe('请输入登录名。')
      expect(apiError.isAuthFailure).toBe(false)
    }
  })

  it('flags 401 as an auth failure', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(401, {
        code: 'NOT_AUTHENTICATED',
        message: '登录已失效。',
        requestId: 'r',
      }),
    )
    try {
      await api.get('/auth/session')
      throw new Error('expected failure')
    } catch (error) {
      expect((error as ApiError).isAuthFailure).toBe(true)
    }
  })

  it('raises NetworkError when fetch rejects', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('failed')))
    await expect(api.get('/health/live')).rejects.toBeInstanceOf(NetworkError)
  })

  it('returns undefined for 204 responses', async () => {
    vi.stubGlobal('fetch', mockFetch(204, null))
    await expect(api.post('/auth/logout')).resolves.toBeUndefined()
  })

  it('handles an unparsable error body without throwing a parse error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('<html>gateway error</html>', {
          status: 502,
          headers: { 'Content-Type': 'text/html' },
        }),
      ),
    )
    try {
      await request('/x')
      throw new Error('expected failure')
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      expect((error as ApiError).status).toBe(502)
      expect((error as ApiError).code).toBe('HTTP_502')
    }
  })

  it('serialises query parameters and skips undefined ones', async () => {
    const fetchMock = mockFetch(200, {})
    vi.stubGlobal('fetch', fetchMock)
    await api.get('/items', { query: { page: 2, filter: undefined, q: 'x' } })
    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toBe('/api/v1/items?page=2&q=x')
  })
})
