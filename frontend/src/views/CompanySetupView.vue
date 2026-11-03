<script setup lang="ts">
/**
 * CompanySetupView -- the four-step company creation wizard.
 *
 * PAGES.md §1 requires: basic info -> tech-company template -> model config /
 * configure later -> confirm, with the step count stated on the page. Two
 * properties matter beyond layout:
 *
 * 1. **Idempotent submit.** One idempotency key is generated when the wizard
 *    opens and reused for every retry, so a double click or a refresh cannot
 *    create a second company. The server returns 200 for a repeat instead of
 *    creating anything.
 * 2. **Honest model step.** "稍后配置" is a real choice, and the model list comes
 *    from the server. When no model exists or no key is configured, the page says
 *    so instead of offering a fake selection.
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import AppButton from '@/components/AppButton.vue'
import AppInput from '@/components/AppInput.vue'
import ErrorState from '@/components/ErrorState.vue'
import FormField from '@/components/FormField.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { ApiError, NetworkError } from '@/api/client'
import { companyApi, modelCentreApi } from '@/api/endpoints'
import type {
  CompanyTemplateResponse,
  ModelCentreResponse,
} from '@/api/organization-types'
import AuthLayout from '@/layouts/AuthLayout.vue'
import { useSessionStore } from '@/stores/session'

const router = useRouter()
const session = useSessionStore()

const STEP_TITLES = ['基本信息', '公司模板', '模型配置', '确认创建'] as const
const step = ref(0)

const name = ref('')
const goal = ref('')

const template = ref<CompanyTemplateResponse | null>(null)
const centre = ref<ModelCentreResponse | null>(null)
const loading = ref(true)
const loadError = ref<{ message: string; requestId: string | null } | null>(null)

const modelChoice = ref<'later' | 'assign'>('later')
const selectedModelId = ref<number | null>(null)

const submitting = ref(false)
const formError = ref<string | null>(null)
const fieldErrors = ref<Record<string, string | null>>({})

/**
 * Generated once per wizard session and reused across retries. A refresh makes a
 * new key, which is correct: the server still refuses a second company, and the
 * owner is then sent to the existing one.
 */
const idempotencyKey = `company-wizard-${crypto.randomUUID()}`

const availableModels = computed(() =>
  (centre.value?.models ?? []).filter(
    (model) =>
      model.status === 'ENABLED' &&
      model.providerStatus !== 'DISABLED',
  ),
)

const canAdvance = computed(() => {
  if (step.value === 0) return name.value.trim().length > 0 && goal.value.trim().length > 0
  if (step.value === 2) return modelChoice.value === 'later' || selectedModelId.value !== null
  return true
})

const stepLabel = computed(() => `第 ${step.value + 1} 步，共 ${STEP_TITLES.length} 步`)

async function load() {
  loading.value = true
  loadError.value = null
  try {
    const [templateBody, centreBody] = await Promise.all([
      companyApi.template(),
      modelCentreApi.overview(),
    ])
    template.value = templateBody
    centre.value = centreBody
    const firstUsable = availableModels.value[0]
    if (firstUsable && centreBody.providers.some((p) => p.secretConfigured)) {
      modelChoice.value = 'assign'
      selectedModelId.value = firstUsable.id
    }
  } catch (error) {
    if (error instanceof ApiError) {
      loadError.value = { message: error.message, requestId: error.requestId }
    } else if (error instanceof NetworkError) {
      loadError.value = { message: error.message, requestId: null }
    } else {
      loadError.value = { message: '无法读取模板信息，请稍后重试。', requestId: null }
    }
  } finally {
    loading.value = false
  }
}

onMounted(load)

function next() {
  formError.value = null
  if (!canAdvance.value) return
  if (step.value < STEP_TITLES.length - 1) step.value += 1
}

function back() {
  formError.value = null
  if (step.value > 0) step.value -= 1
}

