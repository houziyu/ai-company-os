<script setup lang="ts">
/**
 * AuditView -- read-only audit log.
 *
 * PAGES.md §8: subject, action, target, time, related execution and reason, with
 * structured fields shown after redaction. This page offers no edit or delete
 * affordance because the API has none; offering a disabled button would suggest
 * the capability exists.
 */
import { computed, onMounted, ref } from 'vue'

import AppButton from '@/components/AppButton.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { ApiError, NetworkError } from '@/api/client'
import { auditApi } from '@/api/endpoints'
import type { AuditListResponse } from '@/api/organization-types'

const PAGE_SIZE = 50

const loading = ref(true)
const loadError = ref<{ message: string; requestId: string | null } | null>(null)
const data = ref<AuditListResponse | null>(null)
const actionFilter = ref('')
const offset = ref(0)

const events = computed(() => data.value?.events ?? [])

const ACTION_LABELS: Record<string, string> = {
  OWNER_SETUP_COMPLETED: '所有者初始化',
  LOGIN_SUCCEEDED: '登录成功',
  LOGIN_FAILED: '登录失败',
  LOGIN_BLOCKED: '登录被锁定',
  LOGOUT: '注销',
  SESSION_EXPIRED: '会话过期',
  COMPANY_INITIALIZED: '公司初始化',
  COMPANY_UPDATED: '公司资料更新',
  POSITION_UPDATED: '岗位更新',
  AGENT_UPDATED: '员工资料更新',
  AGENT_STATUS_CHANGED: '员工状态变更',
  AGENT_MODEL_ASSIGNED: '员工模型绑定',
  PROVIDER_CREATED: '供应商创建',
  PROVIDER_UPDATED: '供应商更新',
  PROVIDER_MODEL_CREATED: '模型创建',
  PROVIDER_MODEL_UPDATED: '模型更新',
  MODEL_PROBE_COMPLETED: '模型探测成功',
  MODEL_PROBE_FAILED: '模型探测失败',
}

function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action
}

function outcomeTone(outcome: string): 'success' | 'danger' | 'warning' | 'neutral' {
  const value = outcome.toUpperCase()
  if (value === 'SUCCESS') return 'success'
  if (value === 'FAILURE') return 'danger'
  if (value === 'BLOCKED' || value === 'EXPIRED') return 'warning'
  return 'neutral'
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleString('zh-CN', { hour12: false })
}

async function load(reset = false) {
  loading.value = true
  loadError.value = null
  if (reset) offset.value = 0
  try {
    data.value = await auditApi.events({
      limit: PAGE_SIZE,
      offset: offset.value,
      action: actionFilter.value || undefined,
    })
  } catch (error) {
    if (error instanceof ApiError) {
      loadError.value = { message: error.message, requestId: error.requestId }
    } else if (error instanceof NetworkError) {
      loadError.value = { message: error.message, requestId: null }
    } else {
      loadError.value = { message: '无法读取审计记录，请稍后重试。', requestId: null }
    }
  } finally {
    loading.value = false
  }
}

onMounted(() => load())

const canPrev = computed(() => offset.value > 0)
const canNext = computed(() =>
  data.value ? offset.value + PAGE_SIZE < data.value.total : false,
)

function prevPage() {
  offset.value = Math.max(0, offset.value - PAGE_SIZE)
  void load()
}

function nextPage() {
  offset.value += PAGE_SIZE
  void load()
}

function applyFilter() {
  void load(true)
}

function clearFilter() {
  actionFilter.value = ''
  void load(true)
}
</script>

