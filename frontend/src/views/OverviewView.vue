<script setup lang="ts">
/**
 * OverviewView -- company overview.
 *
 * Phase 02 adds the one business fact this phase really has: the company and its
 * five employees. Everything else is still honestly empty:
 *   1. Service connection status from the real readiness endpoint.
 *   2. Company name, goal and headcount from the company/organization API.
 *   3. Empty states for metrics that Phase 03+ will fill.
 *
 * Deliberately absent: fabricated metrics, invented percentages, and a
 * "create project" button that does nothing.
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import AppButton from '@/components/AppButton.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { ApiError, NetworkError } from '@/api/client'
import { budgetsApi, companyApi, governanceApi, systemApi, tasksApi } from '@/api/endpoints'
import type { CompanyResponse } from '@/api/organization-types'
import type { ReadinessResponse, ServiceStatus } from '@/api/types'
import { formatTime, formatTokens, taskStatus } from '@/api/task-presentation'
import type {
  BudgetAccountResponse,
  CompanyStateResponse,
  TaskSummary,
} from '@/api/task-types'
import { useSessionStore } from '@/stores/session'

const session = useSessionStore()
const router = useRouter()

const loading = ref(true)
const readiness = ref<ReadinessResponse | null>(null)
const status = ref<ServiceStatus | null>(null)
const company = ref<CompanyResponse | null>(null)
const loadError = ref<{ message: string; requestId: string | null } | null>(
  null,
)

// Phase 03 facts. Each is optional: the overview must still render when one of
// them is unavailable, and it shows "—" rather than an invented number.
const budget = ref<BudgetAccountResponse | null>(null)
const companyState = ref<CompanyStateResponse | null>(null)
const recentTasks = ref<TaskSummary[]>([])
const taskSnapshot = ref<{ counts: Record<string, number> } | null>(null)

interface OperationalSummary {
  runningCount: number
  queuedCount: number
  succeededCount: number
  failedCount: number
  blockedCount: number
  interruptedCount: number
  issueCount: number
}

const operational = computed<OperationalSummary | null>(() => {
  const snapshot = taskSnapshot.value
  if (!snapshot) return null
  // A response without counts (an older server, or a partial mock) still has to
  // render: fall back to an empty map so the metric shows 0 measured tasks
  // instead of crashing the page.
  const counts = snapshot.counts ?? {}
  const running = (counts.RUNNING ?? 0) + (counts.WAITING_APPROVAL ?? 0)
  const queued = counts.QUEUED ?? 0
  return {
    runningCount: running,
    queuedCount: queued,
    succeededCount: counts.SUCCEEDED ?? 0,
    failedCount: counts.FAILED ?? 0,
    blockedCount: counts.BLOCKED ?? 0,
    interruptedCount: counts.INTERRUPTED ?? 0,
    issueCount:
      (counts.FAILED ?? 0) +
      (counts.BLOCKED ?? 0) +
      (counts.INTERRUPTED ?? 0) +
      (counts.CANCELLED ?? 0),
  }
})

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const [readyBody, statusBody] = await Promise.all([
      systemApi.readiness(),
      systemApi.status(),
    ])
    readiness.value = readyBody
    status.value = statusBody
    // The company is optional: before the wizard runs, /company answers 404 and
    // that is a normal pre-initialization state, not an error worth blocking on.
    try {
      company.value = await companyApi.get()
    } catch (companyError) {
      if (!(companyError instanceof ApiError && companyError.status === 404)) {
        throw companyError
      }
      company.value = null
    }
  } catch (error) {
    if (error instanceof ApiError) {
      loadError.value = { message: error.message, requestId: error.requestId }
    } else if (error instanceof NetworkError) {
      loadError.value = { message: error.message, requestId: null }
    } else {
      loadError.value = {
        message: '无法读取服务状态，请稍后重试。',
        requestId: null,
      }
    }
  } finally {
    loading.value = false
  }
}

/**
 * Phase 03 metrics, loaded separately from the service facts.
 *
 * They are deliberately not part of ``load()``: the readiness panel must render
 * as soon as health is known, and a slow or failing task/budget query must not
 * hold the whole page in a skeleton. A failure leaves each metric as "—".
 */