async function submit() {
  if (submitting.value) return
  formError.value = null
  fieldErrors.value = {}
  submitting.value = true
  try {
    const created = await companyApi.create({
      name: name.value.trim(),
      goal: goal.value.trim(),
      templateKey: template.value?.templateKey ?? 'tech-company',
      templateVersion: template.value?.templateVersion ?? 1,
      modelId: modelChoice.value === 'assign' ? selectedModelId.value : null,
      idempotencyKey,
    })
    session.markCompanyInitialized(created.name)
    await router.replace({ name: 'overview', query: { created: 'company' } })
  } catch (error) {
    if (error instanceof ApiError) {
      formError.value = error.message
      for (const item of error.fieldErrors) {
        fieldErrors.value[item.field] = item.message
      }
      if (error.code === 'COMPANY_ALREADY_EXISTS') {
        // Someone (or a retry) already created it: go there rather than retry.
        session.companyInitialized = true
        await router.replace({ name: 'overview' })
      }
    } else if (error instanceof NetworkError) {
      formError.value = error.message
    } else {
      formError.value = '创建公司时发生未知错误，请稍后重试。'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <AuthLayout
    eyebrow="公司初始化"
    title="创建你的 AI 公司"
    description="使用默认科技公司模板一次创建五个岗位和五个独立员工。创建后可在组织页调整资料、职责、汇报关系与模型。"
    :footnote="`默认模板会创建 ${template?.positions.length ?? 5} 个岗位；重复提交不会复制公司。`"
  >
    <ErrorState
      v-if="loadError"
      title="模板信息读取失败"
      :description="loadError.message"
      :request-id="loadError.requestId"
      :retrying="loading"
      @retry="load"
    />

    <div v-else-if="loading" class="wizard__loading" aria-live="polite" aria-busy="true">
      <span class="acos-visually-hidden">正在读取公司模板</span>
      <div class="wizard__skeleton" />
      <div class="wizard__skeleton wizard__skeleton--short" />
    </div>

    <template v-else>
      <ol class="wizard__steps" aria-label="创建步骤">
        <li
          v-for="(title, index) in STEP_TITLES"
          :key="title"
          class="wizard__step"
          :class="{
            'wizard__step--current': index === step,
            'wizard__step--done': index < step,
          }"
          :aria-current="index === step ? 'step' : undefined"
        >
          <span class="wizard__step-index" aria-hidden="true">{{ index + 1 }}</span>
          <span class="wizard__step-text">{{ title }}</span>
        </li>
      </ol>

      <p class="wizard__counter acos-caption">{{ stepLabel }}</p>

      <div v-if="formError" class="wizard__error" role="alert" data-testid="wizard-error">
        {{ formError }}
      </div>

      <!-- Step 1: basic info -->
      <div v-if="step === 0" class="wizard__panel" data-testid="wizard-step-basic">
        <FormField
          label="公司名称"
          field-name="company-name"
          required
          :error="fieldErrors.name ?? null"
        >
          <template #default="{ controlId, describedBy, invalid }">
            <AppInput
              :id="controlId"
              v-model="name"
              :described-by="describedBy"
              :invalid="invalid"
              :disabled="submitting"
              placeholder="例如：精一科技"
            />
          </template>
        </FormField>

        <FormField
          label="公司目标"
          field-name="company-goal"
          required
          hint="用于后续任务优先级；可随时在概览页修改。"
          :error="fieldErrors.goal ?? null"
        >
          <template #default="{ controlId, describedBy, invalid }">
            <textarea
              :id="controlId"
              v-model="goal"
              class="wizard__textarea"
              rows="5"
              :disabled="submitting"
              :aria-invalid="invalid ? 'true' : undefined"
              :aria-describedby="describedBy"
              placeholder="例如：为中小企业交付可验证的自动化研发能力。"
            />
          </template>
        </FormField>
      </div>

      <!-- Step 2: template -->
      <div v-else-if="step === 1" class="wizard__panel" data-testid="wizard-step-template">
        <div class="wizard__template-head">
          <h2 class="wizard__template-title">{{ template?.label }}</h2>
          <StatusBadge label="默认模板" tone="info" />
        </div>
        <p class="acos-text-secondary">{{ template?.description }}</p>

        <ul class="wizard__positions">
          <li
            v-for="position in template?.positions ?? []"
            :key="position.code"
            class="wizard__position"
          >
            <div class="wizard__position-head">
              <strong>{{ position.title }}</strong>
              <span class="acos-caption">
                {{
                  position.reportsToCode
                    ? `汇报给 ${template?.positions.find((p) => p.code === position.reportsToCode)?.title ?? position.reportsToCode}`
                    : '最高负责人'
                }}
              </span>
            </div>
            <p class="acos-caption wizard__position-duty">{{ position.duty }}</p>
            <p class="acos-caption">初始员工：{{ position.defaultAgentName }}</p>
          </li>
        </ul>
        <p class="acos-caption">
          模板是可编辑起点：创建后修改岗位或员工不会改动模板本身。
        </p>
      </div>

      <!-- Step 3: model -->
      <div v-else-if="step === 2" class="wizard__panel" data-testid="wizard-step-models">
        <fieldset class="wizard__choice-group">
          <legend class="wizard__choice-legend">模型配置</legend>

          <label class="wizard__choice">
            <input
              v-model="modelChoice"
              type="radio"
              value="later"
              name="model-choice"
              :disabled="submitting"
            />
            <span>
              <strong>稍后配置</strong>
              <span class="acos-caption wizard__choice-hint">
                先创建公司与员工；模型中心可随时配置并在组织页绑定。
              </span>
            </span>
          </label>

          <label class="wizard__choice">
            <input
              v-model="modelChoice"
              type="radio"
              value="assign"
              name="model-choice"
              :disabled="submitting || availableModels.length === 0"
            />
            <span>
              <strong>立即绑定一个模型</strong>
              <span class="acos-caption wizard__choice-hint">
                创建时把同一模型绑定到全部五名员工。
              </span>
            </span>
          </label>
        </fieldset>

        <div v-if="availableModels.length === 0" class="wizard__notice acos-caption">
          模型中心还没有启用的模型。可以先“稍后配置”，创建完成后再添加供应商与模型。
        </div>

        <FormField
          v-else
          label="选择模型"
          field-name="company-model"
          :hint="
            centre?.providers.some((p) => p.secretConfigured)
              ? undefined
              : '当前供应商尚未配置可用密钥；可以先绑定，密钥在模型中心填写后即可测试连接。'
          "
          :error="fieldErrors.modelId ?? null"
        >
          <template #default="{ controlId, describedBy }">
            <select
              :id="controlId"
              v-model="selectedModelId"
              class="wizard__select"
              :disabled="submitting || modelChoice === 'later'"
              :aria-describedby="describedBy"
            >
              <option :value="null" disabled>请选择模型</option>
              <option v-for="model in availableModels" :key="model.id" :value="model.id">
                {{ model.label }} · {{ model.providerName }}
              </option>
            </select>
          </template>
        </FormField>

        <p class="acos-caption">
          绑定只是配置，不代表已验证能力；是否支持 JSON 输出需要以模型中心的测试连接结果为准。
        </p>
      </div>

      <!-- Step 4: confirm -->
      <div v-else class="wizard__panel" data-testid="wizard-step-confirm">
        <dl class="wizard__summary">
          <div>
            <dt>公司名称</dt>
            <dd>{{ name.trim() || '—' }}</dd>
          </div>
          <div>
            <dt>公司目标</dt>
            <dd>{{ goal.trim() || '—' }}</dd>
          </div>
          <div>
            <dt>模板</dt>
            <dd>{{ template?.label }}（v{{ template?.templateVersion }}）</dd>
          </div>
          <div>
            <dt>将创建</dt>
            <dd>
              {{ template?.positions.length ?? 5 }} 个岗位 ·
              {{ template?.positions.length ?? 5 }} 个独立员工
            </dd>
          </div>
          <div>
            <dt>模型</dt>
            <dd>
              {{
                modelChoice === 'assign' && selectedModelId !== null
                  ? availableModels.find((m) => m.id === selectedModelId)?.label ?? '已选模型'
                  : '稍后配置'
              }}
            </dd>
          </div>
        </dl>
        <p class="acos-caption">
          创建会写入审计记录。公司只创建一次；重复提交不会产生第二家公司。
        </p>
      </div>

      <div class="wizard__actions">
        <AppButton
          v-if="step > 0"
          variant="secondary"
          :disabled="submitting"
          @click="back"
        >
          上一步
        </AppButton>
        <AppButton
          v-if="step < STEP_TITLES.length - 1"
          :disabled="!canAdvance"
          @click="next"
        >
          下一步
        </AppButton>
        <AppButton
          v-else
          size="large"
          block
          :loading="submitting"
          :disabled="!canAdvance"
          loading-text="创建中…"
          @click="submit"
        >
          创建公司
        </AppButton>
      </div>
    </template>
  </AuthLayout>
</template>

<style scoped>
.wizard__loading {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-8);
}

.wizard__skeleton {
  height: var(--acos-control-large-height);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-surface-subtle);
}

