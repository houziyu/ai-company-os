<script setup lang="ts">
/**
 * TaskDetailView -- one task, its attempts and its timeline.
 *
 * PAGES.md §5/§6 require:
 * - the objective and acceptance stay visible;
 * - **every attempt is independent**: selecting an old attempt changes only the
 *   evidence pane, never the task's current status;
 * - cancel is cooperative and said to be so; retry shows the added allowance;
 * - progress arrives over SSE and, after a dropped connection, the page says
 *   "连接中断，结果可能未更新" and re-reads from the last event id instead of
 *   assuming nothing changed.
 *
 * The page never infers success: it renders the status the server reported.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useTaskFeed } from '@/composables/useTaskFeed'
import { useRoute, useRouter } from 'vue-router'

import AppButton from '@/components/AppButton.vue'
import BudgetMeter from '@/components/BudgetMeter.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { ApiError, NetworkError } from '@/api/client'
import { tasksApi } from '@/api/endpoints'
import {
  TERMINAL_STATUSES,
  demoBehaviorLabel,
  eventTypeLabel,
  executionStatus,
  formatTime,
  formatTokens,
  formatTokensOrPending,
  stepStatus,
  taskStatus,
} from '@/api/task-presentation'
import type { ExecutionSummary, TaskDetail } from '@/api/task-types'

const route = useRoute()
const router = useRouter()

const taskId = computed(() => Number(route.params.id))

const loading = ref(true)
const loadError = ref<{ message: string; requestId: string | null } | null>(null)
const task = ref<TaskDetail | null>(null)
const selectedExecutionId = ref<number | null>(null)

const actionError = ref<string | null>(null)
const actionMessage = ref<string | null>(null)
const cancelling = ref(false)
const retrying = ref(false)
const showCancelConfirm = ref(false)
const cancelReason = ref('')

const feed = useTaskFeed(() => load(true), event =>
  event.taskId === taskId.value || event.type.startsWith('COMPANY_'))
const { state: streamState, stale, notice: streamNotice } = feed
let loadVersion = 0
let disposed = false
let retryCommand: { idempotencyKey: string; expectedAttempt: number } | null = null

function errorFrom(error: unknown): { message: string; requestId: string | null } {
  if (error instanceof ApiError) {
    return { message: error.message, requestId: error.requestId }
  }
  if (error instanceof NetworkError) return { message: error.message, requestId: null }
  return { message: '请求失败，请稍后重试。', requestId: null }
}

async function load(silent = false) {
  const version = ++loadVersion
  const id = taskId.value
  if (!silent) loading.value = true
  if (!silent) loadError.value = null
  try {
    const detail = await tasksApi.get(id)
    if (disposed || version !== loadVersion) return true
    task.value = detail
    loadError.value = null
    if (selectedExecutionId.value === null && detail.executions.length > 0) {
      selectedExecutionId.value = detail.executions[0]?.id ?? null
    } else if (
      selectedExecutionId.value !== null &&
      !detail.executions.some((item) => item.id === selectedExecutionId.value)
    ) {
      selectedExecutionId.value = detail.executions[0]?.id ?? null
    }
    feed.snapshotResult(true)
    return true
  } catch (error) {
    if (disposed || version !== loadVersion) return true
    if (!task.value) loadError.value = errorFrom(error)
    feed.snapshotResult(false)
    return false
  } finally {
    if (version === loadVersion) loading.value = false
  }
}

onMounted(() => { void load() })
onBeforeUnmount(() => { disposed = true; loadVersion += 1 })
watch(taskId, () => {
  selectedExecutionId.value = null
  task.value = null
  retryCommand = null
  feed.reset()
  void load()
})

const selectedExecution = computed<ExecutionSummary | null>(() => {
  if (!task.value) return null
  return (
    task.value.executions.find((item) => item.id === selectedExecutionId.value) ??
    task.value.executions[0] ??
    null
  )
})

const isTerminal = computed(
  () => !!task.value && TERMINAL_STATUSES.includes(task.value.status),
)
const canCancel = computed(
  () => !!task.value && !isTerminal.value && !task.value.cancelPending,
)
const canRetry = computed(
  () =>
    !!task.value &&
    ['FAILED', 'BLOCKED', 'INTERRUPTED'].includes(task.value.status) &&
    !task.value.cancelRequestedAt,
)

/** Retry costs a fresh envelope; show it rather than hiding the effect. */
const retryNotice = computed(() => {
  if (!task.value) return ''
  return `重试会创建新的执行记录，并重新预留 ${formatTokens(task.value.budgetTokens)} Token；上一次执行的证据与账本保留。`
})

