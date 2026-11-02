<script setup lang="ts">
/**
 * OrganizationView -- the Phase 02 organization editor.
 *
 * Layout follows PAGES.md §3: hierarchy/list switch, five positions visible at a
 * glance, and a 560px drawer for the selected employee with profile / duty /
 * model / history. The accessible hierarchy list is the primary structure;
 * dragging is optional and not implemented, so the list is the only path and it
 * must be keyboard operable.
 *
 * What this page deliberately does not do:
 * - It never implies a management relation grants permissions. Copy says that
 *   changing a manager changes reporting only.
 * - It never deletes an employee. Pause and archive are status changes that keep
 *   identity, the position, the duty history and the model-binding history.
 * - It does not hide server rejections. A cycle error appears on the manager
 *   field, exactly where the user made the choice.
 */
import { computed, onMounted, ref } from 'vue'

import AppButton from '@/components/AppButton.vue'
import AppDrawer from '@/components/AppDrawer.vue'
import AppInput from '@/components/AppInput.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import FormField from '@/components/FormField.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { ApiError, NetworkError } from '@/api/client'
import { modelCentreApi, organizationApi } from '@/api/endpoints'
import type {
  AgentHistoryEntry,
  AgentStatus,
  AgentSummary,
  ModelCentreResponse,
  OrganizationResponse,
  PositionSummary,
} from '@/api/organization-types'

const loading = ref(true)
const loadError = ref<{ message: string; requestId: string | null } | null>(null)
const organization = ref<OrganizationResponse | null>(null)
const centre = ref<ModelCentreResponse | null>(null)

const view = ref<'tree' | 'list'>('tree')
const selectedAgentId = ref<number | null>(null)
const drawerOpen = ref(false)

// ---------------------------------------------------------------- edit state
const draftName = ref('')
const draftDuty = ref('')
const draftManagerId = ref<number | null>(null)
const managerChanged = ref(false)
const draftModelId = ref<number | null>(null)
const saving = ref(false)
const saveError = ref<string | null>(null)
const fieldErrors = ref<Record<string, string | null>>({})

// ------------------------------------------------------------------ position
const positionDrawerOpen = ref(false)
const editingPosition = ref<PositionSummary | null>(null)
const draftPositionTitle = ref('')
const draftPositionDuty = ref('')
const positionSaving = ref(false)
const positionError = ref<string | null>(null)
const positionFieldErrors = ref<Record<string, string | null>>({})

// -------------------------------------------------------------------- history
// Set when step 1 of a two-step save succeeded: the profile is persisted but the
// model binding failed. The UI must say so instead of reporting a plain failure,
// because retrying step 1 again is not what the user needs (review finding R8).
const partialSaveNotice = ref<string | null>(null)
const history = ref<AgentHistoryEntry[]>([])
const historyLoading = ref(false)
const historyError = ref<string | null>(null)

// ----------------------------------------------------------- archive confirm
const archiveConfirmOpen = ref(false)

const archiveDescription = computed(() => {
  const agent = selectedAgent.value
  return agent
    ? `归档后 ${agent.displayName} 将不再接受新工作，但身份、岗位与履历全部保留。`
    : ''
})

const agents = computed(() => organization.value?.agents ?? [])
const positions = computed(() => organization.value?.positions ?? [])
const selectedAgent = computed<AgentSummary | null>(
  () => agents.value.find((agent) => agent.id === selectedAgentId.value) ?? null,
)

interface TreeNode {
  agent: AgentSummary
  depth: number
}

/** Employees arranged by manager for the hierarchy list, roots first. */
const treeNodes = computed<TreeNode[]>(() => {
  const byManager = new Map<number | null, AgentSummary[]>()
  for (const agent of agents.value) {
    const bucket = byManager.get(agent.managerId) ?? []
    bucket.push(agent)
    byManager.set(agent.managerId, bucket)
  }
  const nodes: TreeNode[] = []
  const walk = (agent: AgentSummary, depth: number) => {
    nodes.push({ agent, depth })
    for (const child of byManager.get(agent.id) ?? []) walk(child, depth + 1)
  }
  ;(byManager.get(null) ?? []).forEach((root) => walk(root, 0))
  // An employee whose chain is broken still appears: nothing is ever hidden.
  for (const agent of agents.value) {
    if (!nodes.some((node) => node.agent.id === agent.id)) walk(agent, 0)
  }
  return nodes
})

