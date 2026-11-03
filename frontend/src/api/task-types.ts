/** Phase 03 API types mirroring backend/app/api/schemas_tasks.py. */

export type TaskStatus =
  | 'QUEUED'
  | 'RUNNING'
  | 'WAITING_APPROVAL'
  | 'BLOCKED'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLED'
  | 'INTERRUPTED'

export type ExecutionStatus =
  | 'RUNNING'
  | 'BLOCKED'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLED'
  | 'INTERRUPTED'

export type StepStatus =
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'SKIPPED'
  | 'CANCELLED'
  | 'INTERRUPTED'

export type RunMode = 'DEMO' | 'REAL'

export type DemoBehavior =
  | 'SUCCESS'
  | 'TRANSIENT_FAILURE'
  | 'PERMANENT_FAILURE'
  | 'SLOW'
  | 'UNKNOWN_USAGE'
  | 'TIMEOUT'

export interface BudgetUsage {
  limitTokens: number
  consumedTokens: number
  reservedTokens: number
  availableTokens: number
  needsReconciliationCount: number
}

export interface ProjectSummary {
  id: number
  key: string
  name: string
  objective: string
  status: 'ACTIVE' | 'ARCHIVED'
  runMode: RunMode
  version: number
  createdAt: string
  updatedAt: string
  taskCounts: Record<string, number>
  budget: BudgetUsage | null
}

export interface ProjectListResponse {
  projects: ProjectSummary[]
}

export interface ProjectCreatePayload {
  key: string
  name: string
  objective: string
  runMode: RunMode
  budgetTokens: number
}

export interface ExecutionStep {
  id: number
  sequence: number
  name: string
  status: StepStatus
  startedAt: string
  finishedAt: string | null
  estimatedTokens: number | null
  promptTokens: number | null
  completionTokens: number | null
  usageKnown: boolean
  detail: string | null
}

export interface ExecutionSummary {
  id: number
  taskId: number
  attempt: number
  status: ExecutionStatus
  runMode: RunMode
  demoBehavior: DemoBehavior
  workerId: string | null
  fencingToken: number
  leaseExpiresAt: string | null
  heartbeatAt: string | null
  startedAt: string
  finishedAt: string | null
  reservedTokens: number | null
  settledTokens: number | null
  promptTokens: number | null
  completionTokens: number | null
  usageKnown: boolean
  needsReconciliation: boolean
  errorCode: string | null
  errorMessage: string | null
  resultSummary: string | null
  interruptedUnknownSideEffects: boolean
  recoveryNote: string | null
  retriedFromExecutionId: number | null
  steps: ExecutionStep[]
}

export interface TaskEvent {
  id: number
  eventType: string
  statusFrom: string | null
  statusTo: string | null
  actorType: string
  message: string
  detail: string | null
  createdAt: string
}

export interface TaskSummary {
  id: number
  projectId: number
  projectName: string
  companyId: number
  title: string
  status: TaskStatus
  runMode: RunMode
  demoBehavior: DemoBehavior
  budgetTokens: number
  attemptCount: number
  maxAutoRetries: number
  assigneeAgentId: number | null
  assigneeName: string | null
  currentExecutionId: number | null
  currentExecutionStatus: ExecutionStatus | null
  lastErrorCode: string | null
  lastErrorMessage: string | null
  cancelRequestedAt: string | null
  createdAt: string
  updatedAt: string
  startedAt: string | null
  finishedAt: string | null
  budget: BudgetUsage | null
  cancelPending: boolean
}

export interface TaskListResponse {
  tasks: TaskSummary[]
  total: number
  limit: number
  offset: number
  counts: Record<string, number>
}

export interface TaskDetail extends TaskSummary {
  objective: string
  acceptance: string | null
  resultSummary: string | null
  executions: ExecutionSummary[]
  events: TaskEvent[]
  idempotencyKey: string | null
}

export interface TaskMutationResponse {
  task: TaskSummary
  created: boolean
  message: string
}

export interface TaskCreatePayload {
  title: string
  objective: string
  acceptance?: string | null
  budgetTokens: number
  demoBehavior: DemoBehavior
  maxAutoRetries: number
  assigneeAgentId?: number | null
  idempotencyKey: string
}

export interface BudgetAccountResponse {
  scope: 'COMPANY' | 'PROJECT' | 'TASK'
  scopeId: number
  label: string
  limitTokens: number
  consumedTokens: number
  reservedTokens: number
  availableTokens: number
  needsReconciliationCount: number
}

export interface LedgerEntry {
  id: number
  createdAt: string
  entryKind: 'RESERVE' | 'SETTLE' | 'RELEASE'
  source: 'TASK_CALL' | 'PROBE'
  scope: 'COMPANY' | 'PROJECT' | 'TASK'
  projectId: number | null
  taskId: number | null
  executionId: number | null
  reservationId: number | null
  deltaReserved: number
  deltaConsumed: number
  usageTokens: number | null
  usageKnown: boolean
  needsReconciliation: boolean
  detail: string | null
}

export interface BudgetOverviewResponse {
  company: BudgetAccountResponse
  projects: BudgetAccountResponse[]
  ledger: LedgerEntry[]
  pendingReconciliationCount: number
  periodStart: string
  generatedAt: string
}

export interface ExecutionLedgerPage {
  entries: LedgerEntry[]
  nextCursor: number | null
  totalReserved: number
  totalConsumed: number
  needsReconciliationCount: number
}

export interface PendingReconciliationEntry {
  reservationId: number
  accountScope: 'COMPANY' | 'PROJECT' | 'TASK'
  amountTokens: number
  settledTokens: number | null
  kind: 'PLANNING' | 'CALL' | 'PROBE'
  createdAt: string
  taskId: number | null
  executionId: number | null
  detail: string | null
}

export interface CompanyStateResponse {
  status: 'ACTIVE' | 'PAUSED'
  pausedAt: string | null
  pauseReason: string | null
  runningExecutions: number
  queuedTasks: number
  message: string
}

export interface DomainEventEnvelope {
  id: number
  type: string
  createdAt: string
  projectId: number | null
  taskId: number | null
  executionId: number | null
  payload: Record<string, unknown>
}

export interface EventPage {
  events: {
    id: number
    eventType: string
    createdAt: string
    projectId: number | null
    taskId: number | null
    executionId: number | null
    payload: Record<string, unknown>
  }[]
  latestId: number
  hasMore: boolean
}
