<script setup lang="ts">
/**
 * SettingsView -- account, service status, version and backup notes.
 *
 * Only facts this phase can obtain are shown. No "backup now" button: the
 * backup procedure is documented as a manual, explicit operation because an
 * unimplemented button would be a fake affordance.
 */
import { onMounted, ref } from 'vue'

import AppButton from '@/components/AppButton.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { ApiError, NetworkError } from '@/api/client'
import { systemApi } from '@/api/endpoints'
import type { ServiceStatus } from '@/api/types'
import { useSessionStore } from '@/stores/session'
import { useLogout } from '@/composables/useLogout'

const session = useSessionStore()

const loading = ref(true)
const loadError = ref<{ message: string; requestId: string | null } | null>(
  null,
)
const status = ref<ServiceStatus | null>(null)
const confirmLogout = ref(false)
const { loggingOut, logoutError, logout } = useLogout()

async function load() {
  loading.value = true
  loadError.value = null
  try {
    status.value = await systemApi.status()
  } catch (error) {
    if (error instanceof ApiError) {
      loadError.value = { message: error.message, requestId: error.requestId }
    } else if (error instanceof NetworkError) {
      loadError.value = { message: error.message, requestId: null }
    } else {
      loadError.value = { message: '无法读取服务信息。', requestId: null }
    }
  } finally {
    loading.value = false
  }
}

onMounted(load)

function formatUtc(value: string | null): string {
  if (!value) return '未知'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '未知'
  return `${date.toLocaleString('zh-CN', { hour12: false })}（本地时区）`
}

const workerTone = (value: ServiceStatus['workerStatus']) =>
  value === 'ok'
    ? 'success'
    : value === 'stale'
      ? 'warning'
      : value === 'missing'
        ? 'neutral'
        : 'danger'

async function handleLogoutFromDialog() {
  if (await logout()) confirmLogout.value = false
}

const workerLabel = (value: ServiceStatus['workerStatus']) =>
  value === 'ok'
    ? '心跳正常'
    : value === 'stale'
      ? '心跳超时'
      : value === 'missing'
        ? '尚无心跳'
        : '未知'
</script>