function statusLabel(status: AgentStatus): string {
  return status === 'ACTIVE' ? '在职' : status === 'PAUSED' ? '已停用' : '已归档'
}

function statusTone(status: AgentStatus): 'success' | 'warning' | 'neutral' {
  return status === 'ACTIVE' ? 'success' : status === 'PAUSED' ? 'warning' : 'neutral'
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleString('zh-CN', { hour12: false })
}

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const [org, centreBody] = await Promise.all([
      organizationApi.get(),
      modelCentreApi.overview(),
    ])
    organization.value = org
    centre.value = centreBody
    return true
  } catch (error) {
    if (error instanceof ApiError) {
      loadError.value = { message: error.message, requestId: error.requestId }
    } else if (error instanceof NetworkError) {
      loadError.value = { message: error.message, requestId: null }
    } else {
      loadError.value = { message: '无法读取组织信息，请稍后重试。', requestId: null }
    }
    return false
  } finally {
    loading.value = false
  }
}

onMounted(load)

function resetDraft(agent: AgentSummary) {
  draftName.value = agent.displayName
  draftDuty.value = agent.dutyOverride ?? ''
  draftManagerId.value = agent.managerId
  managerChanged.value = false
  draftModelId.value = agent.model?.modelId ?? null
  saveError.value = null
  partialSaveNotice.value = null
  fieldErrors.value = {}
}

async function openAgent(agentId: number) {
  selectedAgentId.value = agentId
  const agent = agents.value.find((item) => item.id === agentId)
  if (!agent) return
  resetDraft(agent)
  drawerOpen.value = true
  await loadHistory(agentId)
}

async function loadHistory(agentId: number) {
  historyLoading.value = true
  historyError.value = null
  try {
    const body = await organizationApi.history(agentId)
    history.value = body.entries
  } catch (error) {
    history.value = []
    historyError.value =
      error instanceof ApiError || error instanceof NetworkError
        ? error.message
        : '无法读取履历。'
  } finally {
    historyLoading.value = false
  }
}

function closeDrawer() {
  drawerOpen.value = false
  selectedAgentId.value = null
  history.value = []
}

/**
 * Manager options exclude the employee itself and everyone reporting to it.
 * The server enforces the same rule; filtering here only avoids the obvious
 * mistake, and the server stays the authority.
 */
const managerOptions = computed(() => {
  const current = selectedAgent.value
  if (!current) return []
  const blocked = new Set<number>([current.id])
  let changed = true
  while (changed) {
    changed = false
    for (const agent of agents.value) {
      if (agent.managerId !== null && blocked.has(agent.managerId) && !blocked.has(agent.id)) {
        blocked.add(agent.id)
        changed = true
      }
    }
  }
  return agents.value.filter(
    (agent) => !blocked.has(agent.id) && agent.status !== 'ARCHIVED',
  )
})

const selectedPositionVersion = computed(() => {
  const agent = selectedAgent.value
  if (!agent) return null
  return positions.value.find((position) => position.id === agent.positionId)?.version ?? null
})

const assignableModels = computed(() =>
  (centre.value?.models ?? []).filter(
    (model) => model.status === 'ENABLED' && model.providerStatus !== 'DISABLED',
  ),
)

function onManagerChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  draftManagerId.value = value === '' ? null : Number(value)
  managerChanged.value = true
}

/**
 * Save the employee profile, then the model binding.
 *
 * The two writes are separate server commands (a profile edit and a binding
 * change have different audit meaning), so the first can succeed while the
 * second fails. Apply the committed profile response immediately; a later
 * refresh can itself fail. The user is told which half was saved, and the
 * unsaved half stays in the form with the correct version for retry.
 */
