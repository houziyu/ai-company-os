<script setup lang="ts">
/**
 * ProjectsView -- projects and the task board.
 *
 * Structure follows PAGES.md §5: the page states the project's objective and
 * supported scope, then a board grouped by business status (with a list toggle),
 * then the create form. There is no drag-to-move on purpose: a status change is
 * a server decision, reachable only through the cancel/retry commands.
 *
 * Demo vs. real is explicit. Only DEMO runs exist in Phase 03, every card shows
 * the rehearsal label, and choosing REAL is refused by the server (the page shows
 * that error instead of hiding the option).
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useTaskFeed } from '@/composables/useTaskFeed'
import LiveUpdateStatus from '@/components/LiveUpdateStatus.vue'
import { useRouter } from 'vue-router'

import AppButton from '@/components/AppButton.vue'
import AppDrawer from '@/components/AppDrawer.vue'
import AppInput from '@/components/AppInput.vue'
import BudgetMeter from '@/components/BudgetMeter.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import FormField from '@/components/FormField.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { ApiError, NetworkError } from '@/api/client'
import { projectsApi, tasksApi } from '@/api/endpoints'
import {
  clearSubmissionKey,
  idempotencyKeyFor,
  type TaskSubmissionKey,
} from '@/api/task-submission'
import {
  BOARD_COLUMNS,
  demoBehaviorLabel,
  formatTime,
  formatTokens,
  taskStatus,
} from '@/api/task-presentation'
import type {
  DemoBehavior,
  ProjectCreatePayload,
  ProjectSummary,
  TaskStatus,
  TaskSummary,
} from '@/api/task-types'

type LoadError = { message: string; requestId: string | null }

const router = useRouter()

const loading = ref(true)
const loadError = ref<LoadError | null>(null)
const projects = ref<ProjectSummary[]>([])
const tasks = ref<TaskSummary[]>([])
const selectedProjectId = ref<number | null>(null)
// COMPONENTS.md: phones default to the task list, with the board kept as an
// optional horizontal area. Wider viewports start in board mode. The helper is
// guarded because a non-browser renderer (and some test environments) has no
// matchMedia at all.
function prefersListView(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false
  }
  return window.matchMedia('(max-width: 767px)').matches
}

const viewMode = ref<'board' | 'list'>(prefersListView() ? 'list' : 'board')

const showCreateProject = ref(false)
const creatingProject = ref(false)
const projectError = ref<string | null>(null)
const projectFieldErrors = ref<Record<string, string>>({})
const projectDraft = ref({
  key: '',
  name: '',
  objective: '',
  runMode: 'DEMO' as 'DEMO' | 'REAL',
  budgetTokens: 100000,
})

const showCreateTask = ref(false)
const creatingTask = ref(false)
const taskError = ref<string | null>(null)
const taskFieldErrors = ref<Record<string, string>>({})
const taskDraft = ref({
  title: '',
  objective: '',
  acceptance: '',
  budgetTokens: 2000,
  demoBehavior: 'SUCCESS' as DemoBehavior,
  maxAutoRetries: 1,
})

const activeProject = computed(
  () => projects.value.find((project) => project.id === selectedProjectId.value) ?? null,
)

function errorFrom(error: unknown): LoadError {
  if (error instanceof ApiError) {
    return { message: error.message, requestId: error.requestId }
  }
  if (error instanceof NetworkError) return { message: error.message, requestId: null }
  return { message: '请求失败，请稍后重试。', requestId: null }
}

let loadVersion = 0
let disposed = false
const feed = useTaskFeed(() => load(true), () => true)
const { state: streamState, stale, notice } = feed

async function load(silent = false) {
  const version = ++loadVersion
  if (!silent) { loading.value = true; loadError.value = null }
  try {
    const projectList = await projectsApi.list()
    if (disposed || version !== loadVersion) return true
    const selected = projectList.projects.find(project => project.id === selectedProjectId.value)?.id
      ?? projectList.projects[0]?.id ?? null
    const taskList = selected
      ? (await tasksApi.list({ projectId: selected, limit: 200 })).tasks : []
    if (disposed || version !== loadVersion) return true
    projects.value = projectList.projects
    selectedProjectId.value = selected
    tasks.value = taskList
    loadError.value = null
    feed.snapshotResult(true)
    return true
  } catch (error) {
    if (disposed || version !== loadVersion) return true
    if (!projects.value.length) loadError.value = errorFrom(error)
    feed.snapshotResult(false)
    return false
  } finally {
    if (version === loadVersion) loading.value = false
  }
}

async function selectProject(projectId: number) {
  selectedProjectId.value = projectId
  return load()
}

onMounted(() => { void load() })
onBeforeUnmount(() => { disposed = true; loadVersion += 1 })

const columnTasks = computed<Record<string, TaskSummary[]>>(() => {
  const grouped: Record<string, TaskSummary[]> = {}
  for (const column of BOARD_COLUMNS) grouped[column.status] = []
  for (const task of tasks.value) {
    // A status the board does not render (WAITING_APPROVAL, reserved for Phase
    // 06) still has to be visible, so it falls back to the queue column rather
    // than disappearing from the page.
    const bucket = grouped[task.status] ?? grouped.QUEUED
    if (bucket) bucket.push(task)
  }
  return grouped
})

const taskCountByStatus = computed<Record<string, number>>(() => {
  const counts: Record<string, number> = {}
  for (const task of tasks.value) {
    counts[task.status] = (counts[task.status] ?? 0) + 1
  }
  return counts
})

/**
 * Columns actually rendered.
 *
 * Empty columns are omitted from the board: seven 300px columns put four empty
 * ones ahead of the real cards and push them off-screen. Empty statuses are still
 * named in the toolbar with a zero count, so nothing is silently hidden.
 */
