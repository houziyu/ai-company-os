<script setup lang="ts">
/**
 * BudgetsView -- company/project allowances and the token ledger.
 *
 * PAGES.md §8 requires four values per allowance (上限/已用/预留/可用), says a
 * chart needs text, and says "待结算不是零". So:
 * - the four numbers are always written out;
 * - entries whose provider usage was never reported are listed separately and
 *   labelled 待核对, never summed into a clean total;
 * - there is no "充值" or editable limit button, because no such command exists
 *   in this phase. The page says so instead of showing a fake control.
 */
import { computed, onMounted, ref } from 'vue'

import AppButton from '@/components/AppButton.vue'
import BudgetMeter from '@/components/BudgetMeter.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { ApiError, NetworkError } from '@/api/client'
import { budgetsApi } from '@/api/endpoints'
import {
  formatLedgerUsage,
  formatTime,
  formatTokens,
  ledgerKind,
  ledgerSource,
  scopeLabel,
} from '@/api/task-presentation'
import type {
  BudgetAccountResponse,
  BudgetOverviewResponse,
  LedgerEntry,
  PendingReconciliationEntry,
} from '@/api/task-types'

const loading = ref(true)
const loadError = ref<{ message: string; requestId: string | null } | null>(null)
const overview = ref<BudgetOverviewResponse | null>(null)
const pending = ref<PendingReconciliationEntry[]>([])
const filter = ref<'all' | 'RESERVE' | 'SETTLE' | 'RELEASE'>('all')

function errorFrom(error: unknown) {
  if (error instanceof ApiError) {
    return { message: error.message, requestId: error.requestId }
  }
  if (error instanceof NetworkError) return { message: error.message, requestId: null }
  return { message: '请求失败，请稍后重试。', requestId: null }
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const [body, pendingRows] = await Promise.all([
      budgetsApi.overview(),
      budgetsApi.pendingReconciliation(),
    ])
    overview.value = body
    pending.value = pendingRows
  } catch (error) {
    loadError.value = errorFrom(error)
  } finally {
    loading.value = false
  }
}

onMounted(load)

const filteredLedger = computed<LedgerEntry[]>(() => {
  const rows = overview.value?.ledger ?? []
  return filter.value === 'all' ? rows : rows.filter((row) => row.entryKind === filter.value)
})

const companyBudget = computed(() => {
  const company = overview.value?.company
  if (!company) return null
  return {
    limitTokens: company.limitTokens,
    consumedTokens: company.consumedTokens,
    reservedTokens: company.reservedTokens,
    availableTokens: company.availableTokens,
    needsReconciliationCount: company.needsReconciliationCount,
  }
})

function accountBudget(account: BudgetAccountResponse) {
  return {
    limitTokens: account.limitTokens,
    consumedTokens: account.consumedTokens,
    reservedTokens: account.reservedTokens,
    availableTokens: account.availableTokens,
    needsReconciliationCount: account.needsReconciliationCount,
  }
}

const filters: { value: 'all' | 'RESERVE' | 'SETTLE' | 'RELEASE'; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'RESERVE', label: '预留' },
  { value: 'SETTLE', label: '结算' },
  { value: 'RELEASE', label: '释放' },
]
</script>

