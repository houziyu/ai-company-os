<script setup lang="ts">
/**
 * ComponentsShowcaseView -- design acceptance entry point.
 *
 * UI-SPEC.md §7 requires this page to cover buttons, input, select, status,
 * table, dialog, drawer, notification and empty state, and to *measure* the
 * real control heights rather than assume them. The measurement panel below
 * reads getBoundingClientRect() from the rendered DOM, so a regression in any
 * wrapper shows up here as a visible number.
 *
 * In production the route is not linked from the navigation; it stays reachable
 * by URL for acceptance runs.
 */
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { ElNotification, ElOption, ElSelect, ElTable, ElTableColumn } from 'element-plus'

import AppButton from '@/components/AppButton.vue'
import AppDrawer from '@/components/AppDrawer.vue'
import AppInput from '@/components/AppInput.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import FormField from '@/components/FormField.vue'
import PageHeader from '@/components/PageHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { colorValues, controlSizes, statusTone } from '@/styles/tokens'

const dialogOpen = ref(false)
const drawerOpen = ref(false)
const selectValue = ref('queued')
const inputValue = ref('')
const passwordValue = ref('')
const loadingButton = ref(false)
const tableLoading = ref(false)

function toggleLoading() {
  loadingButton.value = true
  window.setTimeout(() => {
    loadingButton.value = false
  }, 1200)
}

function toggleTableLoading() {
  tableLoading.value = !tableLoading.value
}

function notify(kind: 'success' | 'warning' | 'error') {
  const messages = {
    success: { title: '已保存', message: '配置保存成功。此通知 3 秒后自动关闭。' },
    warning: { title: '需要注意', message: '该警告不会自动消失，需要你确认。' },
    error: { title: '操作失败', message: '错误通知保留在页面上，直到你关闭。' },
  }
  ElNotification({
    ...messages[kind],
    type: kind,
    duration: kind === 'success' ? 3000 : 0,
    position: 'bottom-right',
  })
}

interface MeasurementTarget {
  label: string
  selector: string
  expected: number
  /** Table cells are minimums (content can wrap); controls are exact. */
  minOnly?: boolean
}

interface Measurement {
  label: string
  selector: string
  expected: number
  actual: number | null
  pass: boolean
}

const measurements = reactive<Measurement[]>([])

const measurementTargets: MeasurementTarget[] = [
  { label: 'AppButton small 高度', selector: '[data-measure="btn-small"]', expected: controlSizes.small },
  { label: 'AppButton medium 高度', selector: '[data-measure="btn-medium"]', expected: controlSizes.medium },
  { label: 'AppButton large 高度', selector: '[data-measure="btn-large"]', expected: controlSizes.large },
  { label: 'AppInput medium 高度', selector: '[data-measure="input-medium"]', expected: controlSizes.medium },
  { label: 'AppInput large 高度', selector: '[data-measure="input-large"]', expected: controlSizes.large },
  { label: '表头高度', selector: '.el-table__header th', expected: 44 },
  { label: '数据行高度', selector: '.el-table__body tr', expected: 56, minOnly: true },
]

async function measure() {
  await nextTick()
  measurements.length = 0
  for (const target of measurementTargets) {
    const element = document.querySelector<HTMLElement>(target.selector)
    const rect = element?.getBoundingClientRect()
    const actual = rect ? Math.round(rect.height) : null
    // Table rows are "at least" bounds per COMPONENTS.md, so a taller cell is
    // acceptable; control heights are exact.
    const ok =
      actual === null
        ? false
        : target.minOnly
          ? actual >= target.expected
          : actual === target.expected
    measurements.push({
      label: target.label,
      selector: target.selector,
      expected: target.expected,
      actual,
      pass: ok,
    })
  }
}

onMounted(async () => {
  // Element Plus renders table rows after mount, so wait one frame before
  // reading the DOM; otherwise table measurements report 0.
  await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
  await measure()
})

const allPassed = computed(
  () => measurements.length > 0 && measurements.every((item) => item.pass),
)