const visibleColumns = computed(() =>
  BOARD_COLUMNS.filter(column => (columnTasks.value[column.status]?.length ?? 0) > 0),
)

const hiddenColumns = computed(() =>
  BOARD_COLUMNS.filter(column => (columnTasks.value[column.status]?.length ?? 0) === 0),
)

function openCreateProject() {
  projectError.value = null
  projectFieldErrors.value = {}
  showCreateProject.value = true
}

async function submitProject() {
  if (creatingProject.value) return
  creatingProject.value = true
  projectError.value = null
  projectFieldErrors.value = {}
  try {
    const payload: ProjectCreatePayload = {
      key: projectDraft.value.key.trim(),
      name: projectDraft.value.name.trim(),
      objective: projectDraft.value.objective.trim(),
      runMode: projectDraft.value.runMode,
      budgetTokens: Number(projectDraft.value.budgetTokens),
    }
    const project = await projectsApi.create(payload)
    showCreateProject.value = false
    projectDraft.value = {
      key: '',
      name: '',
      objective: '',
      runMode: 'DEMO',
      budgetTokens: 100000,
    }
    await load()
    await selectProject(project.id)
  } catch (error) {
    if (error instanceof ApiError) {
      projectError.value = error.message
      projectFieldErrors.value = Object.fromEntries(
        error.fieldErrors.map((item) => [item.field, item.message]),
      )
    } else {
      projectError.value = errorFrom(error).message
    }
  } finally {
    creatingProject.value = false
  }
}

function openCreateTask() {
  if (!activeProject.value) return
  // A new, deliberate task starts with a new idempotency key even if the
  // previous attempt failed. A *retry* keeps it — see submitTask().
  taskSubmissionKey = clearSubmissionKey()
  taskError.value = null
  taskFieldErrors.value = {}
  showCreateTask.value = true
}

/**
 * Key for the logical submission currently in the form.
 *
 * Deliberately module-scoped state (per component instance) instead of a fresh
 * value inside submitTask(): a response lost after the server committed must be
 * retried with the *same* key, otherwise the retry legally creates a second task
 * and a second reservation (review finding R13).
 */
let taskSubmissionKey: TaskSubmissionKey | null = null

