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
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import AppButton from '@/components/AppButton.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { ApiError, NetworkError } from '@/api/client'
import { companyApi, systemApi } from '@/api/endpoints'
import type { CompanyResponse } from '@/api/organization-types'
import type { ReadinessResponse, ServiceStatus } from '@/api/types'
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

onMounted(load)

const metricPlaceholders = [
  { label: '进行中任务', hint: '阶段 03 接入任务调度后显示' },
  { label: '待决策提案', hint: '阶段 06 接入提案治理后显示' },
  { label: '已验证交付', hint: '阶段 05 接入验证证据后显示' },
  { label: 'Token 剩余额度', hint: '阶段 03 接入预算账本后显示' },
]
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

      <!-- Honest empty metrics -->
      <section class="overview__metrics" aria-label="业务指标（尚未接入）">
        <div
          v-for="metric in metricPlaceholders"
          :key="metric.label"
          class="overview__metric"
        >
          <span class="overview__metric-label">{{ metric.label }}</span>
          <span class="overview__metric-value" aria-label="暂无数据">—</span>
          <span class="overview__metric-hint acos-caption">{{
            metric.hint
          }}</span>
        </div>
      </section>

      <section class="overview__panels">
        <EmptyState
          title="还没有任务"
          description="任务调度在阶段 03 接入。当前阶段只提供账号、服务健康检查与工作台骨架，不会创建任何模拟任务。"
          phase-note="阶段 03 开放项目与任务"
        />
        <EmptyState
          title="没有等待你处理的提案"
          description="提案中心会在阶段 06 接入 Finding → Proposal → Decision 流程。现在不会出现模拟审批。"
          phase-note="阶段 06 开放提案与分级治理"
        />
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
