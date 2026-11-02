/**
 * Typed API client.
 *
 * Contract rules (docs/engineering-standards.md):
 * - All failures surface as ApiError with the server's Chinese message.
 * - Every authenticated write echoes the CSRF cookie in X-CSRF-Token.
 * - The frontend never infers success from a timeout or chat text; it uses the
 *   response body from the server.
 */

import { getSessionGeneration, notifyAuthFailure } from './auth-failure'

export interface ApiFieldError {
  field: string
  message: string
}

export interface ApiErrorBody {
  code: string
  message: string
  requestId?: string
  fieldErrors?: ApiFieldError[]
  details?: Record<string, unknown>
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId: string | null
  readonly fieldErrors: ApiFieldError[]
  readonly details: Record<string, unknown> | null

  constructor(status: number, body: ApiErrorBody) {
    super(body.message)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code
    this.requestId = body.requestId ?? null
    this.fieldErrors = body.fieldErrors ?? []
    this.details = body.details ?? null
  }

  /** True when the failure means "log in again" rather than "your input is wrong". */
  get isAuthFailure(): boolean {
    return this.code === 'NOT_AUTHENTICATED' || this.status === 401
  }

  fieldErrorFor(field: string): string | null {
    return (
      this.fieldErrors.find((item) => item.field === field)?.message ?? null
    )
  }
}

export class NetworkError extends Error {
  constructor(message = '无法连接服务，请检查网络或服务状态后重试。') {
    super(message)
    this.name = 'NetworkError'
  }
}

const API_BASE = '/api/v1'

/** Read a cookie by name (the CSRF cookie is intentionally not HttpOnly). */
export function readCookie(name: string): string | null {
  const prefix = `${name}=`
  const parts = document.cookie ? document.cookie.split(';') : []
  for (const part of parts) {
    const trimmed = part.trim()
    if (trimmed.startsWith(prefix)) {
      return decodeURIComponent(trimmed.slice(prefix.length))
    }
  }
  return null
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  query?: Record<string, string | number | boolean | undefined>
  signal?: AbortSignal
  /** Writes require the CSRF header; defaults to true for non-GET. */
  withCsrf?: boolean
  /** Public authentication commands must not invalidate an existing session. */
  authenticated?: boolean
}

async function parseError(response: Response): Promise<ApiError> {
  let body: ApiErrorBody
  try {
    body = (await response.json()) as ApiErrorBody
  } catch {
    body = {
      code: `HTTP_${response.status}`,
      message: '服务返回了无法解析的响应，请稍后重试。',
    }
  }
  return new ApiError(response.status, body)
}

export async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const method = options.method ?? 'GET'
  const headers: Record<string, string> = { Accept: 'application/json' }

  let url = `${API_BASE}${path}`
  if (options.query) {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(options.query)) {
      if (value !== undefined) params.set(key, String(value))
    }
    const queryString = params.toString()
    if (queryString) url += `?${queryString}`
  }

  const init: RequestInit = {
    method,
    credentials: 'same-origin',
    headers,
    signal: options.signal ?? null,
  }

  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(options.body)
  }

  const isWrite = method !== 'GET'
  const needsCsrf = options.withCsrf ?? isWrite
  if (needsCsrf && isWrite) {
    const csrf = readCookie('acos_csrf')
    if (csrf) headers['X-CSRF-Token'] = csrf
  }

  let response: Response
  const requestGeneration = getSessionGeneration()
  try {
    response = await fetch(url, init)
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error
    }
    throw new NetworkError()
  }

  if (response.status === 204) {
    return undefined as T
  }

  if (!response.ok) {
    if (response.status === 401 && options.authenticated !== false)
      notifyAuthFailure(requestGeneration)
    throw await parseError(response)
  }

  return (await response.json()) as T
}

export const api = {
  get: <T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'GET' }),
  post: <T>(
    path: string,
    body?: unknown,
    options?: Omit<RequestOptions, 'method'>,
  ) => request<T>(path, { ...options, method: 'POST', body }),
  patch: <T>(
    path: string,
    body?: unknown,
    options?: Omit<RequestOptions, 'method'>,
  ) => request<T>(path, { ...options, method: 'PATCH', body }),
}