async function saveProfile() {
  const agent = selectedAgent.value
  if (!agent || saving.value) return
  saving.value = true
  saveError.value = null
  partialSaveNotice.value = null
  fieldErrors.value = {}

  const trimmedDuty = draftDuty.value.trim()
  const requestedModelId = draftModelId.value
  let profileSavedVersion: number | null = null

  try {
    const updated = await organizationApi.updateAgent(agent.id, {
      displayName: draftName.value.trim(),
      dutyOverride: trimmedDuty === '' ? null : trimmedDuty,
      clearDutyOverride: trimmedDuty === '',
      managerId: draftManagerId.value,
      changeManager: managerChanged.value,
      expectedVersion: agent.version,
    })
    profileSavedVersion = updated.version
    // The PATCH response is committed server state. A later GET can fail;
    // retain this entity/version independently of that refresh.
    if (organization.value) {
      organization.value.agents = organization.value.agents.map((item) =>
        item.id === updated.id ? updated : item,
      )
    }
  } catch (error) {
    // Nothing was saved: the draft is still accurate, so keep every field.
    if (error instanceof ApiError) {
      saveError.value = error.message
      for (const item of error.fieldErrors) {
        fieldErrors.value[item.field] = item.message
      }
    } else if (error instanceof NetworkError) {
      saveError.value = error.message
    } else {
      saveError.value = '保存时发生未知错误，请稍后重试。'
    }
    saving.value = false
    return
  }

  // Step 1 is committed. Refresh local state before touching the binding so the
  // version in the form matches the server even if step 2 fails below.
  const currentModelId = agent.model?.modelId ?? null
  const modelChanged = requestedModelId !== currentModelId
  let bindingError: string | null = null
  try {
    if (modelChanged) {
      if (requestedModelId === null) {
        // An explicit "unbound" selection is a real command; it ends the binding
        // and keeps the history (review finding R11).
        await organizationApi.unbindModel(agent.id, { note: '在组织页解除绑定' })
      } else {
        await organizationApi.assignModel(agent.id, requestedModelId)
      }
    }
  } catch (error) {
    if (error instanceof ApiError || error instanceof NetworkError) {
      bindingError = error.message
    } else {
      bindingError = '模型绑定失败，请稍后重试。'
    }
  }

  // Refresh enriches the saved state; failure must never reset the draft from
  // an older entity or lose the version already returned by the PATCH.
  const refreshedSuccessfully = await load()
  const refreshed = agents.value.find((item) => item.id === agent.id)
  if (refreshedSuccessfully && refreshed && selectedAgentId.value === agent.id) {
    resetDraft(refreshed)
    // Keep the unsaved model choice so the user can retry just that part.
    if (bindingError) draftModelId.value = requestedModelId
  }
  await loadHistory(agent.id)

  if (bindingError) {
    saveError.value = bindingError
    partialSaveNotice.value =
      `资料已保存（版本 ${profileSavedVersion ?? '—'}），模型绑定未保存：${bindingError}` +
      ' 绑定选择已保留，修正后再次保存即可，不会覆盖已保存的资料。'
  }
  saving.value = false
}

async function changeStatus(next: AgentStatus) {
  const agent = selectedAgent.value
  if (!agent || saving.value) return
  saving.value = true
  saveError.value = null
  try {
    await organizationApi.updateAgent(agent.id, {
      status: next,
      expectedVersion: agent.version,
    })
    await load()
    const refreshed = agents.value.find((item) => item.id === agent.id)
    if (refreshed) resetDraft(refreshed)
  } catch (error) {
    if (error instanceof ApiError || error instanceof NetworkError) {
      saveError.value = error.message
    } else {
      saveError.value = '状态变更失败，请稍后重试。'
    }
  } finally {
    saving.value = false
    archiveConfirmOpen.value = false
  }
}

