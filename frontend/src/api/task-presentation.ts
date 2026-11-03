/**
 * Task/execution status presentation.
 *
 * CENTRAL mapping so the board, the detail page and the overview cannot disagree
 * with each other or with the backend enum. COMPONENTS.md §5 fixes the labels and
 * tones; a status always shows Chinese text, never colour alone.
 */
import type { ExecutionStatus, StepStatus, TaskStatus } from '@/api/task-types'
import type { StatusTone } from '@/styles/tokens'

export interface StatusPresentation {
  label: string
  tone: StatusTone
}

const TASK_STATUS: Record<TaskStatus, StatusPresentation> = {
  QUEUED: { label: '排队中', tone: 'neutral' },
  RUNNING: { label: '执行中', tone: 'info' },
  WAITING_APPROVAL: { label: '等待审批', tone: 'warning' },
  BLOCKED: { label: '已阻塞', tone: 'warning' },
  SUCCEEDED: { label: '已通过', tone: 'success' },
  FAILED: { label: '失败', tone: 'danger' },
  CANCELLED: { label: '已取消', tone: 'neutral' },
  INTERRUPTED: { label: '已中断', tone: 'warning' },
}

const EXECUTION_STATUS: Record<ExecutionStatus, StatusPresentation> = {
  RUNNING: { label: '执行中', tone: 'info' },
  BLOCKED: { label: '已阻塞', tone: 'warning' },
  SUCCEEDED: { label: '已通过', tone: 'success' },
  FAILED: { label: '失败', tone: 'danger' },
  CANCELLED: { label: '已取消', tone: 'neutral' },
  INTERRUPTED: { label: '已中断', tone: 'warning' },
}

const STEP_STATUS: Record<StepStatus, StatusPresentation> = {
  RUNNING: { label: '进行中', tone: 'info' },
  SUCCEEDED: { label: '完成', tone: 'success' },
  FAILED: { label: '失败', tone: 'danger' },
  SKIPPED: { label: '跳过', tone: 'neutral' },
  CANCELLED: { label: '已停止', tone: 'neutral' },
  INTERRUPTED: { label: '已中断', tone: 'warning' },
}

const LEDGER_KIND: Record<string, string> = {
  RESERVE: '预留',
  SETTLE: '结算',
  RELEASE: '释放',
}

const LEDGER_SOURCE: Record<string, string> = {
  TASK_CALL: '任务调用',
  PROBE: '连接测试',
}

const SCOPE_LABEL: Record<string, string> = {
  COMPANY: '公司',
  PROJECT: '项目',
  TASK: '任务',
}

const DEMO_BEHAVIOR: Record<string, string> = {
  SUCCESS: '正常完成',
  TRANSIENT_FAILURE: '临时失败（可重试）',
  PERMANENT_FAILURE: '永久失败',
  SLOW: '慢速执行',
  UNKNOWN_USAGE: '用量未知',
  TIMEOUT: '超时',
}

const EVENT_TYPE: Record<string, string> = {
  TASK_CREATED: '任务创建',
  TASK_STARTED: '开始执行',
  TASK_STEP_UPDATED: '步骤更新',
  TASK_CANCEL_REQUESTED: '请求取消',
  TASK_RETRY_SCHEDULED: '安排重试',
  TASK_REQUEUED: '重新排队',
  TASK_SUCCEEDED: '执行成功',
  TASK_FAILED: '执行失败',
  TASK_CANCELLED: '任务取消',
  TASK_INTERRUPTED: '任务中断',
  TASK_BLOCKED: '任务阻塞',
  PROJECT_CREATED: '项目创建',
  COMPANY_PAUSED: '公司暂停',
  COMPANY_RESUMED: '公司恢复',
}

export function taskStatus(status: TaskStatus): StatusPresentation {
  return TASK_STATUS[status] ?? { label: status, tone: 'neutral' }
}

export function executionStatus(status: ExecutionStatus): StatusPresentation {
  return EXECUTION_STATUS[status] ?? { label: status, tone: 'neutral' }
}

export function stepStatus(status: StepStatus): StatusPresentation {
  return STEP_STATUS[status] ?? { label: status, tone: 'neutral' }
}

export function ledgerKind(kind: string): string {
  return LEDGER_KIND[kind] ?? kind
}

export function ledgerSource(source: string): string {
  return LEDGER_SOURCE[source] ?? source
}

export function scopeLabel(scope: string): string {
  return SCOPE_LABEL[scope] ?? scope
}

export function demoBehaviorLabel(behavior: string): string {
  return DEMO_BEHAVIOR[behavior] ?? behavior
}

export function eventTypeLabel(type: string): string {
  return EVENT_TYPE[type] ?? type
}

/** Board grouping order. Terminal columns come last, as in the status matrix. */
export const BOARD_COLUMNS: { status: TaskStatus; title: string }[] = [
  { status: 'QUEUED', title: '排队中' },
  { status: 'RUNNING', title: '执行中' },
  { status: 'BLOCKED', title: '已阻塞' },
  { status: 'INTERRUPTED', title: '已中断' },
  { status: 'SUCCEEDED', title: '已通过' },
  { status: 'FAILED', title: '失败' },
  { status: 'CANCELLED', title: '已取消' },
]

/** Terminal states: cancel/retry availability is decided by the server, and the
 *  UI only hides buttons it knows the server will refuse. */
export const TERMINAL_STATUSES: TaskStatus[] = ['SUCCEEDED', 'FAILED', 'CANCELLED']
export const RETRYABLE_STATUSES: TaskStatus[] = ['FAILED', 'BLOCKED', 'INTERRUPTED']

/** Format a token count without inventing precision it does not have. */
export function formatTokens(value: number | null | undefined): string {
  if (value === null || value === undefined) return '未知'
  return value.toLocaleString('zh-CN')
}

/** Unknown usage is text, never 0. */
export function formatTokensOrPending(value: number | null | undefined, known: boolean): string {
  if (!known || value === null || value === undefined) return '待核对'
  return formatTokens(value)
}

/**
 * The usage column of a ledger row.
 *
 * A RESERVE or RELEASE entry has no usage by definition (nothing was consumed
 * yet, or nothing was consumed at all), so "待核对" would be misleading there.
 * Only a SETTLE row can be unresolved.
 */
export function formatLedgerUsage(entry: {
  entryKind: string
  usageTokens: number | null
  usageKnown: boolean
}): string {
  if (entry.entryKind !== 'SETTLE') return '—'
  return formatTokensOrPending(entry.usageTokens, entry.usageKnown)
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

export function formatClock(value: string | null | undefined): string {
  if (!value) return '—'
  return new Date(value).toLocaleTimeString('zh-CN', { hour12: false })
}