async function submitTask() {
  if (creatingTask.value || !activeProject.value) return
  creatingTask.value = true
  taskError.value = null
  taskFieldErrors.value = {}
  const body = {
    title: taskDraft.value.title.trim(),
    objective: taskDraft.value.objective.trim(),
    acceptance: taskDraft.value.acceptance.trim() || null,
    budgetTokens: Number(taskDraft.value.budgetTokens),
    demoBehavior: taskDraft.value.demoBehavior,
    maxAutoRetries: Number(taskDraft.value.maxAutoRetries),
  }
  // Same body -> same key (safe retry). Changed body -> new key (new command).
  taskSubmissionKey = idempotencyKeyFor(taskSubmissionKey, body)
  try {
    const response = await tasksApi.create(activeProject.value.id, {
      ...body,
      idempotencyKey: taskSubmissionKey.key,
    })
    // Confirmed by the server: this command is finished, so the key is retired.
    taskSubmissionKey = clearSubmissionKey()
    showCreateTask.value = false
    taskDraft.value = {
      title: '',
      objective: '',
      acceptance: '',
      budgetTokens: 2000,
      demoBehavior: 'SUCCESS',
      maxAutoRetries: 1,
    }
    // ``created=false`` means the server recognised this exact command from an
    // earlier attempt whose response was lost. It is still the user's task, so
    // the page opens it instead of creating a second one.
    await router.push(`/tasks/${response.task.id}`)
  } catch (error) {
    // Keep the key: the failure may have happened after the server committed, and
    // an unchanged draft must retry the same command.
    if (error instanceof ApiError) {
      taskError.value = error.message
      taskFieldErrors.value = Object.fromEntries(
        error.fieldErrors.map((item) => [item.field, item.message]),
      )
      if (error.code === 'IDEMPOTENCY_CONFLICT') {
        // The stored body no longer matches this draft; the next attempt must be
        // a new command rather than repeating a conflicting one.
        taskSubmissionKey = clearSubmissionKey()
      }
    } else {
      taskError.value = errorFrom(error).message
    }
  } finally {
    creatingTask.value = false
  }
}

const demoBehaviors: { value: DemoBehavior; label: string }[] = [
  { value: 'SUCCESS', label: '正常完成' },
  { value: 'TRANSIENT_FAILURE', label: '临时失败（有限自动重试）' },
  { value: 'PERMANENT_FAILURE', label: '永久失败（不自动重试）' },
  { value: 'SLOW', label: '慢速执行（可观察取消）' },
  { value: 'UNKNOWN_USAGE', label: '用量未知（保守计入待核对）' },
  { value: 'TIMEOUT', label: '超时（用量未知）' },
]

function statusCount(status: TaskStatus): number {
  return taskCountByStatus.value[status] ?? 0
}
</script>