function openPosition(position: PositionSummary) {
  editingPosition.value = position
  draftPositionTitle.value = position.title
  draftPositionDuty.value = position.duty
  positionError.value = null
  positionFieldErrors.value = {}
  positionDrawerOpen.value = true
}

async function savePosition() {
  const position = editingPosition.value
  if (!position || positionSaving.value) return
  positionSaving.value = true
  positionError.value = null
  positionFieldErrors.value = {}
  try {
    await organizationApi.updatePosition(position.id, {
      title: draftPositionTitle.value.trim(),
      duty: draftPositionDuty.value.trim(),
      expectedVersion: position.rowVersion,
    })
    await load()
    positionDrawerOpen.value = false
  } catch (error) {
    if (error instanceof ApiError) {
      positionError.value = error.message
      for (const item of error.fieldErrors) {
        positionFieldErrors.value[item.field] = item.message
      }
    } else if (error instanceof NetworkError) {
      positionError.value = error.message
    } else {
      positionError.value = '保存岗位时发生未知错误，请稍后重试。'
    }
  } finally {
    positionSaving.value = false
  }
}
</script>

<template>
  <div class="org">
    <PageHeader
      title="组织"
      description="五个岗位与五名独立员工。可编辑员工资料、岗位职责、汇报关系与模型绑定；汇报关系只表示管理线，不授予任何资源权限。"
    >
      <template #actions>
        <AppButton variant="secondary" :disabled="view === 'tree'" @click="view = 'tree'">
          层级视图
        </AppButton>
        <AppButton variant="secondary" :disabled="view === 'list'" @click="view = 'list'">
          列表视图
        </AppButton>
      </template>
    </PageHeader>

    <div v-if="loading" class="org__skeleton" aria-live="polite" aria-busy="true">
      <span class="acos-visually-hidden">正在加载组织</span>
      <div v-for="index in 5" :key="index" class="org__skeleton-row" />
    </div>

    <ErrorState
      v-else-if="loadError"
      title="组织读取失败"
      :description="loadError.message"
      :request-id="loadError.requestId"
      :retrying="loading"
      @retry="load"
    />

    <EmptyState
      v-else-if="!organization"
      title="公司尚未创建"
      description="组织页需要先完成公司初始化。向导会创建五个岗位和五名员工。"
    />

    <template v-else>
      <section class="org__positions" aria-labelledby="positions-heading">
        <div class="org__section-head">
          <h2 id="positions-heading">岗位（{{ positions.length }}）</h2>
          <span class="acos-caption">
            {{ organization.departmentName }} · {{ organization.departmentPurpose }}
          </span>
        </div>
        <ul class="org__position-list">
          <li v-for="position in positions" :key="position.id" class="org__position-card">
            <div class="org__position-head">
              <strong>{{ position.title }}</strong>
              <StatusBadge :label="`职责 v${position.version}`" tone="neutral" />
            </div>
            <p class="org__position-duty">{{ position.duty }}</p>
            <AppButton size="small" variant="ghost" @click="openPosition(position)">
              编辑岗位职责
            </AppButton>
          </li>
        </ul>
      </section>

      <section class="org__agents" aria-labelledby="agents-heading">
        <div class="org__section-head">
          <h2 id="agents-heading">员工（{{ agents.length }}）</h2>
          <span class="acos-caption">
            主管调整只改变汇报线；暂停或归档不会删除身份与履历。
          </span>
        </div>

        <ul
          v-if="view === 'tree'"
          class="org__tree"
          data-testid="org-tree"
          aria-label="汇报层级"
        >
          <li
            v-for="node in treeNodes"
            :key="node.agent.id"
            class="org__tree-node"
            :style="{ '--depth': node.depth }"
          >
            <button
              type="button"
              class="org__tree-button"
              :aria-label="`查看 ${node.agent.displayName}（${node.agent.positionTitle}）`"
              @click="openAgent(node.agent.id)"
            >
              <span class="org__tree-branch" aria-hidden="true">
                {{ node.depth > 0 ? '└' : '•' }}
              </span>
              <span class="org__tree-name">{{ node.agent.displayName }}</span>
              <span class="org__tree-role acos-caption">{{ node.agent.positionTitle }}</span>
              <StatusBadge
                :label="statusLabel(node.agent.status)"
                :tone="statusTone(node.agent.status)"
              />
              <span class="org__tree-model acos-caption">
                {{ node.agent.model ? node.agent.model.label : '未绑定模型' }}
              </span>
            </button>
          </li>
        </ul>

        <div v-else class="org__table-wrap">
          <el-table :data="agents" row-key="id" style="width: 100%" data-testid="org-table">
            <el-table-column label="员工" min-width="160">
              <template #default="{ row }">
                <div class="org__cell-strong">{{ row.displayName }}</div>
                <div class="acos-caption">{{ row.employeeCode }}</div>
              </template>
            </el-table-column>
            <el-table-column label="岗位" min-width="140" prop="positionTitle" />
            <el-table-column label="主管" min-width="120">
              <template #default="{ row }">{{ row.managerName ?? '—' }}</template>
            </el-table-column>
            <el-table-column label="状态" min-width="100">
              <template #default="{ row }">
                <StatusBadge :label="statusLabel(row.status)" :tone="statusTone(row.status)" />
              </template>
            </el-table-column>
            <el-table-column label="模型" min-width="160">
              <template #default="{ row }">
                {{ row.model ? row.model.label : '未绑定模型' }}
              </template>
            </el-table-column>
            <el-table-column label="操作" width="120">
              <template #default="{ row }">
                <AppButton size="small" variant="ghost" @click="openAgent(row.id)">
                  查看
                </AppButton>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </section>
    </template>

    <AppDrawer
      :model-value="drawerOpen"
      :title="
        selectedAgent
          ? `${selectedAgent.displayName} · ${selectedAgent.positionTitle}`
          : '员工资料'
      "
      @update:model-value="(open: boolean) => (open ? (drawerOpen = true) : closeDrawer())"
    >
      <div v-if="selectedAgent" class="org__drawer">
        <div v-if="saveError" class="org__alert" role="alert" data-testid="agent-save-error">
          {{ saveError }}
        </div>
        <div
          v-if="partialSaveNotice"
          class="org__alert org__alert--warning"
          role="status"
          data-testid="agent-partial-save"
        >
          {{ partialSaveNotice }}
        </div>

        <section class="org__drawer-section" aria-labelledby="agent-profile-heading">
          <h3 id="agent-profile-heading">资料</h3>
          <FormField
            label="显示名称"
            field-name="agent-display-name"
            required
            :error="fieldErrors.displayName ?? null"
          >
            <template #default="{ controlId, describedBy, invalid }">
              <AppInput
                :id="controlId"
                v-model="draftName"
                :described-by="describedBy"
                :invalid="invalid"
                :disabled="saving || selectedAgent.status === 'ARCHIVED'"
              />
            </template>
          </FormField>
          <dl class="org__meta">
            <div>
              <dt>员工编号</dt>
              <dd>{{ selectedAgent.employeeCode }}</dd>
            </div>
            <div>
              <dt>岗位职责版本</dt>
              <dd>
                {{ selectedPositionVersion ?? '—' }}
              </dd>
            </div>
            <div>
              <dt>状态变更时间</dt>
              <dd>{{ formatTime(selectedAgent.statusChangedAt) }}</dd>
            </div>
          </dl>
        </section>

        <section class="org__drawer-section" aria-labelledby="agent-duty-heading">
          <h3 id="agent-duty-heading">职责</h3>
          <p class="acos-caption">
            留空表示沿用岗位当前版本；填写后作为该员工的职责覆盖，并写入履历。
          </p>
          <FormField
            label="职责覆盖（可选）"
            field-name="agent-duty"
            :error="fieldErrors.dutyOverride ?? null"
          >
            <template #default="{ controlId, describedBy, invalid }">
              <textarea
                :id="controlId"
                v-model="draftDuty"
                class="org__textarea"
                rows="4"
                :aria-invalid="invalid ? 'true' : undefined"
                :aria-describedby="describedBy"
                :disabled="saving || selectedAgent.status === 'ARCHIVED'"
              />
            </template>
          </FormField>
          <p class="acos-caption">当前生效职责：{{ selectedAgent.effectiveDuty }}</p>
        </section>

        <section class="org__drawer-section" aria-labelledby="agent-manager-heading">
          <h3 id="agent-manager-heading">主管</h3>
          <FormField
            label="汇报给"
            field-name="agent-manager"
            hint="只影响汇报线与管理升级路径，不授予资源权限。循环汇报关系会被服务端拒绝。"
            :error="fieldErrors.managerId ?? null"
          >
            <template #default="{ controlId, describedBy, invalid }">
              <select
                :id="controlId"
                class="org__select"
                :value="draftManagerId ?? ''"
                :aria-invalid="invalid ? 'true' : undefined"
                :aria-describedby="describedBy"
                :disabled="saving || selectedAgent.status === 'ARCHIVED'"
                @change="onManagerChange"
              >
                <option value="">不向任何人汇报</option>
                <option v-for="option in managerOptions" :key="option.id" :value="option.id">
                  {{ option.displayName }}（{{ option.positionTitle }}）
                </option>
              </select>
            </template>
          </FormField>
        </section>

        <section class="org__drawer-section" aria-labelledby="agent-model-heading">
          <h3 id="agent-model-heading">模型</h3>
          <FormField
            label="绑定模型"
            field-name="agent-model"
            :hint="
              assignableModels.length === 0
                ? '模型中心还没有启用的模型，请先添加供应商与模型。'
                : '更换模型后旧绑定会结束并保留在履历中。'
            "
          >
            <template #default="{ controlId, describedBy }">
              <select
                :id="controlId"
                v-model="draftModelId"
                class="org__select"
                :aria-describedby="describedBy"
                :disabled="
                  saving || assignableModels.length === 0 || selectedAgent.status === 'ARCHIVED'
                "
              >
                <option :value="null">未绑定</option>
                <option v-for="model in assignableModels" :key="model.id" :value="model.id">
                  {{ model.label }} · {{ model.providerName }}
                </option>
              </select>
            </template>
          </FormField>
          <p v-if="selectedAgent.model" class="acos-caption">
            JSON 输出能力：
            {{
              selectedAgent.model.supportsJsonOutput === true
                ? '探测已验证'
                : selectedAgent.model.supportsJsonOutput === false
                  ? '探测未通过'
                  : '未验证'
            }}
          </p>
        </section>

        <section class="org__drawer-section" aria-labelledby="agent-state-heading">
          <h3 id="agent-state-heading">状态</h3>
          <div class="org__status-row">
            <StatusBadge
              :label="statusLabel(selectedAgent.status)"
              :tone="statusTone(selectedAgent.status)"
            />
            <AppButton
              v-if="selectedAgent.status === 'ACTIVE'"
              variant="secondary"
              size="small"
              :disabled="saving"
              @click="changeStatus('PAUSED')"
            >
              暂停员工
            </AppButton>
            <AppButton
              v-else-if="selectedAgent.status === 'PAUSED'"
              variant="secondary"
              size="small"
              :disabled="saving"
              @click="changeStatus('ACTIVE')"
            >
              恢复在职
            </AppButton>
            <AppButton
              v-if="selectedAgent.status !== 'ARCHIVED'"
              variant="danger-text"
              size="small"
              :disabled="saving"
              @click="archiveConfirmOpen = true"
            >
              归档（离职）
            </AppButton>
          </div>
          <p class="acos-caption">
            归档保留身份、岗位、职责履历与模型绑定历史；不会删除任何记录。
          </p>
        </section>

        <section class="org__drawer-section" aria-labelledby="agent-history-heading">
          <h3 id="agent-history-heading">履历</h3>
          <p v-if="historyLoading" class="acos-caption">正在读取履历…</p>
          <p v-else-if="historyError" class="org__alert" role="alert">{{ historyError }}</p>
          <ul v-else-if="history.length" class="org__history">
            <li v-for="(entry, index) in history" :key="`${entry.kind}-${index}`">
              <span class="org__history-time acos-caption">{{ formatTime(entry.occurredAt) }}</span>
              <span>{{ entry.summary }}</span>
              <span v-if="entry.detail" class="acos-caption org__history-detail">
                {{ entry.detail }}
              </span>
            </li>
          </ul>
          <p v-else class="acos-caption">尚无变更履历。</p>
        </section>
      </div>

      <template #footer>
        <AppButton variant="secondary" :disabled="saving" @click="closeDrawer">取消</AppButton>
        <AppButton
          :loading="saving"
          :disabled="!selectedAgent || selectedAgent.status === 'ARCHIVED'"
          loading-text="保存中…"
          @click="saveProfile"
        >
          保存员工资料
        </AppButton>
      </template>
    </AppDrawer>

    <AppDrawer
      :model-value="positionDrawerOpen"
      :title="editingPosition ? `编辑岗位：${editingPosition.title}` : '编辑岗位'"
      @update:model-value="(open: boolean) => (positionDrawerOpen = open)"
    >
      <div v-if="editingPosition" class="org__drawer">
        <div v-if="positionError" class="org__alert" role="alert">
          {{ positionError }}
        </div>
        <FormField
          label="岗位名称"
          field-name="position-title"
          required
          :error="positionFieldErrors.title ?? null"
        >
          <template #default="{ controlId, describedBy, invalid }">
            <AppInput
              :id="controlId"
              v-model="draftPositionTitle"
              :described-by="describedBy"
              :invalid="invalid"
              :disabled="positionSaving"
            />
          </template>
        </FormField>
        <FormField
          label="岗位职责"
          field-name="position-duty"
          required
          hint="职责变更会生成新版本，旧版本保留用于历史执行对照。"
          :error="positionFieldErrors.duty ?? null"
        >
          <template #default="{ controlId, describedBy, invalid }">
            <textarea
              :id="controlId"
              v-model="draftPositionDuty"
              class="org__textarea"
              rows="7"
              :aria-invalid="invalid ? 'true' : undefined"
              :aria-describedby="describedBy"
              :disabled="positionSaving"
            />
          </template>
        </FormField>
        <p class="acos-caption">当前职责版本：v{{ editingPosition.version }}</p>
      </div>

      <template #footer>
        <AppButton
          variant="secondary"
          :disabled="positionSaving"
          @click="positionDrawerOpen = false"
        >
          取消
        </AppButton>
        <AppButton :loading="positionSaving" loading-text="保存中…" @click="savePosition">
          保存岗位
        </AppButton>
      </template>
    </AppDrawer>

    <ConfirmDialog
      v-model="archiveConfirmOpen"
      title="归档员工"
      :description="archiveDescription"
      confirm-label="归档员工"
      cancel-label="取消"
      variant="danger"
      :loading="saving"
      @confirm="changeStatus('ARCHIVED')"
    />
  </div>
