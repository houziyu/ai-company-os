/** Endpoint wrappers grouped by domain. */

import { api } from './client'
import type {
  AgentHistoryResponse,
  AgentSummary,
  AgentUpdatePayload,
  AuditListResponse,
  CompanyInitPayload,
  CompanyResponse,
  CompanyTemplateResponse,
  ModelCentreResponse,
  ModelCreatePayload,
  ModelResponse,
  OrganizationResponse,
  PositionUpdatePayload,
  PositionUpdateResponse,
  ProbeResponse,
  ProviderCreatePayload,
  ProviderResponse,
  ProviderUpdatePayload,
} from './organization-types'
import type {
  CurrentSessionResponse,
  LoginPayload,
  ReadinessResponse,
  ServiceStatus,
  SessionResponse,
  SetupPayload,
  SetupStatus,
} from './types'

export const authApi = {
  setupStatus: () =>
    api.get<SetupStatus>('/auth/setup-status', { authenticated: false }),
  setup: (payload: SetupPayload) =>
    api.post<SessionResponse>('/auth/setup', payload, { authenticated: false }),
  login: (payload: LoginPayload) =>
    api.post<SessionResponse>('/auth/login', payload, { authenticated: false }),
  session: () => api.get<CurrentSessionResponse>('/auth/session'),
  logout: () => api.post<void>('/auth/logout'),
}

export const systemApi = {
  readiness: () => api.get<ReadinessResponse>('/health/ready'),
  status: () => api.get<ServiceStatus>('/system/status'),
}

export const companyApi = {
  template: () => api.get<CompanyTemplateResponse>('/company/template'),
  get: () => api.get<CompanyResponse>('/company'),
  /** Creates the company and its five employees. Safe to retry with a key. */
  create: (payload: CompanyInitPayload) =>
    api.post<CompanyResponse>('/company', payload),
  update: (payload: { name: string; goal: string; expectedVersion: number }) =>
    api.patch<CompanyResponse>('/company', payload),
}

export const organizationApi = {
  get: () => api.get<OrganizationResponse>('/organization'),
  updatePosition: (positionId: number, payload: PositionUpdatePayload) =>
    api.patch<PositionUpdateResponse>(`/positions/${positionId}`, payload),
  updateAgent: (agentId: number, payload: AgentUpdatePayload) =>
    api.patch<AgentSummary>(`/agents/${agentId}`, payload),
  assignModel: (agentId: number, modelId: number, note?: string | null) =>
    api.post<AgentSummary>(`/agents/${agentId}/model-assignment`, {
      modelId,
      note: note ?? null,
    }),
  /** End the current binding; the row is closed, not deleted (history kept). */
  unbindModel: (agentId: number, payload: { note?: string | null } = {}) =>
    api.post<AgentHistoryResponse>(`/agents/${agentId}/model-unbind`, {
      note: payload.note ?? null,
    }),
  history: (agentId: number) =>
    api.get<AgentHistoryResponse>(`/agents/${agentId}/history`),
}

export const modelCentreApi = {
  overview: () => api.get<ModelCentreResponse>('/models/centre'),
  createProvider: (payload: ProviderCreatePayload) =>
    api.post<ProviderResponse>('/providers', payload),
  updateProvider: (providerId: number, payload: ProviderUpdatePayload) =>
    api.patch<ProviderResponse>(`/providers/${providerId}`, payload),
  createModel: (payload: ModelCreatePayload) =>
    api.post<ModelResponse>('/models', payload),
  updateModel: (
    modelId: number,
    payload: {
      /** Required: the version the form was rendered from (409 when stale). */
      expectedVersion: number
      label?: string | null
      status?: 'ENABLED' | 'DISABLED' | null
      maxOutputTokens?: number | null
      changeMaxOutputTokens?: boolean
    },
  ) => api.patch<ModelResponse>(`/models/${modelId}`, payload),
  /** One connectivity probe. Spends a small amount of tokens; never retried. */
  probe: (modelId: number) =>
    api.post<ProbeResponse>(`/models/${modelId}/probe`),
}

export const auditApi = {
  events: (params?: { limit?: number; offset?: number; action?: string }) =>
    api.get<AuditListResponse>('/audit/events', { query: params }),
}