async function requestCancel() {
  if (!task.value || cancelling.value) return
  cancelling.value = true
  actionError.value = null
  actionMessage.value = null
  try {
    const response = await tasksApi.cancel(task.value.id, cancelReason.value.trim() || null)
    actionMessage.value = response.message
    showCancelConfirm.value = false
    cancelReason.value = ''
    await load(true)
  } catch (error) {
    actionError.value = errorFrom(error).message
  } finally {
    cancelling.value = false
  }
}

async function requestRetry() {
  if (!task.value || retrying.value) return
  retrying.value = true
  actionError.value = null
  actionMessage.value = null
  try {
    retryCommand ??= { idempotencyKey: crypto.randomUUID(), expectedAttempt: task.value.attemptCount }
    const response = await tasksApi.retry(task.value.id, retryCommand)
    retryCommand = null
    actionMessage.value = response.message
    await load(true)
  } catch (error) {
    actionError.value = errorFrom(error).message
  } finally {
    retrying.value = false
  }
}

</script>

<template>
  <div class="task-detail">
    <div v-if="loading" class="task-detail__skeleton" aria-live="polite" aria-busy="true">
      <span class="acos-visually-hidden">正在加载任务详情</span>
      <div class="task-detail__skeleton-card" />
      <div class="task-detail__skeleton-card" />
    </div>

    <ErrorState
      v-else-if="loadError"
      title="任务读取失败"
      :description="loadError.message"
      :request-id="loadError.requestId"
      @retry="load()"
    />

    <template v-else-if="task">
      <PageHeader :title="task.title" :description="`#${task.id} · 项目 ${task.projectName}`">
        <template #actions>
          <AppButton variant="secondary" @click="router.push('/projects')">返回任务看板</AppButton>
          <AppButton
            v-if="canRetry"
            variant="secondary"
            :loading="retrying"
            loading-text="排队中…"
            @click="requestRetry"
          >
            重试执行
          </AppButton>
          <AppButton
            v-if="canCancel"
            variant="danger-text"
            :disabled="!canCancel"
            @click="showCancelConfirm = true"
          >
            取消任务
          </AppButton>
        </template>
      </PageHeader>

      <div
        class="task-detail__state-bar"
        :class="{ 'task-detail__state-bar--warn': streamState !== 'live' || stale }"
        role="status"
      >
        <StatusBadge
          :label="taskStatus(task.status).label"
          :tone="taskStatus(task.status).tone"
        />
        <span class="acos-caption">{{ streamNotice }}</span>
        <AppButton v-if="stale" variant="ghost" size="small" @click="feed.scheduleRefresh(0)">重新读取</AppButton>
        <span v-if="task.runMode === 'DEMO'" class="acos-caption task-detail__demo">
          演练运行（确定性处理器，非真实模型输出）
        </span>
        <span v-if="task.cancelPending" class="acos-caption task-detail__pending">
          已请求取消：正在运行的步骤会在安全检查点停止
        </span>
      </div>

      <p v-if="actionError" class="task-detail__alert task-detail__alert--error" role="alert">
        {{ actionError }}
      </p>
      <p
        v-if="actionMessage"
        class="task-detail__alert task-detail__alert--info"
        role="status"
      >
        {{ actionMessage }}
      </p>

      <p
        v-if="canRetry"
        class="task-detail__alert task-detail__alert--info"
        role="note"
      >
        {{ retryNotice }}
      </p>

      <div class="task-detail__layout">
        <div class="task-detail__main">
          <section class="task-detail__panel" aria-labelledby="objective-heading">
            <h2 id="objective-heading">任务目标</h2>
            <p class="task-detail__prose">{{ task.objective }}</p>
            <template v-if="task.acceptance">
              <h3>验收说明</h3>
              <p class="task-detail__prose">{{ task.acceptance }}</p>
            </template>
            <p v-if="task.resultSummary" class="task-detail__prose task-detail__result">
              {{ task.resultSummary }}
            </p>
            <p v-if="task.lastErrorCode" class="task-detail__error-line">
              错误码 {{ task.lastErrorCode }}：{{ task.lastErrorMessage ?? '—' }}
            </p>
          </section>

          <section class="task-detail__panel" aria-labelledby="attempts-heading">
            <div class="task-detail__panel-head">
              <h2 id="attempts-heading">执行尝试</h2>
              <span class="acos-caption">
                每次尝试独立记录；切换历史尝试不会改变任务当前状态。
              </span>
            </div>

            <EmptyState
              v-if="task.executions.length === 0"
              title="还没有执行"
              description="任务在队列中等待 Worker 领取。领取后会生成独立的执行记录、租约与步骤。"
            />

            <div v-else class="task-detail__attempts">
              <ul class="task-detail__attempt-list" aria-label="执行尝试列表">
                <li v-for="execution in task.executions" :key="execution.id">
                  <button
                    type="button"
                    class="task-detail__attempt"
                    :class="{
                      'task-detail__attempt--selected':
                        selectedExecution?.id === execution.id,
                    }"
                    :aria-pressed="selectedExecution?.id === execution.id"
                    @click="selectedExecutionId = execution.id"
                  >
                    <span class="task-detail__attempt-title">
                      第 {{ execution.attempt }} 次 · #{{ execution.id }}
                    </span>
                    <StatusBadge
                      :label="executionStatus(execution.status).label"
                      :tone="executionStatus(execution.status).tone"
                    />
                    <span class="acos-caption">{{ formatTime(execution.startedAt) }}</span>
                  </button>
                </li>
              </ul>

              <div v-if="selectedExecution" class="task-detail__execution">
                <div class="task-detail__execution-characteristics">
                  <div>
                    <dt>执行模式</dt>
                    <dd>{{ selectedExecution.runMode === 'DEMO' ? '演练' : '真实' }}</dd>
                  </div>
                  <div>
                    <dt>演练行为</dt>
                    <dd>{{ demoBehaviorLabel(selectedExecution.demoBehavior) }}</dd>
                  </div>
                  <div>
                    <dt>Worker</dt>
                    <dd>{{ selectedExecution.workerId ?? '—' }}</dd>
                  </div>
                  <div>
                    <dt>Fencing token</dt>
                    <dd class="acos-tabular">{{ selectedExecution.fencingToken }}</dd>
                  </div>
                  <div>
                    <dt>开始</dt>
                    <dd>{{ formatTime(selectedExecution.startedAt) }}</dd>
                  </div>
                  <div>
                    <dt>结束</dt>
                    <dd>{{ formatTime(selectedExecution.finishedAt) }}</dd>
                  </div>
                  <div>
                    <dt>输入 / 输出 Token</dt>
                    <dd class="acos-tabular">
                      {{ formatTokensOrPending(selectedExecution.promptTokens, selectedExecution.usageKnown) }}
                      /
                      {{ formatTokensOrPending(selectedExecution.completionTokens, selectedExecution.usageKnown) }}
                    </dd>
                  </div>
                  <div>
                    <dt>结算</dt>
                    <dd class="acos-tabular">
                      {{ formatTokensOrPending(selectedExecution.settledTokens, selectedExecution.usageKnown) }}
                    </dd>
                  </div>
                </div>

                <p
                  v-if="selectedExecution.needsReconciliation"
                  class="task-detail__alert task-detail__alert--warn"
                >
                  本次执行未获得供应商用量：已按预留额度保守计入已用并标记为待核对，不会记为 0。
                </p>
                <p
                  v-if="selectedExecution.interruptedUnknownSideEffects"
                  class="task-detail__alert task-detail__alert--warn"
                >
                  该尝试的进程中断，外部副作用未知；系统未自动重放。
                  <template v-if="selectedExecution.recoveryNote">
                    {{ selectedExecution.recoveryNote }}
                  </template>
                </p>
                <p v-if="selectedExecution.errorCode" class="task-detail__error-line">
                  错误码 {{ selectedExecution.errorCode }}：{{ selectedExecution.errorMessage ?? '—' }}
                </p>

                <h3>步骤</h3>
                <EmptyState
                  v-if="selectedExecution.steps.length === 0"
                  title="没有步骤记录"
                  description="执行在开始第一个步骤前就结束了。"
                />
                <div v-else class="task-detail__table-wrap">
                  <table class="task-detail__table">
                    <thead>
                      <tr>
                        <th scope="col">#</th>
                        <th scope="col">步骤</th>
                        <th scope="col">状态</th>
                        <th scope="col">预估</th>
                        <th scope="col">输入</th>
                        <th scope="col">输出</th>
                        <th scope="col">完成时间</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="step in selectedExecution.steps" :key="step.id">
                        <td class="acos-tabular" data-label="序号">{{ step.sequence + 1 }}</td>
                        <td data-label="步骤">{{ step.name }}</td>
                        <td data-label="状态">
                          <StatusBadge
                            :label="stepStatus(step.status).label"
                            :tone="stepStatus(step.status).tone"
                          />
                        </td>
                        <td class="acos-tabular" data-label="预估">
                          {{ formatTokens(step.estimatedTokens) }}
                        </td>
                        <td class="acos-tabular" data-label="输入">
                          {{ formatTokensOrPending(step.promptTokens, step.usageKnown) }}
                        </td>
                        <td class="acos-tabular" data-label="输出">
                          {{ formatTokensOrPending(step.completionTokens, step.usageKnown) }}
                        </td>
                        <td class="acos-caption" data-label="完成时间">
                          {{ formatTime(step.finishedAt) }}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>

                <p v-if="selectedExecution.id" class="acos-caption">
                  完整执行页：
                  <RouterLink :to="`/executions/${selectedExecution.id}`" class="task-detail__link">
                    查看执行 #{{ selectedExecution.id }}
                  </RouterLink>
                </p>
              </div>
            </div>
          </section>

          <section class="task-detail__panel" aria-labelledby="timeline-heading">
            <h2 id="timeline-heading">时间线</h2>
            <EmptyState
              v-if="task.events.length === 0"
              title="还没有事件"
              description="任务创建、领取、步骤与结果都会写入事件日志。"
            />
            <ol v-else class="task-detail__timeline">
              <li v-for="event in task.events" :key="event.id" class="task-detail__timeline-item">
                <span class="task-detail__timeline-time acos-caption acos-tabular">
                  {{ formatTime(event.createdAt) }}
                </span>
                <span class="task-detail__timeline-type">{{ eventTypeLabel(event.eventType) }}</span>
                <span class="task-detail__timeline-message">{{ event.message }}</span>
                <span class="task-detail__timeline-actor acos-caption">
                  {{ event.actorType === 'OWNER' ? '所有者' : event.actorType === 'WORKER' ? 'Worker' : '系统' }}
                </span>
              </li>
            </ol>
          </section>
        </div>

        <aside class="task-detail__side" aria-label="任务元信息">
          <section class="task-detail__panel">
            <h2>额度</h2>
            <BudgetMeter v-if="task.budget" :budget="task.budget" label="任务额度（Token）" />
            <p v-else class="acos-caption">尚未创建任务账户。</p>
            <p class="acos-caption">
              任务额度从项目额度中划出；公司额度是更上层的限制。未知用量保守计入。
            </p>
          </section>

          <section class="task-detail__panel">
            <h2>基本信息</h2>
            <dl class="task-detail__facts">
              <div>
                <dt>项目</dt>
                <dd>{{ task.projectName }}</dd>
              </div>
              <div>
                <dt>负责人</dt>
                <dd>{{ task.assigneeName ?? '未指定' }}</dd>
              </div>
              <div>
                <dt>尝试次数</dt>
                <dd class="acos-tabular">{{ task.attemptCount }}</dd>
              </div>
              <div>
                <dt>自动重试上限</dt>
                <dd class="acos-tabular">{{ task.maxAutoRetries }}</dd>
              </div>
              <div>
                <dt>创建时间</dt>
                <dd>{{ formatTime(task.createdAt) }}</dd>
              </div>
              <div>
                <dt>更新时间</dt>
                <dd>{{ formatTime(task.updatedAt) }}</dd>
              </div>
            </dl>
            <p class="acos-caption">
              执行模式：{{ task.runMode === 'DEMO' ? '演练（确定性处理器）' : '真实' }}
            </p>
          </section>
        </aside>
      </div>
    </template>

    <ConfirmDialog
      v-model="showCancelConfirm"
      title="取消这个任务？"
      description="排队中的任务会立即取消。正在运行的调用不会被立即杀死：它会在下一个安全检查点停止，并记录停止时的用量。历史证据与账本保留。"
      confirm-label="请求取消"
      variant="danger"
      :loading="cancelling"
      @confirm="requestCancel"
    >
      <label class="task-detail__cancel-reason">
        <span>取消原因（可选，写入审计）</span>
        <textarea v-model="cancelReason" rows="3" class="task-detail__textarea" />
      </label>
    </ConfirmDialog>
  </div>