async function loadOperational() {
  try {
    const currentCompany = company.value ?? (await companyApi.get().catch(() => null))
    if (!currentCompany) return
    const [budgetResult, stateResult, tasks] = await Promise.allSettled([
      budgetsApi.overview(1),
      governanceApi.state(),
      tasksApi.list({ limit: 5 }),
    ])
    if (budgetResult.status === 'fulfilled') {
      budget.value = budgetResult.value.company
    }
    if (stateResult.status === 'fulfilled') {
      companyState.value = stateResult.value
    }
    if (tasks.status === 'fulfilled') {
      // Guard the shape: a partial or older payload must leave the list empty
      // rather than assign undefined into a ref the template iterates.
      recentTasks.value = Array.isArray(tasks.value.tasks) ? tasks.value.tasks : []
      taskSnapshot.value = { counts: tasks.value.counts ?? {} }
    }
  } catch {
    // Nothing to do: the metrics stay "—" and the service panel is unaffected.
  }
}

onMounted(async () => {
  await load()
  // Started after the service panel has loaded so the metrics never delay it.
  void loadOperational()
})
</script>

<template>
  <div class="overview">
    <PageHeader
      title="公司概览"
      :description="
        company
          ? `${company.name}：${company.goal}`
          : '查看工作进展与需要你处理的事项。公司、任务、提案与预算随阶段逐步接入。'
      "
    />

    <div
      v-if="loading"
      class="overview__skeleton"
      aria-live="polite"
      aria-busy="true"
    >
      <span class="acos-visually-hidden">正在加载服务状态</span>
      <div v-for="index in 4" :key="index" class="overview__skeleton-card" />
    </div>

    <ErrorState
      v-else-if="loadError"
      title="服务状态读取失败"
      :description="loadError.message"
      :request-id="loadError.requestId"
      :retrying="loading"
      @retry="load"
    />

    <template v-else>
      <!-- Real company facts from Phase 02 -->
      <section
        v-if="company"
        class="overview__panel"
        aria-labelledby="company-heading"
      >
        <div class="overview__panel-head">
          <h2 id="company-heading">{{ company.name }}</h2>
          <StatusBadge label="运行中" tone="success" />
        </div>
        <p class="acos-text-secondary">{{ company.goal }}</p>
        <dl class="overview__company-facts">
          <div>
            <dt>岗位</dt>
            <dd>{{ company.positionCount }}</dd>
          </div>
          <div>
            <dt>员工</dt>
            <dd>{{ company.agentCount }}</dd>
          </div>
          <div>
            <dt>模板</dt>
            <dd>{{ company.templateKey }} v{{ company.templateVersion }}</dd>
          </div>
          <div>
            <dt>初始化时间</dt>
            <dd>{{ new Date(company.initializedAt).toLocaleString('zh-CN', { hour12: false }) }}</dd>
          </div>
        </dl>
        <p class="acos-caption">
          组织与模型配置可在组织、模型中心修改；每次变更写入审计记录。
        </p>
      </section>

      <EmptyState
        v-else
        title="公司尚未创建"
        description="公司初始化向导会按默认科技公司模板创建五个岗位和五名员工。"
      >
        <template #action>
          <AppButton @click="router.push('/setup/company')">开始创建公司</AppButton>
        </template>
      </EmptyState>

      <!-- Real service facts -->
      <section class="overview__panel" aria-labelledby="service-status-heading">
        <div class="overview__panel-head">
          <h2 id="service-status-heading">服务状态</h2>
          <StatusBadge
            v-if="readiness"
            :label="readiness.ready ? 'API 可服务' : 'API 未就绪'"
            :tone="readiness.ready ? 'success' : 'warning'"
          />
        </div>

        <p class="acos-caption">
          API 就绪检查数据库与迁移；后台 Worker
          心跳单独列示，异常时仍可登录查看状态。
        </p>

        <ul v-if="readiness" class="overview__checks">
          <li
            v-for="check in readiness.checks"
            :key="check.name"
            class="overview__check"
          >
            <span class="overview__check-name">{{
              check.name === 'database'
                ? '数据库'
                : check.name === 'migrations'
                  ? '数据库迁移'
                  : check.name === 'worker'
                    ? '后台 Worker'
                    : check.name
            }}</span>
            <StatusBadge
              :label="
                check.status === 'ok'
                  ? '正常'
                  : check.status === 'fail'
                    ? '异常'
                    : '未检查'
              "
              :tone="
                check.status === 'ok'
                  ? 'success'
                  : check.status === 'fail'
                    ? 'danger'
                    : 'neutral'
              "
            />
            <span class="overview__check-detail acos-caption">{{
              check.detail ?? '—'
            }}</span>
          </li>
        </ul>

        <p class="overview__meta acos-caption">
          <template v-if="status">
            应用版本 {{ status.appVersion }} · 环境 {{ status.environment }} ·
            数据库版本
            <code>{{ status.databaseRevision ?? '未知' }}</code>
          </template>
        </p>
      </section>

      <!-- Metrics: only the ones this phase can measure are filled in; the rest
           stay an em dash rather than a fabricated zero. -->
      <section class="overview__metrics" aria-label="业务指标">
        <div class="overview__metric">
          <span class="overview__metric-label">进行中任务</span>
          <span class="overview__metric-value acos-tabular">
            {{ operational ? operational.runningCount + operational.queuedCount : '—' }}
          </span>
          <span class="overview__metric-hint acos-caption">
            {{ operational
              ? `执行中 ${operational.runningCount} · 排队中 ${operational.queuedCount}`
              : '任务数据读取失败' }}
          </span>
        </div>
        <div class="overview__metric">
          <span class="overview__metric-label">待决策提案</span>
          <span class="overview__metric-value acos-tabular">—</span>
          <span class="overview__metric-hint acos-caption">阶段 06 接入提案治理后显示</span>
        </div>
        <div class="overview__metric">
          <span class="overview__metric-label">已验证交付</span>
          <span class="overview__metric-value acos-tabular">
            {{ operational ? operational.succeededCount : '—' }}
          </span>
          <span class="overview__metric-hint acos-caption">阶段 03 起统计任务成功数；验证证据属阶段 05</span>
        </div>
        <div class="overview__metric">
          <span class="overview__metric-label">Token 剩余额度</span>
          <span class="overview__metric-value acos-tabular">
            {{ budget ? formatTokens(budget.availableTokens) : '—' }}
          </span>
          <span class="overview__metric-hint acos-caption">
            <template v-if="budget">
              已用 {{ formatTokens(budget.consumedTokens) }} · 预留
              {{ formatTokens(budget.reservedTokens) }}
              <template v-if="budget.needsReconciliationCount > 0">
                · {{ budget.needsReconciliationCount }} 笔待核对
              </template>
            </template>
            <template v-else>预算数据读取失败</template>
          </span>
        </div>
      </section>

      <section class="overview__panels">
        <section class="overview__panel" aria-labelledby="recent-tasks-heading">
          <div class="overview__panel-head">
            <h2 id="recent-tasks-heading">近期任务</h2>
            <AppButton variant="ghost" size="small" @click="router.push('/projects')">
              查看全部
            </AppButton>
          </div>
          <EmptyState
            v-if="recentTasks.length === 0"
            title="还没有任务"
            description="在项目与任务页创建项目后即可提交任务；任务由 Worker 从持久队列领取。"
          >
            <template #action>
              <AppButton variant="secondary" @click="router.push('/projects')">
                前往项目与任务
              </AppButton>
            </template>
          </EmptyState>
          <ul v-else class="overview__tasks">
            <li v-for="item in recentTasks" :key="item.id" class="overview__task">
              <button
                type="button"
                class="overview__task-button"
                @click="router.push(`/tasks/${item.id}`)"
              >
                <span class="overview__task-title">{{ item.title }}</span>
                <StatusBadge
                  :label="taskStatus(item.status).label"
                  :tone="taskStatus(item.status).tone"
                />
                <span class="acos-caption">{{ formatTime(item.updatedAt) }}</span>
              </button>
            </li>
          </ul>
        </section>

        <section class="overview__panel" aria-labelledby="blockers-heading">
          <div class="overview__panel-head">
            <h2 id="blockers-heading">预算与阻塞</h2>
            <StatusBadge
              v-if="companyState"
              :label="companyState.status === 'PAUSED' ? '公司已暂停' : '公司运行中'"
              :tone="companyState.status === 'PAUSED' ? 'warning' : 'success'"
            />
          </div>
          <ul class="overview__blockers">
            <li v-if="companyState?.status === 'PAUSED'">
              公司已暂停：不会再发起新的模型调用或创建新任务。
              <template v-if="companyState.pauseReason">
                原因：{{ companyState.pauseReason }}
              </template>
            </li>
            <li v-if="budget && budget.availableTokens === 0">
              公司可用 Token 为 0，新的付费调用会被拒绝。
            </li>
            <li v-if="operational && operational.blockedCount > 0">
              {{ operational.blockedCount }} 个任务已阻塞，需要处理后重试。
            </li>
            <li v-if="operational && operational.interruptedCount > 0">
              {{ operational.interruptedCount }} 个任务因 Worker 中断停止；副作用未知的不会自动重放，
              需要在任务详情人工确认后重试。
            </li>
            <li v-if="operational && operational.failedCount > 0">
              {{ operational.failedCount }} 个任务失败，可在任务详情查看原因并重试。
            </li>
            <li
              v-if="
                (!companyState || companyState.status !== 'PAUSED') &&
                  (!operational || operational.issueCount === 0)
              "
            >
              没有需要处理的阻塞项。
            </li>
          </ul>
          <p class="acos-caption">
            点开任务可查看执行历史、预算预留与审计记录；预算页提供完整账本。
          </p>
        </section>
      </section>

      <div class="overview__actions">
        <AppButton variant="secondary" @click="load">刷新服务状态</AppButton>
        <span class="acos-caption">
          当前账号：{{ session.user?.displayName ?? '所有者' }}（{{
            session.user?.username ?? '—'
          }}）
        </span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.overview {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-24);
}

