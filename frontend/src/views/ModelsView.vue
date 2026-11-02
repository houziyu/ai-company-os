<script setup lang="ts">
/**
 * ModelsView -- the model centre.
 *
 * PAGES.md §4 splits this page into three tabs (providers / models / employee
 * assignments) precisely so an Agent id is never confused with a model id. The
 * page also enforces two product rules in its structure:
 *
 * 1. **Saving and probing are separate.** The provider drawer's primary action
 *    is "保存配置"; "测试连接" is a secondary action that calls a different
 *    endpoint against already-saved settings, so a probe can never apply an
 *    unconfirmed edit.
 * 2. **No secret is displayed or collected as a value.** The form takes an
 *    environment-variable *name* and the list shows 已配置/未配置. There is no
 *    input where a key could be typed, pasted or revealed.
 *
 * A probe spends tokens, so the button is disabled while a probe runs, the
 * warning is shown before the action, and the result reports whether usage was
 * measured instead of assuming zero.
 */
import { computed, nextTick, onMounted, ref } from 'vue'

import AppButton from '@/components/AppButton.vue'
import AppDrawer from '@/components/AppDrawer.vue'
import AppInput from '@/components/AppInput.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import FormField from '@/components/FormField.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { ApiError, NetworkError } from '@/api/client'
import { modelCentreApi } from '@/api/endpoints'
import type {
  Enablement,
  ModelCentreResponse,
  ProbeOutcome,
  ModelResponse,
  ProbeResponse,
  ProviderKind,
  ProviderResponse,
} from '@/api/organization-types'

type Tab = 'providers' | 'models' | 'assignments'

const tab = ref<Tab>('providers')

const tabItems = computed<{ key: Tab; label: string }[]>(() => [
  { key: 'providers', label: `供应商（${providers.value.length}）` },
  { key: 'models', label: `模型（${models.value.length}）` },
  { key: 'assignments', label: `员工分配（${assignments.value.length}）` },
])

const tabRefs = new Map<Tab, HTMLButtonElement>()

function setTabRef(key: Tab, element: unknown) {
  if (element instanceof HTMLButtonElement) tabRefs.set(key, element)
  else tabRefs.delete(key)
}

/**
 * Move selection *and* focus to a tab.
 *
 * The WAI-ARIA tabs pattern needs both: with a roving tabindex only the selected
 * tab is reachable by Tab, so a handler that changes selection without focusing
 * leaves the keyboard user on a tab that is no longer selected and unable to
 * reach the rest (review finding R10).
 */
function selectTab(key: Tab, focus = false) {
  tab.value = key
  if (focus) {
    // Wait for tabindex to update before moving focus.
    void nextTick(() => tabRefs.get(key)?.focus())
  }
}

function onTabKeydown(event: KeyboardEvent, index: number) {
  const last = tabItems.value.length - 1
  let target: number | null = null
  switch (event.key) {
    case 'ArrowRight':
      target = index === last ? 0 : index + 1
      break
    case 'ArrowLeft':
      target = index === 0 ? last : index - 1
      break
    case 'Home':
      target = 0
      break
    case 'End':
      target = last
      break
    default:
      return
  }
  event.preventDefault()
  const key = tabItems.value[target]?.key
  if (key) selectTab(key, true)
}
const loading = ref(true)
const loadError = ref<{ message: string; requestId: string | null } | null>(null)
const centre = ref<ModelCentreResponse | null>(null)

// --------------------------------------------------------------- provider form
const providerDrawerOpen = ref(false)
const editingProvider = ref<ProviderResponse | null>(null)
const formName = ref('')
const formKind = ref<ProviderKind>('DEEPSEEK')
const formBaseUrl = ref('')
const formSecretRef = ref('')
const formTimeout = ref<number>(30)
const formTimeoutText = ref<string>('30')
const formNotes = ref('')
const savingProvider = ref(false)
const providerError = ref<string | null>(null)
const providerFieldErrors = ref<Record<string, string | null>>({})

// ------------------------------------------------------------------ model form
const modelDrawerOpen = ref(false)
const editingModel = ref<ModelResponse | null>(null)
const modelProviderId = ref<number | null>(null)
const modelLogicalName = ref('')
const modelLabel = ref('')
const modelMaxTokens = ref<string>('')
const savingModel = ref(false)
const modelError = ref<string | null>(null)
const modelFieldErrors = ref<Record<string, string | null>>({})

// ----------------------------------------------------------------- probe state
const probingModelId = ref<number | null>(null)
const lastProbe = ref<ProbeResponse | null>(null)
const probeErrors = ref<Record<number, string | null>>({})

const providers = computed(() => centre.value?.providers ?? [])
const models = computed(() => centre.value?.models ?? [])
const assignments = computed(() => centre.value?.assignments ?? [])
const usage = computed(() => centre.value?.usage ?? null)
const preset = computed(() => centre.value?.deepseekPreset ?? null)

