/** Phase 02 API types mirroring backend/app/api/schemas_organization.py. */

export type AgentStatus = 'ACTIVE' | 'PAUSED' | 'ARCHIVED'
export type ProviderKind = 'DEEPSEEK' | 'OPENAI_COMPATIBLE'
export type Enablement = 'ENABLED' | 'DISABLED'
/** PENDING: attempted, result unknown (interrupted); never auto-retried. */
export type ProbeOutcome = 'PENDING' | 'SUCCESS' | 'FAILURE'
/** ENV = server environment variable (legacy); DB = encrypted in the database. */
export type SecretSource = 'ENV' | 'DB'
export type SecretState = 'NONE' | 'CONFIGURED' | 'MISSING_ENV' | 'UNDECRYPTABLE'

export interface CompanyResponse {
  id: number
  name: string
  goal: string
  status: 'ACTIVE'
  templateKey: string
  templateVersion: number
  version: number
  initializedAt: string
  positionCount: number
  agentCount: number
}

export interface TemplatePositionInfo {
  code: string
  title: string
  defaultAgentName: string
  duty: string
  reportsToCode: string | null
  sortOrder: number
}

export interface CompanyTemplateResponse {
  templateKey: string
  templateVersion: number
  label: string
  description: string
  positions: TemplatePositionInfo[]
}

export interface CompanyInitPayload {
  name: string
  goal: string
  templateKey?: string
  templateVersion?: number
  /** Optional model applied to all five employees at creation. */
  modelId?: number | null
  /** Retrying with the same key must not create a second company. */
  idempotencyKey?: string | null
}

export interface PositionSummary {
  id: number
  code: string
  title: string
  duty: string
  /** Duty snapshot number, 1-based. */
  version: number
  /** Optimistic-locking counter echoed on edit. */
  rowVersion: number
  sortOrder: number
  departmentName: string
}

export interface AgentModelRef {
  modelId: number
  logicalName: string
  label: string
  providerId: number
  providerName: string
  providerKind: ProviderKind
  /** Null until a probe verified JSON output; never assumed. */
  supportsJsonOutput: boolean | null
}

export interface AgentSummary {
  id: number
  employeeCode: string
  displayName: string
  status: AgentStatus
  positionId: number
  positionCode: string
  positionTitle: string
  managerId: number | null
  managerName: string | null
  dutyOverride: string | null
  effectiveDuty: string
  model: AgentModelRef | null
  version: number
  statusChangedAt: string
  archivedAt: string | null
}

export interface OrganizationResponse {
  company: CompanyResponse
  departmentName: string
  departmentPurpose: string
  positions: PositionSummary[]
  agents: AgentSummary[]
}

export interface AgentUpdatePayload {
  displayName?: string | null
  dutyOverride?: string | null
  clearDutyOverride?: boolean
  managerId?: number | null
  changeManager?: boolean
  status?: AgentStatus | null
  expectedVersion?: number | null
  reason?: string | null
}

export interface PositionUpdatePayload {
  title?: string | null
  duty?: string | null
  expectedVersion?: number | null
}

export interface PositionUpdateResponse {
  position: PositionSummary
  versionCreated: boolean
}

export interface AgentHistoryEntry {
  kind: 'DUTY_OVERRIDE' | 'MODEL_ASSIGNMENT'
  occurredAt: string
  summary: string
  detail: string | null
}

export interface AgentHistoryResponse {
  agentId: number
  entries: AgentHistoryEntry[]
}

export interface ProviderResponse {
  id: number
  name: string
  kind: ProviderKind
  /** Optimistic-locking counter; echo it on update (409 when stale). */
  version: number
  baseUrl: string
  /**
   * Environment variable name for a legacy ENV provider; null when the key is
   * stored (encrypted) in the database. Never a credential value.
   */
  secretRef: string | null
  /** ENV = read from a server variable (Phase 02); DB = typed in the UI. */
  secretSource: SecretSource | null
  /** NONE / CONFIGURED / MISSING_ENV / UNDECRYPTABLE. Never a value. */
  secretState: SecretState
  /** True only when the credential is usable right now. */
  secretConfigured: boolean
  status: Enablement
  notes: string | null
  timeoutSeconds: number
  modelCount: number
  createdAt: string
  updatedAt: string
}