<template>
  <div class="settings">
    <PageHeader
      title="设置"
      description="账号、服务状态、版本与备份说明。阶段 01 只读展示部署事实，不提供可点击但未实现的维护操作。"
    >
      <template #actions>
        <AppButton
          variant="secondary"
          :loading="loading"
          loading-text="刷新中…"
          @click="load"
        >
          刷新服务信息
        </AppButton>
      </template>
    </PageHeader>

    <ErrorState
      v-if="loadError"
      title="无法读取服务信息"
      :description="loadError.message"
      :request-id="loadError.requestId"
      @retry="load"
    />

    <div v-else class="settings__grid">
      <section class="settings__card" aria-labelledby="account-heading">
        <h2 id="account-heading" class="settings__card-title">账号</h2>
        <dl class="settings__list">
          <div class="settings__row">
            <dt>显示名称</dt>
            <dd>{{ session.user?.displayName ?? '—' }}</dd>
          </div>
          <div class="settings__row">
            <dt>登录名</dt>
            <dd>{{ session.user?.username ?? '—' }}</dd>
          </div>
          <div class="settings__row">
            <dt>角色</dt>
            <dd>所有者（单所有者部署）</dd>
          </div>
          <div class="settings__row">
            <dt>会话到期</dt>
            <dd>{{ formatUtc(session.expiresAt) }}</dd>
          </div>
        </dl>
        <div class="settings__actions">
          <AppButton variant="secondary" @click="confirmLogout = true">
            注销当前会话
          </AppButton>
        </div>
      </section>

      <section class="settings__card" aria-labelledby="service-heading">
        <div class="settings__card-head">
          <h2 id="service-heading" class="settings__card-title">服务状态</h2>
          <StatusBadge
            :label="
              status?.databaseStatus === 'ok' ? '数据库正常' : '数据库异常'
            "
            :tone="status?.databaseStatus === 'ok' ? 'success' : 'danger'"
          />
        </div>
        <dl v-if="status" class="settings__list">
          <div class="settings__row">
            <dt>应用版本</dt>
            <dd>{{ status.appVersion }}</dd>
          </div>
          <div class="settings__row">
            <dt>环境</dt>
            <dd>{{ status.environment }}</dd>
          </div>
          <div class="settings__row">
            <dt>数据库迁移版本</dt>
            <dd>
              <code>{{ status.databaseRevision ?? '未知' }}</code>
            </dd>
          </div>
          <div class="settings__row">
            <dt>代码期望版本</dt>
            <dd>
              <code>{{ status.expectedRevision ?? '未知' }}</code>
            </dd>
          </div>
          <div class="settings__row">
            <dt>Worker 状态</dt>
            <dd class="settings__inline">
              <StatusBadge
                :label="workerLabel(status.workerStatus)"
                :tone="workerTone(status.workerStatus)"
              />
              <span class="acos-caption">
                最近心跳：{{ formatUtc(status.workerLastHeartbeatAt) }}
              </span>
            </dd>
          </div>
          <div class="settings__row">
            <dt>会话有效期</dt>
            <dd>{{ status.sessionTtlHours }} 小时</dd>
          </div>
          <div class="settings__row">
            <dt>API 启动时间</dt>
            <dd>{{ formatUtc(status.startedAt) }}</dd>
          </div>
        </dl>
        <p v-else class="acos-caption">正在读取服务信息…</p>
      </section>

      <section
        class="settings__card settings__card--wide"
        aria-labelledby="backup-heading"
      >
        <h2 id="backup-heading" class="settings__card-title">备份与恢复</h2>
        <p class="settings__text">
          阶段 01 尚未提供在线备份按钮。数据保存在 Compose
          命名卷中，备份必须同时覆盖数据库，恢复流程属于明确的数据操作。请按
          README 中的命令执行，不要使用 <code>docker compose down -v</code>
          作为升级或修复步骤——该命令会删除数据卷。
        </p>
        <EmptyState
          title="在线备份尚未实现"
          description="在真实备份与恢复流程（含版本、UTC 时间、校验和）实现前，本页不显示可点击的备份动作。"
          phase-note="阶段 08 提供发布与备份恢复验收"
        />
      </section>
    </div>

    <ConfirmDialog
      v-model="confirmLogout"
      title="注销当前会话"
      description="注销后此浏览器需要重新登录，服务器端的会话记录会立即失效。"
      confirm-label="注销会话"
      variant="danger"
      :loading="loggingOut"
      @confirm="handleLogoutFromDialog"
    >
      <ErrorState
        v-if="logoutError"
        title="注销结果未确认"
        :description="logoutError"
        retry-label="重试注销"
        :retrying="loggingOut"
        @retry="handleLogoutFromDialog"
      />
    </ConfirmDialog>
  </div>
</template>

<style scoped>
.settings {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-24);
}

.settings__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--acos-layout-card-gap);
}

.settings__card {
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
  padding: var(--acos-layout-card-padding);
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-16);
}

.settings__card--wide {
  grid-column: 1 / -1;
}

.settings__card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--acos-space-12);
  flex-wrap: wrap;
}

.settings__card-title {
  font-size: var(--acos-font-section-size);
  line-height: var(--acos-font-section-line);
  font-weight: var(--acos-font-section-weight);
}

.settings__list {
  margin: 0;
  display: flex;
  flex-direction: column;
}

.settings__row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--acos-space-16);
  padding: var(--acos-space-12) 0;
  border-top: var(--acos-control-border-width) solid var(--acos-color-border);
  flex-wrap: wrap;
}

.settings__row:first-child {
  border-top: 0;
  padding-top: 0;
}

.settings__row dt {
  color: var(--acos-color-text-secondary);
}

.settings__row dd {
  margin: 0;
  text-align: right;
  word-break: break-word;
}

.settings__row code {
  font-family: var(--acos-font-mono);
  font-size: var(--acos-font-caption-size);
}

.settings__inline {
  display: flex;
  align-items: center;
  gap: var(--acos-space-8);
  flex-wrap: wrap;
  justify-content: flex-end;
}

.settings__actions {
  display: flex;
  gap: var(--acos-space-8);
}

.settings__text {
  color: var(--acos-color-text-secondary);
}

.settings__text code {
  font-family: var(--acos-font-mono);
  background: var(--acos-color-surface-subtle);
  padding: 0 var(--acos-space-4);
  border-radius: var(--acos-radius-control);
}

@media (max-width: 1023px) {
  .settings__grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