<template>
  <div class="audit">
    <PageHeader
      title="审计"
      description="组织与模型配置的变更记录。审计记录只追加，不能通过界面编辑或删除；敏感内容在服务端脱敏后再返回。"
    />

    <form class="audit__filter" @submit.prevent="applyFilter">
      <label class="audit__filter-label" for="audit-action-filter">按动作筛选</label>
      <select id="audit-action-filter" v-model="actionFilter" class="audit__select">
        <option value="">全部动作</option>
        <option v-for="(label, key) in ACTION_LABELS" :key="key" :value="key">
          {{ label }}
        </option>
      </select>
      <AppButton type="submit" variant="secondary" :disabled="loading">筛选</AppButton>
      <AppButton
        type="button"
        variant="ghost"
        :disabled="loading || actionFilter === ''"
        @click="clearFilter"
      >
        清除筛选
      </AppButton>
    </form>

    <div v-if="loading" class="audit__skeleton" aria-live="polite" aria-busy="true">
      <span class="acos-visually-hidden">正在加载审计记录</span>
      <div v-for="index in 6" :key="index" class="audit__skeleton-row" />
    </div>

    <ErrorState
      v-else-if="loadError"
      title="审计记录读取失败"
      :description="loadError.message"
      :request-id="loadError.requestId"
      :retrying="loading"
      @retry="() => load()"
    />

    <EmptyState
      v-else-if="events.length === 0"
      :title="actionFilter ? '该动作没有记录' : '还没有审计记录'"
      description="创建公司、编辑组织或调整模型后，这里会出现对应事件。"
    >
      <template v-if="actionFilter" #action>
        <AppButton variant="secondary" @click="clearFilter">清除筛选</AppButton>
      </template>
    </EmptyState>

    <template v-else>
      <div class="audit__table-wrap">
        <el-table :data="events" row-key="id" style="width: 100%">
          <el-table-column label="时间" min-width="170">
            <template #default="{ row }">{{ formatTime(row.occurredAt) }}</template>
          </el-table-column>
          <el-table-column label="主体" min-width="140">
            <template #default="{ row }">
              {{ row.actorDisplayName ?? row.actorType }}
            </template>
          </el-table-column>
          <el-table-column label="动作" min-width="160">
            <template #default="{ row }">{{ actionLabel(row.action) }}</template>
          </el-table-column>
          <el-table-column label="结果" min-width="100">
            <template #default="{ row }">
              <StatusBadge :label="row.outcome" :tone="outcomeTone(row.outcome)" />
            </template>
          </el-table-column>
          <el-table-column label="对象" min-width="150">
            <template #default="{ row }">
              <span v-if="row.targetType">
                {{ row.targetType }} #{{ row.targetId }}
              </span>
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column label="详情" min-width="260">
            <template #default="{ row }">
              <span class="audit__detail">{{ row.detail ?? '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="请求编号" min-width="130">
            <template #default="{ row }">
              <span class="audit__mono">{{ row.requestId ?? '—' }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="audit__pager">
        <span class="acos-caption">
          共 {{ data?.total ?? 0 }} 条，当前显示 {{ offset + 1 }}–{{
            Math.min(offset + PAGE_SIZE, data?.total ?? 0)
          }}
        </span>
        <div class="audit__pager-actions">
          <AppButton
            variant="secondary"
            size="small"
            :disabled="!canPrev || loading"
            @click="prevPage"
          >
            上一页
          </AppButton>
          <AppButton
            variant="secondary"
            size="small"
            :disabled="!canNext || loading"
            @click="nextPage"
          >
            下一页
          </AppButton>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.audit {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-24);
}

.audit__filter {
  display: flex;
  align-items: center;
  gap: var(--acos-space-8);
  flex-wrap: wrap;
}

.audit__filter-label {
  font-size: var(--acos-font-label-size);
  font-weight: var(--acos-font-label-weight);
}

.audit__select {
  height: var(--acos-control-medium-height);
  padding: 0 var(--acos-control-input-padding-x);
  border: var(--acos-control-border-width) solid var(--acos-color-control-border);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-surface);
  color: var(--acos-color-text);
  font-family: inherit;
  font-size: var(--acos-font-body-size);
}

.audit__skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-8);
}

.audit__skeleton-row {
  height: var(--acos-layout-table-row-min);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-surface-subtle);
}

.audit__table-wrap {
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
  overflow-x: auto;
}

.audit__detail {
  word-break: break-word;
  color: var(--acos-color-text-secondary);
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
}

.audit__mono {
  font-family: var(--acos-font-mono);
  font-size: var(--acos-font-caption-size);
  word-break: break-all;
}

.audit__pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--acos-space-8);
  flex-wrap: wrap;
}

.audit__pager-actions {
  display: flex;
  gap: var(--acos-space-8);
}

.audit__select:focus-visible {
  border-color: var(--acos-color-focus);
  outline: var(--acos-focus-width) solid var(--acos-color-focus);
  outline-offset: var(--acos-focus-offset);
}

@media (max-width: 767px) {
  .audit__select {
    font-size: 16px;
    width: 100%;
  }

  .audit__pager,
  .audit__pager-actions {
    width: 100%;
  }
}
</style>