/** Shown as the field example; kept out of the template to stay type-safe. */
const deepSeekUrlExample = 'https://api.deepseek.com'

const probeCostNotice =
  '测试连接会向供应商发送一次请求，可能消耗少量 Token，不会自动重试。'

async function load() {
  loading.value = true
  loadError.value = null
  try {
    centre.value = await modelCentreApi.overview()
  } catch (error) {
    if (error instanceof ApiError) {
      loadError.value = { message: error.message, requestId: error.requestId }
    } else if (error instanceof NetworkError) {
      loadError.value = { message: error.message, requestId: null }
    } else {
      loadError.value = { message: '无法读取模型配置，请稍后重试。', requestId: null }
    }
  } finally {
    loading.value = false
  }
}

onMounted(load)

function statusLabel(status: Enablement): string {
  return status === 'ENABLED' ? '启用' : '停用'
}

function secretLabel(provider: ProviderResponse): string {
  if (!provider.secretRef) return '未设置密钥引用'
  return provider.secretConfigured ? '已配置' : '未配置'
}

/** Truthful capability text: protocol compatibility is not verified ability. */
function jsonCapability(model: ModelResponse): string {
  // A stored result that belongs to a superseded configuration is not evidence
  // about the current one, so it is shown as unverified.
  if (model.probeEvidenceStale) return '未验证（配置已变更）'
  if (model.supportsJsonOutput === true) return '探测已验证'
  if (model.supportsJsonOutput === false) return '探测未通过'
  return '未验证'
}

function probeOutcomeTone(outcome: ProbeOutcome): 'success' | 'danger' | 'warning' {
  if (outcome === 'SUCCESS') return 'success'
  if (outcome === 'PENDING') return 'warning'
  return 'danger'
}

function probeOutcomeLabel(outcome: ProbeOutcome): string {
  if (outcome === 'SUCCESS') return '成功'
  if (outcome === 'PENDING') return '结果与用量未知'
  return '失败'
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleString('zh-CN', { hour12: false })
}

// ----------------------------------------------------------------- providers
function openCreateProvider() {
  editingProvider.value = null
  formName.value = ''
  formKind.value = 'DEEPSEEK'
  formBaseUrl.value = preset.value?.baseUrl ?? ''
  formSecretRef.value = preset.value?.secretRef ?? ''
  formTimeout.value = 30
  formTimeoutText.value = '30'
  formNotes.value = preset.value?.notes ?? ''
  providerError.value = null
  providerFieldErrors.value = {}
  providerDrawerOpen.value = true
}

function openEditProvider(provider: ProviderResponse) {
  editingProvider.value = provider
  formName.value = provider.name
  formKind.value = provider.kind
  formBaseUrl.value = provider.baseUrl
  formSecretRef.value = provider.secretRef ?? ''
  formTimeout.value = provider.timeoutSeconds
  formTimeoutText.value = String(provider.timeoutSeconds)
  formNotes.value = provider.notes ?? ''
  providerError.value = null
  providerFieldErrors.value = {}
  providerDrawerOpen.value = true
}

function applyPreset() {
  if (!preset.value) return
  formKind.value = preset.value.kind
  formBaseUrl.value = preset.value.baseUrl
  formSecretRef.value = preset.value.secretRef
  formNotes.value = preset.value.notes
}

async function saveProvider() {
  if (savingProvider.value) return
  savingProvider.value = true
  providerError.value = null
  providerFieldErrors.value = {}
  const secretRef = formSecretRef.value.trim()
  const parsedTimeout = Number(formTimeoutText.value.replace(',', '.'))
  if (!Number.isFinite(parsedTimeout)) {
    providerFieldErrors.value = { timeoutSeconds: '请输入数字（秒）。' }
    savingProvider.value = false
    return
  }
  formTimeout.value = parsedTimeout
  try {
    if (editingProvider.value) {
      await modelCentreApi.updateProvider(editingProvider.value.id, {
        // The version this form was rendered from; a mismatch is a 409 rather
        // than a silent overwrite (review findings R7 and R9).
        expectedVersion: editingProvider.value.version,
        name: formName.value.trim(),
        kind: formKind.value,
        baseUrl: formBaseUrl.value.trim(),
        secretRef: secretRef === '' ? null : secretRef,
        changeSecretRef: true,
        timeoutSeconds: formTimeout.value,
        // Explicit null so clearing the box actually clears the note; the server
        // distinguishes "omitted" from "cleared".
        notes: formNotes.value.trim() === '' ? null : formNotes.value.trim(),
      })
    } else {
      await modelCentreApi.createProvider({
        name: formName.value.trim(),
        kind: formKind.value,
        baseUrl: formBaseUrl.value.trim(),
        secretRef: secretRef === '' ? null : secretRef,
        timeoutSeconds: formTimeout.value,
        notes: formNotes.value.trim() === '' ? null : formNotes.value.trim(),
      })
    }
    providerDrawerOpen.value = false
    await load()
  } catch (error) {
    if (error instanceof ApiError) {
      providerError.value = error.message
      for (const item of error.fieldErrors) {
        providerFieldErrors.value[item.field] = item.message
      }
    } else if (error instanceof NetworkError) {
      providerError.value = error.message
    } else {
      providerError.value = '保存供应商时发生未知错误，请稍后重试。'
    }
  } finally {
    savingProvider.value = false
  }
}