<template>
  <div class="projects">
    <PageHeader
      title="项目与任务"
      description="项目给出目标与额度；任务由独立 Worker 从持久队列领取，每次执行单独留证。本阶段只运行明确标识的确定性演练处理器。"
    >
      <template #actions>
        <AppButton :disabled="!activeProject" @click="openCreateTask">新建任务</AppButton>
        <AppButton variant="secondary" @click="openCreateProject">创建项目</AppButton>
      </template>
    </PageHeader>
    <LiveUpdateStatus :notice="notice" :warning="streamState !== 'live' || stale" :stale="stale" @retry="feed.scheduleRefresh(0)" />

    <div v-if="loading" class="projects__skeleton" aria-live="polite" aria-busy="true">
      <span class="acos-visually-hidden">正在加载项目与任务</span>
      <div v-for="index in 3" :key="index" class="projects__skeleton-card" />
    </div>

    <ErrorState
      v-else-if="loadError"
      title="项目与任务读取失败"
      :description="loadError.message"
      :request-id="loadError.requestId"
      @retry="load"
    />

    <template v-else>
      <EmptyState
        v-if="projects.length === 0"
        title="还没有项目"
        description="创建项目后才能提交任务。项目会设定目标、运行模式与 Token 额度；任务的额度从项目中划出。"
        phase-note="本阶段只支持演练运行；真实模型执行计划在后续阶段接入。"
      >
        <template #action>
          <AppButton @click="openCreateProject">创建第一个项目</AppButton>
        </template>
      </EmptyState>

      <template v-else>
        <section class="projects__projects" aria-labelledby="project-list-heading">
          <h2 id="project-list-heading" class="projects__section-title">项目</h2>
          <ul class="projects__project-list">
            <li
              v-for="project in projects"
              :key="project.id"
              class="projects__project"
              :class="{ 'projects__project--active': project.id === selectedProjectId }"
            >
              <button
                type="button"
                class="projects__project-button"
                :aria-current="project.id === selectedProjectId ? 'true' : undefined"
                @click="selectProject(project.id)"
              >
                <span class="projects__project-key">{{ project.key }}</span>
                <span class="projects__project-name">{{ project.name }}</span>
                <span class="projects__project-meta acos-caption">
                  {{ project.taskCounts
                    ? Object.entries(project.taskCounts)
                      .map(([key, value]) => `${taskStatus(key as TaskStatus).label} ${value}`)
                      .join(' · ')
                    : '暂无任务' }}
                </span>
              </button>
            </li>
          </ul>
        </section>

        <section
          v-if="activeProject"
          class="projects__summary"
          aria-labelledby="active-project-heading"
        >
          <div class="projects__summary-head">
            <div>
              <h2 id="active-project-heading">{{ activeProject.name }}</h2>
              <p class="acos-text-secondary">{{ activeProject.objective }}</p>
            </div>
            <div class="projects__summary-badges">
              <StatusBadge
                :label="activeProject.runMode === 'DEMO' ? '演练模式' : '真实模式'"
                :tone="activeProject.runMode === 'DEMO' ? 'neutral' : 'warning'"
              />
              <StatusBadge
                :label="activeProject.status === 'ACTIVE' ? '进行中' : '已归档'"
                :tone="activeProject.status === 'ACTIVE' ? 'success' : 'neutral'"
              />
            </div>
          </div>
          <BudgetMeter
            v-if="activeProject.budget"
            :budget="activeProject.budget"
            label="项目额度（Token）"
          />
          <p class="acos-caption">
            运行模式与额度变更属于后续阶段；当前不能在此页改额度或切换真实模式。
          </p>
        </section>

        <section class="projects__board-section" aria-labelledby="board-heading">
          <div class="projects__board-head">
            <h2 id="board-heading">任务看板</h2>
            <div class="projects__board-toggle" role="group" aria-label="看板显示方式">
              <AppButton
                size="small"
                :variant="viewMode === 'board' ? 'primary' : 'secondary'"
                :aria-pressed="viewMode === 'board'"
                @click="viewMode = 'board'"
              >
                看板
              </AppButton>
              <AppButton
                size="small"
                :variant="viewMode === 'list' ? 'primary' : 'secondary'"
                :aria-pressed="viewMode === 'list'"
                @click="viewMode = 'list'"
              >
                列表
              </AppButton>
            </div>
          </div>

          <EmptyState
            v-if="tasks.length === 0"
            title="这个项目还没有任务"
            description="任务会进入持久队列，由 Worker 领取并执行；刷新页面不会丢失进度。"
          >
            <template #action>
              <AppButton @click="openCreateTask">新建任务</AppButton>
            </template>
          </EmptyState>

          <template v-else-if="viewMode === 'board'">
            <p v-if="hiddenColumns.length > 0" class="projects__board-note acos-caption">
              空状态列不占宽度：
              <template v-for="(column, index) in hiddenColumns" :key="column.status">
                {{ column.title }} 0<template v-if="index < hiddenColumns.length - 1"> · </template>
              </template>
            </p>

            <div class="projects__board">
              <section
                v-for="column in visibleColumns"
                :key="column.status"
                class="projects__column"
                :aria-labelledby="`column-${column.status}`"
              >
                <header class="projects__column-head">
                  <h3 :id="`column-${column.status}`">{{ column.title }}</h3>
                  <span class="projects__column-count acos-tabular">
                    {{ statusCount(column.status) }}
                  </span>
                </header>
                <p
                  v-if="(columnTasks[column.status]?.length ?? 0) === 0"
                  class="projects__column-empty"
                >
                  无任务
                </p>
                <article
                  v-for="task in columnTasks[column.status] ?? []"
                  :key="task.id"
                  class="projects__card"
                >
                  <button
                    type="button"
                    class="projects__card-button"
                    @click="router.push(`/tasks/${task.id}`)"
                  >
                    <span class="projects__card-title">{{ task.title }}</span>
                    <span class="projects__card-meta acos-caption">
                      #{{ task.id }} · 尝试 {{ task.attemptCount }} 次
                      <template v-if="task.assigneeName"> · {{ task.assigneeName }}</template>
                    </span>
                    <span class="projects__card-meta acos-caption">
                      演练行为：{{ demoBehaviorLabel(task.demoBehavior) }}
                    </span>
                    <span
                      v-if="task.cancelPending"
                      class="projects__card-meta acos-caption projects__card-pending"
                    >
                      已请求取消，等待安全检查点
                    </span>
                    <span
                      v-else-if="task.lastErrorCode"
                      class="projects__card-meta acos-caption projects__card-error"
                    >
                      {{ task.lastErrorMessage ?? task.lastErrorCode }}
                    </span>
                  </button>
                </article>
              </section>
            </div>
          </template>

          <div v-else class="projects__list">
            <div class="projects__list-row projects__list-row--head">
              <span>任务</span>
              <span>状态</span>
              <span>负责人</span>
              <span>额度</span>
              <span>更新时间</span>
            </div>
            <div v-for="task in tasks" :key="task.id" class="projects__list-row">
              <span>
                <button
                  type="button"
                  class="projects__link"
                  @click="router.push(`/tasks/${task.id}`)"
                >
                  {{ task.title }}
                </button>
                <span class="acos-caption">#{{ task.id }}</span>
              </span>
              <span>
                <StatusBadge
                  :label="taskStatus(task.status).label"
                  :tone="taskStatus(task.status).tone"
                />
              </span>
              <span>{{ task.assigneeName ?? '未指定' }}</span>
              <span class="acos-tabular">{{ formatTokens(task.budgetTokens) }}</span>
              <span class="acos-caption">{{ formatTime(task.updatedAt) }}</span>
            </div>
          </div>
        </section>
      </template>
    </template>

    <AppDrawer v-model="showCreateProject" title="创建项目">
      <form class="projects__form" @submit.prevent="submitProject">
        <p v-if="projectError" class="projects__form-error" role="alert">{{ projectError }}</p>
        <FormField
          label="项目编号"
          field-name="project-key"
          required
          :error="projectFieldErrors.key"
        >
          <template #default="{ controlId, describedBy, invalid }">
            <AppInput
              :id="controlId"
              v-model="projectDraft.key"
              :described-by="describedBy"
              :invalid="invalid"
              placeholder="例如 AUTH-REFRESH"
            />
          </template>
        </FormField>
        <FormField
          label="项目名称"
          field-name="project-name"
          required
          :error="projectFieldErrors.name"
        >
          <template #default="{ controlId, describedBy, invalid }">
            <AppInput
              :id="controlId"
              v-model="projectDraft.name"
              :described-by="describedBy"
              :invalid="invalid"
            />
          </template>
        </FormField>
        <FormField
          label="项目目标"
          field-name="project-objective"
          required
          :error="projectFieldErrors.objective"
          hint="说明这个项目要交付什么、支持范围到哪里。"
        >
          <template #default="{ controlId, describedBy, invalid }">
            <textarea
              :id="controlId"
              v-model="projectDraft.objective"
              class="projects__textarea"
              :aria-describedby="describedBy"
              :aria-invalid="invalid || undefined"
              rows="5"
            />
          </template>
        </FormField>
        <FormField
          label="运行模式"
          field-name="project-run-mode"
          required
          hint="本阶段只提供演练处理器。选择真实模式会被服务端拒绝，系统不会静默降级为演练。"
        >
          <template #default="{ controlId }">
            <select :id="controlId" v-model="projectDraft.runMode" class="projects__select">
              <option value="DEMO">演练（确定性处理器）</option>
              <option value="REAL">真实模型（本阶段未实现）</option>
            </select>
          </template>
        </FormField>
        <FormField
          label="项目额度（Token）"
          field-name="project-budget"
          required
          :error="projectFieldErrors.budgetTokens"
          hint="任务的额度从项目额度中划出；公司总额度在预算页查看。"
        >
          <template #default="{ controlId, describedBy, invalid }">
            <AppInput
              :id="controlId"
              :model-value="String(projectDraft.budgetTokens)"
              type="text"
              :described-by="describedBy"
              :invalid="invalid"
              @update:model-value="projectDraft.budgetTokens = Number($event) || 0"
            />
          </template>
        </FormField>
      </form>
      <template #footer>
        <AppButton variant="secondary" @click="showCreateProject = false">取消</AppButton>
        <AppButton :loading="creatingProject" loading-text="创建中…" @click="submitProject">
          创建项目
        </AppButton>
      </template>
    </AppDrawer>

    <AppDrawer v-model="showCreateTask" title="新建任务">
      <form class="projects__form" @submit.prevent="submitTask">
        <p v-if="taskError" class="projects__form-error" role="alert">{{ taskError }}</p>
        <FormField label="任务标题" field-name="task-title" required :error="taskFieldErrors.title">
          <template #default="{ controlId, describedBy, invalid }">
            <AppInput
              :id="controlId"
              v-model="taskDraft.title"
              :described-by="describedBy"
              :invalid="invalid"
            />
          </template>
        </FormField>
        <FormField
          label="任务目标"
          field-name="task-objective"
          required
          :error="taskFieldErrors.objective"
        >
          <template #default="{ controlId, describedBy, invalid }">
            <textarea
              :id="controlId"
              v-model="taskDraft.objective"
              class="projects__textarea"
              :aria-describedby="describedBy"
              :aria-invalid="invalid || undefined"
              rows="4"
            />
          </template>
        </FormField>
        <FormField
          label="验收说明"
          field-name="task-acceptance"
          :error="taskFieldErrors.acceptance"
          hint="可选：怎样才算完成。"
        >
          <template #default="{ controlId, describedBy }">
            <textarea
              :id="controlId"
              v-model="taskDraft.acceptance"
              class="projects__textarea"
              :aria-describedby="describedBy"
              rows="3"
            />
          </template>
        </FormField>
        <FormField
          label="任务额度（Token）"
          field-name="task-budget"
          required
          :error="taskFieldErrors.budgetTokens"
        >
          <template #default="{ controlId, describedBy, invalid }">
            <AppInput
              :id="controlId"
              :model-value="String(taskDraft.budgetTokens)"
              type="text"
              :described-by="describedBy"
              :invalid="invalid"
              @update:model-value="taskDraft.budgetTokens = Number($event) || 0"
            />
          </template>
        </FormField>
        <FormField
          label="演练行为"
          field-name="task-demo-behavior"
          required
          hint="演练处理器是确定性测试工具，结果会明确标注为演练，不是真实模型产出。"
        >
          <template #default="{ controlId }">
            <select :id="controlId" v-model="taskDraft.demoBehavior" class="projects__select">
              <option v-for="behavior in demoBehaviors" :key="behavior.value" :value="behavior.value">
                {{ behavior.label }}
              </option>
            </select>
          </template>
        </FormField>
        <FormField
          label="自动重试次数上限"
          field-name="task-retries"
          required
          :error="taskFieldErrors.maxAutoRetries"
          hint="只对明确可重试且幂等的步骤生效，默认 1 次。"
        >
          <template #default="{ controlId }">
            <select
              :id="controlId"
              v-model.number="taskDraft.maxAutoRetries"
              class="projects__select"
            >
              <option :value="0">不自动重试</option>
              <option :value="1">1 次</option>
              <option :value="2">2 次</option>
            </select>
          </template>
        </FormField>
      </form>
      <template #footer>
        <AppButton variant="secondary" @click="showCreateTask = false">取消</AppButton>
        <AppButton :loading="creatingTask" loading-text="提交中…" @click="submitTask">
          创建任务
        </AppButton>
      </template>
    </AppDrawer>
  </div>