</template>

<style scoped>
.task-detail {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-24);
}

.task-detail__skeleton {
  display: grid;
  gap: var(--acos-space-16);
}

.task-detail__skeleton-card {
  height: calc(var(--acos-space-64) + var(--acos-space-32));
  border-radius: var(--acos-radius-card);
  background: var(--acos-color-surface-subtle);
}

.task-detail__state-bar {
  display: flex;
  align-items: center;
  gap: var(--acos-space-12);
  flex-wrap: wrap;
  padding: var(--acos-space-12) var(--acos-space-16);
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
}

.task-detail__state-bar--warn {
  border-color: var(--acos-color-warning);
  background: var(--acos-color-warning-subtle);
}

.task-detail__demo,
.task-detail__pending {
  color: var(--acos-color-warning);
}

.task-detail__alert {
  margin: 0;
  padding: var(--acos-space-12) var(--acos-space-16);
  border-radius: var(--acos-radius-control);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  background: var(--acos-color-surface);
}

.task-detail__alert--error {
  border-color: var(--acos-color-danger);
  background: var(--acos-color-danger-subtle);
  color: var(--acos-color-danger);
}

.task-detail__alert--info {
  border-color: var(--acos-color-primary);
  background: var(--acos-color-primary-subtle);
}

.task-detail__alert--warn {
  margin: var(--acos-space-12) 0;
  border-color: var(--acos-color-warning);
  background: var(--acos-color-warning-subtle);
  color: var(--acos-color-warning);
}