const statusSamples = [
  { label: '排队中', tone: statusTone.QUEUED },
  { label: '执行中', tone: statusTone.RUNNING },
  { label: '验证中', tone: statusTone.VERIFYING },
  { label: '等待审批', tone: statusTone.WAITING_APPROVAL },
  { label: '已阻塞', tone: statusTone.BLOCKED },
  { label: '已通过', tone: statusTone.SUCCEEDED },
  { label: '失败', tone: statusTone.FAILED },
  { label: '已取消', tone: statusTone.CANCELLED },
] as const

const tableRows = [
  { name: '示例项目 A', status: '已通过', budget: '12,400', updated: '10-29 09:12' },
  { name: '示例项目 B', status: '执行中', budget: '3,180', updated: '10-29 09:20' },
  { name: '示例项目 C', status: '已阻塞', budget: '0', updated: '10-28 22:03' },
]

// Values are read from the generated token module, never typed by hand, so
// this reference page cannot drift from design-tokens.json.
const colorReference = [
  { token: 'primary', value: colorValues.primary, usage: '主操作、链接、选中项', bg: 'var(--acos-color-primary)', fg: 'var(--acos-color-on-primary)' },
  { token: 'success', value: colorValues.success, usage: '已验证成功', bg: 'var(--acos-color-success)', fg: 'var(--acos-color-on-primary)' },
  { token: 'warning', value: colorValues.warning, usage: '等待、阻塞', bg: 'var(--acos-color-warning)', fg: 'var(--acos-color-on-primary)' },
  { token: 'danger', value: colorValues.danger, usage: '失败与危险动作', bg: 'var(--acos-color-danger)', fg: 'var(--acos-color-on-primary)' },
  { token: 'neutral', value: colorValues.neutral, usage: '排队、取消、归档', bg: 'var(--acos-color-neutral)', fg: 'var(--acos-color-on-primary)' },
  { token: 'text', value: colorValues.text, usage: '标题与正文', bg: 'var(--acos-color-text)', fg: 'var(--acos-color-surface)' },
]
</script>