</template>

<style scoped>
.org {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-24);
}

.org__skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-8);
}

.org__skeleton-row {
  height: var(--acos-layout-table-row-min);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-surface-subtle);
}

.org__section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--acos-space-8);
  margin-bottom: var(--acos-space-12);
  flex-wrap: wrap;
}

.org__position-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: var(--acos-layout-card-gap);
  list-style: none;
  margin: 0;
  padding: 0;
}

.org__position-card {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-8);
  padding: var(--acos-layout-card-padding);
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
}

.org__position-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--acos-space-8);
  flex-wrap: wrap;
}

.org__position-duty {
  color: var(--acos-color-text-secondary);
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
}

.org__tree {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-4);
  list-style: none;
  margin: 0;
  padding: var(--acos-space-8);
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
}

.org__tree-node {
  padding-left: calc(var(--depth) * var(--acos-space-20));
}

.org__tree-button {
  display: flex;
  align-items: center;
  gap: var(--acos-space-8);
  width: 100%;
  min-height: var(--acos-layout-table-row-min);
  padding: var(--acos-space-8) var(--acos-space-12);
  border: 0;
  border-radius: var(--acos-radius-control);
  background: transparent;
  color: var(--acos-color-text);
  font: inherit;
  text-align: left;
  cursor: pointer;
  flex-wrap: wrap;
}