</template>

<style scoped>
.projects {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-24);
}

.projects__skeleton {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--acos-space-16);
}

.projects__skeleton-card {
  height: calc(var(--acos-space-64) + var(--acos-space-32));
  border-radius: var(--acos-radius-card);
  background: var(--acos-color-surface-subtle);
}

.projects__section-title {
  font-size: var(--acos-font-subtitle-size);
  line-height: var(--acos-font-subtitle-line);
  font-weight: var(--acos-font-subtitle-weight);
  margin-bottom: var(--acos-space-12);
}

.projects__project-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: var(--acos-space-12);
}

.projects__project-button {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--acos-space-4);
  padding: var(--acos-space-16);
  min-height: var(--acos-control-medium-height);
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: inherit;
}

.projects__project--active .projects__project-button {
  border-color: var(--acos-color-primary);
  background: var(--acos-color-primary-subtle);
}

.projects__project-key {
  font-family: var(--acos-font-mono);
  font-size: var(--acos-font-caption-size);
  color: var(--acos-color-text-secondary);
}

.projects__project-name {
  font-weight: 500;
}

.projects__project-meta {
  color: var(--acos-color-text-secondary);
}

.projects__summary {
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
  padding: var(--acos-layout-card-padding);
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-16);
}

.projects__summary-head {
  display: flex;
  justify-content: space-between;
  gap: var(--acos-space-16);
  flex-wrap: wrap;
}

