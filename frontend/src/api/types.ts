/** Auth API types mirroring backend/app/api/schemas.py. */

export interface OwnerProfile {
  id: number
  username: string
  displayName: string
  role: 'OWNER'
}

export interface SessionResponse {
  user: OwnerProfile
  expiresAt: string
  csrfToken: string
}

export interface CurrentSessionResponse {
  user: OwnerProfile
  expiresAt: string
  sessionCreatedAt: string
}

export interface SetupStatus {
  setupComplete: boolean
  appVersion: string
  passwordMinLength: number
  /** True once the company wizard has created the company and its employees. */
  companyInitialized: boolean
  companyName: string | null
}

export interface SetupPayload {
  username: string
  displayName: string
  password: string
  passwordConfirm: string
}

export interface LoginPayload {
  username: string
  password: string
}

export type CheckStatus = 'ok' | 'fail' | 'skip'

export interface HealthCheck {
  name: string
  status: CheckStatus
  detail: string | null
}

export interface ReadinessResponse {
  ready: boolean
  service: string
  version: string
  checks: HealthCheck[]
  evaluatedAt: string
}

export interface ServiceStatus {
  appVersion: string
  environment: string
  databaseStatus: 'ok' | 'fail' | 'unknown'
  databaseRevision: string | null
  expectedRevision: string | null
  workerLastHeartbeatAt: string | null
  workerStatus: 'ok' | 'stale' | 'missing' | 'unknown'
  workerVersion: string | null
  sessionTtlHours: number
  startedAt: string
}