<template>
  <div class="showcase">
    <PageHeader
      title="组件展示与尺寸实测"
      description="设计验收入口：验证 token 映射、公共组件尺寸与状态覆盖。此页仅用于开发与验收，不进入生产导航。"
    >
      <template #actions>
        <AppButton variant="secondary" @click="measure">重新实测尺寸</AppButton>
      </template>
    </PageHeader>

    <!-- Measurement -->
    <section class="showcase__section" aria-labelledby="measure-heading">
      <div class="showcase__section-head">
        <h2 id="measure-heading">实测尺寸</h2>
        <StatusBadge
          :label="allPassed ? '全部符合 token' : '存在偏差'"
          :tone="allPassed ? 'success' : 'danger'"
        />
      </div>
      <p class="showcase__note">
        以下数值由浏览器渲染后的 getBoundingClientRect() 读取，不是文档抄写。期望值来自
        design-tokens.json。
      </p>
      <div class="showcase__measure-grid" data-testid="measurements">
        <div
          v-for="item in measurements"
          :key="item.selector"
          class="showcase__measure"
          :class="{ 'showcase__measure--fail': !item.pass }"
          :data-pass="item.pass"
        >
          <span class="showcase__measure-label">{{ item.label }}</span>
          <span class="showcase__measure-value acos-tabular">
            {{ item.actual ?? '未找到' }}px / 期望 {{ item.expected }}px
          </span>
        </div>
      </div>
    </section>

    <!-- Buttons -->
    <section class="showcase__section" aria-labelledby="buttons-heading">
      <h2 id="buttons-heading">按钮</h2>
      <p class="showcase__note">S 32 用于表格行，M 40 用于页头与表单，L 48 用于登录与初始化提交。</p>

      <div class="showcase__row">
        <AppButton data-measure="btn-small" size="small">查看详情</AppButton>
        <AppButton data-measure="btn-medium">保存配置</AppButton>
        <AppButton data-measure="btn-large" size="large">创建公司</AppButton>
      </div>

      <div class="showcase__row">
        <AppButton>主要操作</AppButton>
        <AppButton variant="secondary">次要操作</AppButton>
        <AppButton variant="ghost">文本操作</AppButton>
        <AppButton variant="danger">取消执行</AppButton>
        <AppButton variant="danger-text">删除</AppButton>
        <AppButton disabled>预算不足</AppButton>
      </div>

      <div class="showcase__row">
        <AppButton :loading="loadingButton" loading-text="保存中…" @click="toggleLoading">
          点击测试加载
        </AppButton>
        <AppButton variant="secondary" :loading="true" loading-text="测试连接中…">
          测试连接
        </AppButton>
        <AppButton aria-label="搜索" icon-only>
          <template #icon>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8">
              <circle cx="10.5" cy="10.5" r="6" />
              <path d="m15 15 4.5 4.5" />
            </svg>
          </template>
        </AppButton>
      </div>
    </section>

    <!-- Inputs -->
    <section class="showcase__section" aria-labelledby="inputs-heading">
      <h2 id="inputs-heading">输入与选择</h2>
      <div class="showcase__form">
        <FormField label="项目名称" field-name="showcase-name" required hint="字段高度 40px，帮助文字 12px。">
          <template #default="{ controlId, describedBy, invalid }">
            <AppInput
              :id="controlId"
              v-model="inputValue"
              data-measure="input-medium"
              placeholder="示例：任务管理工具"
              :described-by="describedBy"
              :invalid="invalid"
            />
          </template>
        </FormField>

        <FormField label="登录密码" field-name="showcase-password" required error="密码至少需要 12 个字符。">
          <template #default="{ controlId, describedBy, invalid }">
            <AppInput
              :id="controlId"
              v-model="passwordValue"
              type="password"
              data-measure="input-large"
              size="large"
              :described-by="describedBy"
              :invalid="invalid"
            />
          </template>
        </FormField>

        <FormField label="任务状态筛选" field-name="showcase-select" hint="Element Plus 选择器，单列筛选使用。">
          <template #default="{ controlId }">
            <ElSelect :id="controlId" v-model="selectValue" size="default" class="showcase__select">
              <ElOption label="排队中" value="queued" />
              <ElOption label="执行中" value="running" />
              <ElOption label="已通过" value="succeeded" />
            </ElSelect>
          </template>
        </FormField>
      </div>
    </section>

    <!-- Status -->
    <section class="showcase__section" aria-labelledby="status-heading">
      <h2 id="status-heading">状态标签</h2>
      <p class="showcase__note">颜色不单独表达含义：每个状态都有中文文本。</p>
      <div class="showcase__row">
        <StatusBadge
          v-for="sample in statusSamples"
          :key="sample.label"
          :label="sample.label"
          :tone="sample.tone"
        />
      </div>
    </section>

    <!-- Colours -->
    <section class="showcase__section" aria-labelledby="color-heading">
      <h2 id="color-heading">语义颜色</h2>
      <div class="showcase__colors">
        <div v-for="item in colorReference" :key="item.token" class="showcase__color">
          <div
            class="showcase__color-swatch"
            :style="{ background: item.bg, color: item.fg }"
          >
            <span>白字示例</span>
          </div>
          <strong>{{ item.token }}</strong>
          <code>{{ item.value }}</code>
          <span class="acos-caption">{{ item.usage }}</span>
        </div>
      </div>
    </section>

    <!-- Table -->
    <section class="showcase__section" aria-labelledby="table-heading">
      <div class="showcase__section-head">
        <h2 id="table-heading">表格</h2>
        <AppButton variant="secondary" size="small" @click="toggleTableLoading">
          {{ tableLoading ? '结束加载态' : '模拟加载态' }}
        </AppButton>
      </div>
      <div class="showcase__table-wrap">
        <ElTable
          v-loading="tableLoading"
          :data="tableRows"
          class="showcase__table"
          empty-text="没有符合筛选条件的数据"
        >
          <ElTableColumn prop="name" label="项目" min-width="180" />
          <ElTableColumn prop="status" label="状态" width="120" />
          <ElTableColumn prop="budget" label="已用 Token" width="140" align="right" />
          <ElTableColumn prop="updated" label="更新时间" width="140" />
          <ElTableColumn label="操作" width="120">
            <template #default>
              <AppButton variant="ghost" size="small">查看</AppButton>
            </template>
          </ElTableColumn>
        </ElTable>
      </div>
      <p class="showcase__note">
        表格行使用 Element Plus 渲染；行高实测目标 56px，见上方“实测尺寸”面板。
      </p>
    </section>

    <!-- Overlays -->
    <section class="showcase__section" aria-labelledby="overlay-heading">
      <h2 id="overlay-heading">弹窗、抽屉与通知</h2>
      <div class="showcase__row">
        <AppButton variant="secondary" @click="dialogOpen = true">打开确认弹窗</AppButton>
        <AppButton variant="secondary" @click="drawerOpen = true">打开详情抽屉</AppButton>
        <AppButton variant="secondary" @click="notify('success')">成功通知</AppButton>
        <AppButton variant="secondary" @click="notify('warning')">警告通知</AppButton>
        <AppButton variant="secondary" @click="notify('error')">错误通知</AppButton>
      </div>
    </section>

    <!-- States -->
    <section class="showcase__section" aria-labelledby="states-heading">
      <h2 id="states-heading">空状态与错误状态</h2>
      <div class="showcase__stack">
        <EmptyState
          title="还没有任务"
          description="空状态必须说明原因并给出一个可行操作，不能显示编造的 0 值行。"
          phase-note="示例：阶段 03 开放"
        >
          <template #action>
            <AppButton variant="secondary" size="small">示例操作</AppButton>
          </template>
        </EmptyState>
        <ErrorState
          title="加载失败"
          description="服务暂时不可用，请稍后重试。错误状态保留请求编号以便排查。"
          request-id="8f3c1a2b4d5e6f708192a3b4c5d6e7f8"
        />
      </div>
    </section>

    <ConfirmDialog
      v-model="dialogOpen"
      title="取消这次执行"
      description="取消后无法恢复，已产生的证据会保留。示例弹窗仅演示交互，不调用接口。"
      confirm-label="取消执行"
      variant="danger"
      @confirm="dialogOpen = false"
    />

    <AppDrawer v-model="drawerOpen" title="执行详情（示例）">
      <p class="showcase__note">
        抽屉标题与底部操作区固定，内容区滚动。此处内容为静态示例。
      </p>
      <template #footer>
        <AppButton variant="secondary" @click="drawerOpen = false">关闭</AppButton>
        <AppButton @click="drawerOpen = false">保存</AppButton>
      </template>
    </AppDrawer>
  </div>