.projects__summary-badges {
  display: flex;
  gap: var(--acos-space-8);
  flex-wrap: wrap;
  align-items: flex-start;
}

.projects__board-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--acos-space-12);
  flex-wrap: wrap;
  margin-bottom: var(--acos-space-12);
}

.projects__board-head h2 {
  font-size: var(--acos-font-subtitle-size);
  line-height: var(--acos-font-subtitle-line);
  font-weight: var(--acos-font-subtitle-weight);
  margin: 0;
}

.projects__board-toggle {
  display: flex;
  gap: var(--acos-space-8);
}

.projects__board-note {
  margin: 0 0 var(--acos-space-8);
  color: var(--acos-color-text-secondary);
}

.projects__board {
  display: flex;
  gap: var(--acos-layout-card-gap);
  overflow-x: auto;
  padding-bottom: var(--acos-space-8);
  align-items: flex-start;
}

.projects__column {
  flex: 0 0 auto;
  width: 300px;
  background: var(--acos-color-surface-subtle);
  border-radius: var(--acos-radius-card);
  padding: var(--acos-space-12);
  display: flex;
  flex-direction: column;  gap: var(--acos-space-8);
}

.projects__column-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--acos-space-8);
}

.projects__column-head h3 {
  font-size: var(--acos-font-label-size);
  line-height: var(--acos-font-label-line);
  font-weight: var(--acos-font-label-weight);
  margin: 0;
}