<template>
  <div class="budgets">
    <PageHeader
      title="预算"
      description="Token 额度按公司、项目、任务三层校验。每次付费调用前预留、调用后结算；未知用量保守计入并标记待核对。"
    >
      <template #actions>
        <AppButton variant="secondary" :loading="loading" loading-text="刷新中…" @click="load">
          刷新账本
        </AppButton>
      </template>
    </PageHeader>

    <div v-if="loading" class="budgets__skeleton" aria-busy="true" aria-live="polite">
      <span class="acos-visually-hidden">正在加载预算</span>
      <div v-for="index in 3" :key="index" class="budgets__skeleton-card" />
    </div>

    <ErrorState
      v-else-if="loadError"
      title="预算读取失败"
      :description="loadError.message"
      :request-id="loadError.requestId"
      @retry="load"
    />

    <template v-else-if="overview">
      <section class="budgets__panel" aria-labelledby="company-budget-heading">
        <div class="budgets__panel-head">
          <h2 id="company-budget-heading">公司额度</h2>
          <StatusBadge
            :label="overview.pendingReconciliationCount > 0 ? '存在待核对用量' : '账本一致'"
            :tone="overview.pendingReconciliationCount > 0 ? 'warning' : 'success'"
          />
        </div>
        <BudgetMeter v-if="companyBudget" :budget="companyBudget" label="公司总额度（Token）" />
        <p class="acos-caption">
          统计口径：账本记录自 {{ formatTime(overview.periodStart) }} 起；生成时间
          {{ formatTime(overview.generatedAt) }}。额度上限当前只能由部署或管理员设置，页面不提供“充值”或在线改额度。
        </p>
        <p class="acos-caption">
          币值估算未配置价格：本页只展示 Token，不把它换算成金额，避免把估算当作账单。
        </p>
      </section>

      <section class="budgets__panel" aria-labelledby="project-budget-heading">
        <h2 id="project-budget-heading">项目额度</h2>
        <EmptyState
          v-if="overview.projects.length === 0"
          title="还没有项目额度"
          description="创建项目后会在这里显示项目额度的上限、已用、预留与可用。"
        />
        <ul v-else class="budgets__accounts">
          <li v-for="account in overview.projects" :key="`${account.scope}-${account.scopeId}`">
            <BudgetMeter :budget="accountBudget(account)" :label="account.label" />
          </li>
        </ul>
      </section>

      <section class="budgets__panel" aria-labelledby="pending-heading">
        <div class="budgets__panel-head">
          <h2 id="pending-heading">待核对用量</h2>
          <span class="acos-caption">
            这些预留未获得供应商确认的用量；已按保守额度计入已用，不会显示为 0。
          </span>
        </div>
        <EmptyState
          v-if="pending.length === 0"
          title="没有待核对用量"
          description="所有已结算的调用都获得了可核对的用量，或尚未发生调用。"
        />
        <div v-else class="budgets__table-wrap">
          <table class="budgets__table">
            <thead>
              <tr>
                <th scope="col">范围</th>
                <th scope="col">类型</th>
                <th scope="col">预留额度</th>
                <th scope="col">结算</th>
                <th scope="col">任务 / 执行</th>
                <th scope="col">创建时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="entry in pending" :key="entry.reservationId">
                <td>{{ scopeLabel(entry.accountScope) }}</td>
                <td>{{ entry.kind }}</td>
                <td class="acos-tabular">{{ formatTokens(entry.amountTokens) }}</td>
                <td class="acos-tabular">
                  {{ entry.settledTokens === null ? '待核对' : formatTokens(entry.settledTokens) }}
                </td>
                <td>
                  <template v-if="entry.taskId">任务 #{{ entry.taskId }}</template>
                  <template v-if="entry.executionId"> / 执行 #{{ entry.executionId }}</template>
                  <template v-if="!entry.taskId && !entry.executionId">—</template>
                </td>
                <td class="acos-caption">{{ formatTime(entry.createdAt) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="budgets__panel" aria-labelledby="ledger-heading">
        <div class="budgets__panel-head">
          <h2 id="ledger-heading">账本明细</h2>
          <div class="budgets__filters" role="group" aria-label="账本类型筛选">
            <AppButton
              v-for="item in filters"
              :key="item.value"
              size="small"
              :variant="filter === item.value ? 'primary' : 'secondary'"
              :aria-pressed="filter === item.value"
              @click="filter = item.value"
            >
              {{ item.label }}
            </AppButton>
          </div>
        </div>
        <EmptyState
          v-if="filteredLedger.length === 0"
          title="没有符合筛选的账本记录"
          description="更改筛选条件，或等待任务产生调用。"
        />
        <div v-else class="budgets__table-wrap">
          <table class="budgets__table">
            <thead>
              <tr>
                <th scope="col">时间</th>
                <th scope="col">类型</th>
                <th scope="col">来源</th>
                <th scope="col">范围</th>
                <th scope="col">预留变动</th>
                <th scope="col">已用变动</th>
                <th scope="col">用量</th>
                <th scope="col">关联</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="entry in filteredLedger" :key="entry.id">
                <td class="acos-caption">{{ formatTime(entry.createdAt) }}</td>
                <td>{{ ledgerKind(entry.entryKind) }}</td>
                <td>{{ ledgerSource(entry.source) }}</td>
                <td>{{ scopeLabel(entry.scope) }}</td>
                <td class="acos-tabular">{{ formatTokens(entry.deltaReserved) }}</td>
                <td class="acos-tabular">{{ formatTokens(entry.deltaConsumed) }}</td>
                <td class="acos-tabular">
                  {{ formatLedgerUsage(entry) }}
                </td>
                <td class="acos-caption">
                  <template v-if="entry.taskId">任务 #{{ entry.taskId }}</template>
                  <template v-if="entry.executionId"> · 执行 #{{ entry.executionId }}</template>
                  <template v-if="!entry.taskId && !entry.executionId">—</template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.budgets {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-24);
}

.budgets__skeleton {
  display: grid;
  gap: var(--acos-space-16);
}

.budgets__skeleton-card {
  height: calc(var(--acos-space-64) + var(--acos-space-32));
  border-radius: var(--acos-radius-card);
  background: var(--acos-color-surface-subtle);
}

.budgets__panel {
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
  padding: var(--acos-layout-card-padding);
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-16);
  min-width: 0;
}

.budgets__panel h2 {
  font-size: var(--acos-font-subtitle-size);
  line-height: var(--acos-font-subtitle-line);
  font-weight: var(--acos-font-subtitle-weight);
  margin: 0;
}

.budgets__panel-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--acos-space-12);
  flex-wrap: wrap;
}

.budgets__accounts {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--acos-space-20);
}

.budgets__filters {
  display: flex;
  gap: var(--acos-space-8);
  flex-wrap: wrap;
}

.budgets__table-wrap {
  overflow-x: auto;
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-control);
}

.budgets__table {
  width: 100%;
  border-collapse: collapse;
  min-width: 720px;
  font-size: var(--acos-font-body-size);
  line-height: var(--acos-font-body-line);
}

.budgets__table th,
.budgets__table td {
  text-align: left;
  padding: var(--acos-layout-cell-padding-y) var(--acos-layout-cell-padding-x);
  border-top: var(--acos-control-border-width) solid var(--acos-color-border);
}

.budgets__table thead th {
  border-top: 0;
  background: var(--acos-color-surface-subtle);
  color: var(--acos-color-text-secondary);
  font-weight: 600;
}
</style>
