<script setup lang="ts">
/**
 * ExecutionDetailView -- one attempt, its steps and its money.
 *
 * PAGES.md §6: timeline + evidence + budget side panel. The page states the
 * attempt is a demo run, shows each step's status and usage, and shows the
 * ledger rows that belong to this attempt. Unknown usage is written as
 * "待核对" — never as 0 — and an interrupted attempt says its outside effects
 * are unknown.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useTaskFeed } from '@/composables/useTaskFeed'
import LiveUpdateStatus from '@/components/LiveUpdateStatus.vue'
import { useRoute, useRouter } from 'vue-router'

import AppButton from '@/components/AppButton.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { ApiError, NetworkError } from '@/api/client'
import { executionsApi } from '@/api/endpoints'
import {
  demoBehaviorLabel,
  executionStatus,
  formatLedgerUsage,
  formatTime,
  formatTokens,
  formatTokensOrPending,
  ledgerKind,
  stepStatus,
} from '@/api/task-presentation'
import type { ExecutionSummary, LedgerEntry } from '@/api/task-types'

const route = useRoute()
const router = useRouter()
const executionId = computed(() => Number(route.params.id))

const loading = ref(true)
const loadError = ref<{ message: string; requestId: string | null } | null>(null)
const execution = ref<ExecutionSummary | null>(null)
const ledger = ref<LedgerEntry[]>([])
const totalReserved = ref(0)
const totalConsumed = ref(0)
const unknownCount = ref(0)
const nextCursor = ref<number | null>(null)
const loadingMore = ref(false)
const moreError = ref<string | null>(null)
let loadVersion = 0
let disposed = false
const feed = useTaskFeed(() => load(true), event =>
  event.executionId === executionId.value || event.taskId === execution.value?.taskId)
const { state: streamState, stale, notice } = feed

function errorFrom(error: unknown) {
  if (error instanceof ApiError) {
    return { message: error.message, requestId: error.requestId }
  }
  if (error instanceof NetworkError) return { message: error.message, requestId: null }
  return { message: '请求失败，请稍后重试。', requestId: null }
}

async function load(silent = false) {
  const id = executionId.value
  const version = ++loadVersion
  if (!silent) { loading.value = true; loadError.value = null }
  try {
    const [detail, page] = await Promise.all([executionsApi.get(id), executionsApi.ledger(id)])
    if (disposed || version !== loadVersion) return true
    execution.value = detail
    ledger.value = page.entries
    nextCursor.value = page.nextCursor
    totalReserved.value = page.totalReserved
    totalConsumed.value = page.totalConsumed
    unknownCount.value = page.needsReconciliationCount
    loadError.value = null
    moreError.value = null
    feed.snapshotResult(true)
    return true
  } catch (error) {
    if (disposed || version !== loadVersion) return true
    if (!execution.value) loadError.value = errorFrom(error)
    feed.snapshotResult(false)
    return false
  } finally {
    if (version === loadVersion) loading.value = false
  }
}

async function loadMore() {
  if (nextCursor.value === null || loadingMore.value) return
  const version = loadVersion
  loadingMore.value = true
  moreError.value = null
  try {
    const page = await executionsApi.ledger(executionId.value, nextCursor.value)
    if (disposed || version !== loadVersion) return
    ledger.value.push(...page.entries)
    nextCursor.value = page.nextCursor
    totalReserved.value = page.totalReserved
    totalConsumed.value = page.totalConsumed
    unknownCount.value = page.needsReconciliationCount
  } catch (error) {
    if (!disposed && version === loadVersion) moreError.value = errorFrom(error).message
  } finally { loadingMore.value = false }
}

onMounted(() => { void load() })
onBeforeUnmount(() => { disposed = true; loadVersion += 1 })
watch(executionId, () => {
  execution.value = null
  ledger.value = []
  feed.reset()
  void load()
})
</script>

<template>
  <div class="execution-detail">
    <div v-if="loading" class="execution-detail__skeleton" aria-busy="true" aria-live="polite">
      <span class="acos-visually-hidden">正在加载执行详情</span>
      <div class="execution-detail__skeleton-card" />
    </div>

    <ErrorState
      v-else-if="loadError"
      title="执行读取失败"
      :description="loadError.message"
      :request-id="loadError.requestId"
      @retry="load"
    />

    <template v-else-if="execution">
      <PageHeader
        :title="`执行 #${execution.id}`"
        :description="`任务 #${execution.taskId} · 第 ${execution.attempt} 次尝试`"
      >
        <template #actions>
          <AppButton variant="secondary" @click="router.push(`/tasks/${execution.taskId}`)">
            返回任务详情
          </AppButton>
        </template>
      </PageHeader>

      <LiveUpdateStatus :notice="notice" :warning="streamState !== 'live' || stale" :stale="stale" @retry="feed.scheduleRefresh(0)" />
      <div class="execution-detail__bar">
        <StatusBadge
          :label="executionStatus(execution.status).label"
          :tone="executionStatus(execution.status).tone"
        />
        <span class="acos-caption execution-detail__demo">
          演练运行（确定性处理器，非真实模型输出）
        </span>
        <span class="acos-caption">行为：{{ demoBehaviorLabel(execution.demoBehavior) }}</span>
      </div>

      <p
        v-if="execution.needsReconciliation"
        class="execution-detail__alert"
        role="alert"
      >
        供应商未返回用量：已按预留额度保守计入已用并标记为待核对，不会记为 0。
      </p>
      <p
        v-if="execution.interruptedUnknownSideEffects"
        class="execution-detail__alert"
        role="alert"
      >
        该尝试的进程中断，外部副作用未知，系统未自动重放。
        <template v-if="execution.recoveryNote">{{ execution.recoveryNote }}</template>
      </p>

      <div class="execution-detail__layout">
        <div class="execution-detail__main">
          <section class="execution-detail__panel" aria-labelledby="steps-heading">
            <h2 id="steps-heading">步骤时间线</h2>
            <EmptyState
              v-if="execution.steps.length === 0"
              title="没有步骤"
              description="该执行在第一个步骤开始前结束。"
            />
            <ol v-else class="execution-detail__steps">
              <li v-for="step in execution.steps" :key="step.id" class="execution-detail__step">
                <div class="execution-detail__step-head">
                  <span class="execution-detail__step-index acos-tabular">
                    {{ step.sequence + 1 }}
                  </span>
                  <span class="execution-detail__step-name">{{ step.name }}</span>
                  <StatusBadge
                    :label="stepStatus(step.status).label"
                    :tone="stepStatus(step.status).tone"
                  />
                </div>
                <dl class="execution-detail__step-facts">
                  <div>
                    <dt>预估</dt>
                    <dd class="acos-tabular">{{ formatTokens(step.estimatedTokens) }}</dd>
                  </div>
                  <div>
                    <dt>输入 Token</dt>
                    <dd class="acos-tabular">
                      {{ formatTokensOrPending(step.promptTokens, step.usageKnown) }}
                    </dd>
                  </div>
                  <div>
                    <dt>输出 Token</dt>
                    <dd class="acos-tabular">
                      {{ formatTokensOrPending(step.completionTokens, step.usageKnown) }}
                    </dd>
                  </div>
                  <div>
                    <dt>开始</dt>
                    <dd>{{ formatTime(step.startedAt) }}</dd>
                  </div>
                  <div>
                    <dt>结束</dt>
                    <dd>{{ formatTime(step.finishedAt) }}</dd>
                  </div>
                </dl>
                <p v-if="step.detail" class="acos-caption">{{ step.detail }}</p>
              </li>
            </ol>
          </section>

          <section class="execution-detail__panel" aria-labelledby="ledger-heading">
            <h2 id="ledger-heading">本次执行的账本</h2>
            <p v-if="moreError" role="alert">{{ moreError }}</p>
            <AppButton v-if="nextCursor !== null" variant="secondary" :loading="loadingMore" @click="loadMore">加载更多账本</AppButton>
            <p class="acos-caption">
              预留、结算与释放都逐笔保留；未知用量会写“待核对”，不会写成 0。
            </p>
            <EmptyState
              v-if="ledger.length === 0"
              title="没有账本记录"
              description="该执行没有产生或结算任何额度。"
            />
            <div v-else class="execution-detail__table-wrap">
              <table class="execution-detail__table">
                <thead>
                  <tr>
                    <th scope="col">时间</th>
                    <th scope="col">类型</th>
                    <th scope="col">变动（预留）</th>
                    <th scope="col">变动（已用）</th>
                    <th scope="col">用量</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="entry in ledger" :key="entry.id">
                    <td class="acos-caption">{{ formatTime(entry.createdAt) }}</td>
                    <td>{{ ledgerKind(entry.entryKind) }}</td>
                    <td class="acos-tabular">{{ formatTokens(entry.deltaReserved) }}</td>
                    <td class="acos-tabular">{{ formatTokens(entry.deltaConsumed) }}</td>
                    <td class="acos-tabular">
                      {{ formatLedgerUsage(entry) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>

        <aside class="execution-detail__side" aria-label="执行元信息">
          <section class="execution-detail__panel">
            <h2>预算</h2>
            <dl class="execution-detail__facts">
              <div>
                <dt>本次预留合计</dt>
                <dd class="acos-tabular">{{ formatTokens(totalReserved) }}</dd>
              </div>
              <div>
                <dt>本次结算合计</dt>
                <dd class="acos-tabular">
                  {{ unknownCount > 0 ? '含待核对' : formatTokens(totalConsumed) }}
                </dd>
              </div>
              <div>
                <dt>待核对条目</dt>
                <dd class="acos-tabular">{{ unknownCount }}</dd>
              </div>
            </dl>
          </section>

          <section class="execution-detail__panel">
            <h2>执行信息</h2>
            <dl class="execution-detail__facts">
              <div>
                <dt>Worker</dt>
                <dd>{{ execution.workerId ?? '—' }}</dd>
              </div>
              <div>
                <dt>Fencing token</dt>
                <dd class="acos-tabular">{{ execution.fencingToken }}</dd>
              </div>
              <div>
                <dt>租约到期</dt>
                <dd>{{ formatTime(execution.leaseExpiresAt) }}</dd>
              </div>
              <div>
                <dt>最近心跳</dt>
                <dd>{{ formatTime(execution.heartbeatAt) }}</dd>
              </div>
              <div>
                <dt>开始</dt>
                <dd>{{ formatTime(execution.startedAt) }}</dd>
              </div>
              <div>
                <dt>结束</dt>
                <dd>{{ formatTime(execution.finishedAt) }}</dd>
              </div>
              <div>
                <dt>重试来源</dt>
                <dd>
                  {{ execution.retriedFromExecutionId
                    ? `#${execution.retriedFromExecutionId}`
                    : '首次执行' }}
                </dd>
              </div>
            </dl>
            <p v-if="execution.errorCode" class="execution-detail__error">
              错误码 {{ execution.errorCode }}：{{ execution.errorMessage ?? '—' }}
            </p>
            <p v-if="execution.resultSummary" class="execution-detail__result">
              {{ execution.resultSummary }}
            </p>
          </section>
        </aside>
      </div>
    </template>
  </div>
</template>

<style scoped>
.execution-detail {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-24);
}

.execution-detail__skeleton {
  display: grid;
  gap: var(--acos-space-16);
}

.execution-detail__skeleton-card {
  height: calc(var(--acos-space-64) + var(--acos-space-32));
  border-radius: var(--acos-radius-card);
  background: var(--acos-color-surface-subtle);
}

.execution-detail__bar {
  display: flex;
  align-items: center;
  gap: var(--acos-space-12);
  flex-wrap: wrap;
  padding: var(--acos-space-12) var(--acos-space-16);
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
}

.execution-detail__demo {
  color: var(--acos-color-warning);
}

.execution-detail__alert {
  margin: 0;
  padding: var(--acos-space-12) var(--acos-space-16);
  border: var(--acos-control-border-width) solid var(--acos-color-warning);
  background: var(--acos-color-warning-subtle);
  color: var(--acos-color-warning);
  border-radius: var(--acos-radius-control);
}

.execution-detail__layout {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(260px, 1fr);
  gap: var(--acos-layout-card-gap);
  align-items: start;
}

.execution-detail__main,
.execution-detail__side {
  display: flex;
  flex-direction: column;
  gap: var(--acos-layout-card-gap);
  min-width: 0;
}

.execution-detail__panel {
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
  padding: var(--acos-layout-card-padding);
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-12);
  min-width: 0;
}

.execution-detail__panel h2 {
  font-size: var(--acos-font-subtitle-size);
  line-height: var(--acos-font-subtitle-line);
  font-weight: var(--acos-font-subtitle-weight);
  margin: 0;
}

.execution-detail__steps {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-16);
}

.execution-detail__step {
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-control);
  padding: var(--acos-space-12);
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-8);
}

.execution-detail__step-head {
  display: flex;
  align-items: center;
  gap: var(--acos-space-8);
  flex-wrap: wrap;
}

.execution-detail__step-index {
  width: var(--acos-space-24);
  height: var(--acos-space-24);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--acos-radius-pill);
  background: var(--acos-color-surface-subtle);
  color: var(--acos-color-text-secondary);
  font-weight: 600;
}

.execution-detail__step-name {
  font-weight: 500;
  flex: 1 1 auto;
  min-width: 0;
  word-break: break-word;
}

.execution-detail__step-facts,
.execution-detail__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: var(--acos-space-12);
  margin: 0;
}

.execution-detail__facts {
  grid-template-columns: minmax(0, 1fr);
}

.execution-detail__step-facts dt,
.execution-detail__facts dt {
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
  color: var(--acos-color-text-secondary);
}

.execution-detail__step-facts dd,
.execution-detail__facts dd {
  margin: var(--acos-space-4) 0 0;
  font-weight: 500;
  word-break: break-word;
}

.execution-detail__table-wrap {
  overflow-x: auto;
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-control);
}

.execution-detail__table {
  width: 100%;
  border-collapse: collapse;
  min-width: 520px;
  font-size: var(--acos-font-body-size);
  line-height: var(--acos-font-body-line);
}

.execution-detail__table th,
.execution-detail__table td {
  text-align: left;
  padding: var(--acos-layout-cell-padding-y) var(--acos-layout-cell-padding-x);
  border-top: var(--acos-control-border-width) solid var(--acos-color-border);
}

.execution-detail__table thead th {
  border-top: 0;
  background: var(--acos-color-surface-subtle);
  color: var(--acos-color-text-secondary);
  font-weight: 600;
}

.execution-detail__error {
  margin: 0;
  color: var(--acos-color-danger);
  word-break: break-word;
}

.execution-detail__result {
  margin: 0;
  padding: var(--acos-space-12);
  background: var(--acos-color-success-subtle);
  border-radius: var(--acos-radius-control);
  word-break: break-word;
}

@media (max-width: 1279px) {
  .execution-detail__layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