.projects__column-count {
  color: var(--acos-color-text-secondary);
}

.projects__column-empty {
  margin: 0;
  font-size: var(--acos-font-caption-size);
  color: var(--acos-color-text-secondary);
}

.projects__card-button {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--acos-space-4);
  padding: var(--acos-space-16);
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-control);
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: inherit;
}

.projects__card-title {
  font-weight: 500;
}

.projects__card-meta {
  color: var(--acos-color-text-secondary);
}

.projects__card-pending {
  color: var(--acos-color-warning);
}

.projects__card-error {
  color: var(--acos-color-danger);
}

.projects__list {
  display: flex;
  flex-direction: column;
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
  overflow-x: auto;
}

.projects__list-row {
  display: grid;
  grid-template-columns: minmax(200px, 2fr) 120px 140px 120px 180px;
  gap: var(--acos-space-12);
  padding: var(--acos-space-12) var(--acos-space-16);
  align-items: center;
  border-top: var(--acos-control-border-width) solid var(--acos-color-border);
  min-width: 720px;
}

.projects__list-row--head {
  border-top: 0;
  background: var(--acos-color-surface-subtle);
  font-weight: 600;
  color: var(--acos-color-text-secondary);
}

.projects__link {
  background: none;
  border: 0;
  padding: 0;
  color: var(--acos-color-primary);
  cursor: pointer;
  font: inherit;
  text-decoration: underline;
  margin-right: var(--acos-space-8);
}

.projects__form {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-20);
}

.projects__form-error {
  margin: 0;
  padding: var(--acos-space-12);
  background: var(--acos-color-danger-subtle);
  color: var(--acos-color-danger);
  border-radius: var(--acos-radius-control);
}

.projects__textarea,
.projects__select {
  width: 100%;
  border: var(--acos-control-border-width) solid var(--acos-color-control-border);
  border-radius: var(--acos-radius-control);
  padding: var(--acos-space-8) var(--acos-space-12);
  font: inherit;
  color: var(--acos-color-text);
  background: var(--acos-color-surface);
  min-height: var(--acos-control-medium-height);
}

.projects__textarea {
  /* COMPONENTS.md: textareas are 120 high including padding and border. */
  min-height: calc(
    var(--acos-control-textarea-min-height) - var(--acos-space-32)
  );
  resize: vertical;
}

.projects__textarea:focus-visible,
.projects__select:focus-visible {
  outline: var(--acos-focus-width) solid var(--acos-color-focus);
  outline-offset: var(--acos-focus-offset);
}

@media (max-width: 767px) {
  .projects__column {
    width: min(300px, calc(100vw - var(--acos-space-32)));
  }
}
</style>