async function toggleProviderStatus(provider: ProviderResponse) {
  try {
    await modelCentreApi.updateProvider(provider.id, {
      expectedVersion: provider.version,
      status: provider.status === 'ENABLED' ? 'DISABLED' : 'ENABLED',
    })
    await load()
  } catch (error) {
    loadError.value = {
      message:
        error instanceof ApiError || error instanceof NetworkError
          ? error.message
          : '状态变更失败。',
      requestId: error instanceof ApiError ? error.requestId : null,
    }
  }
}

// -------------------------------------------------------------------- models
function openCreateModel(providerId?: number) {
  editingModel.value = null
  modelProviderId.value = providerId ?? providers.value[0]?.id ?? null
  modelLogicalName.value = ''
  modelLabel.value = ''
  modelMaxTokens.value = ''
  modelError.value = null
  modelFieldErrors.value = {}
  modelDrawerOpen.value = true
}

function openEditModel(model: ModelResponse) {
  editingModel.value = model
  modelProviderId.value = model.providerId
  modelLogicalName.value = model.logicalName
  modelLabel.value = model.label
  modelMaxTokens.value = model.maxOutputTokens === null ? '' : String(model.maxOutputTokens)
  modelError.value = null
  modelFieldErrors.value = {}
  modelDrawerOpen.value = true
}

async function saveModel() {
  if (savingModel.value) return
  savingModel.value = true
  modelError.value = null
  modelFieldErrors.value = {}
  const rawTokens = modelMaxTokens.value.trim()
  const maxTokens = rawTokens === '' ? null : Number(rawTokens)
  try {
    if (editingModel.value) {
      await modelCentreApi.updateModel(editingModel.value.id, {
        expectedVersion: editingModel.value.version,
        label: modelLabel.value.trim() === '' ? null : modelLabel.value.trim(),
        maxOutputTokens: maxTokens,
        changeMaxOutputTokens: true,
      })
    } else {
      if (modelProviderId.value === null) {
        modelError.value = '请先选择供应商。'
        return
      }
      await modelCentreApi.createModel({
        providerId: modelProviderId.value,
        logicalName: modelLogicalName.value.trim(),
        label: modelLabel.value.trim() === '' ? null : modelLabel.value.trim(),
        maxOutputTokens: maxTokens,
      })
    }
    modelDrawerOpen.value = false
    await load()
  } catch (error) {
    if (error instanceof ApiError) {
      modelError.value = error.message
      for (const item of error.fieldErrors) {
        modelFieldErrors.value[item.field] = item.message
      }
    } else if (error instanceof NetworkError) {
      modelError.value = error.message
    } else {
      modelError.value = '保存模型时发生未知错误，请稍后重试。'
    }
  } finally {
    savingModel.value = false
  }
}

async function toggleModelStatus(model: ModelResponse) {
  try {
    await modelCentreApi.updateModel(model.id, {
      expectedVersion: model.version,
      status: model.status === 'ENABLED' ? 'DISABLED' : 'ENABLED',
    })
    await load()
  } catch (error) {
    loadError.value = {
      message:
        error instanceof ApiError || error instanceof NetworkError
          ? error.message
          : '状态变更失败。',
      requestId: error instanceof ApiError ? error.requestId : null,
    }
  }
}

// --------------------------------------------------------------------- probe
async function runProbe(model: ModelResponse) {
  if (probingModelId.value !== null) return
  probingModelId.value = model.id
  lastProbe.value = null
  probeErrors.value = { ...probeErrors.value, [model.id]: null }
  try {
    const result = await modelCentreApi.probe(model.id)
    lastProbe.value = result
    await load()
  } catch (error) {
    const message =
      error instanceof ApiError || error instanceof NetworkError
        ? error.message
        : '测试连接失败，请稍后重试。'
    probeErrors.value = { ...probeErrors.value, [model.id]: message }
  } finally {
    probingModelId.value = null
  }
}
</script>