.wizard__skeleton--short {
  width: 60%;
}

.wizard__steps {
  display: flex;
  flex-wrap: wrap;
  gap: var(--acos-space-8);
  list-style: none;
  margin: 0;
  padding: 0;
}

.wizard__step {
  display: inline-flex;
  align-items: center;
  gap: var(--acos-space-4);
  padding: var(--acos-space-4) var(--acos-space-8);
  border-radius: var(--acos-radius-pill);
  background: var(--acos-color-surface-subtle);
  color: var(--acos-color-text-secondary);
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
}

.wizard__step--current {
  background: var(--acos-color-primary-subtle);
  color: var(--acos-color-primary);
  font-weight: 600;
}

.wizard__step--done {
  color: var(--acos-color-success);
}

.wizard__step-index {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.wizard__counter {
  color: var(--acos-color-text-secondary);
}

.wizard__error {
  padding: var(--acos-space-12);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-danger-subtle);
  color: var(--acos-color-danger);
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
}

.wizard__panel {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-20);
}

.wizard__textarea {
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

.wizard__textarea:focus-visible {
  border-color: var(--acos-color-focus);
  outline: var(--acos-focus-width) solid var(--acos-color-focus);
  outline-offset: var(--acos-focus-offset);
}

.wizard__textarea:disabled {
  background: var(--acos-color-disabled-bg);
  color: var(--acos-color-disabled-text);
}

.wizard__select {
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

.wizard__select:disabled {
  background: var(--acos-color-disabled-bg);
  color: var(--acos-color-disabled-text);
}

.wizard__template-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--acos-space-8);
  flex-wrap: wrap;
}