.task-detail__layout {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(260px, 1fr);
  gap: var(--acos-layout-card-gap);
  align-items: start;
}

.task-detail__main {
  display: flex;
  flex-direction: column;
  gap: var(--acos-layout-card-gap);
  min-width: 0;
}

.task-detail__side {
  display: flex;
  flex-direction: column;
  gap: var(--acos-layout-card-gap);
}

.task-detail__panel {
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
  padding: var(--acos-layout-card-padding);
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-12);
  min-width: 0;
}

.task-detail__panel h2 {
  font-size: var(--acos-font-subtitle-size);
  line-height: var(--acos-font-subtitle-line);
  font-weight: var(--acos-font-subtitle-weight);
  margin: 0;
}

.task-detail__panel h3 {
  font-size: var(--acos-font-label-size);
  line-height: var(--acos-font-label-line);
  font-weight: var(--acos-font-label-weight);
  margin: 0;
}

.task-detail__panel-head {
  display: flex;
  justify-content: space-between;
  gap: var(--acos-space-12);
  flex-wrap: wrap;
  align-items: center;
}

.task-detail__prose {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.task-detail__result {
  padding: var(--acos-space-12);
  background: var(--acos-color-success-subtle);
  border-radius: var(--acos-radius-control);
}

.task-detail__error-line {
  margin: 0;
  color: var(--acos-color-danger);
  word-break: break-word;
}

.task-detail__attempts {
  display: grid;
  grid-template-columns: minmax(180px, 220px) minmax(0, 1fr);
  gap: var(--acos-space-16);
  align-items: start;
}

.task-detail__attempt-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-8);
}