<template>
  <div class="models">
    <PageHeader
      title="模型中心"
      description="分别管理供应商、模型与员工分配。密钥只以服务端环境变量引用的形式出现，界面不显示、不接收密钥值。"
    >
      <template #actions>
        <AppButton @click="openCreateProvider">添加供应商</AppButton>
      </template>
    </PageHeader>

    <div v-if="loading" class="models__skeleton" aria-live="polite" aria-busy="true">
      <span class="acos-visually-hidden">正在加载模型配置</span>
      <div v-for="index in 3" :key="index" class="models__skeleton-row" />
    </div>

    <ErrorState
      v-else-if="loadError"
      title="模型配置读取失败"
      :description="loadError.message"
      :request-id="loadError.requestId"
      :retrying="loading"
      @retry="load"
    />

    <template v-else>
      <div class="models__tabs" role="tablist" aria-label="模型中心分区">
        <button
          v-for="(item, index) in tabItems"
          :key="item.key"
          :ref="(element) => setTabRef(item.key, element)"
          type="button"
          role="tab"
          class="models__tab"
          :class="{ 'models__tab--active': tab === item.key }"
          :aria-selected="tab === item.key"
          :tabindex="tab === item.key ? 0 : -1"
          @click="selectTab(item.key)"
          @keydown="onTabKeydown($event, index)"
        >
          {{ item.label }}
        </button>
      </div>

      <!-- Providers -->
      <section v-if="tab === 'providers'" aria-label="供应商列表" data-testid="tab-providers">
        <EmptyState
          v-if="providers.length === 0"
          title="还没有供应商"
          description="添加一个 DeepSeek 预设或自定义 OpenAI 兼容接口。密钥值不会进入浏览器或数据库。"
        >
          <template #action>
            <AppButton @click="openCreateProvider">添加供应商</AppButton>
          </template>
        </EmptyState>

        <ul v-else class="models__cards">
          <li v-for="provider in providers" :key="provider.id" class="models__card">
            <div class="models__card-head">
              <div>
                <h2 class="models__card-title">{{ provider.name }}</h2>
                <p class="acos-caption models__mono">{{ provider.baseUrl }}</p>
              </div>
              <StatusBadge
                :label="statusLabel(provider.status)"
                :tone="provider.status === 'ENABLED' ? 'success' : 'neutral'"
              />
            </div>

            <dl class="models__facts">
              <div>
                <dt>类型</dt>
                <dd>{{ provider.kind === 'DEEPSEEK' ? 'DeepSeek 预设' : 'OpenAI 兼容' }}</dd>
              </div>
              <div>
                <dt>密钥引用</dt>
                <dd>
                  <span class="models__mono">{{ provider.secretRef ?? '—' }}</span>
                  <StatusBadge
                    :label="secretLabel(provider)"
                    :tone="provider.secretConfigured ? 'success' : 'warning'"
                  />
                </dd>
              </div>
              <div>
                <dt>超时</dt>
                <dd>{{ provider.timeoutSeconds }} 秒</dd>
              </div>
              <div>
                <dt>模型数</dt>
                <dd>{{ provider.modelCount }}</dd>
              </div>
              <div>
                <dt>更新时间</dt>
                <dd>{{ formatTime(provider.updatedAt) }}</dd>
              </div>
            </dl>

            <div class="models__card-actions">
              <AppButton size="small" variant="secondary" @click="openEditProvider(provider)">
                编辑
              </AppButton>
              <AppButton size="small" variant="ghost" @click="openCreateModel(provider.id)">
                添加模型
              </AppButton>
              <AppButton
                size="small"
                variant="ghost"
                @click="toggleProviderStatus(provider)"
              >
                {{ provider.status === 'ENABLED' ? '停用' : '启用' }}
              </AppButton>
            </div>
          </li>
        </ul>
      </section>

      <!-- Models -->
      <section v-else-if="tab === 'models'" aria-label="模型列表" data-testid="tab-models">
        <div v-if="lastProbe" class="models__probe-result" role="status">
          <div class="models__probe-head">
            <StatusBadge
              :label="
                lastProbe.outcome === 'SUCCESS'
                  ? '测试成功'
                  : lastProbe.outcome === 'PENDING'
                    ? '结果与用量未知'
                    : '测试失败'
              "
              :tone="probeOutcomeTone(lastProbe.outcome)"
            />
            <span class="acos-caption">{{ formatTime(lastProbe.startedAt) }}</span>
          </div>
          <p>{{ lastProbe.message }}</p>
          <p class="acos-caption">
            调用次数：{{ lastProbe.callCount }} ·
            {{
              lastProbe.latencyMs === null ? '延迟未知' : `延迟 ${lastProbe.latencyMs} ms`
            }}
            · JSON 输出：{{
              lastProbe.jsonOutputVerified === true
                ? '已验证'
                : lastProbe.jsonOutputVerified === false
                  ? '未通过'
                  : '未验证'
            }}
            · 用量：{{
              lastProbe.usageKnown
                ? `${lastProbe.promptTokens ?? '?'} / ${lastProbe.completionTokens ?? '?'} Token`
                : '供应商未返回，未知'
            }}
          </p>
          <p v-if="lastProbe.resolvedModelName" class="acos-caption models__mono">
            供应商返回的模型：{{ lastProbe.resolvedModelName }}
          </p>
        </div>

        <EmptyState
          v-if="models.length === 0"
          title="还没有模型"
          description="先在供应商分区添加供应商，再为它登记逻辑模型 ID。"
        >
          <template #action>
            <AppButton :disabled="providers.length === 0" @click="openCreateModel()">
              添加模型
            </AppButton>
          </template>
        </EmptyState>

        <div v-else class="models__table-wrap">
          <el-table :data="models" row-key="id" style="width: 100%">
            <el-table-column label="模型" min-width="180">
              <template #default="{ row }">
                <div class="models__cell-strong">{{ row.label }}</div>
                <div class="acos-caption models__mono">{{ row.logicalName }}</div>
              </template>
            </el-table-column>
            <el-table-column label="供应商" min-width="140" prop="providerName" />
            <el-table-column label="JSON 输出" min-width="120">
              <template #default="{ row }">
                <StatusBadge
                  :label="jsonCapability(row)"
                  :tone="
                    row.supportsJsonOutput === true
                      ? 'success'
                      : row.supportsJsonOutput === false
                        ? 'danger'
                        : 'neutral'
                  "
                />
              </template>
            </el-table-column>
            <el-table-column label="最近探测" min-width="150">
              <template #default="{ row }">
                <div>{{ formatTime(row.lastProbeAt) }}</div>
                <div v-if="row.lastProbeDetail" class="acos-caption">
                  {{ row.lastProbeDetail }}
                </div>
              </template>
            </el-table-column>
            <el-table-column label="状态" min-width="90">
              <template #default="{ row }">
                <StatusBadge
                  :label="statusLabel(row.status)"
                  :tone="row.status === 'ENABLED' ? 'success' : 'neutral'"
                />
              </template>
            </el-table-column>
            <el-table-column label="操作" min-width="260">
              <template #default="{ row }">
                <div class="models__row-actions">
                  <AppButton size="small" variant="ghost" @click="openEditModel(row)">
                    编辑
                  </AppButton>
                  <AppButton
                    size="small"
                    variant="secondary"
                    :loading="probingModelId === row.id"
                    :disabled="probingModelId !== null && probingModelId !== row.id"
                    loading-text="测试中…"
                    @click="runProbe(row)"
                  >
                    测试连接
                  </AppButton>
                  <AppButton
                    size="small"
                    variant="ghost"
                    @click="toggleModelStatus(row)"
                  >
                    {{ row.status === 'ENABLED' ? '停用' : '启用' }}
                  </AppButton>
                </div>
                <p v-if="probeErrors[row.id]" class="models__row-error" role="alert">
                  {{ probeErrors[row.id] }}
                </p>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <p class="acos-caption models__notice">{{ probeCostNotice }}</p>

        <div v-if="usage" class="models__usage">
          <h2 class="models__usage-title">探测用量（独立登记，阶段 03 纳入统一预算）</h2>
          <dl class="models__facts">
            <div>
              <dt>探测次数</dt>
              <dd>
                {{ usage.probeCalls }}
                <span v-if="usage.pendingProbes > 0" class="acos-caption models__usage-caveat">
                  （{{ usage.pendingProbes }} 次中断，结果与用量未知，未自动重试）
                </span>
              </dd>
            </div>
            <div>
              <dt>完整计量 / 部分计量 / 未计量</dt>
              <dd>
                {{ usage.measuredCalls }} / {{ usage.partiallyMeasuredCalls }} /
                {{ usage.unmeasuredCalls }}
              </dd>
            </div>
            <div>
              <dt>输入 Token</dt>
              <dd>
                {{ usage.promptTokens === null ? '未知' : usage.promptTokens }}
                <span
                  v-if="usage.promptTokens !== null && usage.promptTokensUnreportedCalls > 0"
                  class="acos-caption models__usage-caveat"
                >
                  （{{ usage.promptTokensUnreportedCalls }} 次调用未报告，未计入）
                </span>
              </dd>
            </div>
            <div>
              <dt>输出 Token</dt>
              <dd>
                {{ usage.completionTokens === null ? '未知' : usage.completionTokens }}
                <span
                  v-if="
                    usage.completionTokens !== null && usage.completionTokensUnreportedCalls > 0
                  "
                  class="acos-caption models__usage-caveat"
                >
                  （{{ usage.completionTokensUnreportedCalls }} 次调用未报告，未计入）
                </span>
              </dd>
            </div>
          </dl>
        </div>

        <div v-if="centre?.recentProbes.length" class="models__history">
          <h2 class="models__usage-title">最近探测记录</h2>
          <ul class="models__history-list">
            <li v-for="probe in centre.recentProbes" :key="probe.id">
              <StatusBadge
                :label="probeOutcomeLabel(probe.outcome)"
                :tone="probeOutcomeTone(probe.outcome)"
              />
              <span class="acos-caption">{{ formatTime(probe.startedAt) }}</span>
              <span>{{ probe.detail ?? probe.errorCode ?? '—' }}</span>
              <span class="acos-caption">
                调用 {{ probe.callCount }} 次 ·
                {{ probe.usageKnown ? '用量已读取' : '用量未知' }}
              </span>
            </li>
          </ul>
        </div>
      </section>

      <!-- Assignments -->
      <section v-else aria-label="员工模型分配" data-testid="tab-assignments">
        <EmptyState
          v-if="assignments.length === 0"
          title="还没有员工"
          description="完成公司初始化后，这里会显示每名员工的当前模型绑定。"
        />
        <div v-else class="models__table-wrap">
          <el-table :data="assignments" row-key="agentId" style="width: 100%">
            <el-table-column label="员工" min-width="160">
              <template #default="{ row }">
                <div class="models__cell-strong">{{ row.displayName }}</div>
                <div class="acos-caption">{{ row.employeeCode }}</div>
              </template>
            </el-table-column>
            <el-table-column label="岗位" min-width="140" prop="positionTitle" />
            <el-table-column label="当前模型" min-width="170">
              <template #default="{ row }">
                {{ row.modelLabel ?? '未绑定' }}
                <div v-if="row.providerName" class="acos-caption">
                  {{ row.providerName }}
                </div>
              </template>
            </el-table-column>
            <el-table-column label="连接状态" min-width="200">
              <template #default="{ row }">
                <div class="models__badges">
                  <StatusBadge
                    v-if="row.providerStatus"
                    :label="`供应商 ${statusLabel(row.providerStatus)}`"
                    :tone="row.providerStatus === 'ENABLED' ? 'success' : 'neutral'"
                  />
                  <StatusBadge
                    v-if="row.secretConfigured !== null"
                    :label="row.secretConfigured ? '密钥已配置' : '密钥未配置'"
                    :tone="row.secretConfigured ? 'success' : 'warning'"
                  />
                  <StatusBadge
                    v-if="row.modelId !== null"
                    :label="
                      row.jsonVerified === true
                        ? 'JSON 已验证'
                        : row.jsonVerified === false
                          ? 'JSON 未通过'
                          : 'JSON 未验证'
                    "
                    :tone="
                      row.jsonVerified === true
                        ? 'success'
                        : row.jsonVerified === false
                          ? 'danger'
                          : 'neutral'
                    "
                  />
                </div>
              </template>
            </el-table-column>
            <el-table-column label="绑定时间" min-width="160">
              <template #default="{ row }">{{ formatTime(row.assignedAt) }}</template>
            </el-table-column>
          </el-table>
        </div>
        <p class="acos-caption models__notice">
          在组织页的员工详情中更改绑定；改绑会结束旧绑定并保留历史。
        </p>
      </section>
    </template>

    <!-- Provider drawer -->
    <AppDrawer
      :model-value="providerDrawerOpen"
      :title="editingProvider ? '编辑供应商' : '添加供应商'"
      @update:model-value="(open: boolean) => (providerDrawerOpen = open)"
    >
      <div class="models__form">
        <div v-if="providerError" class="models__alert" role="alert">
          {{ providerError }}
        </div>

        <div v-if="!editingProvider && preset" class="models__preset">
          <p class="acos-caption">
            使用 DeepSeek 预设填充常见配置（地址、环境变量名、超时）；仍可修改。
          </p>
          <AppButton size="small" variant="secondary" @click="applyPreset">
            填入 DeepSeek 预设
          </AppButton>
        </div>

        <FormField
          label="供应商名称"
          field-name="provider-name"
          required
          :error="providerFieldErrors.name ?? null"
        >
          <template #default="{ controlId, describedBy, invalid }">
            <AppInput
              :id="controlId"
              v-model="formName"
              :described-by="describedBy"
              :invalid="invalid"
            />
          </template>
        </FormField>

        <FormField
          label="接口类型"
          field-name="provider-kind"
          required
          :error="providerFieldErrors.kind ?? null"
        >
          <template #default="{ controlId }">
            <select :id="controlId" v-model="formKind" class="models__select">
              <option value="DEEPSEEK">DeepSeek（OpenAI 兼容）</option>
              <option value="OPENAI_COMPATIBLE">自定义 OpenAI 兼容接口</option>
            </select>
          </template>
        </FormField>

        <FormField
          label="密钥引用（环境变量名）"
          field-name="provider-secret-ref"
          hint="填写服务端环境变量名，例如 DEEPSEEK_API_KEY。系统不接收、不显示密钥值；界面只显示是否已在服务端配置。"
          :error="providerFieldErrors.secretRef ?? null"
        >
          <template #default="{ controlId, describedBy, invalid }">
            <AppInput
              :id="controlId"
              v-model="formSecretRef"
              :described-by="describedBy"
              :invalid="invalid"
              placeholder="DEEPSEEK_API_KEY"
            />
          </template>
        </FormField>

        <FormField
          label="超时（秒）"
          field-name="provider-timeout"
          hint="1 到 120 秒。测试连接不会自动重试。"
          :error="providerFieldErrors.timeoutSeconds ?? null"
        >
          <template #default="{ controlId, describedBy, invalid }">
            <AppInput
              :id="controlId"
              v-model="formTimeoutText"
              :described-by="describedBy"
              :invalid="invalid"
            />
          </template>
        </FormField>

        <FormField
          label="备注（可选）"
          field-name="provider-notes"
          :error="providerFieldErrors.notes ?? null"
        >
          <template #default="{ controlId, describedBy, invalid }">
            <textarea
              :id="controlId"
              v-model="formNotes"
              class="models__textarea"
              rows="3"
              :aria-describedby="describedBy"
              :aria-invalid="invalid ? 'true' : undefined"
            />
          </template>
        </FormField>

        <!-- The example URL lives in the hint, not in a placeholder: a
             scheme-qualified literal inside an attribute defeats vue-tsc's
             slot-scope inference and produces bogus "cannot find name" errors. -->
        <FormField
          label="接口地址"
          field-name="provider-base-url"
          required
          :hint="`可以填写主机地址、带 /v1 的地址或完整 chat/completions 地址（例如 ${deepSeekUrlExample}）；不能包含账号或密码。`"
          :error="providerFieldErrors.baseUrl ?? null"
        >
          <template #default="{ controlId, describedBy, invalid }">
            <AppInput
              :id="controlId"
              v-model="formBaseUrl"
              :described-by="describedBy"
              :invalid="invalid"
            />
          </template>
        </FormField>
      </div>

      <template #footer>
        <AppButton
          variant="secondary"
          :disabled="savingProvider"
          @click="providerDrawerOpen = false"
        >
          取消
        </AppButton>
        <AppButton :loading="savingProvider" loading-text="保存中…" @click="saveProvider">
          保存配置
        </AppButton>
      </template>
    </AppDrawer>

    <!-- Model drawer -->
    <AppDrawer
      :model-value="modelDrawerOpen"
      :title="editingModel ? '编辑模型' : '添加模型'"
      @update:model-value="(open: boolean) => (modelDrawerOpen = open)"
    >
      <div class="models__form">
        <div v-if="modelError" class="models__alert" role="alert">{{ modelError }}</div>

        <FormField
          label="供应商"
          field-name="model-provider"
          required
          :error="modelFieldErrors.providerId ?? null"
        >
          <template #default="{ controlId }">
            <select
              :id="controlId"
              v-model="modelProviderId"
              class="models__select"
              :disabled="!!editingModel"
            >
              <option v-for="provider in providers" :key="provider.id" :value="provider.id">
                {{ provider.name }}
              </option>
            </select>
          </template>
        </FormField>

        <FormField
          label="模型 ID"
          field-name="model-logical-name"
          required
          hint="发给供应商的模型标识，例如 deepseek-v4.1-flash。不能包含空白字符。"
          :error="modelFieldErrors.logicalName ?? null"
        >
          <template #default="{ controlId, describedBy, invalid }">
            <AppInput
              :id="controlId"
              v-model="modelLogicalName"
              :described-by="describedBy"
              :invalid="invalid"
              :disabled="!!editingModel"
              placeholder="deepseek-v4.1-flash"
            />
          </template>
        </FormField>

        <FormField
          label="显示名称"
          field-name="model-label"
          hint="留空则使用模型 ID。"
          :error="modelFieldErrors.label ?? null"
        >
          <template #default="{ controlId, describedBy, invalid }">
            <AppInput
              :id="controlId"
              v-model="modelLabel"
              :described-by="describedBy"
              :invalid="invalid"
              placeholder="DeepSeek V4.1 Flash"
            />
          </template>
        </FormField>

        <FormField
          label="输出 Token 上限（可选）"
          field-name="model-max-tokens"
          :error="modelFieldErrors.maxOutputTokens ?? null"
        >
          <template #default="{ controlId, describedBy, invalid }">
            <AppInput
              :id="controlId"
              v-model="modelMaxTokens"
              :described-by="describedBy"
              :invalid="invalid"
              placeholder="留空表示不设置"
            />
          </template>
        </FormField>

        <p class="acos-caption">
          保存配置不会联网。能力（如 JSON 输出）只有在模型分区执行“测试连接”并成功后才会标记为已验证。
        </p>
      </div>

      <template #footer>
        <AppButton
          variant="secondary"
          :disabled="savingModel"
          @click="modelDrawerOpen = false"
        >
          取消
        </AppButton>
        <AppButton :loading="savingModel" loading-text="保存中…" @click="saveModel">
          保存模型
        </AppButton>
      </template>
    </AppDrawer>
  </div>