.org__tree-button:hover {
  background: var(--acos-color-surface-subtle);
}

.org__tree-branch {
  color: var(--acos-color-text-muted);
  font-family: var(--acos-font-mono);
}

.org__tree-name {
  font-weight: 500;
}

.org__tree-role {
  color: var(--acos-color-text-secondary);
}

.org__tree-model {
  margin-left: auto;
  color: var(--acos-color-text-secondary);
}

.org__table-wrap {
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
  overflow-x: auto;
}

.org__cell-strong {
  font-weight: 500;
}

.org__drawer {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-24);
}

.org__drawer-section {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-8);
}

.org__drawer-section h3 {
  font-size: var(--acos-font-subtitle-size);
  line-height: var(--acos-font-subtitle-line);
  font-weight: var(--acos-font-subtitle-weight);
}

.org__textarea {
  width: 100%;
  min-height: var(--acos-control-textarea-min-height);
  padding: var(--acos-space-12);
  border: var(--acos-control-border-width) solid var(--acos-color-control-border);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-surface);
  color: var(--acos-color-text);
  font-family: inherit;
  font-size: var(--acos-font-body-size);
  line-height: var(--acos-font-body-line);
  resize: vertical;
}

.org__textarea:focus-visible {
  border-color: var(--acos-color-focus);
  outline: var(--acos-focus-width) solid var(--acos-color-focus);
  outline-offset: var(--acos-focus-offset);
}