.task-detail__attempt {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--acos-space-4);
  padding: var(--acos-space-12);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-surface);
  cursor: pointer;
  font: inherit;
  color: inherit;
  text-align: left;
}

.task-detail__attempt--selected {
  border-color: var(--acos-color-primary);
  background: var(--acos-color-primary-subtle);
}

.task-detail__attempt-title {
  font-weight: 500;
}

.task-detail__execution {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-12);
  min-width: 0;
}

.task-detail__execution-characteristics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--acos-space-12);
}

.task-detail__execution-characteristics dt,
.task-detail__facts dt {
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
  color: var(--acos-color-text-secondary);
}

.task-detail__execution-characteristics dd,
.task-detail__facts dd {
  margin: var(--acos-space-4) 0 0;
  font-weight: 500;
  word-break: break-word;
}

.task-detail__facts {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-12);
  margin: 0;
}

.task-detail__table-wrap {
  overflow-x: auto;
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-control);
}

.task-detail__table {
  width: 100%;
  border-collapse: collapse;
  /* No fixed min-width: the seven columns carry short values, so letting them
     wrap keeps every number visible inside the 2fr content column instead of
     clipping the last ones behind a horizontal scrollbar. */
  table-layout: auto;
  font-size: var(--acos-font-body-size);
  line-height: var(--acos-font-body-line);
}