</template>

<style scoped>
.models {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-24);
}

.models__skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-8);
}

.models__skeleton-row {
  height: calc(var(--acos-layout-table-row-min) * 2);
  border-radius: var(--acos-radius-card);
  background: var(--acos-color-surface-subtle);
}

.models__tabs {
  display: flex;
  gap: var(--acos-space-4);
  border-bottom: var(--acos-control-border-width) solid var(--acos-color-border);
  overflow-x: auto;
}

.models__tab {
  min-height: var(--acos-layout-nav-item);
  padding: 0 var(--acos-space-16);
  border: 0;
  border-bottom: var(--acos-layout-nav-active-border) solid transparent;
  background: transparent;
  color: var(--acos-color-text-secondary);
  font: inherit;
  cursor: pointer;
  white-space: nowrap;
}

.models__tab--active {
  color: var(--acos-color-primary);
  border-bottom-color: var(--acos-color-primary);
  font-weight: 600;
}

.models__cards {
  display: flex;
  flex-direction: column;
  gap: var(--acos-layout-card-gap);
  list-style: none;
  margin: 0;
  padding: 0;
}

.models__card {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-16);
  padding: var(--acos-layout-card-padding);
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
}

.models__card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--acos-space-12);
  flex-wrap: wrap;
}

.models__card-title {
  font-size: var(--acos-font-subtitle-size);
  line-height: var(--acos-font-subtitle-line);
  font-weight: var(--acos-font-subtitle-weight);
}