export interface ProviderCreatePayload {
  name: string
  kind: ProviderKind
  baseUrl: string
  /** The key typed by the operator. Encrypted server-side; never echoed back. */
  secretValue?: string | null
  /** Legacy Phase 02 field: an environment variable *name*. */
  secretRef?: string | null
  status?: Enablement
  notes?: string | null
  timeoutSeconds?: number | null
}

export interface ProviderUpdatePayload {
  /** Required: the version the form was rendered from. */
  expectedVersion: number
  name?: string | null
  kind?: ProviderKind | null
  baseUrl?: string | null
  secretValue?: string | null
  /**
   * Explicit: an omitted secretValue would otherwise be ambiguous between
   * "leave the stored key alone" and "clear it".
   */
  changeSecretValue?: boolean
  secretRef?: string | null
  /** Legacy flag for the environment-variable reference. */
  changeSecretRef?: boolean
  status?: Enablement | null
  notes?: string | null
  timeoutSeconds?: number | null
}

export interface ModelResponse {
  id: number
  providerId: number
  providerName: string
  /** Provider facts needed to explain unusability without a second request. */
  providerStatus: Enablement
  providerSecretConfigured: boolean
  version: number
  logicalName: string
  label: string
  status: Enablement
  maxOutputTokens: number | null
  supportsJsonOutput: boolean | null
  /** True when the stored capability was learned under a since-changed config. */
  probeEvidenceStale: boolean
  resolvedName: string | null
  lastProbeOutcome: ProbeOutcome | null
  lastProbeAt: string | null
  lastProbeDetail: string | null
  createdAt: string
  updatedAt: string
}

export interface ModelCreatePayload {
  providerId: number
  logicalName: string
  label?: string | null
  status?: Enablement
  maxOutputTokens?: number | null
}

export interface ProbeResponse {
  id: number
  modelId: number
  /** PENDING = the call was attempted but no result was recorded. */
  outcome: ProbeOutcome
  errorCode: string | null
  message: string
  startedAt: string
  finishedAt: string | null
  latencyMs: number | null
  jsonOutputVerified: boolean | null
  resolvedModelName: string | null
  callCount: number
  promptTokens: number | null
  completionTokens: number | null
  usageKnown: boolean
  tokenWarning: string
}

export interface ProbeHistoryEntry {
  id: number
  modelId: number
  outcome: ProbeOutcome
  errorCode: string | null
  detail: string | null
  startedAt: string
  latencyMs: number | null
  callCount: number
  jsonOutputVerified: boolean | null
  usageKnown: boolean
}

export interface UsageSummary {
  probeCalls: number
  measuredCalls: number
  /** Some dimensions reported, at least one not. */
  partiallyMeasuredCalls: number
  unmeasuredCalls: number
  promptTokens: number | null
  completionTokens: number | null
  /** How many calls are missing from the matching total. */
  promptTokensUnreportedCalls: number
  completionTokensUnreportedCalls: number
  /** Attempted but never completed: result and usage unknown. */
  pendingProbes: number
}

export interface DeepSeekPreset {
  kind: ProviderKind
  baseUrl: string
  secretRef: string
  suggestedModel: string
  notes: string
}

export interface AgentModelBinding {
  agentId: number
  employeeCode: string
  displayName: string
  agentStatus: AgentStatus
  positionTitle: string
  modelId: number | null
  modelLabel: string | null
  providerName: string | null
  providerKind: string | null
  modelStatus: Enablement | null
  providerStatus: Enablement | null
  secretConfigured: boolean | null
  jsonVerified: boolean | null
  assignedAt: string | null
}

export interface ModelCentreResponse {
  providers: ProviderResponse[]
  models: ModelResponse[]
  assignments: AgentModelBinding[]
  recentProbes: ProbeHistoryEntry[]
  usage: UsageSummary
  deepseekPreset: DeepSeekPreset
}

export interface AuditEvent {
  id: number
  occurredAt: string
  actorType: string
  actorUserId: number | null
  actorDisplayName: string | null
  action: string
  outcome: string
  targetType: string | null
  targetId: string | null
  detail: string | null
  requestId: string | null
}

export interface AuditListResponse {
  events: AuditEvent[]
  total: number
  limit: number
  offset: number
}