.wizard__template-title {
  font-size: var(--acos-font-subtitle-size);
  line-height: var(--acos-font-subtitle-line);
  font-weight: var(--acos-font-subtitle-weight);
}

.wizard__positions {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-12);
  list-style: none;
  margin: 0;
  padding: 0;
}

.wizard__position {
  padding: var(--acos-space-12);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-surface);
}

.wizard__position-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--acos-space-8);
  flex-wrap: wrap;
}

.wizard__position-duty {
  margin-top: var(--acos-space-4);
  color: var(--acos-color-text-secondary);
}

.wizard__choice-group {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-12);
  margin: 0;
  padding: 0;
  border: 0;
}

.wizard__choice-legend {
  padding: 0;
  font-size: var(--acos-font-label-size);
  font-weight: var(--acos-font-label-weight);
  margin-bottom: var(--acos-space-8);
}

.wizard__choice {
  display: flex;
  align-items: flex-start;
  gap: var(--acos-space-8);
  padding: var(--acos-space-12);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-control);
  cursor: pointer;
}

.wizard__choice input {
  margin-top: var(--acos-space-4);
  width: var(--acos-space-16);
  height: var(--acos-space-16);
  accent-color: var(--acos-color-primary);
}

.wizard__choice-hint {
  display: block;
  margin-top: var(--acos-space-4);
  color: var(--acos-color-text-secondary);
}

.wizard__notice {
  padding: var(--acos-space-12);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-warning-subtle);
  color: var(--acos-color-warning);
}

.wizard__summary {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-12);
  margin: 0;
}

.wizard__summary div {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-4);
}

.wizard__summary dt {
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
  color: var(--acos-color-text-secondary);
}

.wizard__summary dd {
  margin: 0;
  word-break: break-word;
}

.wizard__actions {
  display: flex;
  align-items: center;
  gap: var(--acos-space-8);
  flex-wrap: wrap;
}

.wizard__actions > :last-child {
  margin-left: auto;
}

@media (max-width: 767px) {
  .wizard__actions {
    flex-direction: column;
    align-items: stretch;
  }

  .wizard__actions > :last-child {
    margin-left: 0;
  }

  .wizard__select,
  .wizard__textarea {
    font-size: 16px;
  }
}
</style>