.models__usage-caveat {
  display: block;
  color: var(--acos-color-text-secondary);
}

.models__mono {
  font-family: var(--acos-font-mono);
  word-break: break-all;
}

.models__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: var(--acos-space-12);
  margin: 0;
}

.models__facts dt {
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
  color: var(--acos-color-text-secondary);
}

.models__facts dd {
  margin: var(--acos-space-4) 0 0;
  display: flex;
  align-items: center;
  gap: var(--acos-space-8);
  flex-wrap: wrap;
}

.models__card-actions {
  display: flex;
  gap: var(--acos-space-8);
  flex-wrap: wrap;
}

.models__table-wrap {
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
  overflow-x: auto;
}

.models__cell-strong {
  font-weight: 500;
}

.models__row-actions {
  display: flex;
  gap: var(--acos-space-4);
  flex-wrap: wrap;
}

.models__row-error {
  margin-top: var(--acos-space-4);
  color: var(--acos-color-danger);
  font-size: var(--acos-font-caption-size);
}

.models__badges {
  display: flex;
  gap: var(--acos-space-4);
  flex-wrap: wrap;
}

.models__notice {
  margin-top: var(--acos-space-8);
  color: var(--acos-color-text-secondary);
}

.models__probe-result {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-4);
  margin-bottom: var(--acos-space-16);
  padding: var(--acos-space-16);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
  background: var(--acos-color-surface);
}