.org__select {
  width: 100%;
  height: var(--acos-control-medium-height);
  padding: 0 var(--acos-control-input-padding-x);
  border: var(--acos-control-border-width) solid var(--acos-color-control-border);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-surface);
  color: var(--acos-color-text);
  font-family: inherit;
  font-size: var(--acos-font-body-size);
}

.org__select:disabled,
.org__textarea:disabled {
  background: var(--acos-color-disabled-bg);
  color: var(--acos-color-disabled-text);
  cursor: not-allowed;
}

.org__meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--acos-space-12);
  margin: 0;
}

.org__meta dt {
  font-size: var(--acos-font-caption-size);
  color: var(--acos-color-text-secondary);
}

.org__meta dd {
  margin: 0;
}

.org__status-row {
  display: flex;
  align-items: center;
  gap: var(--acos-space-8);
  flex-wrap: wrap;
}

.org__history {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-8);
  list-style: none;
  margin: 0;
  padding: 0;
}

.org__history li {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-4);
  padding-bottom: var(--acos-space-8);
  border-bottom: var(--acos-control-border-width) solid var(--acos-color-border);
}

.org__history li:last-child {
  border-bottom: 0;
}

.org__history-time,
.org__history-detail {
  color: var(--acos-color-text-secondary);
  word-break: break-word;
}

.org__alert {
  padding: var(--acos-space-12);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-danger-subtle);
  color: var(--acos-color-danger);
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
}

/* A partial success is not an error: it is a warning that one of two steps was
   saved. Same box, different tone, always with text. */
.org__alert--warning {
  background: var(--acos-color-warning-subtle);
  color: var(--acos-color-warning);
}

@media (max-width: 767px) {
  .org__select,
  .org__textarea {
    font-size: 16px;
  }

  .org__tree-node {
    padding-left: calc(var(--depth) * var(--acos-space-12));
  }
}
</style>