.task-detail__table th,
.task-detail__table td {
  text-align: left;
  padding: var(--acos-layout-cell-padding-y) var(--acos-layout-cell-padding-x);
  border-top: var(--acos-control-border-width) solid var(--acos-color-border);
  overflow-wrap: anywhere;
}

.task-detail__table thead th {
  border-top: 0;
  background: var(--acos-color-surface-subtle);
  color: var(--acos-color-text-secondary);
  font-weight: 600;
}

.task-detail__timeline {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}

.task-detail__timeline-item {
  display: grid;
  grid-template-columns: 150px 110px minmax(0, 1fr) 64px;
  gap: var(--acos-space-12);
  padding: var(--acos-space-8) 0;
  border-top: var(--acos-control-border-width) solid var(--acos-color-border);
  align-items: baseline;
}

.task-detail__timeline-item:first-child {
  border-top: 0;
}

.task-detail__timeline-type {
  font-weight: 500;
}

.task-detail__timeline-message {
  word-break: break-word;
}

.task-detail__timeline-actor {
  color: var(--acos-color-text-secondary);
  text-align: right;
}

.task-detail__link {
  color: var(--acos-color-primary);
}

.task-detail__cancel-reason {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-8);
  margin-top: var(--acos-space-16);
  font-size: var(--acos-font-label-size);
  font-weight: var(--acos-font-label-weight);
}

.task-detail__textarea {
  width: 100%;
  min-height: var(--acos-control-medium-height);
  border: var(--acos-control-border-width) solid var(--acos-color-control-border);
  border-radius: var(--acos-radius-control);
  padding: var(--acos-space-8) var(--acos-space-12);
  font: inherit;
  resize: vertical;
}

.task-detail__textarea:focus-visible {
  outline: var(--acos-focus-width) solid var(--acos-color-focus);
  outline-offset: var(--acos-focus-offset);
}

@media (max-width: 1279px) {
  .task-detail__layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 767px) {
  .task-detail__attempts {
    grid-template-columns: minmax(0, 1fr);
  }

  .task-detail__timeline-item {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--acos-space-4);
  }

  .task-detail__timeline-actor {
    text-align: left;
  }

  /* Seven columns cannot fit a phone width without a horizontal scroll that
     hides the usage numbers. Below the mobile breakpoint each step becomes a
     labelled card: same facts, no clipping. */
  .task-detail__table-wrap {
    border: 0;
    border-radius: 0;
    overflow: visible;
  }

  .task-detail__table {
    min-width: 0;
  }

  .task-detail__table thead {
    display: none;
  }

  .task-detail__table,
  .task-detail__table tbody,
  .task-detail__table tr,
  .task-detail__table td {
    display: block;
    width: 100%;
  }

  .task-detail__table tr {
    border: var(--acos-control-border-width) solid var(--acos-color-border);
    border-radius: var(--acos-radius-control);
    padding: var(--acos-space-8) var(--acos-space-12);
    margin-bottom: var(--acos-space-8);
  }

  .task-detail__table td {
    border-top: 0;
    padding: var(--acos-space-4) 0;
    display: flex;
    justify-content: space-between;
    gap: var(--acos-space-12);
  }

  .task-detail__table td::before {
    content: attr(data-label);
    color: var(--acos-color-text-secondary);
    font-size: var(--acos-font-caption-size);
    line-height: var(--acos-font-caption-line);
    flex: 0 0 auto;
  }
}
</style>