.models__probe-head {
  display: flex;
  align-items: center;
  gap: var(--acos-space-8);
  flex-wrap: wrap;
}

.models__usage,
.models__history {
  margin-top: var(--acos-space-24);
  padding: var(--acos-layout-card-padding);
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
}

.models__usage-title {
  font-size: var(--acos-font-subtitle-size);
  line-height: var(--acos-font-subtitle-line);
  font-weight: var(--acos-font-subtitle-weight);
  margin-bottom: var(--acos-space-12);
}

.models__history-list {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-8);
  list-style: none;
  margin: 0;
  padding: 0;
}

.models__history-list li {
  display: flex;
  align-items: center;
  gap: var(--acos-space-8);
  flex-wrap: wrap;
  padding-bottom: var(--acos-space-8);
  border-bottom: var(--acos-control-border-width) solid var(--acos-color-border);
}

.models__history-list li:last-child {
  border-bottom: 0;
}

.models__form {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-20);
}

.models__select {
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

.models__textarea {
  width: 100%;
  min-height: calc(var(--acos-control-textarea-min-height) - var(--acos-space-48));
  padding: var(--acos-space-12);
  border: var(--acos-control-border-width) solid var(--acos-color-control-border);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-surface);
  color: var(--acos-color-text);
  font-family: inherit;
  font-size: var(--acos-font-body-size);
  resize: vertical;
}

.models__preset {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--acos-space-8);
  padding: var(--acos-space-12);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-surface-subtle);
  flex-wrap: wrap;
}

.models__alert {
  padding: var(--acos-space-12);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-danger-subtle);
  color: var(--acos-color-danger);
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
}

.models__select:focus-visible,
.models__textarea:focus-visible {
  border-color: var(--acos-color-focus);
  outline: var(--acos-focus-width) solid var(--acos-color-focus);
  outline-offset: var(--acos-focus-offset);
}

@media (max-width: 767px) {
  .models__select,
  .models__textarea,
  .models__tab {
    font-size: 16px;
  }

  .models__tab {
    min-height: var(--acos-control-mobile-target);
  }
}
</style>