.overview__skeleton {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--acos-space-16);
}

.overview__skeleton-card {
  height: calc(var(--acos-space-64) + var(--acos-space-32));
  border-radius: var(--acos-radius-card);
  background: var(--acos-color-surface-subtle);
  animation: overview-pulse 1.4s ease-in-out infinite;
}

@keyframes overview-pulse {
  0%,
  100% {
    opacity: 0.6;
  }
  50% {
    opacity: 1;
  }
}

.overview__panel {
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
  padding: var(--acos-layout-card-padding);
}

.overview__panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--acos-space-12);
  margin-bottom: var(--acos-space-16);
  flex-wrap: wrap;
}

.overview__checks {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}

.overview__check {
  display: flex;
  align-items: center;
  gap: var(--acos-space-12);
  min-height: var(--acos-layout-table-row-min);
  border-top: var(--acos-control-border-width) solid var(--acos-color-border);
  flex-wrap: wrap;
}

.overview__check:first-child {
  border-top: 0;
}

.overview__check-name {
  min-width: 96px;
  font-weight: 500;
}

.overview__check-detail {
  color: var(--acos-color-text-secondary);
  word-break: break-word;
}

.overview__meta {
  margin-top: var(--acos-space-12);
}

.overview__meta code {
  font-family: var(--acos-font-mono);
}