</template>

<style scoped>
.showcase {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-24);
}

.showcase__section {
  background: var(--acos-color-surface);
  border: var(--acos-control-border-width) solid var(--acos-color-border);
  border-radius: var(--acos-radius-card);
  padding: var(--acos-layout-card-padding);
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-16);
}

.showcase__section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--acos-space-12);
  flex-wrap: wrap;
}

.showcase__note {
  color: var(--acos-color-text-secondary);
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
}

.showcase__row {
  display: flex;
  align-items: center;
  gap: var(--acos-control-gap);
  flex-wrap: wrap;
}

.showcase__form {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-20);
  max-width: var(--acos-layout-form-max);
}

.showcase__select {
  width: 240px;
  max-width: 100%;
}

.showcase__measure-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: var(--acos-space-8);
}

.showcase__measure {
  display: flex;
  justify-content: space-between;
  gap: var(--acos-space-8);
  padding: var(--acos-space-8) var(--acos-space-12);
  border-radius: var(--acos-radius-control);
  background: var(--acos-color-success-subtle);
  color: var(--acos-color-success);
  font-size: var(--acos-font-caption-size);
  line-height: var(--acos-font-caption-line);
}

.showcase__measure--fail {
  background: var(--acos-color-danger-subtle);
  color: var(--acos-color-danger);
}

.showcase__measure-value {
  font-weight: 600;
}

.showcase__colors {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--acos-space-16);
}

.showcase__color {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-4);
}

.showcase__color-swatch {
  height: var(--acos-space-64);
  border-radius: var(--acos-radius-control);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--acos-font-caption-size);
  margin-bottom: var(--acos-space-4);
}

.showcase__color code {
  font-family: var(--acos-font-mono);
  font-size: var(--acos-font-caption-size);
}

.showcase__table-wrap {
  overflow-x: auto;
}

.showcase__table {
  width: 100%;
  min-width: 640px;
}

.showcase__stack {
  display: flex;
  flex-direction: column;
  gap: var(--acos-space-16);
}
</style>