.overview__company-facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--acos-space-16);
  margin: var(--acos-space-16) 0;
}

.overview__company-facts dt {
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
  color: var(--acos-color-text-secondary);
}

.overview__company-facts dd {
  margin: var(--acos-space-4) 0 0;
  font-weight: 500;
  word-break: break-word;
}

.overview__metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--acos-layout-card-gap);
}

.overview__metric {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-4);
  padding: var(--acos-space-20);
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
}

.overview__metric-label {
  color: var(--acos-color-text-secondary);
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
}

.overview__metric-value {
  font-size: var(--acos-font-metric-size);
  line-height: var(--acos-font-metric-line);
  font-weight: var(--acos-font-metric-weight);
  font-variant-numeric: tabular-nums;
  color: var(--acos-color-text-muted);
}

.overview__metric-hint {
  color: var(--acos-color-text-muted);
}

.overview__panels {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: var(--acos-layout-card-gap);
}

.overview__tasks {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}

.overview__task {
  border-top: var(--acos-control-border-width) solid var(--acos-color-border);
}

.overview__task:first-child {
  border-top: 0;
}

.overview__task-button {
  width: 100%;
  display: flex;
  align-items: center;
  gap: var(--acos-space-12);
  padding: var(--acos-space-12) 0;
  min-height: var(--acos-layout-table-row-min);
  background: none;
  border: 0;
  cursor: pointer;
  font: inherit;
  color: inherit;
  text-align: left;
  flex-wrap: wrap;
}

.overview__task-title {
  flex: 1 1 auto;
  min-width: 0;
  word-break: break-word;
}

.overview__blockers {
  margin: 0;
  padding-left: var(--acos-space-20);
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-8);
}

.overview__actions {
  display: flex;
  align-items: center;
  gap: var(--acos-space-16);
  flex-wrap: wrap;
}

@media (max-width: 1279px) {
  .overview__company-facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--acos-space-16);
  margin: var(--acos-space-16) 0;
}

.overview__company-facts dt {
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
  color: var(--acos-color-text-secondary);
}

.overview__company-facts dd {
  margin: var(--acos-space-4) 0 0;
  font-weight: 500;
  word-break: break-word;
}

.overview__metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 767px) {
  .overview__company-facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--acos-space-16);
  margin: var(--acos-space-16) 0;
}

.overview__company-facts dt {
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
  color: var(--acos-color-text-secondary);
}

.overview__company-facts dd {
  margin: var(--acos-space-4) 0 0;
  font-weight: 500;
  word-break: break-word;
}

.overview__metrics {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
